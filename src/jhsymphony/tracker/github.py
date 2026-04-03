from __future__ import annotations

import asyncio
import logging
import random

import httpx

from jhsymphony.models import Issue, IssueState

_API_BASE = "https://api.github.com"

logger = logging.getLogger(__name__)

_RETRYABLE_EXCEPTIONS = (httpx.TransportError, httpx.TimeoutException)
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


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

    async def _get_with_retry(
        self, url: str, params: dict | None = None, max_retries: int = 3,
    ) -> httpx.Response:
        """GET with exponential backoff retry for transient errors."""
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                resp = await self._client.get(url, params=params)
                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries - 1:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning("GET %s returned %d, retrying in %.1fs", url, resp.status_code, wait)
                    await asyncio.sleep(wait)
                    continue
                return resp
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning("GET %s failed (%s), retrying in %.1fs", url, exc, wait)
                    await asyncio.sleep(wait)
        raise last_exc  # type: ignore[misc]

    async def fetch_candidates(self) -> list[Issue]:
        url = f"{_API_BASE}/repos/{self._repo}/issues"
        resp = await self._get_with_retry(url, params={
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
        resp = await self._get_with_retry(url, params={"per_page": 100})
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
        resp = await self._get_with_retry(url)
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
