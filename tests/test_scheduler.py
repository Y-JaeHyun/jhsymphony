import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jhsymphony.models import Issue, IssueState
from jhsymphony.orchestrator.scheduler import Scheduler
from jhsymphony.storage.sqlite import SQLiteStorage


@pytest.fixture
async def storage(tmp_dir: Path):
    store = SQLiteStorage(str(tmp_dir / "test.sqlite"))
    await store.initialize()
    yield store
    await store.close()


@pytest.fixture
def mock_deps(storage):
    tracker = AsyncMock()
    dispatcher = AsyncMock()
    reconciler = AsyncMock()
    return tracker, dispatcher, reconciler


@pytest.fixture
def scheduler(storage, mock_deps):
    tracker, dispatcher, reconciler = mock_deps
    return Scheduler(
        storage=storage,
        tracker=tracker,
        dispatcher=dispatcher,
        reconciler=reconciler,
        poll_interval_sec=1,
    )


async def test_single_tick_dispatches_new_issues(scheduler, mock_deps):
    tracker, dispatcher, reconciler = mock_deps
    tracker.fetch_candidates.return_value = [
        Issue(id="gh-1", number=1, repo="o/r", title="Fix bug", labels=["jhsymphony"]),
    ]
    dispatcher.can_dispatch.return_value = True
    dispatcher.dispatch.return_value = "run-1"
    await scheduler.tick()
    dispatcher.dispatch.assert_called_once()


async def test_tick_skips_already_active_issues(scheduler, mock_deps, storage):
    tracker, dispatcher, reconciler = mock_deps
    await storage.upsert_issue(
        Issue(id="gh-1", number=1, repo="o/r", title="A", state=IssueState.RUNNING)
    )
    tracker.fetch_candidates.return_value = [
        Issue(id="gh-1", number=1, repo="o/r", title="A")
    ]
    await scheduler.tick()
    dispatcher.dispatch.assert_not_called()


async def test_tick_calls_reconciler(scheduler, mock_deps):
    tracker, dispatcher, reconciler = mock_deps
    tracker.fetch_candidates.return_value = []
    await scheduler.tick()
    reconciler.reconcile.assert_called_once()


async def test_tick_upserts_new_candidate(scheduler, mock_deps, storage):
    tracker, dispatcher, reconciler = mock_deps
    tracker.fetch_candidates.return_value = [
        Issue(id="gh-5", number=5, repo="o/r", title="New issue"),
    ]
    dispatcher.dispatch.return_value = "run-5"
    await scheduler.tick()
    issue = await storage.get_issue("gh-5")
    assert issue is not None


async def test_tick_handles_tracker_exception_gracefully(scheduler, mock_deps):
    tracker, dispatcher, reconciler = mock_deps
    tracker.fetch_candidates.side_effect = RuntimeError("network error")
    # Should not raise; exceptions are caught and logged
    await scheduler.tick()
    dispatcher.dispatch.assert_not_called()


async def test_run_stop(scheduler, mock_deps):
    tracker, dispatcher, reconciler = mock_deps
    tracker.fetch_candidates.return_value = []

    async def stop_soon():
        await asyncio.sleep(0.05)
        await scheduler.stop()

    asyncio.create_task(stop_soon())
    await asyncio.wait_for(scheduler.run(), timeout=5.0)
    assert not scheduler._running
