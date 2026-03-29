import pytest

from jhsymphony.config import ProviderEntry, RoutingRule
from jhsymphony.providers.base import AgentEvent, AgentProvider, EventType, ProviderCapabilities
from jhsymphony.providers.router import ProviderRouter


class FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_tools=True, supports_streaming=True)

    async def start_session(self, ctx):
        return {"session_id": "fake"}

    async def run_turn(self, session, prompt: str):
        yield AgentEvent(type=EventType.MESSAGE_DELTA, data={"text": "hello"})
        yield AgentEvent(type=EventType.COMPLETED, data={"reason": "done"})

    async def cancel(self, session):
        pass


def test_provider_capabilities():
    caps = ProviderCapabilities(supports_tools=True, supports_streaming=True)
    assert caps.supports_tools is True
    assert caps.supports_shell is False


def test_agent_event_creation():
    event = AgentEvent(type=EventType.MESSAGE_DELTA, data={"text": "hi"})
    assert event.type == EventType.MESSAGE_DELTA


def test_router_default_provider():
    router = ProviderRouter(
        default_provider="claude",
        providers={"claude": FakeProvider("claude"), "codex": FakeProvider("codex")},
        routing_rules=[],
    )
    provider = router.select(labels=[])
    assert provider.name == "claude"


def test_router_label_routing():
    router = ProviderRouter(
        default_provider="claude",
        providers={"claude": FakeProvider("claude"), "codex": FakeProvider("codex")},
        routing_rules=[
            RoutingRule(label="use-codex", provider="codex"),
        ],
    )
    provider = router.select(labels=["bug", "use-codex"])
    assert provider.name == "codex"


def test_router_first_matching_rule_wins():
    router = ProviderRouter(
        default_provider="claude",
        providers={
            "claude": FakeProvider("claude"),
            "codex": FakeProvider("codex"),
            "gemini": FakeProvider("gemini"),
        },
        routing_rules=[
            RoutingRule(label="use-codex", provider="codex"),
            RoutingRule(label="use-gemini", provider="gemini"),
        ],
    )
    provider = router.select(labels=["use-gemini", "use-codex"])
    assert provider.name == "codex"


def test_router_unknown_provider_falls_back():
    router = ProviderRouter(
        default_provider="claude",
        providers={"claude": FakeProvider("claude")},
        routing_rules=[
            RoutingRule(label="use-unknown", provider="nonexistent"),
        ],
    )
    provider = router.select(labels=["use-unknown"])
    assert provider.name == "claude"


# --- Claude, Codex, Gemini provider tests ---

import pytest
from jhsymphony.providers.claude import ClaudeProvider
from jhsymphony.providers.codex import CodexProvider
from jhsymphony.providers.gemini import GeminiProvider
from jhsymphony.providers.base import RunContext


@pytest.mark.asyncio
async def test_claude_provider_capabilities():
    provider = ClaudeProvider(command="echo", model="opus", max_turns=10)
    caps = provider.capabilities()
    assert caps.supports_tools is True
    assert caps.supports_streaming is True
    assert caps.supports_shell is True


@pytest.mark.asyncio
async def test_claude_provider_run_produces_events():
    provider = ClaudeProvider(command="echo", model="opus", max_turns=1)
    ctx = RunContext(workspace_path="/tmp", branch="test", issue_title="Test issue")
    session = await provider.start_session(ctx)
    events = []
    async for event in provider.run_turn(session, "say hello"):
        events.append(event)
    assert len(events) >= 1
    assert any(e.type == EventType.COMPLETED for e in events)


@pytest.mark.asyncio
async def test_codex_provider_capabilities():
    provider = CodexProvider(command="echo", model="gpt-5.4", sandbox="read-only")
    caps = provider.capabilities()
    assert caps.supports_tools is True


@pytest.mark.asyncio
async def test_codex_provider_run():
    provider = CodexProvider(command="echo", model="gpt-5.4", sandbox="read-only")
    ctx = RunContext(workspace_path="/tmp", branch="test", issue_title="Test")
    session = await provider.start_session(ctx)
    events = [e async for e in provider.run_turn(session, "test")]
    assert any(e.type == EventType.COMPLETED for e in events)


@pytest.mark.asyncio
async def test_gemini_provider_capabilities():
    provider = GeminiProvider(command="echo", model="gemini-2.5-pro")
    caps = provider.capabilities()
    assert caps.supports_tools is True


@pytest.mark.asyncio
async def test_gemini_provider_run():
    provider = GeminiProvider(command="echo", model="gemini-2.5-pro")
    ctx = RunContext(workspace_path="/tmp", branch="test", issue_title="Test")
    session = await provider.start_session(ctx)
    events = [e async for e in provider.run_turn(session, "test")]
    assert any(e.type == EventType.COMPLETED for e in events)
