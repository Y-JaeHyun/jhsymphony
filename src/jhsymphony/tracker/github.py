from __future__ import annotations

import httpx

from jhsymphony.models import Issue, IssueState

_API_BASE = "https://api.github.com"


class GitHubTracker:
    def __init__(self, repo: str, label: str, token: str | None = None) -> None:
        self._repo = repo
        self._label = label
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
                id=f"gh-{item['number']}",
                number=item["number"],
                repo=self._repo,
                title=item["title"],
                labels=labels,
                state=IssueState.PENDING,
            ))
        return issues

    async def post_comment(self, issue_number: int, body: str) -> None:
        url = f"{_API_BASE}/repos/{self._repo}/issues/{issue_number}/comments"
        resp = await self._client.post(url, json={"body": body})
        resp.raise_for_status()

    async def create_pr(self, title: str, head: str, base: str, body: str) -> dict:
        url = f"{_API_BASE}/repos/{self._repo}/pulls"
        resp = await self._client.post(url, json={
            "title": title, "head": head, "base": base, "body": body,
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

    async def close(self) -> None:
        await self._client.aclose()
