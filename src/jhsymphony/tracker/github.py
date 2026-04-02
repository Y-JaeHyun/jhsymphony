from __future__ import annotations

import httpx

from jhsymphony.models import Issue, IssueState

_API_BASE = "https://api.github.com"


def _repo_slug(repo: str) -> str:
    """Convert 'owner/repo' to 'owner-repo' for use in IDs."""
    return repo.replace("/", "-")


class GitHubTracker:
    def __init__(self, repo: str, label: str, token: str | None = None) -> None:
        self._repo = repo
        self._label = label
        self._slug = _repo_slug(repo)
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(headers=headers, timeout=30.0)

    async def fetch_candidates(self) -> list[Issue]:
        url = f"{_API_BASE}/repos/{self._repo}/issues"
        resp = await self._client.get(url, params={
            "labels": self._label,
            "state": "open",
            "per_page": 100,
        })
        resp.raise_for_status()
        issues = []
        for item in resp.json():
            if "pull_request" in item:
                continue
            labels = [l["name"] for l in item.get("labels", [])]
            issues.append(Issue(
                id=f"gh-{self._slug}-{item['number']}",
                number=item["number"],
                repo=self._repo,
                title=item["title"],
                body=item.get("body", "") or "",
                labels=labels,
                state=IssueState.PENDING,
            ))
        return issues

    async def post_comment(self, issue_number: int, body: str) -> int:
        url = f"{_API_BASE}/repos/{self._repo}/issues/{issue_number}/comments"
        resp = await self._client.post(url, json={"body": body})
        resp.raise_for_status()
        return resp.json()["id"]

    async def fetch_comments(self, issue_number: int) -> list[dict]:
        """Fetch all comments on an issue, ordered by creation time."""
        url = f"{_API_BASE}/repos/{self._repo}/issues/{issue_number}/comments"
        resp = await self._client.get(url, params={"per_page": 100})
        resp.raise_for_status()
        return [
            {
                "id": c["id"],
                "author": c["user"]["login"],
                "body": c["body"],
                "created_at": c["created_at"],
            }
            for c in resp.json()
        ]

    async def create_pr(self, title: str, head: str, base: str, body: str, draft: bool = False) -> dict:
        url = f"{_API_BASE}/repos/{self._repo}/pulls"
        resp = await self._client.post(url, json={
            "title": title, "head": head, "base": base, "body": body, "draft": draft,
        })
        resp.raise_for_status()
        return resp.json()

    async def add_labels(self, issue_number: int, labels: list[str]) -> None:
        url = f"{_API_BASE}/repos/{self._repo}/issues/{issue_number}/labels"
        resp = await self._client.post(url, json={"labels": labels})
        resp.raise_for_status()

    async def remove_label(self, issue_number: int, label: str) -> None:
        url = f"{_API_BASE}/repos/{self._repo}/issues/{issue_number}/labels/{label}"
        resp = await self._client.delete(url)
        if resp.status_code != 404:
            resp.raise_for_status()

    async def check_approved(self, issue_number: int) -> bool:
        """Check if an issue has the 'approved' label."""
        return await self.check_label(issue_number, "approved")

    async def check_label(self, issue_number: int, label: str) -> bool:
        """Check if an issue has a specific label."""
        url = f"{_API_BASE}/repos/{self._repo}/issues/{issue_number}/labels"
        resp = await self._client.get(url)
        resp.raise_for_status()
        labels = [l["name"] for l in resp.json()]
        return label in labels

    async def close_issue(self, issue_number: int) -> None:
        url = f"{_API_BASE}/repos/{self._repo}/issues/{issue_number}"
        resp = await self._client.patch(url, json={"state": "closed"})
        resp.raise_for_status()

    async def push_branch(self, workspace_path: str, branch: str) -> None:
        """Push the workspace branch to remote."""
        import asyncio
        import os
        env = os.environ.copy()
        proc = await asyncio.create_subprocess_exec(
            "git", "push", "origin", branch, "--force",
            cwd=workspace_path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def close(self) -> None:
        await self._client.aclose()
