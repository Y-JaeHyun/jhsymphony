"""JHSymphony — Multi-Repo Service Runner"""
import asyncio
import logging
import os
import platform
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "src")

from jhsymphony.config import load_config, RepoConfig
from jhsymphony.orchestrator.dispatcher import Dispatcher
from jhsymphony.orchestrator.lease import LeaseManager
from jhsymphony.orchestrator.reconciler import Reconciler
from jhsymphony.orchestrator.scheduler import Scheduler
from jhsymphony.providers.claude import ClaudeProvider
from jhsymphony.providers.codex import CodexProvider
from jhsymphony.providers.gemini import GeminiProvider
from jhsymphony.providers.router import ProviderRouter
from jhsymphony.storage.sqlite import SQLiteStorage
from jhsymphony.tracker.github import GitHubTracker
from jhsymphony.workspace.manager import WorkspaceManager
from jhsymphony.dashboard.app import create_app
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from rich.console import Console

console = Console()
REPOS_ROOT = Path.home() / ".jhsymphony" / "repos"


def _ensure_repo_clone(repo: str, gh_token: str) -> Path:
    """Clone repo if not already present. Returns local path."""
    slug = repo.replace("/", "__")
    local_path = REPOS_ROOT / slug
    if not local_path.exists():
        REPOS_ROOT.mkdir(parents=True, exist_ok=True)
        url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
        os.system(f"git clone {url} {local_path}")
    else:
        # Fetch latest
        os.system(f"cd {local_path} && git fetch origin --prune 2>/dev/null")
    # Ensure remote has token for push
    url = f"https://x-access-token:{gh_token}@github.com/{repo}.git"
    os.system(f"cd {local_path} && git remote set-url origin {url} 2>/dev/null")
    return local_path


async def run_repo(
    repo_config: RepoConfig,
    storage: SQLiteStorage,
    router: ProviderRouter,
    gh_token: str,
    max_concurrent: int,
    budget_daily: float,
    budget_per_run: float,
    lease_ttl: int,
    ws_root_base: Path,
):
    """Run orchestration loop for a single repo."""
    repo = repo_config.repo
    slug = repo.replace("/", "-")
    logger = logging.getLogger(f"jhsymphony.repo.{slug}")

    # Clone/fetch repo
    repo_path = _ensure_repo_clone(repo, gh_token)

    # Per-repo tracker
    tracker = GitHubTracker(repo=repo, label=repo_config.label, token=gh_token)

    # Per-repo workspace manager
    ws_root = ws_root_base / slug
    ws_root.mkdir(parents=True, exist_ok=True)
    workspace_mgr = WorkspaceManager(
        workspace_root=ws_root,
        repo_path=repo_path,
        cleanup_on_success=False,
        keep_on_failure=True,
    )

    # Shared components
    owner_id = f"{platform.node()}-{uuid.uuid4().hex[:8]}"
    lease_mgr = LeaseManager(storage=storage, owner_id=owner_id, ttl_sec=lease_ttl)

    dispatcher = Dispatcher(
        storage=storage,
        lease_manager=lease_mgr,
        workspace_manager=workspace_mgr,
        provider_router=router,
        tracker=tracker,
        max_concurrent=max_concurrent,
        budget_daily_limit=budget_daily,
        budget_per_run_limit=budget_per_run,
    )

    reconciler = Reconciler(storage=storage, tracker=tracker, dispatcher=dispatcher)

    scheduler = Scheduler(
        storage=storage,
        tracker=tracker,
        dispatcher=dispatcher,
        reconciler=reconciler,
        poll_interval_sec=repo_config.poll_interval_sec,
    )

    logger.info("Monitoring %s (label=%s, poll=%ds)", repo, repo_config.label, repo_config.poll_interval_sec)
    await scheduler.run()


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_config(Path("jhsymphony.yaml"))
    repos = config.get_repos()

    if not repos:
        console.print("[red]Error[/red]: No repos configured")
        return

    console.print(f"\n[bold cyan]JHSymphony[/bold cyan] starting...")
    console.print(f"  Repos: [green]{len(repos)}[/green]")
    for r in repos:
        console.print(f"    - {r.repo} (label={r.label})")
    console.print(f"  Max agents: {config.orchestrator.max_concurrent_agents}")
    console.print(f"  Budget: ${config.budget.daily_limit_usd}/day\n")

    # Storage (shared across all repos)
    db_path = str(Path(config.storage.path).expanduser())
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    storage = SQLiteStorage(db_path)
    await storage.initialize()

    # GitHub token
    gh_token = os.environ.get("GITHUB_TOKEN") or os.popen("gh auth token").read().strip()

    # Providers (shared across all repos)
    providers = {}
    if config.providers.claude:
        p = config.providers.claude
        providers["claude"] = ClaudeProvider(command=p.command, model=p.model, max_turns=p.max_turns)
    if config.providers.codex:
        p = config.providers.codex
        providers["codex"] = CodexProvider(command=p.command, model=p.model, sandbox=p.sandbox or "read-only")
    if config.providers.gemini:
        p = config.providers.gemini
        providers["gemini"] = GeminiProvider(command=p.command, model=p.model, max_turns=p.max_turns)

    router = ProviderRouter(
        default_provider=config.providers.default,
        providers=providers,
        routing_rules=config.routing,
    )

    ws_root_base = Path(config.workspace.root).expanduser()

    # Dashboard
    app = create_app(storage)
    static_dir = Path("src/jhsymphony/dashboard/static")
    if static_dir.exists():
        @app.get("/")
        async def serve_index():
            return FileResponse(static_dir / "index.html")
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="static")

    import uvicorn
    server_config = uvicorn.Config(
        app, host=config.dashboard.host, port=config.dashboard.port, log_level="warning",
    )
    server = uvicorn.Server(server_config)

    console.print(f"[green]Dashboard[/green]: http://localhost:{config.dashboard.port}")
    console.print(f"[green]Service running[/green] — monitoring {len(repos)} repos...\n")

    # Launch per-repo tasks + dashboard
    tasks = []
    for repo_config in repos:
        tasks.append(asyncio.create_task(
            run_repo(
                repo_config=repo_config,
                storage=storage,
                router=router,
                gh_token=gh_token,
                max_concurrent=config.orchestrator.max_concurrent_agents,
                budget_daily=config.budget.daily_limit_usd,
                budget_per_run=config.budget.per_run_limit_usd,
                lease_ttl=config.orchestrator.lease_ttl_sec,
                ws_root_base=ws_root_base,
            ),
            name=f"repo-{repo_config.repo}",
        ))
    tasks.append(asyncio.create_task(server.serve(), name="dashboard"))

    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await storage.close()
        console.print("\n[yellow]Service stopped.[/yellow]")


if __name__ == "__main__":
    asyncio.run(main())
