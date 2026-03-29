from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from jhsymphony.workspace.isolation import run_subprocess


@dataclass
class Workspace:
    path: Path
    branch: str
    issue_key: str


class WorkspaceManager:
    def __init__(
        self,
        workspace_root: Path,
        repo_path: Path,
        cleanup_on_success: bool = True,
        keep_on_failure: bool = True,
    ) -> None:
        self._root = Path(workspace_root)
        self._repo = Path(repo_path)
        self._cleanup_on_success = cleanup_on_success
        self._keep_on_failure = keep_on_failure
        self._workspaces: dict[str, Workspace] = {}

    def _ws_path(self, issue_key: str) -> Path:
        safe_key = issue_key.replace("/", "-").replace(" ", "-")
        return self._root / safe_key

    async def create(self, issue_key: str) -> Workspace:
        if issue_key in self._workspaces:
            return self._workspaces[issue_key]

        ws_path = self._ws_path(issue_key)
        branch = f"jhsymphony/{issue_key}"

        if not ws_path.exists():
            # Prune stale worktree entries
            await run_subprocess(
                ["git", "worktree", "prune"],
                cwd=str(self._repo), env=None, timeout_sec=30,
            )
            # Create branch (ignore error if already exists)
            await run_subprocess(
                ["git", "branch", branch],
                cwd=str(self._repo), env=None, timeout_sec=30,
            )
            result = await run_subprocess(
                ["git", "worktree", "add", "-f", str(ws_path), branch],
                cwd=str(self._repo), env=None, timeout_sec=30,
            )
            if result.returncode != 0 and not ws_path.exists():
                raise RuntimeError(f"Failed to create worktree: {result.stderr}")

        ws = Workspace(path=ws_path, branch=branch, issue_key=issue_key)
        self._workspaces[issue_key] = ws
        return ws

    async def cleanup(self, issue_key: str, success: bool) -> None:
        ws_path = self._ws_path(issue_key)

        if success and self._cleanup_on_success:
            await run_subprocess(
                ["git", "worktree", "remove", str(ws_path), "--force"],
                cwd=str(self._repo),
                env=None,
                timeout_sec=30,
            )
            if ws_path.exists():
                shutil.rmtree(ws_path, ignore_errors=True)
        elif not success and not self._keep_on_failure:
            await run_subprocess(
                ["git", "worktree", "remove", str(ws_path), "--force"],
                cwd=str(self._repo),
                env=None,
                timeout_sec=30,
            )
            if ws_path.exists():
                shutil.rmtree(ws_path, ignore_errors=True)

        self._workspaces.pop(issue_key, None)

    async def get(self, issue_key: str) -> Workspace | None:
        if issue_key in self._workspaces:
            return self._workspaces[issue_key]
        ws_path = self._ws_path(issue_key)
        if ws_path.exists():
            branch = f"jhsymphony/{issue_key}"
            ws = Workspace(path=ws_path, branch=branch, issue_key=issue_key)
            self._workspaces[issue_key] = ws
            return ws
        return None
