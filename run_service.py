"""JHSymphony — Real Service Runner"""
import asyncio
import logging
import os
import platform
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "src")

from jhsymphony.config import load_config
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

REPO_LOCAL_PATH = Path.home() / ".jhsymphony" / "jhsymphony-test"


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_config(Path("jhsymphony.yaml"))

    console.print(f"\n[bold cyan]JHSymphony[/bold cyan] starting...")
    console.print(f"  Repo: [green]{config.project.repo}[/green]")
    console.print(f"  Label: [yellow]{config.tracker.label}[/yellow]")
    console.print(f"  Poll: every {config.tracker.poll_interval_sec}s")
    console.print(f"  Max agents: {config.orchestrator.max_concurrent_agents}")
    console.print(f"  Budget: ${config.budget.daily_limit_usd}/day\n")

    # Storage
    db_path = str(Path(config.storage.path).expanduser())
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    storage = SQLiteStorage(db_path)
    await storage.initialize()

    # Tracker
    gh_token = os.environ.get("GITHUB_TOKEN") or os.popen("gh auth token").read().strip()
    tracker = GitHubTracker(
        repo=config.project.repo,
        label=config.tracker.label,
        token=gh_token,
    )

    # Workspace — use the cloned test repo
    ws_root = Path(config.workspace.root).expanduser()
    ws_root.mkdir(parents=True, exist_ok=True)
    workspace_mgr = WorkspaceManager(
        workspace_root=ws_root,
        repo_path=REPO_LOCAL_PATH,
        cleanup_on_success=config.workspace.cleanup_on_success,
        keep_on_failure=config.workspace.keep_on_failure,
    )

    # Providers
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

    # Orchestrator
    owner_id = f"{platform.node()}-{uuid.uuid4().hex[:8]}"
    lease_mgr = LeaseManager(storage=storage, owner_id=owner_id, ttl_sec=config.orchestrator.lease_ttl_sec)

    dispatcher = Dispatcher(
        storage=storage,
        lease_manager=lease_mgr,
        workspace_manager=workspace_mgr,
        provider_router=router,
        tracker=tracker,
        max_concurrent=config.orchestrator.max_concurrent_agents,
        budget_daily_limit=config.budget.daily_limit_usd,
        budget_per_run_limit=config.budget.per_run_limit_usd,
    )

    reconciler = Reconciler(storage=storage, tracker=tracker, dispatcher=dispatcher)

    scheduler = Scheduler(
        storage=storage,
        tracker=tracker,
        dispatcher=dispatcher,
        reconciler=reconciler,
        poll_interval_sec=config.tracker.poll_interval_sec,
    )

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
    console.print(f"[green]Service running[/green] — monitoring GitHub Issues...\n")

    tasks = [
        asyncio.create_task(scheduler.run()),
        asyncio.create_task(server.serve()),
    ]

    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await scheduler.stop()
        await storage.close()
        console.print("\n[yellow]Service stopped.[/yellow]")


if __name__ == "__main__":
    asyncio.run(main())
