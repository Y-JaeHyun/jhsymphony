from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jhsymphony.models import Issue, IssueState, Run, RunStatus
from jhsymphony.orchestrator.reconciler import Reconciler
from jhsymphony.storage.sqlite import SQLiteStorage


@pytest.fixture
async def storage(tmp_dir: Path):
    store = SQLiteStorage(str(tmp_dir / "test.sqlite"))
    await store.initialize()
    yield store
    await store.close()


@pytest.fixture
def mock_tracker():
    return AsyncMock()


@pytest.fixture
def mock_dispatcher():
    return AsyncMock()


@pytest.fixture
def reconciler(storage, mock_tracker, mock_dispatcher):
    return Reconciler(storage=storage, tracker=mock_tracker, dispatcher=mock_dispatcher)


async def test_cancel_run_for_closed_issue(reconciler, storage, mock_tracker):
    await storage.upsert_issue(
        Issue(id="gh-1", number=1, repo="o/r", title="A", state=IssueState.RUNNING)
    )
    await storage.insert_run(
        Run(id="r1", issue_id="gh-1", provider="claude", status=RunStatus.RUNNING)
    )
    mock_tracker.fetch_candidates.return_value = []
    await reconciler.reconcile(current_candidates=[], active_issue_ids={"gh-1"})
    issue = await storage.get_issue("gh-1")
    assert issue.state == IssueState.CANCELLED


async def test_no_change_for_still_open_issue(reconciler, storage):
    await storage.upsert_issue(
        Issue(id="gh-1", number=1, repo="o/r", title="A", state=IssueState.RUNNING)
    )
    await storage.insert_run(
        Run(id="r1", issue_id="gh-1", provider="claude", status=RunStatus.RUNNING)
    )
    await reconciler.reconcile(
        current_candidates=[Issue(id="gh-1", number=1, repo="o/r", title="A")],
        active_issue_ids={"gh-1"},
    )
    issue = await storage.get_issue("gh-1")
    assert issue.state == IssueState.RUNNING


async def test_reconcile_empty_active_is_noop(reconciler, storage):
    # No active issues, nothing to reconcile
    await reconciler.reconcile(current_candidates=[], active_issue_ids=set())
    # No assertions needed; just verify no exceptions


async def test_reconcile_cancels_multiple_runs(reconciler, storage, mock_dispatcher):
    await storage.upsert_issue(
        Issue(id="gh-2", number=2, repo="o/r", title="B", state=IssueState.RUNNING)
    )
    await storage.insert_run(
        Run(id="r1", issue_id="gh-2", provider="claude", status=RunStatus.RUNNING)
    )
    await storage.insert_run(
        Run(id="r2", issue_id="gh-2", provider="claude", status=RunStatus.RUNNING)
    )
    await reconciler.reconcile(current_candidates=[], active_issue_ids={"gh-2"})
    issue = await storage.get_issue("gh-2")
    assert issue.state == IssueState.CANCELLED
    # Both runs should have been cancelled via dispatcher
    assert mock_dispatcher.cancel_run.call_count == 2
