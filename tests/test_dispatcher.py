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
        bot_login="test-bot",
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


async def test_collect_agent_response_message_delta(dispatcher, storage):
    """message.delta events should be collected as the response."""
    run_id = "run-msg"
    await storage.insert_run(
        Run(id=run_id, issue_id="gh-1", provider="codex", status=RunStatus.RUNNING)
    )
    await storage.insert_event(run_id, 0, "session.started", {"pid": 1})
    await storage.insert_event(run_id, 1, "message.delta", {"text": "## Summary"})
    await storage.insert_event(run_id, 2, "message.delta", {"text": "This is the plan."})
    result = await dispatcher._collect_agent_response(run_id)
    assert "## Summary" in result
    assert "This is the plan." in result


async def test_collect_agent_response_fallback_to_tool_result(dispatcher, storage):
    """When no message.delta, should fall back to last substantial tool_result."""
    run_id = "run-tool"
    await storage.insert_run(
        Run(id=run_id, issue_id="gh-1", provider="codex", status=RunStatus.RUNNING)
    )
    await storage.insert_event(run_id, 0, "tool.call", {"tool": "Read", "input": {}})
    long_text = "## Detailed Analysis\n" + "x" * 200
    await storage.insert_event(run_id, 1, "tool.result", {"content": long_text})
    result = await dispatcher._collect_agent_response(run_id)
    assert "Detailed Analysis" in result
    assert result != "Analysis completed."


async def test_collect_agent_response_tool_result_list_format(dispatcher, storage):
    """tool_result with list-of-blocks content format should be handled."""
    run_id = "run-list"
    await storage.insert_run(
        Run(id=run_id, issue_id="gh-1", provider="codex", status=RunStatus.RUNNING)
    )
    blocks = [{"type": "text", "text": "## Plan\n" + "y" * 200}]
    await storage.insert_event(run_id, 0, "session.started", {"pid": 1})
    await storage.insert_event(run_id, 1, "tool.result", {"content": blocks})
    result = await dispatcher._collect_agent_response(run_id)
    assert "## Plan" in result


async def test_collect_agent_response_empty_fallback(dispatcher, storage):
    """With no useful events, should return the fallback string."""
    run_id = "run-empty"
    await storage.insert_run(
        Run(id=run_id, issue_id="gh-1", provider="codex", status=RunStatus.RUNNING)
    )
    await storage.insert_event(run_id, 0, "session.started", {"pid": 123})
    result = await dispatcher._collect_agent_response(run_id)
    assert result == "Analysis completed."


async def test_decision_footer_appended_when_decisions_present(dispatcher, storage):
    """When agent response contains DECISION patterns, footer should include decision instructions."""
    run_id = "run-footer"
    await storage.insert_run(
        Run(id=run_id, issue_id="gh-1", provider="codex", status=RunStatus.RUNNING)
    )
    await storage.insert_event(run_id, 1, "message.delta", {"text": "## Summary\nPlan here\n\n### DECISION-1: DB choice\n> Pick A or B"})
    response = await dispatcher._collect_agent_response(run_id)
    footer = dispatcher._build_plan_footer(response)
    assert "DECISION-1:" in footer
    assert "결정이 필요한 항목이 있습니다" in footer


async def test_no_decision_footer_when_no_decisions(dispatcher, storage):
    """When no DECISION patterns, use simple footer."""
    run_id = "run-nofooter"
    await storage.insert_run(
        Run(id=run_id, issue_id="gh-1", provider="codex", status=RunStatus.RUNNING)
    )
    await storage.insert_event(run_id, 1, "message.delta", {"text": "## Summary\nSimple plan"})
    response = await dispatcher._collect_agent_response(run_id)
    footer = dispatcher._build_plan_footer(response)
    assert "결정이 필요한 항목이 있습니다" not in footer
    assert "Action Required" in footer


async def test_extract_admin_decisions_by_comment_id(dispatcher):
    comments = [
        {"id": 100, "author": "bot", "body": "## Plan\n### DECISION-1: DB\n> A or B", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 101, "author": "admin", "body": "DECISION-1: A\nWe prefer postgres", "created_at": "2026-01-01T01:00:00Z"},
    ]
    decisions, raw = Dispatcher._extract_admin_decisions(comments, "bot", analysis_comment_id=100)
    assert decisions["1"] == "A"
    assert "We prefer postgres" in raw


