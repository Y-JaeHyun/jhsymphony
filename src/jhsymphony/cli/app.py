from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console

import jhsymphony
from jhsymphony.config import load_config

app = typer.Typer(name="jhsymphony", help="JHSymphony - Autonomous Agent Orchestration")
config_app = typer.Typer(help="Configuration management")
app.add_typer(config_app, name="config")

console = Console()

_DEFAULT_CONFIG = Path("jhsymphony.yaml")


def _version_callback(value: bool):
    if value:
        console.print(f"JHSymphony v{jhsymphony.__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
):
    pass


@config_app.command("check")
def config_check(
    config: Path = typer.Option(_DEFAULT_CONFIG, "--config", "-c", help="Config file path"),
):
    """Validate configuration file."""
    try:
        cfg = load_config(config)
        console.print(f"[green]OK[/green] - Config is valid")
        console.print(f"  Project: {cfg.project.name}")
        console.print(f"  Repo: {cfg.project.repo}")
        console.print(f"  Tracker: {cfg.tracker.kind}")
        console.print(f"  Default provider: {cfg.providers.default}")
    except FileNotFoundError:
        console.print(f"[red]Error[/red]: Config file not found: {config}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error[/red]: {e}")
        raise typer.Exit(1)


@app.command()
def start(
    config: Path = typer.Option(_DEFAULT_CONFIG, "--config", "-c"),
    dashboard: bool = typer.Option(True, "--dashboard/--no-dashboard"),
):
    """Start JHSymphony orchestrator."""
    from jhsymphony.main import run_app
    asyncio.run(run_app(config, dashboard=dashboard))


@app.command()
def status(
    config: Path = typer.Option(_DEFAULT_CONFIG, "--config", "-c"),
):
    """Show current orchestrator status."""
    from jhsymphony.main import show_status
    asyncio.run(show_status(config))
