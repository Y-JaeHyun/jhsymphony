from datetime import datetime, timezone

from jhsymphony.models import (
    AgentEvent,
    EventType,
    Issue,
    IssueState,
    Lease,
    Run,
    RunStatus,
    UsageRecord,
)


def test_issue_creation():
    issue = Issue(
        id="gh-123",
        number=123,
        repo="owner/repo",
        title="Fix bug",
        labels=["jhsymphony", "bug"],
        state=IssueState.PENDING,
    )
    assert issue.id == "gh-123"
    assert issue.state == IssueState.PENDING
    assert "jhsymphony" in issue.labels


def test_issue_state_transitions():
    assert IssueState.PENDING.is_active()
    assert IssueState.RUNNING.is_active()
    assert not IssueState.COMPLETED.is_active()
    assert not IssueState.FAILED.is_active()
    assert not IssueState.CANCELLED.is_active()


def test_run_creation():
    run = Run(
        id="run-abc",
        issue_id="gh-123",
        provider="claude",
        status=RunStatus.STARTING,
        attempt=1,
    )
    assert run.id == "run-abc"
    assert run.status == RunStatus.STARTING
    assert run.ended_at is None


def test_run_duration():
    now = datetime.now(timezone.utc)
    run = Run(
        id="run-1",
        issue_id="gh-1",
        provider="claude",
        status=RunStatus.COMPLETED,
        attempt=1,
        started_at=now,
        ended_at=now,
    )
    assert run.duration_sec() == 0.0


def test_agent_event_message_delta():
    event = AgentEvent(type=EventType.MESSAGE_DELTA, data={"text": "Hello"})
    assert event.type == EventType.MESSAGE_DELTA
    assert event.data["text"] == "Hello"


def test_agent_event_usage():
    event = AgentEvent(
        type=EventType.USAGE,
        data={"input_tokens": 100, "output_tokens": 50},
    )
    assert event.data["input_tokens"] == 100


def test_lease_is_expired():
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    lease = Lease(issue_id="gh-1", owner_id="worker-1", expires_at=past)
    assert lease.is_expired()

    from datetime import timedelta
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    lease2 = Lease(issue_id="gh-2", owner_id="worker-1", expires_at=future)
    assert not lease2.is_expired()


def test_usage_record():
    record = UsageRecord(
        run_id="run-1",
        provider="claude",
        input_tokens=1000,
        output_tokens=500,
        estimated_cost_usd=0.05,
    )
    assert record.total_tokens() == 1500
