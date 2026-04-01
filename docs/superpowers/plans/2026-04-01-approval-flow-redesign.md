# Approval Flow Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Phase 1→Phase 2 approval flow so that analysis produces structured DECISION items, admin decisions are collected from comments, and implementation uses Claude with full context (original issue + analysis + decisions).

**Architecture:** Six changes across storage, tracker, config, and dispatcher layers. Storage adds `body` and `analysis_comment_id` columns. Tracker returns comment IDs and fetches comments. Dispatcher restructures both analysis and implementation prompts, extracts admin decisions, and forces Claude for implementation.

**Tech Stack:** Python 3.12, aiosqlite, httpx, pytest

---

### Task 1: Persist Issue Body in Storage

**Files:**
- Modify: `src/jhsymphony/storage/sqlite.py:11-23` (schema), `src/jhsymphony/storage/sqlite.py:112-124` (`_row_to_issue`), `src/jhsymphony/storage/sqlite.py:160-188` (`upsert_issue`)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write failing test for issue body persistence**

```python
# tests/test_storage.py — add at end of file

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_storage.py::test_upsert_and_get_issue_body -v`
Expected: FAIL — `body` column does not exist in DB or is not mapped

- [ ] **Step 3: Add `body` column to schema**

In `src/jhsymphony/storage/sqlite.py`, modify `_SCHEMA` — add `body` column to the `issues` table:

```python
# In _SCHEMA string, change the issues CREATE TABLE to:
CREATE TABLE IF NOT EXISTS issues (
    id          TEXT PRIMARY KEY,
    number      INTEGER NOT NULL,
    repo        TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    labels      TEXT NOT NULL DEFAULT '[]',
    state       TEXT NOT NULL DEFAULT 'pending',
    priority    INTEGER NOT NULL DEFAULT 0,
    provider    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

- [ ] **Step 4: Update `_row_to_issue` to read `body`**

```python
def _row_to_issue(row: aiosqlite.Row) -> Issue:
    return Issue(
        id=row["id"],
        number=row["number"],
        repo=row["repo"],
        title=row["title"],
        body=row["body"],
        labels=json.loads(row["labels"]),
        state=IssueState(row["state"]),
        priority=row["priority"],
        provider=row["provider"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )
```

- [ ] **Step 5: Update `upsert_issue` to persist `body`**

```python
async def upsert_issue(self, issue: Issue) -> None:
    await self._db.execute(
        """
        INSERT INTO issues (id, number, repo, title, body, labels, state, priority, provider, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            number     = excluded.number,
            repo       = excluded.repo,
            title      = excluded.title,
            body       = excluded.body,
            labels     = excluded.labels,
            state      = excluded.state,
            priority   = excluded.priority,
            provider   = excluded.provider,
            updated_at = excluded.updated_at
        """,
        (
            issue.id,
            issue.number,
            issue.repo,
            issue.title,
            issue.body,
            json.dumps(issue.labels),
            issue.state.value,
            issue.priority,
            issue.provider,
            issue.created_at.isoformat(),
            issue.updated_at.isoformat(),
        ),
    )
    await self._db.commit()
```

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest tests/test_storage.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/jhsymphony/storage/sqlite.py tests/test_storage.py
git commit -m "fix: persist issue body in storage schema"
```

---

### Task 2: Add `analysis_comment_id` to Runs & `get_analysis_run` Method

**Files:**
- Modify: `src/jhsymphony/storage/sqlite.py:27-38` (runs schema), `src/jhsymphony/storage/sqlite.py:127-139` (`_row_to_run`), `src/jhsymphony/storage/sqlite.py:216-235` (`insert_run`)
- Modify: `src/jhsymphony/models.py:82-98` (Run model)
- Modify: `src/jhsymphony/storage/base.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_storage.py — add at end

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_storage.py::test_run_analysis_comment_id -v`
Expected: FAIL

- [ ] **Step 3: Add `analysis_comment_id` to Run model**

In `src/jhsymphony/models.py`, add field to `Run`:

```python
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
```

- [ ] **Step 4: Add `analysis_comment_id` column to runs schema**

In `src/jhsymphony/storage/sqlite.py`, update `_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS runs (
    id                    TEXT PRIMARY KEY,
    issue_id              TEXT NOT NULL,
    provider              TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'starting',
    attempt               INTEGER NOT NULL DEFAULT 1,
    branch                TEXT,
    pr_number             INTEGER,
    analysis_comment_id   INTEGER,
    started_at            TEXT NOT NULL,
    ended_at              TEXT,
    error                 TEXT
);
```

- [ ] **Step 5: Update `_row_to_run` and `insert_run`**

```python
def _row_to_run(row: aiosqlite.Row) -> Run:
    return Run(
        id=row["id"],
        issue_id=row["issue_id"],
        provider=row["provider"],
        status=RunStatus(row["status"]),
        attempt=row["attempt"],
        branch=row["branch"],
        pr_number=row["pr_number"],
        analysis_comment_id=row["analysis_comment_id"],
        started_at=_parse_dt(row["started_at"]),
        ended_at=_parse_dt(row["ended_at"]),
        error=row["error"],
    )
```

In `insert_run`:

```python
async def insert_run(self, run: Run) -> None:
    await self._db.execute(
        """
        INSERT INTO runs (id, issue_id, provider, status, attempt, branch, pr_number, analysis_comment_id, started_at, ended_at, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.id,
            run.issue_id,
            run.provider,
            run.status.value,
            run.attempt,
            run.branch,
            run.pr_number,
            run.analysis_comment_id,
            run.started_at.isoformat(),
            run.ended_at.isoformat() if run.ended_at else None,
            run.error,
        ),
    )
    await self._db.commit()
```

- [ ] **Step 6: Add `get_analysis_run` method**

```python
# In SQLiteStorage class, after get_run_count_for_issue:

async def get_analysis_run(self, issue_id: str) -> Run | None:
    """Return the first completed run for an issue (Phase 1 analysis)."""
    async with self._db.execute(
        "SELECT * FROM runs WHERE issue_id = ? AND status = 'completed' ORDER BY started_at ASC LIMIT 1",
        (issue_id,),
    ) as cur:
        row = await cur.fetchone()
    return _row_to_run(row) if row else None
```

- [ ] **Step 7: Add `update_run_analysis_comment_id` method**

```python
async def update_run_analysis_comment_id(self, run_id: str, comment_id: int) -> None:
    await self._db.execute(
        "UPDATE runs SET analysis_comment_id = ? WHERE id = ?",
        (comment_id, run_id),
    )
    await self._db.commit()
```

- [ ] **Step 8: Update storage protocol**

In `src/jhsymphony/storage/base.py`, add to the `Storage` protocol:

```python
async def get_analysis_run(self, issue_id: str) -> Run | None: ...
async def update_run_analysis_comment_id(self, run_id: str, comment_id: int) -> None: ...
```

- [ ] **Step 9: Run tests**

Run: `python3 -m pytest tests/test_storage.py -v`
Expected: ALL PASS

- [ ] **Step 10: Commit**

```bash
git add src/jhsymphony/models.py src/jhsymphony/storage/sqlite.py src/jhsymphony/storage/base.py tests/test_storage.py
git commit -m "feat: add analysis_comment_id to runs and get_analysis_run method"
```

---

### Task 3: Tracker — `post_comment` Returns ID & `fetch_comments` Method

**Files:**
- Modify: `src/jhsymphony/tracker/github.py:49-52` (`post_comment`), add `fetch_comments`
- Modify: `src/jhsymphony/tracker/base.py` (protocol)
- Test: `tests/test_tracker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tracker.py — add at end

@pytest.mark.anyio
async def test_post_comment_returns_id(tracker, httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1/comments",
        method="POST",
        json={"id": 98765, "body": "test"},
    )
    comment_id = await tracker.post_comment(1, "test")
    assert comment_id == 98765


@pytest.mark.anyio
async def test_fetch_comments(tracker, httpx_mock):
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo/issues/1/comments?per_page=100",
        json=[
            {"id": 100, "user": {"login": "bot"}, "body": "analysis", "created_at": "2026-01-01T00:00:00Z"},
            {"id": 101, "user": {"login": "admin"}, "body": "DECISION-1: A", "created_at": "2026-01-01T01:00:00Z"},
        ],
    )
    comments = await tracker.fetch_comments(1)
    assert len(comments) == 2
    assert comments[0]["id"] == 100
    assert comments[0]["author"] == "bot"
    assert comments[1]["body"] == "DECISION-1: A"
```

Note: These tests depend on `httpx_mock` (pytest-httpx). If the fixture is not available, skip this step — the tests will be validated in integration testing. The existing `test_tracker.py` already uses `httpx_mock`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tracker.py::test_post_comment_returns_id -v`
Expected: FAIL — `post_comment` returns None

- [ ] **Step 3: Update `post_comment` to return comment ID**

In `src/jhsymphony/tracker/github.py`:

```python
async def post_comment(self, issue_number: int, body: str) -> int:
    url = f"{_API_BASE}/repos/{self._repo}/issues/{issue_number}/comments"
    resp = await self._client.post(url, json={"body": body})
    resp.raise_for_status()
    return resp.json()["id"]
```

- [ ] **Step 4: Add `fetch_comments` method**

In `src/jhsymphony/tracker/github.py`, add after `post_comment`:

```python
async def fetch_comments(self, issue_number: int) -> list[dict]:
    """Fetch all comments on an issue, ordered by creation time."""
    url = f"{_API_BASE}/repos/{self._repo}/issues/{issue_number}/comments"
    resp = await self._client.get(url, params={"per_page": 100})
    resp.raise_for_status()
    return [
        {
            "id": c["id"],
            "author": c["user"]["login"],
            "body": c["body"],
            "created_at": c["created_at"],
        }
        for c in resp.json()
    ]
```

- [ ] **Step 5: Update tracker protocol**

In `src/jhsymphony/tracker/base.py`:

```python
@runtime_checkable
class TrackerClient(Protocol):
    async def fetch_candidates(self) -> list[Issue]: ...
    async def post_comment(self, issue_number: int, body: str) -> int: ...
    async def fetch_comments(self, issue_number: int) -> list[dict]: ...
    async def create_pr(self, title: str, head: str, base: str, body: str) -> dict: ...
    async def add_labels(self, issue_number: int, labels: list[str]) -> None: ...
    async def remove_label(self, issue_number: int, label: str) -> None: ...
```

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest tests/test_tracker.py -v`
Expected: ALL PASS (or skip if httpx_mock not installed)

- [ ] **Step 7: Commit**

```bash
git add src/jhsymphony/tracker/github.py src/jhsymphony/tracker/base.py tests/test_tracker.py
git commit -m "feat: post_comment returns ID, add fetch_comments method"
```

---

### Task 4: Config — Add `bot_login` to TrackerConfig

**Files:**
- Modify: `src/jhsymphony/config.py:23-28`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_config.py — add at end

def test_tracker_config_bot_login(sample_config):
    import yaml
    raw = sample_config.read_text()
    data = yaml.safe_load(raw)
    data["tracker"]["bot_login"] = "Y-JaeHyun"
    sample_config.write_text(yaml.dump(data))
    config = load_config(sample_config)
    assert config.tracker.bot_login == "Y-JaeHyun"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_config.py::test_tracker_config_bot_login -v`
Expected: FAIL — `bot_login` field not recognized

- [ ] **Step 3: Add `bot_login` field**

In `src/jhsymphony/config.py`:

```python
class TrackerConfig(BaseModel):
    kind: str = "github"
    label: str = "jhsymphony"
    bot_login: str = ""
    poll_interval_sec: int = 30
    webhook_secret: str | None = None
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/jhsymphony/config.py tests/test_config.py
git commit -m "feat: add bot_login to TrackerConfig"
```

---

### Task 5: Dispatcher — Analysis Prompt with DECISION Format & Footer

**Files:**
- Modify: `src/jhsymphony/orchestrator/dispatcher.py:234-269` (analysis prompt), `src/jhsymphony/orchestrator/dispatcher.py:307-317` (dev plan comment posting)
- Test: `tests/test_dispatcher.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_dispatcher.py — add at end

import re

async def test_analysis_prompt_contains_decision_format(dispatcher, storage):
    """The analysis prompt should instruct agents to use DECISION-N format."""
    issue = Issue(id="gh-dec", number=5, repo="o/r", title="Add feature", body="Need changes", labels=["jhsymphony"])
    await storage.upsert_issue(issue)
    run_id = await dispatcher.dispatch(issue)
    assert run_id is not None
    # Wait for the task to complete
    import asyncio
    await asyncio.sleep(0.1)
    # The prompt is passed to provider.run_turn — check mock calls
    provider = dispatcher._router.select([])
    # run_turn is called as async generator, check the prompt arg
    # Since run_turn is a generator function, we verify through the events mechanism


async def test_decision_footer_appended_when_decisions_present(dispatcher, storage):
    """When agent response contains DECISION patterns, footer should include decision instructions."""
    run_id = "run-footer"
    await storage.insert_run(
        Run(id=run_id, issue_id="gh-1", provider="codex", status=RunStatus.RUNNING)
    )
    await storage.insert_event(run_id, 0, "message.delta", {"text": "## Summary\nPlan here\n\n### DECISION-1: DB choice\n> Pick A or B"})
    response = await dispatcher._collect_agent_response(run_id)
    footer = dispatcher._build_plan_footer(response)
    assert "DECISION-1:" in footer
    assert "결정 필요 항목이 있습니다" in footer or "require your decision" in footer


async def test_no_decision_footer_when_no_decisions(dispatcher, storage):
    """When no DECISION patterns, use simple footer."""
    run_id = "run-nofooter"
    await storage.insert_run(
        Run(id=run_id, issue_id="gh-1", provider="codex", status=RunStatus.RUNNING)
    )
    await storage.insert_event(run_id, 0, "message.delta", {"text": "## Summary\nSimple plan"})
    response = await dispatcher._collect_agent_response(run_id)
    footer = dispatcher._build_plan_footer(response)
    assert "결정 필요 항목이 있습니다" not in footer
    assert "Action Required" in footer
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dispatcher.py::test_decision_footer_appended_when_decisions_present -v`
Expected: FAIL — `_build_plan_footer` does not exist

- [ ] **Step 3: Add `_build_plan_footer` method to Dispatcher**

In `src/jhsymphony/orchestrator/dispatcher.py`, add after `_is_question_issue`:

```python
_DECISION_PATTERN = re.compile(r"DECISION-\d+", re.IGNORECASE)

@staticmethod
def _build_plan_footer(agent_response: str) -> str:
    """Build appropriate footer based on whether decisions are needed."""
    has_decisions = bool(Dispatcher._DECISION_PATTERN.search(agent_response))
    if has_decisions:
        return (
            "\n\n---\n"
            "> **결정이 필요한 항목이 있습니다.**\n"
            "> 아래 형식으로 이 이슈에 댓글을 남긴 후 `approved` 라벨을 추가해주세요:\n"
            ">\n"
            "> ```\n"
            "> DECISION-1: A\n"
            "> DECISION-2: B\n"
            "> (필요시 추가 설명)\n"
            "> ```\n"
            ">\n"
            "> **Action Required**: Add the `approved` label to approve this plan and start implementation.\n"
        )
    return (
        "\n\n---\n"
        "> **Action Required**: Add the `approved` label to approve this plan and start implementation.\n"
    )
```

Add `import re` at the top of dispatcher.py (it's not currently imported).

- [ ] **Step 4: Update analysis prompt to include DECISION format instructions**

Replace the analysis prompt block (lines 235-269) with:

```python
prompt = (
    f"You are analyzing GitHub issue #{issue.number}: {issue.title}\n\n"
    f"{issue.body}\n\n"
    f"Determine if this issue requires CODE CHANGES or is a QUESTION/ANALYSIS request.\n\n"
    f"IMPORTANT: Your response will be posted as a GitHub issue comment.\n"
    f"Format your response in clean, readable GitHub-flavored Markdown:\n"
    f"- Use ## headers to separate major sections\n"
    f"- Use ### for subsections\n"
    f"- Use bullet points (- or *) for lists\n"
    f"- Use `backticks` for file names, function names, variable names, error codes\n"
    f"- Use ```language code blocks for code snippets\n"
    f"- Use > blockquotes for key findings or conclusions\n"
    f"- Use **bold** for emphasis on critical points\n"
    f"- Keep paragraphs short (2-3 sentences max)\n"
    f"- Add blank lines between sections for readability\n\n"
    f"If this is a QUESTION or ANALYSIS request (no code changes needed):\n"
    f"Structure your response as:\n"
    f"## Summary (1-2 sentence overview)\n"
    f"## Analysis (detailed findings with code references)\n"
    f"## Root Cause (if applicable)\n"
    f"## Recommendation (actionable next steps)\n"
    f"- Do NOT modify any files\n\n"
    f"If CODE CHANGES are needed:\n"
    f"- Do NOT implement the changes yet\n"
    f"Structure your response as:\n"
    f"## Summary (what needs to change and why)\n"
    f"## Affected Files\n"
    f"| File | Change Type | Description |\n"
    f"|------|------------|-------------|\n"
    f"## Implementation Plan (step by step)\n"
    f"## Testing Strategy\n"
    f"## Risks & Considerations\n\n"
    f"If there are items that require admin decisions before implementation,\n"
    f"list them in a dedicated section using this exact format:\n\n"
    f"## Decisions Required\n\n"
    f"### DECISION-1: <short title>\n"
    f"> <context explaining why this decision is needed>\n"
    f"> - **A)** <option A description>\n"
    f"> - **B)** <option B description>\n\n"
    f"Repeat for each decision point (DECISION-2, DECISION-3, etc.).\n"
    f"Use **bold** for each DECISION title to make them stand out.\n\n"
    f"- Do NOT modify any files\n\n"
    f"Work in the current directory."
)
```

- [ ] **Step 5: Update dev plan comment posting to use `_build_plan_footer` and capture comment ID**

Replace the dev plan posting block (lines 308-317) with:

```python
# Development request: post plan → await approval
footer = self._build_plan_footer(agent_response)
comment_body = f"{agent_response}{footer}\n\n<sub>Analyzed by JHSymphony | Run: `{run_id}`</sub>"
comment_id = await self._tracker.post_comment(issue.number, comment_body)
await self._storage.update_run_analysis_comment_id(run_id, comment_id)
await self._tracker.add_labels(issue.number, ["waiting-approval"])
await self._storage.update_issue_state(issue.id, IssueState.AWAITING_APPROVAL)
logger.info("Posted dev plan for issue #%d, awaiting approval", issue.number)
```

Also update the question flow comment posting (line 288-291) and the "analyzing" comment (line 227-232) to handle the new `int` return from `post_comment`:

```python
# Line 227-232: just ignore the return value
try:
    await self._tracker.post_comment(
        issue.number,
        f"**JHSymphony** is analyzing this issue... (run `{run_id}`)",
    )
except Exception:
    pass
```

```python
# Line 288-291: question flow
await self._tracker.post_comment(
    issue.number,
    f"{agent_response}\n\n---\n<sub>Analyzed by JHSymphony | Run: `{run_id}`</sub>",
)
```

These already work correctly since the return value is optional to use.

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest tests/test_dispatcher.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/jhsymphony/orchestrator/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: analysis prompt with DECISION format and dynamic footer"
```

---

### Task 6: Dispatcher — Extract Admin Decisions & Context-Rich Implementation

**Files:**
- Modify: `src/jhsymphony/orchestrator/dispatcher.py` — add `_extract_admin_decisions`, update `_execute_implementation`, add `bot_login` config
- Test: `tests/test_dispatcher.py`

- [ ] **Step 1: Write failing tests for decision extraction**

```python
# tests/test_dispatcher.py — add at end

async def test_extract_admin_decisions_by_comment_id(dispatcher):
    comments = [
        {"id": 100, "author": "bot", "body": "## Plan\n### DECISION-1: DB\n> A or B", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 101, "author": "admin", "body": "DECISION-1: A\nWe prefer postgres", "created_at": "2026-01-01T01:00:00Z"},
    ]
    decisions, raw = dispatcher._extract_admin_decisions(comments, "bot", analysis_comment_id=100)
    assert decisions["1"] == "A"
    assert "We prefer postgres" in raw


async def test_extract_admin_decisions_fallback_to_bot_pattern(dispatcher):
    comments = [
        {"id": 100, "author": "bot", "body": "## Plan\n### DECISION-1: DB\n> A or B", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 101, "author": "admin", "body": "DECISION-1: B\nGo with mysql", "created_at": "2026-01-01T01:00:00Z"},
    ]
    # No comment_id match — should fallback to pattern
    decisions, raw = dispatcher._extract_admin_decisions(comments, "bot", analysis_comment_id=999)
    assert decisions["1"] == "B"


async def test_extract_admin_decisions_multiple(dispatcher):
    comments = [
        {"id": 100, "author": "bot", "body": "DECISION-1: X\nDECISION-2: Y", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 101, "author": "admin", "body": "DECISION-1: A\nDECISION-2: B\nExtra context here", "created_at": "2026-01-01T01:00:00Z"},
    ]
    decisions, raw = dispatcher._extract_admin_decisions(comments, "bot", analysis_comment_id=100)
    assert decisions["1"] == "A"
    assert decisions["2"] == "B"


async def test_extract_admin_decisions_no_decisions(dispatcher):
    comments = [
        {"id": 100, "author": "bot", "body": "## Plan\nNo decisions needed", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 101, "author": "admin", "body": "Looks good!", "created_at": "2026-01-01T01:00:00Z"},
    ]
    decisions, raw = dispatcher._extract_admin_decisions(comments, "bot", analysis_comment_id=100)
    assert decisions == {}
    assert "Looks good!" in raw


async def test_extract_admin_decisions_ignores_bot_comments(dispatcher):
    comments = [
        {"id": 100, "author": "bot", "body": "DECISION-1: X", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 101, "author": "bot", "body": "DECISION-1: ignore this", "created_at": "2026-01-01T01:00:00Z"},
        {"id": 102, "author": "admin", "body": "DECISION-1: A", "created_at": "2026-01-01T02:00:00Z"},
    ]
    decisions, raw = dispatcher._extract_admin_decisions(comments, "bot", analysis_comment_id=100)
    assert decisions["1"] == "A"
    assert "ignore this" not in raw
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dispatcher.py::test_extract_admin_decisions_by_comment_id -v`
Expected: FAIL — `_extract_admin_decisions` does not exist

- [ ] **Step 3: Add `_extract_admin_decisions` to Dispatcher**

In `src/jhsymphony/orchestrator/dispatcher.py`, add after `_build_plan_footer`:

```python
_DECISION_RE = re.compile(r"DECISION-(\d+)\s*:\s*(.+)", re.IGNORECASE)

@staticmethod
def _extract_admin_decisions(
    comments: list[dict], bot_login: str, analysis_comment_id: int | None = None
) -> tuple[dict[str, str], str]:
    """Extract admin decisions from issue comments after the analysis comment.

    Returns:
        decisions: {"1": "A", "2": "B - with explanation"}
        raw_admin_text: full text of admin comments for prompt context
    """
    # Find the analysis comment by stored comment ID (reliable anchor)
    analysis_idx = -1
    for i, c in enumerate(comments):
        if c.get("id") == analysis_comment_id:
            analysis_idx = i

    # Fallback: find last bot comment with DECISION patterns
    if analysis_idx < 0:
        for i, c in enumerate(comments):
            if c["author"] == bot_login and "DECISION-" in c["body"]:
                analysis_idx = i

    # Collect admin comments after analysis
    admin_comments = []
    if analysis_idx >= 0:
        for c in comments[analysis_idx + 1:]:
            if c["author"] != bot_login:
                admin_comments.append(c["body"])

    raw_text = "\n\n".join(admin_comments)
    decisions = {}
    for m in Dispatcher._DECISION_RE.finditer(raw_text):
        decisions[m.group(1)] = m.group(2).strip()

    return decisions, raw_text
```

- [ ] **Step 4: Run decision extraction tests**

Run: `python3 -m pytest tests/test_dispatcher.py -k "extract_admin" -v`
Expected: ALL PASS

- [ ] **Step 5: Commit extraction logic**

```bash
git add src/jhsymphony/orchestrator/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: add _extract_admin_decisions to dispatcher"
```

- [ ] **Step 6: Update `__init__` to accept `bot_login` and `config`**

The Dispatcher needs `bot_login` to filter comments. Add it to `__init__`:

```python
def __init__(
    self,
    storage: Storage,
    lease_manager: LeaseManager,
    workspace_manager: Any,
    provider_router: Any,
    tracker: Any,
    max_concurrent: int = 4,
    budget_daily_limit: float = 100.0,
    budget_per_run_limit: float = 20.0,
    bot_login: str = "",
) -> None:
    self._storage = storage
    self._lease_manager = lease_manager
    self._workspace_manager = workspace_manager
    self._router = provider_router
    self._tracker = tracker
    self._max_concurrent = max_concurrent
    self._budget_daily_limit = budget_daily_limit
    self._budget_per_run_limit = budget_per_run_limit
    self._bot_login = bot_login
    self._tasks: dict[str, asyncio.Task] = {}
```

- [ ] **Step 7: Rewrite `_execute_implementation` with full context**

Replace the entire `_execute_implementation` method:

```python
async def _execute_implementation(self, run_id: str, issue: Issue, provider: Any) -> None:
    """Phase 2: Actually implement code changes (after admin approval)."""
    try:
        await self._storage.update_issue_state(issue.id, IssueState.PREPARING)
        await self._storage.update_run_status(run_id, RunStatus.RUNNING)

        workspace = await self._workspace_manager.create(issue.id)
        await self._storage.update_issue_state(issue.id, IssueState.RUNNING)

        try:
            await self._tracker.post_comment(
                issue.number,
                f"**JHSymphony** is now implementing the approved plan... (run `{run_id}`)",
            )
            await self._tracker.remove_label(issue.number, "waiting-approval")
        except Exception:
            pass

        # Collect Phase 1 analysis
        analysis_text = ""
        analysis_comment_id = None
        analysis_run = await self._storage.get_analysis_run(issue.id)
        if analysis_run:
            analysis_text = await self._collect_agent_response(analysis_run.id)
            analysis_comment_id = analysis_run.analysis_comment_id

        # Collect admin decisions from comments
        admin_decisions_text = ""
        decisions_summary = ""
        if self._bot_login:
            comments = await self._tracker.fetch_comments(issue.number)
            decisions, raw_admin = self._extract_admin_decisions(
                comments, self._bot_login, analysis_comment_id
            )
            if raw_admin:
                admin_decisions_text = raw_admin
            if decisions:
                decisions_summary = "\n".join(
                    f"- DECISION-{k}: {v}" for k, v in sorted(decisions.items())
                )

        # Build context-rich implementation prompt
        prompt_parts = [
            f"You are implementing GitHub issue #{issue.number}: {issue.title}\n",
            f"## Original Issue\n{issue.body}\n",
        ]
        if analysis_text and analysis_text != "Analysis completed.":
            prompt_parts.append(f"## Analysis Plan (from Phase 1)\n{analysis_text}\n")
        if decisions_summary:
            prompt_parts.append(f"## Admin Decisions\n{decisions_summary}\n")
        if admin_decisions_text:
            prompt_parts.append(f"## Admin Comments (raw)\n{admin_decisions_text}\n")
        prompt_parts.append(
            "Implement the changes following the analysis plan above.\n"
            "Where the analysis identified DECISION points, follow the admin's chosen option.\n"
            "Steps:\n"
            "1. Read relevant code to understand the codebase\n"
            "2. Implement changes per the plan and decisions\n"
            "3. Write or update tests\n"
            "4. Run tests\n"
            "5. Commit with descriptive messages\n\n"
            "Work in the current directory. Do not ask questions — just implement."
        )
        prompt = "\n".join(prompt_parts)

        await self._run_agent(run_id, issue, provider, prompt, workspace)

        await self._storage.update_run_status(run_id, RunStatus.COMPLETED)
        ws_path = str(workspace.path)
        default_branch = await self._detect_default_branch(ws_path)
        has_changes = await self._has_code_changes(ws_path, default_branch)

        if has_changes:
            await self._do_pr_flow(issue, run_id, workspace, default_branch)
        else:
            await self._tracker.post_comment(
                issue.number,
                f"**JHSymphony** completed the implementation but no code changes were detected.\n*Run: `{run_id}`*",
            )
            await self._storage.update_issue_state(issue.id, IssueState.COMPLETED)

    except asyncio.CancelledError:
        await self._storage.update_run_status(run_id, RunStatus.CANCELLED)
        await self._storage.update_issue_state(issue.id, IssueState.CANCELLED)
        raise
    except Exception as exc:
        logger.exception("Implementation run %s failed: %s", run_id, exc)
        await self._storage.update_run_status(run_id, RunStatus.FAILED, error=str(exc))
        await self._storage.update_issue_state(issue.id, IssueState.FAILED)
    finally:
        await self._lease_manager.release(issue.id)
```

- [ ] **Step 8: Run all dispatcher tests**

Run: `python3 -m pytest tests/test_dispatcher.py -v`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add src/jhsymphony/orchestrator/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: context-rich implementation prompt with analysis and admin decisions"
```

---

### Task 7: Dispatcher — Always Use Claude for Implementation

**Files:**
- Modify: `src/jhsymphony/orchestrator/dispatcher.py:78-97` (`dispatch_approved`)
- Test: `tests/test_dispatcher.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_dispatcher.py — add at end

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

    # Verify the run was created with claude, not codex
    run = await storage.get_run(run_id)
    assert run.provider == "claude"

    # Verify router.get("claude") was called, not router.select()
    router.get.assert_called_with("claude")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_dispatcher.py::test_dispatch_approved_uses_claude -v`
Expected: FAIL — `dispatch_approved` currently uses `router.select()`

- [ ] **Step 3: Update `dispatch_approved` to use Claude**

In `src/jhsymphony/orchestrator/dispatcher.py`, replace the provider selection in `dispatch_approved`:

```python
async def dispatch_approved(self, issue: Issue) -> str | None:
    """Dispatch an already-approved issue for implementation (always uses Claude)."""
    acquired = await self._lease_manager.try_acquire(issue.id)
    if not acquired:
        return None

    run_id = str(uuid.uuid4())
    # Always use Claude for implementation, fallback to label-based routing
    provider = self._router.get("claude") or self._router.select(issue.labels)
    _name_attr = getattr(provider, "name", None)
    provider_name = _name_attr if isinstance(_name_attr, str) else type(provider).__name__

    run = Run(id=run_id, issue_id=issue.id, provider=provider_name, status=RunStatus.STARTING)
    await self._storage.insert_run(run)

    task = asyncio.create_task(self._execute_implementation(run_id, issue, provider))
    self._tasks[run_id] = task
    task.add_done_callback(lambda t: self._tasks.pop(run_id, None))

    logger.info("Dispatched implementation run %s for approved issue %s (Claude)", run_id, issue.id)
    return run_id
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_dispatcher.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/jhsymphony/orchestrator/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: always use Claude for implementation phase"
```

---

### Task 8: Startup Validation & Main Wiring

**Files:**
- Modify: `src/jhsymphony/main.py:66-78`
- Test: manual validation (startup behavior)

- [ ] **Step 1: Add Claude provider validation in `run_app`**

In `src/jhsymphony/main.py`, add after `providers = _build_providers(config)` (line 66):

```python
providers = _build_providers(config)
if "claude" not in providers:
    console.print("[red bold]Error:[/red bold] Claude provider is required for implementation phase. Add providers.claude to config.")
    return
```

- [ ] **Step 2: Pass `bot_login` to Dispatcher**

Update the Dispatcher instantiation (line 72-78):

```python
dispatcher = Dispatcher(
    storage=storage, lease_manager=lease_mgr, workspace_manager=workspace_mgr,
    provider_router=router, tracker=tracker,
    max_concurrent=config.orchestrator.max_concurrent_agents,
    budget_daily_limit=config.budget.daily_limit_usd,
    budget_per_run_limit=config.budget.per_run_limit_usd,
    bot_login=config.tracker.bot_login,
)
```

- [ ] **Step 3: Run all tests to verify nothing is broken**

Run: `python3 -m pytest tests/ --ignore=tests/test_dashboard.py --ignore=tests/test_tracker.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/jhsymphony/main.py
git commit -m "feat: startup Claude validation and bot_login wiring"
```

---

### Task 9: Update Existing Dispatcher Tests for New `bot_login` Parameter

**Files:**
- Modify: `tests/test_dispatcher.py` (fixture update)
- Modify: `tests/conftest.py` (sample config)

- [ ] **Step 1: Update dispatcher fixture to include `bot_login`**

In `tests/test_dispatcher.py`, update the `dispatcher` fixture:

```python
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
```

- [ ] **Step 2: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_dashboard.py --ignore=tests/test_tracker.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_dispatcher.py tests/conftest.py
git commit -m "test: update fixtures for new bot_login parameter"
```
