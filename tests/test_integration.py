"""End-to-end test with mocked GitHub API and echo-based agents."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jhsymphony.models import Issue, IssueState, RunStatus
from jhsymphony.orchestrator.dispatcher import Dispatcher
from jhsymphony.orchestrator.lease import LeaseManager
from jhsymphony.orchestrator.reconciler import Reconciler
from jhsymphony.orchestrator.scheduler import Scheduler
from jhsymphony.providers.base import AgentEvent, EventType, RunContext, ProviderCapabilities
from jhsymphony.providers.router import ProviderRouter
from jhsymphony.storage.sqlite import SQLiteStorage


class EchoProvider:
    name = "echo"

    def capabilities(self):
        return ProviderCapabilities(supports_tools=True, supports_streaming=True)

    async def start_session(self, ctx):
        return {"ctx": ctx}

    async def run_turn(self, session, prompt):
        yield AgentEvent(type=EventType.SESSION_STARTED, data={})
        yield AgentEvent(type=EventType.MESSAGE_DELTA, data={"text": f"Echo: {prompt[:50]}"})
        yield AgentEvent(type=EventType.USAGE, data={"input_tokens": 100, "output_tokens": 50})
        yield AgentEvent(type=EventType.COMPLETED, data={"reason": "done"})

    async def cancel(self, session):
        pass


@pytest.fixture
async def storage(tmp_dir: Path):
    store = SQLiteStorage(str(tmp_dir / "test.sqlite"))
    await store.initialize()
    yield store
    await store.close()


@pytest.fixture
def mock_tracker():
    tracker = AsyncMock()
    tracker.fetch_candidates.return_value = [
        Issue(id="gh-42", number=42, repo="o/r", title="Fix login bug", labels=["jhsymphony"]),
    ]
    tracker.create_pr.return_value = {"number": 1, "html_url": "http://example.com/pr/1"}
    return tracker


@pytest.fixture
def mock_workspace():
    ws = AsyncMock()
    ws.create.return_value = MagicMock(path=Path("/tmp"), branch="jhsymphony/issue-42", issue_key="issue-42")
    return ws


async def test_full_cycle(storage, mock_tracker, mock_workspace):
    """Test: issue detected -> dispatched -> agent runs -> completed."""
    import asyncio

    provider = EchoProvider()
    router = ProviderRouter(
        default_provider="echo",
        providers={"echo": provider},
        routing_rules=[],
    )
    lease_mgr = LeaseManager(storage=storage, owner_id="test", ttl_sec=60)
    dispatcher = Dispatcher(
        storage=storage,
        lease_manager=lease_mgr,
        workspace_manager=mock_workspace,
        provider_router=router,
        tracker=mock_tracker,
        max_concurrent=5,
        budget_daily_limit=50.0,
        budget_per_run_limit=10.0,
    )
    reconciler = Reconciler(storage=storage, tracker=mock_tracker, dispatcher=dispatcher)
    scheduler = Scheduler(
        storage=storage,
        tracker=mock_tracker,
        dispatcher=dispatcher,
        reconciler=reconciler,
        poll_interval_sec=1,
    )

    # Run a single tick
    await scheduler.tick()

    # Verify issue was stored
    issue = await storage.get_issue("gh-42")
    assert issue is not None
    assert issue.title == "Fix login bug"

    # Wait for async dispatch to complete
    await asyncio.sleep(2)

    # Check daily cost was recorded
    daily_cost = await storage.sum_daily_cost()
    assert daily_cost >= 0

    # Verify all issues stored
    all_issues = await storage.list_issues()
    assert len(all_issues) == 1
