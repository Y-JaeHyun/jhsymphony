from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, AsyncIterator, Protocol, runtime_checkable


class EventType(StrEnum):
    SESSION_STARTED = "session.started"
    MESSAGE_DELTA = "message.delta"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    USAGE = "usage"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class AgentEvent:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ProviderCapabilities:
    supports_tools: bool = False
    supports_streaming: bool = False
    supports_shell: bool = False
    supports_image_input: bool = False
    supports_interrupt: bool = False


@dataclass
class RunContext:
    workspace_path: str
    branch: str
    issue_title: str
    issue_body: str = ""
    env: dict[str, str] = field(default_factory=dict)
    max_turns: int = 30
    timeout_sec: int = 1800


@runtime_checkable
class AgentProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    async def start_session(self, ctx: RunContext) -> Any: ...
    async def run_turn(self, session: Any, prompt: str) -> AsyncIterator[AgentEvent]: ...
    async def cancel(self, session: Any) -> None: ...
