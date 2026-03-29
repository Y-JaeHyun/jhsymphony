from typer.testing import CliRunner
from jhsymphony.cli.app import app

runner = CliRunner()


def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "JHSymphony" in result.output


def test_cli_config_check(sample_config):
    result = runner.invoke(app, ["config", "check", "--config", str(sample_config)])
    assert result.exit_code == 0
    assert "valid" in result.output.lower() or "ok" in result.output.lower()


def test_cli_config_check_missing():
    result = runner.invoke(app, ["config", "check", "--config", "/nonexistent.yaml"])
    assert result.exit_code != 0
