import asyncio
import os
from pathlib import Path

import pytest

from jhsymphony.workspace.manager import WorkspaceManager
from jhsymphony.workspace.isolation import run_subprocess


@pytest.fixture
def ws_root(tmp_dir: Path) -> Path:
    root = tmp_dir / "workspaces"
    root.mkdir()
    return root


@pytest.fixture
def bare_repo(tmp_dir: Path) -> Path:
    repo = tmp_dir / "repo"
    repo.mkdir()
    os.system(f"cd {repo} && git init && git commit --allow-empty -m 'init'")
    return repo


@pytest.fixture
def manager(ws_root: Path, bare_repo: Path) -> WorkspaceManager:
    return WorkspaceManager(
        workspace_root=ws_root,
        repo_path=bare_repo,
        cleanup_on_success=True,
        keep_on_failure=True,
    )


async def test_create_workspace(manager):
    ws = await manager.create("issue-1")
    assert ws.path.exists()
    assert ws.branch == "jhsymphony/issue-1"


async def test_create_workspace_idempotent(manager):
    ws1 = await manager.create("issue-1")
    ws2 = await manager.create("issue-1")
    assert ws1.path == ws2.path


async def test_cleanup_workspace(manager):
    ws = await manager.create("issue-2")
    assert ws.path.exists()
    await manager.cleanup("issue-2", success=True)
    assert not ws.path.exists()


async def test_keep_on_failure(manager):
    ws = await manager.create("issue-3")
    await manager.cleanup("issue-3", success=False)
    assert ws.path.exists()


async def test_run_subprocess():
    result = await run_subprocess(
        command=["echo", "hello"],
        cwd="/tmp",
        env=None,
        timeout_sec=10,
    )
    assert result.returncode == 0
    assert "hello" in result.stdout


async def test_run_subprocess_timeout():
    result = await run_subprocess(
        command=["sleep", "10"],
        cwd="/tmp",
        env=None,
        timeout_sec=1,
    )
    assert result.returncode != 0
    assert result.timed_out is True
