# PR Revision Loop + Step-level Verification + Feedback Structure

**Date**: 2026-04-02
**Status**: Approved

## Problem

1. PR 생성 후 관리자 피드백을 반영할 방법이 없음 (flow가 COMPLETED에서 끝남)
2. 파일 수준 verification은 같은 파일 내 함수 누락을 감지 못함 (PR #24: MetricHelperLinux.go 4개 함수 중 1개만 수정됨에도 100% coverage)
3. DECISION 외 자유형 피드백 (SKIP/ADD/CORRECT)이 구조화되지 않음

## Design

### 1. State Machine Changes

Add `PR_OPEN` and `REVISING` states. Remove auto-close of issues.

```
PENDING → ANALYZING → AWAITING_APPROVAL → IMPLEMENTING → PR_OPEN
                                                           ↕
                                                        REVISING
                                                           ↓
                                                   (issue Close → done)
```

- `PR_OPEN`: PR created, waiting for admin review or merge
- `REVISING`: revision run in progress
- JHSymphony never closes issues. Admin closes after merge.

### 2. PR Revision Loop

**Trigger**: Scheduler detects `PR_OPEN` state + `needs-revision` label on issue.

**Scheduler._check_revisions()**: New method alongside `_check_approvals()`. Iterates `PR_OPEN` issues, checks for `needs-revision` label, calls `dispatcher.dispatch_revision(issue)`.

**Dispatcher.dispatch_revision(issue)**:
1. Reuse existing workspace/branch (same as implementation)
2. Collect: original issue + analysis text + admin decisions + git diff (current state) + post-PR admin comments (revision feedback)
3. Build revision prompt focusing on the feedback
4. Run agent on existing branch
5. Auto-commit + push → PR auto-updates
6. Remove `needs-revision` label
7. State back to `PR_OPEN`

**Revision prompt structure**:
```
You are revising implementation for issue #{number}: {title}

## Original Issue
{body}

## Analysis Plan
{analysis_text}

## Current Implementation (already on this branch)
{git diff --stat}

## Revision Requested
The following feedback was provided after PR review:
{post_pr_admin_comments}

Implement the requested changes. The existing code on this branch is your starting point.
Read ONLY the files mentioned in the feedback, then make the changes.
Commit after each logical unit of work.
```

### 3. Step-level Verification

**Manifest extension**: Add `required_changes` field to PlanManifest.

```json
{
  "required_files": [...],
  "required_changes": [
    {"file": "helper/MetricHelperLinux.go", "symbol": "PopulateMemoryInfra", "step_id": 1},
    {"file": "helper/MetricHelperLinux.go", "symbol": "PopulateProcessGroupInfra", "step_id": 6},
    {"file": "helper/MetricHelperLinux.go", "symbol": "PopulateProcessTopNInfra", "step_id": 6},
    {"file": "helper/MetricHelperLinux.go", "symbol": "PopulateInfraFilesystem", "step_id": 7}
  ],
  "implementation_steps": [...]
}
```

**_check_completeness() enhancement**:
- After file-level check, do symbol-level check
- For each `required_changes` entry, grep the `git diff` output for the symbol name
- Symbol coverage ratio = covered_symbols / total_symbols
- Final coverage = min(file_coverage, symbol_coverage) — whichever is lower wins
- If file coverage is 100% but symbol coverage is 25% → PARTIAL (not COMPLETE)

**Phase 1 prompt update**: Add instruction to include `required_changes` in the manifest block.

### 4. Feedback Structure

**Parsing**: Extend `_extract_admin_decisions()` to also parse structured feedback types.

Supported patterns in admin comments:
```
DECISION-1: A
SKIP step-2: already handled elsewhere
ADD: include field X in infra_process
CORRECT: file path should be Z not Y
```

**New method `_extract_admin_feedback()`**: Parses SKIP/ADD/CORRECT from admin comments.

**Prompt injection**: Admin feedback is included in a dedicated section with clear instruction:
```
## Admin Feedback (MUST be applied)
- SKIP step-2: already handled elsewhere
- ADD: include field X in infra_process
- CORRECT: file path should be Z not Y

Apply ALL feedback items above. Skip any steps marked SKIP. Include any ADD items. Fix any CORRECT items.
```

Free-form comments (not matching any pattern) are still included as raw text in `## Admin Comments (raw)`.

### 5. Affected Files

| File | Change |
|------|--------|
| `src/jhsymphony/models.py` | Add `PR_OPEN`, `REVISING` to IssueState; add `required_changes` to PlanManifest |
| `src/jhsymphony/orchestrator/scheduler.py` | Add `_check_revisions()` method |
| `src/jhsymphony/orchestrator/dispatcher.py` | Add `dispatch_revision()`, `_execute_revision()`, `_extract_admin_feedback()`; update `_check_completeness()` for symbol-level; update `_do_pr_flow()` to set PR_OPEN; update Phase 1 prompt for required_changes |
| `tests/test_dispatcher.py` | Tests for new methods |
| `tests/test_scheduler.py` | Tests for `_check_revisions()` |
