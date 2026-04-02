from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IssueState(StrEnum):
    PENDING = "pending"
    ANALYZING = "analyzing"
    AWAITING_APPROVAL = "awaiting_approval"
    LEASED = "leased"
    PREPARING = "preparing"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY_WAIT = "retry_wait"

    def is_active(self) -> bool:
        return self in {
            IssueState.PENDING,
            IssueState.ANALYZING,
            IssueState.AWAITING_APPROVAL,
            IssueState.LEASED,
            IssueState.PREPARING,
            IssueState.RUNNING,
            IssueState.REVIEWING,
            IssueState.RETRY_WAIT,
        }

    def consumes_slot(self) -> bool:
        """Whether this state uses an agent execution slot."""
        return self in {
            IssueState.ANALYZING,
            IssueState.LEASED,
            IssueState.PREPARING,
            IssueState.RUNNING,
            IssueState.REVIEWING,
        }


class RunStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventType(StrEnum):
    SESSION_STARTED = "session.started"
    MESSAGE_DELTA = "message.delta"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    USAGE = "usage"
    COMPLETED = "completed"
    ERROR = "error"


class ExecutionHealth(StrEnum):
    OK = "ok"
    CHECKPOINT = "checkpoint"
    SUSPECT = "suspect"
    FAILED = "failed"


class CompletenessLevel(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


class PlanManifest(BaseModel):
    required_files: list[str] = []
    optional_files: list[str] = []
    implementation_steps: list[dict] = []
    expected_file_count_min: int = 0


class VerificationResult(BaseModel):
    health: ExecutionHealth = ExecutionHealth.OK
    completeness: CompletenessLevel = CompletenessLevel.UNKNOWN
    coverage_ratio: float = 0.0
    missing_files: list[str] = []
    changed_files: list[str] = []
    event_count: int = 0
    exit_code: int = 0
    has_error_events: bool = False
    remediation_attempted: bool = False
    remediation_helped: bool = False


class Issue(BaseModel):
    id: str
    number: int
    repo: str
    title: str
    body: str = ""
    labels: list[str] = []
    state: IssueState = IssueState.PENDING
    priority: int = 0
    provider: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class Run(BaseModel):
    id: str
    issue_id: str
    provider: str
    status: RunStatus = RunStatus.STARTING
    attempt: int = 1
    branch: str | None = None
    pr_number: int | None = None
    analysis_comment_id: int | None = None
    started_at: datetime = Field(default_factory=_utc_now)
    ended_at: datetime | None = None
    error: str | None = None

    def duration_sec(self) -> float:
        if self.ended_at is None:
            return (_utc_now() - self.started_at).total_seconds()
        return (self.ended_at - self.started_at).total_seconds()


class Lease(BaseModel):
    issue_id: str
    owner_id: str
    expires_at: datetime

    def is_expired(self) -> bool:
        return _utc_now() > self.expires_at


class AgentEvent(BaseModel):
    type: EventType
    data: dict = {}
    timestamp: datetime = Field(default_factory=_utc_now)


class UsageRecord(BaseModel):
    run_id: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    recorded_at: datetime = Field(default_factory=_utc_now)

    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
