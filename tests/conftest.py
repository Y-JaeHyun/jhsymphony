import asyncio
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sample_config(tmp_dir: Path) -> Path:
    config_path = tmp_dir / "jhsymphony.yaml"
    config_path.write_text("""
project:
  name: "test-project"
  repo: "test-owner/test-repo"

tracker:
  kind: github
  label: "jhsymphony"
  poll_interval_sec: 5

orchestrator:
  max_concurrent_agents: 2
  max_retries: 1
  retry_backoff_sec: 1
  lease_ttl_sec: 60

providers:
  default: claude
  claude:
    command: "echo"
    model: "test"
    max_turns: 3

routing: []

workspace:
  root: "{workspace_root}"
  cleanup_on_success: true
  keep_on_failure: true

review:
  enabled: false
  provider: gemini
  auto_approve: false

budget:
  daily_limit_usd: 10.0
  per_run_limit_usd: 5.0
  alert_threshold_pct: 80

dashboard:
  port: 0
  host: "127.0.0.1"

storage:
  backend: sqlite
  path: "{db_path}"
""".replace("{workspace_root}", str(tmp_dir / "workspaces")).replace(
        "{db_path}", str(tmp_dir / "test.sqlite")
    ))
    return config_path