async def test_extract_admin_decisions_fallback_to_bot_pattern(dispatcher):
    comments = [
        {"id": 100, "author": "bot", "body": "## Plan\n### DECISION-1: DB\n> A or B", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 101, "author": "admin", "body": "DECISION-1: B\nGo with mysql", "created_at": "2026-01-01T01:00:00Z"},
    ]
    decisions, raw = Dispatcher._extract_admin_decisions(comments, "bot", analysis_comment_id=999)
    assert decisions["1"] == "B"


async def test_extract_admin_decisions_multiple(dispatcher):
    comments = [
        {"id": 100, "author": "bot", "body": "DECISION-1: X\nDECISION-2: Y", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 101, "author": "admin", "body": "DECISION-1: A\nDECISION-2: B\nExtra context here", "created_at": "2026-01-01T01:00:00Z"},
    ]
    decisions, raw = Dispatcher._extract_admin_decisions(comments, "bot", analysis_comment_id=100)
    assert decisions["1"] == "A"
    assert decisions["2"] == "B"


async def test_extract_admin_decisions_no_decisions(dispatcher):
    comments = [
        {"id": 100, "author": "bot", "body": "## Plan\nNo decisions needed", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 101, "author": "admin", "body": "Looks good!", "created_at": "2026-01-01T01:00:00Z"},
    ]
    decisions, raw = Dispatcher._extract_admin_decisions(comments, "bot", analysis_comment_id=100)
    assert decisions == {}
    assert "Looks good!" in raw


async def test_extract_admin_decisions_ignores_bot_comments(dispatcher):
    comments = [
        {"id": 100, "author": "bot", "body": "DECISION-1: X", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 101, "author": "bot", "body": "DECISION-1: ignore this", "created_at": "2026-01-01T01:00:00Z"},
        {"id": 102, "author": "admin", "body": "DECISION-1: A", "created_at": "2026-01-01T02:00:00Z"},
    ]
    decisions, raw = Dispatcher._extract_admin_decisions(comments, "bot", analysis_comment_id=100)
    assert decisions["1"] == "A"
    assert "ignore this" not in raw


async def test_dispatch_approved_uses_claude(storage, mock_workspace_mgr, mock_tracker):
    """dispatch_approved should always use Claude, regardless of issue labels."""
    claude_provider = AsyncMock()
    claude_provider.name = "claude"

    async def fake_run_turn(session, prompt):
        from jhsymphony.providers.base import AgentEvent, EventType
        yield AgentEvent(type=EventType.COMPLETED, data={"reason": "done"})

    claude_provider.run_turn = fake_run_turn

    codex_provider = AsyncMock()
    codex_provider.name = "codex"

    router = MagicMock()
    router.select.return_value = codex_provider
    router.get.return_value = claude_provider

    lease_mgr = LeaseManager(storage=storage, owner_id="test", ttl_sec=60)
    disp = Dispatcher(
        storage=storage, lease_manager=lease_mgr, workspace_manager=mock_workspace_mgr,
        provider_router=router, tracker=mock_tracker,
        bot_login="test-bot",
    )

    issue = Issue(id="gh-claude", number=7, repo="o/r", title="Test", labels=["use-codex"])
    await storage.upsert_issue(issue)
    run_id = await disp.dispatch_approved(issue)
    assert run_id is not None

    run = await storage.get_run(run_id)
    assert run.provider == "claude"
    router.get.assert_called_with("claude")


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
    assert run.status in (RunStatus.CANCELLED, RunStatus.COMPLETED, RunStatus.FAILED)


from jhsymphony.models import CompletenessLevel, PlanManifest


async def test_parse_plan_manifest_from_analysis():
    """Should extract JSON manifest from analysis text."""
    analysis = '''## Summary
Some plan text.

## Affected Files
| File | Change |
|------|--------|
| foo.go | Modify |

<!-- plan-manifest -->
```json
{
  "required_files": ["foo.go", "bar.go"],
  "optional_files": ["baz.go"],
  "implementation_steps": [{"id": 1, "name": "step one", "critical": true}],
  "expected_file_count_min": 2
}
```

## Implementation Plan
Details here.
'''
    manifest = Dispatcher._parse_plan_manifest(analysis)
    assert manifest is not None
    assert manifest.required_files == ["foo.go", "bar.go"]
    assert manifest.optional_files == ["baz.go"]
    assert len(manifest.implementation_steps) == 1
    assert manifest.expected_file_count_min == 2


