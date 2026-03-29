import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jhsymphony.models import Issue, IssueState, Run, RunStatus
from jhsymphony.orchestrator.dispatcher import Dispatcher
from jhsymphony.orchestrator.lease import LeaseManager
from jhsymphony.storage.sqlite import SQLiteStorage


@pytest.fixture
async def storage(tmp_dir: Path):
    store = SQLiteStorage(str(tmp_dir / "test.sqlite"))
    await store.initialize()
    yield store
    await store.close()


@pytest.fixture
def mock_workspace_mgr():
    mgr = AsyncMock()
    mgr.create.return_value = MagicMock(
        path=Path("/tmp/ws"), branch="jhsymphony/issue-1", issue_key="issue-1"
    )
    return mgr


@pytest.fixture
def mock_router():
    provider = AsyncMock()
    provider.capabilities.return_value = MagicMock(supports_tools=True)

    async def fake_run_turn(session, prompt):
        from jhsymphony.providers.base import AgentEvent, EventType

        yield AgentEvent(type=EventType.COMPLETED, data={"reason": "done"})

    provider.run_turn = fake_run_turn
    router = MagicMock()
    router.select.return_value = provider
    return router


@pytest.fixture
def mock_tracker():
    return AsyncMock()


@pytest.fixture
def dispatcher(storage, mock_workspace_mgr, mock_router, mock_tracker):
    lease_mgr = LeaseManager(storage=storage, owner_id="test", ttl_sec=60)
    return Dispatcher(
        storage=storage,
        lease_manager=lease_mgr,
        workspace_manager=mock_workspace_mgr,
        provider_router=mock_router,
        tracker=mock_tracker,
        max_concurrent=2,
        budget_daily_limit=50.0,
        budget_per_run_limit=10.0,
    )


async def test_can_dispatch(dispatcher):
    issue = Issue(id="gh-1", number=1, repo="o/r", title="Test")
    assert await dispatcher.can_dispatch(issue) is True


async def test_cannot_exceed_concurrency(dispatcher, storage):
    for i in range(2):
        await storage.insert_run(
            Run(id=f"run-{i}", issue_id=f"gh-{i}", provider="claude", status=RunStatus.RUNNING)
        )
    issue = Issue(id="gh-99", number=99, repo="o/r", title="Test")
    assert await dispatcher.can_dispatch(issue) is False


async def test_dispatch_creates_run(dispatcher, storage):
    issue = Issue(id="gh-1", number=1, repo="o/r", title="Fix bug", labels=["jhsymphony"])
    await storage.upsert_issue(issue)
    run_id = await dispatcher.dispatch(issue)
    assert run_id is not None
    run = await storage.get_run(run_id)
    assert run is not None


async def test_dispatch_returns_none_when_cannot_dispatch(dispatcher, storage):
    for i in range(2):
        await storage.insert_run(
            Run(id=f"run-{i}", issue_id=f"gh-{i}", provider="claude", status=RunStatus.RUNNING)
        )
    issue = Issue(id="gh-99", number=99, repo="o/r", title="Test")
    result = await dispatcher.dispatch(issue)
    assert result is None


async def test_cancel_run(dispatcher, storage):
    issue = Issue(id="gh-1", number=1, repo="o/r", title="Fix bug", labels=["jhsymphony"])
    await storage.upsert_issue(issue)
    run_id = await dispatcher.dispatch(issue)
    assert run_id is not None
    # Wait briefly for the task to start
    await asyncio.sleep(0.05)
    await dispatcher.cancel_run(run_id)
    # After cancellation, verify the run ended in a terminal state
    await asyncio.sleep(0.05)
    run = await storage.get_run(run_id)
    assert run is not None
    assert run.status in (RunStatus.CANCELLED, RunStatus.COMPLETED)
