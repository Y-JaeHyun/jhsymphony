import os
from pathlib import Path

import pytest

from jhsymphony.config import JHSymphonyConfig, load_config


def test_load_config_from_file(sample_config: Path):
    config = load_config(sample_config)
    assert config.project.name == "test-project"
    assert config.project.repo == "test-owner/test-repo"
    assert config.tracker.kind == "github"
    assert config.tracker.label == "jhsymphony"
    assert config.orchestrator.max_concurrent_agents == 2


def test_load_config_env_var_substitution(tmp_dir: Path):
    config_path = tmp_dir / "env_config.yaml"
    config_path.write_text("""
project:
  name: "test"
  repo: "owner/repo"
tracker:
  kind: github
  label: "test"
  poll_interval_sec: 30
  webhook_secret: $TEST_WEBHOOK_SECRET
orchestrator:
  max_concurrent_agents: 5
  max_retries: 3
  retry_backoff_sec: 60
  lease_ttl_sec: 3600
providers:
  default: claude
  claude:
    command: "claude"
    model: "opus"
    max_turns: 30
routing: []
workspace:
  root: "~/.jhsymphony/workspaces"
  cleanup_on_success: true
  keep_on_failure: true
review:
  enabled: false
  provider: gemini
  auto_approve: false
budget:
  daily_limit_usd: 50.0
  per_run_limit_usd: 10.0
  alert_threshold_pct: 80
dashboard:
  port: 8080
  host: "0.0.0.0"
storage:
  backend: sqlite
  path: "/tmp/test.sqlite"
""")
    os.environ["TEST_WEBHOOK_SECRET"] = "my-secret-123"
    config = load_config(config_path)
    assert config.tracker.webhook_secret == "my-secret-123"
    del os.environ["TEST_WEBHOOK_SECRET"]


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.yaml"))


def test_config_provider_defaults(sample_config: Path):
    config = load_config(sample_config)
    assert config.providers.default == "claude"
    assert config.providers.claude is not None
    assert config.providers.claude.command == "echo"


def test_config_budget(sample_config: Path):
    config = load_config(sample_config)
    assert config.budget.daily_limit_usd == 10.0
    assert config.budget.per_run_limit_usd == 5.0


def test_tracker_config_bot_login(sample_config):
    import yaml
    raw = sample_config.read_text()
    data = yaml.safe_load(raw)
    data["tracker"]["bot_login"] = "Y-JaeHyun"
    sample_config.write_text(yaml.dump(data))
    config = load_config(sample_config)
    assert config.tracker.bot_login == "Y-JaeHyun"