async def test_parse_plan_manifest_missing():
    """Should return None when no manifest block exists."""
    analysis = "## Summary\nJust a plan without manifest."
    manifest = Dispatcher._parse_plan_manifest(analysis)
    assert manifest is None


async def test_parse_plan_manifest_fallback_from_affected_files():
    """Should extract file list from Affected Files table as fallback."""
    analysis = '''## Affected Files

| File | Change Type | Description |
|------|------------|-------------|
| `src/foo/Bar.go` | Modify | Add field |
| `src/foo/Baz.go` | **New** | New file |
| `tests/test_bar.go` | Modify | Update tests |

## Implementation Plan
Step 1, step 2.
'''
    manifest = Dispatcher._parse_plan_manifest(analysis)
    assert manifest is not None
    assert "src/foo/Bar.go" in manifest.required_files
    assert "src/foo/Baz.go" in manifest.required_files
    assert "tests/test_bar.go" in manifest.required_files


from jhsymphony.models import ExecutionHealth


async def test_health_check_ok(dispatcher, storage):
    """Normal run with exit_code=0, no errors → OK."""
    run_id = "run-health-ok"
    await storage.insert_run(Run(id=run_id, issue_id="gh-1", provider="claude", status=RunStatus.RUNNING))
    for i in range(15):
        await storage.insert_event(run_id, i, "message.delta", {"text": f"chunk {i}"})
    await storage.insert_event(run_id, 15, "completed", {"exit_code": 0, "reason": "done"})
    health, info = await dispatcher._check_execution_health(run_id)
    assert health == ExecutionHealth.OK
    assert info["event_count"] == 16
    assert info["exit_code"] == 0


async def test_health_check_failed_exit_code(dispatcher, storage):
    """Non-zero exit code → FAILED."""
    run_id = "run-health-fail"
    await storage.insert_run(Run(id=run_id, issue_id="gh-1", provider="claude", status=RunStatus.RUNNING))
    await storage.insert_event(run_id, 0, "completed", {"exit_code": 1, "reason": "error"})
    health, info = await dispatcher._check_execution_health(run_id)
    assert health == ExecutionHealth.FAILED


async def test_health_check_error_event(dispatcher, storage):
    """Error event present → FAILED."""
    run_id = "run-health-err"
    await storage.insert_run(Run(id=run_id, issue_id="gh-1", provider="claude", status=RunStatus.RUNNING))
    for i in range(20):
        await storage.insert_event(run_id, i, "message.delta", {"text": f"chunk {i}"})
    await storage.insert_event(run_id, 20, "error", {"error": "timeout"})
    health, info = await dispatcher._check_execution_health(run_id)
    assert health == ExecutionHealth.FAILED


async def test_health_check_suspect_low_events(dispatcher, storage):
    """Very few events with exit_code=0 → SUSPECT."""
    run_id = "run-health-suspect"
    await storage.insert_run(Run(id=run_id, issue_id="gh-1", provider="claude", status=RunStatus.RUNNING))
    await storage.insert_event(run_id, 0, "session.started", {"pid": 1})
    await storage.insert_event(run_id, 1, "completed", {"exit_code": 0, "reason": "done"})
    health, info = await dispatcher._check_execution_health(run_id)
    assert health == ExecutionHealth.SUSPECT


async def test_completeness_complete():
    """All required files changed → COMPLETE."""
    manifest = PlanManifest(required_files=["src/foo/Bar.go", "src/foo/Baz.go"])
    changed = ["src/foo/Bar.go", "src/foo/Baz.go", "README.md"]
    level, ratio, missing = Dispatcher._check_completeness(manifest, changed)
    assert level == CompletenessLevel.COMPLETE
    assert ratio == 1.0
    assert missing == []


async def test_completeness_partial():
    """50-79% coverage → PARTIAL."""
    manifest = PlanManifest(required_files=["a.go", "b.go", "c.go", "d.go"])
    changed = ["a.go", "b.go", "c.go"]
    level, ratio, missing = Dispatcher._check_completeness(manifest, changed)
    assert level == CompletenessLevel.PARTIAL
    assert ratio == 0.75
    assert missing == ["d.go"]


