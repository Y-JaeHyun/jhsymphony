from __future__ import annotations

import asyncio
import logging
import os
import platform
import uuid
from pathlib import Path

from rich.console import Console
from rich.table import Table

from jhsymphony.config import load_config, JHSymphonyConfig
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

logger = logging.getLogger(__name__)
console = Console()


def _build_providers(config: JHSymphonyConfig) -> dict:
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
    return providers


async def run_app(config_path: Path, dashboard: bool = True) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    config = load_config(config_path)
    console.print(f"[bold]JHSymphony[/bold] starting for [cyan]{config.project.repo}[/cyan]")

    db_path = str(Path(config.storage.path).expanduser())
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    storage = SQLiteStorage(db_path)
    await storage.initialize()

    gh_token = os.environ.get("GITHUB_TOKEN", "")
    tracker = GitHubTracker(repo=config.project.repo, label=config.tracker.label, token=gh_token)

    repo_path = Path.cwd()
    ws_root = Path(config.workspace.root).expanduser()
    ws_root.mkdir(parents=True, exist_ok=True)
    workspace_mgr = WorkspaceManager(
        workspace_root=ws_root, repo_path=repo_path,
        cleanup_on_success=config.workspace.cleanup_on_success,
        keep_on_failure=config.workspace.keep_on_failure,
    )

    providers = _build_providers(config)
    router = ProviderRouter(default_provider=config.providers.default, providers=providers, routing_rules=config.routing)

    owner_id = f"{platform.node()}-{uuid.uuid4().hex[:8]}"
    lease_mgr = LeaseManager(storage=storage, owner_id=owner_id, ttl_sec=config.orchestrator.lease_ttl_sec)

    dispatcher = Dispatcher(
        storage=storage, lease_manager=lease_mgr, workspace_manager=workspace_mgr,
        provider_router=router, tracker=tracker,
        max_concurrent=config.orchestrator.max_concurrent_agents,
        budget_daily_limit=config.budget.daily_limit_usd,
        budget_per_run_limit=config.budget.per_run_limit_usd,
    )

    reconciler = Reconciler(storage=storage, tracker=tracker, dispatcher=dispatcher)
    scheduler = Scheduler(
        storage=storage, tracker=tracker, dispatcher=dispatcher,
        reconciler=reconciler, poll_interval_sec=config.tracker.poll_interval_sec,
    )

    tasks = [asyncio.create_task(scheduler.run())]

    if dashboard:
        from jhsymphony.dashboard.app import create_app
        import uvicorn
        fastapi_app = create_app(storage)
        server_config = uvicorn.Config(
            fastapi_app, host=config.dashboard.host, port=config.dashboard.port, log_level="info",
        )
        server = uvicorn.Server(server_config)
        tasks.append(asyncio.create_task(server.serve()))
        console.print(f"[green]Dashboard[/green] at http://{config.dashboard.host}:{config.dashboard.port}")

    console.print("[green]Running[/green] - Press Ctrl+C to stop")
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        await scheduler.stop()
        await storage.close()
        console.print("[yellow]Stopped[/yellow]")


async def show_status(config_path: Path) -> None:
    config = load_config(config_path)
    db_path = str(Path(config.storage.path).expanduser())
    if not Path(db_path).exists():
        console.print("[yellow]No database found. Has JHSymphony been started?[/yellow]")
        return
    storage = SQLiteStorage(db_path)
    await storage.initialize()

    active_runs = await storage.list_active_runs()
    daily_cost = await storage.sum_daily_cost()

    table = Table(title="JHSymphony Status")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Active runs", str(len(active_runs)))
    table.add_row("Daily cost", f"${daily_cost:.2f}")
    table.add_row("Repo", config.project.repo)
    console.print(table)
    await storage.close()
