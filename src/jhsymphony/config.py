from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel


class ProjectConfig(BaseModel):
    name: str
    repo: str


class TrackerConfig(BaseModel):
    kind: str = "github"
    label: str = "jhsymphony"
    poll_interval_sec: int = 30
    webhook_secret: str | None = None


class OrchestratorConfig(BaseModel):
    max_concurrent_agents: int = 5
    max_retries: int = 3
    retry_backoff_sec: int = 60
    lease_ttl_sec: int = 3600


class ProviderEntry(BaseModel):
    command: str
    model: str
    max_turns: int = 30
    sandbox: str | None = None


class ProvidersConfig(BaseModel):
    default: str = "claude"
    claude: ProviderEntry | None = None
    codex: ProviderEntry | None = None
    gemini: ProviderEntry | None = None


class RoutingRule(BaseModel):
    label: str
    provider: str
    role: str | None = None


class WorkspaceConfig(BaseModel):
    root: str = "~/.jhsymphony/workspaces"
    cleanup_on_success: bool = True
    keep_on_failure: bool = True


class ReviewConfig(BaseModel):
    enabled: bool = True
    provider: str = "gemini"
    auto_approve: bool = False


class BudgetConfig(BaseModel):
    daily_limit_usd: float = 50.0
    per_run_limit_usd: float = 10.0
    alert_threshold_pct: int = 80


class DashboardConfig(BaseModel):
    port: int = 8080
    host: str = "0.0.0.0"


class StorageConfig(BaseModel):
    backend: str = "sqlite"
    path: str = "~/.jhsymphony/db.sqlite"


class JHSymphonyConfig(BaseModel):
    project: ProjectConfig
    tracker: TrackerConfig = TrackerConfig()
    orchestrator: OrchestratorConfig = OrchestratorConfig()
    providers: ProvidersConfig = ProvidersConfig()
    routing: list[RoutingRule] = []
    workspace: WorkspaceConfig = WorkspaceConfig()
    review: ReviewConfig = ReviewConfig()
    budget: BudgetConfig = BudgetConfig()
    dashboard: DashboardConfig = DashboardConfig()
    storage: StorageConfig = StorageConfig()


_ENV_VAR_PATTERN = re.compile(r"\$([A-Z_][A-Z0-9_]*)")


def _substitute_env_vars(text: str) -> str:
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))
    return _ENV_VAR_PATTERN.sub(replacer, text)


def load_config(path: Path) -> JHSymphonyConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    raw = path.read_text()
    raw = _substitute_env_vars(raw)
    data = yaml.safe_load(raw)
    return JHSymphonyConfig(**data)