async def test_completeness_incomplete():
    """< 50% coverage → INCOMPLETE."""
    manifest = PlanManifest(required_files=["a.go", "b.go", "c.go", "d.go"])
    changed = ["a.go"]
    level, ratio, missing = Dispatcher._check_completeness(manifest, changed)
    assert level == CompletenessLevel.INCOMPLETE
    assert 0.25 == ratio
    assert set(missing) == {"b.go", "c.go", "d.go"}


async def test_completeness_basename_matching():
    """Should match by basename when full paths differ."""
    manifest = PlanManifest(required_files=[
        "whatap.agent/src/whatap/agent/counter/helper/MetricHelperLinux.go",
        "whatap.agent/src/whatap/osinfo/MemoryLinux.go",
    ])
    changed = [
        "src/whatap/agent/counter/helper/MetricHelperLinux.go",
        "src/whatap/osinfo/MemoryLinux.go",
    ]
    level, ratio, missing = Dispatcher._check_completeness(manifest, changed)
    assert level == CompletenessLevel.COMPLETE
    assert ratio == 1.0


async def test_completeness_no_manifest():
    """No manifest → UNKNOWN."""
    level, ratio, missing = Dispatcher._check_completeness(None, ["a.go"])
    assert level == CompletenessLevel.UNKNOWN
    assert ratio == 0.0


from jhsymphony.models import VerificationResult


async def test_verify_execution_complete(dispatcher, storage):
    """Healthy run with full coverage → COMPLETE verdict."""
    run_id = "run-verify-ok"
    await storage.insert_run(Run(id=run_id, issue_id="gh-1", provider="claude", status=RunStatus.RUNNING))
    for i in range(15):
        await storage.insert_event(run_id, i, "message.delta", {"text": f"chunk {i}"})
    await storage.insert_event(run_id, 15, "completed", {"exit_code": 0, "reason": "done"})

    manifest = PlanManifest(required_files=["foo.go", "bar.go"])
    result = await dispatcher._verify_execution(run_id, manifest, ["foo.go", "bar.go"])
    assert result.health == ExecutionHealth.OK
    assert result.completeness == CompletenessLevel.COMPLETE
    assert result.coverage_ratio == 1.0
    assert result.missing_files == []


async def test_verify_execution_failed(dispatcher, storage):
    """Error in execution → FAILED health."""
    run_id = "run-verify-fail"
    await storage.insert_run(Run(id=run_id, issue_id="gh-1", provider="claude", status=RunStatus.RUNNING))
    await storage.insert_event(run_id, 0, "error", {"error": "crash"})

    manifest = PlanManifest(required_files=["foo.go"])
    result = await dispatcher._verify_execution(run_id, manifest, [])
    assert result.health == ExecutionHealth.FAILED


async def test_verify_execution_no_manifest(dispatcher, storage):
    """No manifest → UNKNOWN completeness, still OK health."""
    run_id = "run-verify-noplan"
    await storage.insert_run(Run(id=run_id, issue_id="gh-1", provider="claude", status=RunStatus.RUNNING))
    for i in range(15):
        await storage.insert_event(run_id, i, "message.delta", {"text": f"chunk {i}"})
    await storage.insert_event(run_id, 15, "completed", {"exit_code": 0, "reason": "done"})

    result = await dispatcher._verify_execution(run_id, None, ["some.go"])
    assert result.health == ExecutionHealth.OK
    assert result.completeness == CompletenessLevel.UNKNOWN


async def test_build_verification_report():
    """Should produce markdown report from VerificationResult."""
    result = VerificationResult(
        health=ExecutionHealth.OK,
        completeness=CompletenessLevel.PARTIAL,
        coverage_ratio=0.67,
        missing_files=["TaskInfraNetstat.go", "CounterManager.go"],
        changed_files=["DataStructure.go", "MemoryLinux.go", "ProcessLinux.go"],
        event_count=47,
        exit_code=0,
    )
    report = Dispatcher._build_verification_report(result)
    assert "## Verification Report" in report
    assert "OK" in report
    assert "67%" in report
    assert "TaskInfraNetstat.go" in report
    assert "CounterManager.go" in report


