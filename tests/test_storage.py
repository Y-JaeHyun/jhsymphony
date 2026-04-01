from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jhsymphony.models import Issue, IssueState, Run, RunStatus, UsageRecord
from jhsymphony.storage.sqlite import SQLiteStorage


@pytest.fixture
async def storage(tmp_dir: Path):
    db_path = str(tmp_dir / "test.sqlite")
    store = SQLiteStorage(db_path)
    await store.initialize()
    yield store
    await store.close()


async def test_upsert_and_get_issue(storage):
    issue = Issue(id="gh-1", number=1, repo="o/r", title="Test")
    await storage.upsert_issue(issue)
    result = await storage.get_issue("gh-1")
    assert result is not None
    assert result.title == "Test"


async def test_get_issue_not_found(storage):
    result = await storage.get_issue("nonexistent")
    assert result is None


async def test_list_issues_by_state(storage):
    await storage.upsert_issue(Issue(id="1", number=1, repo="o/r", title="A", state=IssueState.PENDING))
    await storage.upsert_issue(Issue(id="2", number=2, repo="o/r", title="B", state=IssueState.RUNNING))
    await storage.upsert_issue(Issue(id="3", number=3, repo="o/r", title="C", state=IssueState.PENDING))
    pending = await storage.list_issues(state=IssueState.PENDING)
    assert len(pending) == 2


async def test_update_issue_state(storage):
    await storage.upsert_issue(Issue(id="1", number=1, repo="o/r", title="A"))
    await storage.update_issue_state("1", IssueState.RUNNING)
    result = await storage.get_issue("1")
    assert result.state == IssueState.RUNNING


async def test_insert_and_get_run(storage):
    run = Run(id="run-1", issue_id="gh-1", provider="claude")
    await storage.insert_run(run)
    result = await storage.get_run("run-1")
    assert result is not None
    assert result.provider == "claude"


async def test_update_run_status(storage):
    run = Run(id="run-1", issue_id="gh-1", provider="claude")
    await storage.insert_run(run)
    await storage.update_run_status("run-1", RunStatus.COMPLETED)
    result = await storage.get_run("run-1")
    assert result.status == RunStatus.COMPLETED


async def test_list_active_runs(storage):
    await storage.insert_run(Run(id="r1", issue_id="1", provider="claude", status=RunStatus.RUNNING))
    await storage.insert_run(Run(id="r2", issue_id="2", provider="codex", status=RunStatus.COMPLETED))
    active = await storage.list_active_runs()
    assert len(active) == 1
    assert active[0].id == "r1"


async def test_insert_and_list_events(storage):
    await storage.insert_event("run-1", 1, "message.delta", {"text": "hi"})
    await storage.insert_event("run-1", 2, "tool.call", {"name": "read"})
    events = await storage.list_events("run-1")
    assert len(events) == 2
    assert events[0]["seq"] == 1


async def test_record_and_sum_usage(storage):
    record = UsageRecord(run_id="run-1", provider="claude", input_tokens=1000, output_tokens=500, estimated_cost_usd=0.05)
    await storage.record_usage(record)
    total = await storage.sum_daily_cost()
    assert total == pytest.approx(0.05)


async def test_acquire_and_release_lease(storage):
    acquired = await storage.acquire_lease("gh-1", "worker-1", ttl_sec=60)
    assert acquired is True
    duplicate = await storage.acquire_lease("gh-1", "worker-2", ttl_sec=60)
    assert duplicate is False
    await storage.release_lease("gh-1")
    reacquired = await storage.acquire_lease("gh-1", "worker-2", ttl_sec=60)
    assert reacquired is True


async def test_acquire_expired_lease(storage):
    acquired = await storage.acquire_lease("gh-1", "worker-1", ttl_sec=-1)
    assert acquired is True
    stolen = await storage.acquire_lease("gh-1", "worker-2", ttl_sec=60)
    assert stolen is True


async def test_upsert_and_get_issue_body(storage):
    issue = Issue(id="gh-body", number=10, repo="o/r", title="Test Body", body="This is the issue body text.")
    await storage.upsert_issue(issue)
    result = await storage.get_issue("gh-body")
    assert result is not None
    assert result.body == "This is the issue body text."


async def test_upsert_issue_body_preserved_on_update(storage):
    issue = Issue(id="gh-body2", number=11, repo="o/r", title="T", body="Original body")
    await storage.upsert_issue(issue)
    issue.state = IssueState.RUNNING
    issue.body = "Original body"
    await storage.upsert_issue(issue)
    result = await storage.get_issue("gh-body2")
    assert result.body == "Original body"


async def test_run_analysis_comment_id(storage):
    run = Run(id="run-ac", issue_id="gh-1", provider="claude", analysis_comment_id=12345)
    await storage.insert_run(run)
    result = await storage.get_run("run-ac")
    assert result is not None
    assert result.analysis_comment_id == 12345


async def test_get_analysis_run(storage):
    await storage.insert_run(
        Run(id="run-phase1", issue_id="gh-1", provider="codex", status=RunStatus.COMPLETED)
    )
    await storage.insert_run(
        Run(id="run-phase2", issue_id="gh-1", provider="claude", status=RunStatus.RUNNING)
    )
    result = await storage.get_analysis_run("gh-1")
    assert result is not None
    assert result.id == "run-phase1"


async def test_get_analysis_run_not_found(storage):
    result = await storage.get_analysis_run("nonexistent")
    assert result is None
