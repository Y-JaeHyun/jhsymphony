# Approval Flow Redesign: Structured Decisions & Context-Rich Implementation

## Problem

Current Phase 1 → Phase 2 flow has three critical gaps:

1. **Analysis lacks structured decisions**: Open questions are buried in free-text "Risks & Considerations", making it hard for admins to know what needs deciding
2. **Admin decisions are ignored**: `dispatch_approved()` doesn't read issue comments before implementation, so admin responses to open questions are never consumed
3. **Implementation lacks context**: The implementation prompt only contains issue title + body — no Phase 1 analysis, no admin decisions
4. **Wrong provider for implementation**: `use-codex` label routes implementation to Codex instead of Claude

Evidence: Issue #10 analysis identified 8+ files and multiple open questions. PR #12 only modified 2 files (+17/-5), ignored all decision points.

## Design

### 1. Analysis Prompt: DECISION Format

The analysis prompt in `dispatcher.py` (`_execute_run`) is extended to instruct the agent to use a structured format for items requiring admin decisions.

**Prompt addition:**

```
If there are items that require admin decisions before implementation,
list them in a dedicated "## Decisions Required" section using this exact format:

### DECISION-1: <short title>
> <context explaining why this decision is needed>
> - **A)** <option A description>
> - **B)** <option B description>

Repeat for each decision point (DECISION-2, DECISION-3, etc.).
```

**Post-processing in dispatcher**: After collecting the agent response, if the response contains `DECISION-` patterns, append the admin instruction footer:

```markdown
---
> **Results that require your decision have been identified.**
> Please leave a comment on this issue in the following format, then add the `approved` label:
>
> ```
> DECISION-1: A
> DECISION-2: B
> (add explanation if needed)
> ```
>
> **Action Required**: Add the `approved` label to approve this plan and start implementation.
```

If no DECISION patterns are found, use the existing footer (just the "Action Required" line).

### 2. Comment Collection: `fetch_comments()`

Add a new method to `GitHubTracker`:

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

### 3. Decision Extraction in Dispatcher

Add a helper to `Dispatcher` that, given an issue's comments:

1. Finds the bot's analysis comment (the one containing `DECISION-` patterns)
2. Collects all comments posted **after** the analysis comment by **non-bot** authors
3. Parses `DECISION-N: <choice>` patterns from admin comments
4. Returns both the structured decisions dict and the raw admin comment text

```python
import re

_DECISION_RE = re.compile(r"DECISION-(\d+)\s*:\s*(.+)", re.IGNORECASE)

def _extract_admin_decisions(
    self, comments: list[dict], bot_login: str, analysis_comment_id: int | None = None
) -> tuple[dict[str, str], str]:
    """
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
    for m in _DECISION_RE.finditer(raw_text):
        decisions[m.group(1)] = m.group(2).strip()

    return decisions, raw_text
```

`bot_login` is configured in `jhsymphony.yaml` under `tracker.bot_login` (e.g., `"Y-JaeHyun"`). This is required for filtering bot vs admin comments. Add this field to `TrackerConfig`.

### 4. Implementation Prompt: Full Context

`_execute_implementation()` is restructured to build a rich prompt:

```python
prompt = (
    f"You are implementing GitHub issue #{issue.number}: {issue.title}\n\n"
    f"## Original Issue\n{issue.body}\n\n"
    f"## Analysis Plan (from Phase 1)\n{analysis_text}\n\n"
    f"## Admin Decisions\n{admin_decisions_text}\n\n"
    f"Implement the changes following the analysis plan above.\n"
    f"Where the analysis identified DECISION points, follow the admin's chosen option.\n"
    f"Steps:\n"
    f"1. Read relevant code to understand the codebase\n"
    f"2. Implement changes per the plan and decisions\n"
    f"3. Write or update tests\n"
    f"4. Run tests\n"
    f"5. Commit with descriptive messages\n\n"
    f"Work in the current directory. Do not ask questions - just implement."
)
```

**How to get `analysis_text`**: Query the storage for the Phase 1 (analysis) run on this issue and call `_collect_agent_response()` with that run_id. Add a storage method `get_analysis_run(issue_id)` that returns the first completed run for the issue (Phase 1 is always the first run dispatched for an issue).

### 5. Implementation Always Uses Claude

In `dispatch_approved()`, replace:

```python
provider = self._router.select(issue.labels)
```

With:

```python
provider = self._router.get("claude") or self._router.select(issue.labels)
```

This ensures Claude is always used for implementation, falling back to label-based routing only if Claude is not configured.

### 6. Prerequisites: Persist Issue Body & Analysis Comment ID

**Issue body not persisted (existing bug)**: The `issues` table schema lacks a `body` column. The `Issue` model has `body: str`, but `upsert_issue()` and `_row_to_issue()` omit it. When `dispatch_approved()` loads the issue from storage, `body` is empty. This must be fixed for the implementation prompt to include the original issue text.

- Add `body TEXT NOT NULL DEFAULT ''` to the `issues` table schema
- Update `upsert_issue()` to persist `body`
- Update `_row_to_issue()` to read `body`

**Analysis comment ID not captured**: `post_comment()` discards the GitHub API response, which contains the comment ID. To reliably anchor "admin comments after analysis", store the analysis comment ID.

- `post_comment()` returns the comment ID (int) from GitHub response
- Store `analysis_comment_id` in the `runs` table (new column)
- `_extract_admin_decisions()` uses this ID to fetch only comments posted after the analysis comment, instead of timestamp-based filtering

### 7. Startup Validation for Claude Provider

Since implementation always uses Claude, startup must fail if Claude is not configured.

- In `main.py` (or wherever providers are initialized), validate that `providers.claude` is not None
- Log a clear error message: "Claude provider is required for implementation phase"

## File Changes

| File | Change | Lines (est.) |
|------|--------|-------------|
| `orchestrator/dispatcher.py` | Analysis prompt DECISION format, decision footer, `_extract_admin_decisions()`, implementation prompt rebuild, Claude-only for Phase 2 | ~80 |
| `tracker/github.py` | `fetch_comments()` method, `post_comment()` returns comment ID | ~20 |
| `storage/sqlite.py` | `body` column in issues table, `analysis_comment_id` in runs table, `get_analysis_run(issue_id)` method | ~30 |
| `storage/base.py` | Protocol method declaration for `get_analysis_run` | ~3 |
| `config.py` | `TrackerConfig.bot_login` field | ~1 |
| `main.py` | Startup validation for Claude provider | ~5 |

## Testing Strategy

- Unit test `_extract_admin_decisions()` with various comment patterns (no decisions, single, multiple, mixed with noise)
- Unit test that analysis prompt contains DECISION format instructions
- Unit test that decision footer is appended only when DECISION patterns present
- Unit test that `dispatch_approved()` uses Claude provider regardless of labels
- Integration test: mock comments with DECISION responses, verify implementation prompt contains analysis + decisions

## Risks

- **LLM compliance**: Claude/Codex may not consistently follow the DECISION-N format. Mitigation: the format instructions are explicit, and the admin instruction footer is always appended by code (not by the LLM).
- **Bot login detection**: Need to know the bot's GitHub username. Mitigation: can be derived from the token or configured in yaml.