async def test_build_verification_report_complete():
    """Complete result should show no missing files."""
    result = VerificationResult(
        health=ExecutionHealth.OK,
        completeness=CompletenessLevel.COMPLETE,
        coverage_ratio=1.0,
        missing_files=[],
        changed_files=["a.go", "b.go"],
        event_count=100,
        exit_code=0,
    )
    report = Dispatcher._build_verification_report(result)
    assert "100%" in report
    assert "Missing" not in report


async def test_build_verification_report_unknown():
    """UNKNOWN completeness (no manifest) should show N/A instead of misleading 0%."""
    result = VerificationResult(
        health=ExecutionHealth.FAILED,
        completeness=CompletenessLevel.UNKNOWN,
        coverage_ratio=0.0,
        missing_files=[],
        changed_files=["a.go", "b.go", "c.go", "d.go", "e.go", "f.go"],
        event_count=140,
        exit_code=1,
    )
    report = Dispatcher._build_verification_report(result)
    assert "N/A" in report
    assert "6 files changed" in report
    assert "0%" not in report


async def test_extract_self_decisions_marker_format():
    """Should extract SELF-DECISION: marker lines."""
    text = (
        "Some output\n"
        "- SELF-DECISION: Field naming — Chose snake_case because Go convention. Alternative was camelCase.\n"
        "- SELF-DECISION: DB choice — Chose SQLite because simplicity. Alternative was PostgreSQL.\n"
    )
    decisions = Dispatcher._extract_self_decisions(text)
    assert len(decisions) == 2
    assert "snake_case" in decisions[0]


async def test_extract_self_decisions_heading_fallback():
    """Should extract from ## Self-Decisions section as fallback."""
    text = (
        "## Summary\nDid some work.\n\n"
        "## Self-Decisions\n"
        "- Chose snake_case for field naming because Go convention\n"
        "- Used SQLite for simplicity over PostgreSQL\n\n"
        "## Changes Made\n"
        "Some changes.\n"
    )
    decisions = Dispatcher._extract_self_decisions(text)
    assert len(decisions) == 2
    assert "snake_case" in decisions[0]


async def test_extract_self_decisions_no_decisions():
    """'No self-decisions' text should return empty list."""
    text = (
        "## Self-Decisions\n"
        "- No self-decisions were required.\n"
    )
    decisions = Dispatcher._extract_self_decisions(text)
    assert decisions == []


async def test_run_remediation_builds_correct_prompt(storage, mock_workspace_mgr, mock_tracker):
    """Remediation should build a prompt referencing missing items."""
    captured_prompt = {}

    claude_provider = AsyncMock()
    claude_provider.name = "claude"

    async def capture_run_turn(session, prompt):
        captured_prompt["value"] = prompt
        from jhsymphony.providers.base import AgentEvent, EventType
        yield AgentEvent(type=EventType.COMPLETED, data={"reason": "done", "exit_code": 0})

    claude_provider.run_turn = capture_run_turn

    router = MagicMock()
    router.get.return_value = claude_provider

    lease_mgr = LeaseManager(storage=storage, owner_id="test", ttl_sec=60)
    disp = Dispatcher(
        storage=storage, lease_manager=lease_mgr, workspace_manager=mock_workspace_mgr,
        provider_router=router, tracker=mock_tracker, bot_login="test-bot",
    )

    issue = Issue(id="gh-rem", number=10, repo="o/r", title="Fix stuff", body="body")
    await storage.upsert_issue(issue)
    run_id = "run-rem"
    await storage.insert_run(Run(id=run_id, issue_id="gh-rem", provider="claude", status=RunStatus.RUNNING))

    workspace = MagicMock(path=Path("/tmp/ws"), branch="jhsymphony/issue-10")
    manifest = PlanManifest(
        required_files=["foo.go", "bar.go"],
        implementation_steps=[
            {"id": 1, "name": "step one", "critical": True},
            {"id": 2, "name": "step two", "critical": True},
        ],
    )
    missing = ["bar.go"]
    diff_stat = "foo.go | 10 ++++\n 1 file changed"

    await disp._run_remediation(run_id, issue, claude_provider, workspace, manifest, missing, diff_stat)

    prompt = captured_prompt["value"]
    assert "bar.go" in prompt
    assert "step one" in prompt or "step two" in prompt
    assert "already" in prompt.lower()
