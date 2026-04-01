from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any

from jhsymphony.models import Issue, IssueState, Run, RunStatus, UsageRecord
from jhsymphony.orchestrator.lease import LeaseManager
from jhsymphony.providers.base import EventType, RunContext
from jhsymphony.storage.base import Storage
from jhsymphony.workspace.isolation import run_subprocess

logger = logging.getLogger(__name__)


class Dispatcher:
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

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    async def can_dispatch(self, issue: Issue) -> bool:
        active_runs = await self._storage.list_active_runs()
        if len(active_runs) >= self._max_concurrent:
            return False
        if await self._lease_manager.is_held(issue.id):
            return False
        daily_cost = await self._storage.sum_daily_cost()
        if daily_cost >= self._budget_daily_limit:
            logger.warning("Daily budget limit reached: $%.2f", daily_cost)
            return False
        return True

    async def dispatch(self, issue: Issue) -> str | None:
        if not await self.can_dispatch(issue):
            return None
        acquired = await self._lease_manager.try_acquire(issue.id)
        if not acquired:
            return None

        run_id = str(uuid.uuid4())
        provider = self._router.select(issue.labels)
        _name_attr = getattr(provider, "name", None)
        provider_name = _name_attr if isinstance(_name_attr, str) else type(provider).__name__

        run = Run(id=run_id, issue_id=issue.id, provider=provider_name, status=RunStatus.STARTING)
        await self._storage.insert_run(run)
        await self._storage.update_issue_state(issue.id, IssueState.LEASED)

        task = asyncio.create_task(self._execute_run(run_id, issue, provider))
        self._tasks[run_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(run_id, None))

        logger.info("Dispatched run %s for issue %s", run_id, issue.id)
        return run_id

    async def dispatch_approved(self, issue: Issue) -> str | None:
        """Dispatch an already-approved issue for implementation."""
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

        logger.info("Dispatched implementation run %s for approved issue %s using Claude", run_id, issue.id)
        return run_id

    # ── Agent execution helpers ──

    async def _run_agent(self, run_id: str, issue: Issue, provider: Any, prompt: str, workspace: Any) -> int:
        """Run agent and record events. Returns event sequence count."""
        ctx = RunContext(
            workspace_path=str(workspace.path),
            branch=workspace.branch,
            issue_title=issue.title,
            issue_body=issue.body,
        )
        session = await provider.start_session(ctx)
        seq = 0

        async for event in provider.run_turn(session, prompt):
            await self._storage.insert_event(run_id=run_id, seq=seq, event_type=event.type, payload=event.data)
            seq += 1

            if event.type == EventType.USAGE:
                _pname = getattr(provider, "name", type(provider).__name__)
                usage = UsageRecord(
                    run_id=run_id, provider=_pname,
                    input_tokens=event.data.get("input_tokens", 0),
                    output_tokens=event.data.get("output_tokens", 0),
                    estimated_cost_usd=event.data.get("cost_usd", 0.0),
                )
                await self._storage.record_usage(usage)

                run_cost = await self._storage.sum_run_cost(run_id)
                if run_cost >= self._budget_per_run_limit:
                    logger.warning("Run %s exceeded per-run budget, stopping", run_id)
                    break

        return seq

    async def _collect_agent_response(self, run_id: str) -> str:
        """Collect agent text output from message.delta and tool_result events."""
        events = await self._storage.list_events(run_id)
        message_parts: list[str] = []
        last_tool_result: str = ""

        for evt in events:
            evt_type = evt.get("type") or evt.get("event_type", "")
            payload = evt.get("payload", {})

            if evt_type == "message.delta":
                text = payload.get("text", "")
                if text.strip():
                    message_parts.append(text)
            elif evt_type == "tool.result":
                # Keep track of the last substantial tool result as fallback
                content = payload.get("content", "")
                if isinstance(content, list):
                    # Handle list-of-blocks format: [{"type": "text", "text": "..."}]
                    text_parts = [
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    content = "\n".join(t for t in text_parts if t.strip())
                if isinstance(content, str) and len(content.strip()) > 100:
                    last_tool_result = content.strip()

        if message_parts:
            return "\n".join(message_parts)

        if last_tool_result:
            logger.warning("Run %s: no message.delta events, falling back to last tool_result", run_id)
            return last_tool_result

        logger.warning("Run %s: no agent text output collected", run_id)
        return "Analysis completed."

    async def _detect_default_branch(self, ws_path: str) -> str:
        result = await run_subprocess(
            ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
            cwd=ws_path, env=None, timeout_sec=10,
        )
        branch = result.stdout.strip().replace("origin/", "") if result.returncode == 0 else "main"
        return branch if branch and branch != "HEAD" else "main"

    async def _has_code_changes(self, ws_path: str, default_branch: str) -> bool:
        # Auto-commit uncommitted changes
        await run_subprocess(["git", "add", "-A"], cwd=ws_path, env=None, timeout_sec=10)
        diff_staged = await run_subprocess(["git", "diff", "--cached", "--quiet"], cwd=ws_path, env=None, timeout_sec=10)
        if diff_staged.returncode != 0:
            await run_subprocess(
                ["git", "commit", "-m", "feat: auto-committed by JHSymphony"],
                cwd=ws_path, env=None, timeout_sec=10,
            )
        # Check diff from base
        diff_from_base = await run_subprocess(
            ["git", "diff", f"origin/{default_branch}...HEAD", "--quiet"],
            cwd=ws_path, env=None, timeout_sec=10,
        )
        return diff_from_base.returncode != 0

    # ── Main execution flow ──

    async def _execute_run(self, run_id: str, issue: Issue, provider: Any) -> None:
        """Phase 1: Analyze issue → question response or development plan."""
        try:
            is_question = self._is_question_issue(issue)

            # For questions, check Q&A cache first
            if is_question:
                query = f"{issue.title} {issue.body}"
                cache_hits = await self._storage.search_qa_cache(issue.repo, query, limit=1)
                if cache_hits:
                    cached = cache_hits[0]
                    await self._storage.update_run_status(run_id, RunStatus.COMPLETED)
                    category_parts = [p for p in [cached['category_major'], cached['category_mid'], cached['category_minor']] if p]
                    category_str = " > ".join(category_parts) if category_parts else "General"
                    ref_issue = f"#{cached['issue_number']}" if cached['issue_number'] else "N/A"
                    await self._tracker.post_comment(
                        issue.number,
                        f"{cached['answer']}\n\n---\n"
                        f"<sub>Answered from cache | Category: {category_str} | Ref: {ref_issue}</sub>",
                    )
                    await self._tracker.close_issue(issue.number)
                    await self._storage.update_issue_state(issue.id, IssueState.COMPLETED)
                    logger.info("Answered issue #%d from Q&A cache (hit)", issue.number)
                    return

            await self._storage.update_issue_state(issue.id, IssueState.ANALYZING)
            await self._storage.update_run_status(run_id, RunStatus.RUNNING)

            workspace = await self._workspace_manager.create(issue.id)

            try:
                await self._tracker.post_comment(
                    issue.number,
                    f"**JHSymphony** is analyzing this issue... (run `{run_id}`)",
                )
            except Exception:
                pass

            # Analysis prompt — determines issue type and responds accordingly
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
                f"## Risks & Considerations\n"
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

            await self._run_agent(run_id, issue, provider, prompt, workspace)

            await self._storage.update_run_status(run_id, RunStatus.COMPLETED)
            agent_response = await self._collect_agent_response(run_id)
            ws_path = str(workspace.path)
            default_branch = await self._detect_default_branch(ws_path)
            has_changes = await self._has_code_changes(ws_path, default_branch)

            if has_changes:
                # Agent made code changes despite instructions — treat as question that got code changes
                # Push and create PR anyway
                await self._do_pr_flow(issue, run_id, workspace, default_branch)
            else:
                # Check if this looks like a development request (needs approval)
                is_question = self._is_question_issue(issue)
                if is_question:
                    # Question flow: post answer → cache → close
                    await self._tracker.post_comment(
                        issue.number,
                        f"{agent_response}\n\n---\n<sub>Analyzed by JHSymphony | Run: `{run_id}`</sub>",
                    )
                    # Cache the Q&A for future use
                    try:
                        await self._storage.insert_qa_cache(
                            repo=issue.repo,
                            question=f"{issue.title}\n{issue.body}",
                            answer=agent_response,
                            subject=issue.title,
                            issue_number=issue.number,
                        )
                        logger.info("Cached Q&A for issue #%d", issue.number)
                    except Exception:
                        logger.debug("Failed to cache Q&A", exc_info=True)
                    await self._tracker.close_issue(issue.number)
                    await self._storage.update_issue_state(issue.id, IssueState.COMPLETED)
                    logger.info("Posted analysis and closed question issue #%d", issue.number)
                else:
                    # Development request: post plan → await approval
                    footer = self._build_plan_footer(agent_response)
                    comment_body = f"{agent_response}{footer}\n\n<sub>Analyzed by JHSymphony | Run: `{run_id}`</sub>"
                    comment_id = await self._tracker.post_comment(issue.number, comment_body)
                    await self._storage.update_run_analysis_comment_id(run_id, comment_id)
                    await self._tracker.add_labels(issue.number, ["waiting-approval"])
                    await self._storage.update_issue_state(issue.id, IssueState.AWAITING_APPROVAL)
                    logger.info("Posted dev plan for issue #%d, awaiting approval", issue.number)

        except asyncio.CancelledError:
            logger.info("Run %s was cancelled for issue %s", run_id, issue.id)
            await self._storage.update_run_status(run_id, RunStatus.CANCELLED)
            await self._storage.update_issue_state(issue.id, IssueState.CANCELLED)
            raise
        except Exception as exc:
            logger.exception("Run %s failed for issue %s: %s", run_id, issue.id, exc)
            await self._storage.update_run_status(run_id, RunStatus.FAILED, error=str(exc))
            await self._storage.update_issue_state(issue.id, IssueState.FAILED)
        finally:
            await self._lease_manager.release(issue.id)

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

    async def _do_pr_flow(self, issue: Issue, run_id: str, workspace: Any, default_branch: str) -> None:
        """Push branch, create PR, close issue."""
        ws_path = str(workspace.path)
        await self._tracker.push_branch(ws_path, workspace.branch)
        pr = await self._tracker.create_pr(
            title=f"fix: {issue.title} (#{issue.number})",
            head=workspace.branch,
            base=default_branch,
            body=f"Automatically resolved by JHSymphony.\n\nCloses #{issue.number}",
        )
        pr_url = pr.get("html_url", "")
        await self._tracker.post_comment(
            issue.number,
            f"**JHSymphony** completed this issue.\n- PR: {pr_url}\n- Run: `{run_id}`",
        )
        await self._tracker.close_issue(issue.number)
        await self._storage.update_issue_state(issue.id, IssueState.COMPLETED)
        logger.info("Created PR and closed issue #%d", issue.number)

    @staticmethod
    def _is_question_issue(issue: Issue) -> bool:
        """Heuristic: check if issue is a question/analysis request."""
        question_signals = ["문의", "확인", "질문", "question", "분석", "리뷰", "review", "검토", "어떻게", "왜", "?"]
        no_code_signals = ["수정 불필요", "PR 불필요", "no code", "no pr", "코드 수정 없", "변경 불필요"]
        text = f"{issue.title} {issue.body}".lower()
        for signal in no_code_signals:
            if signal in text:
                return True
        score = sum(1 for s in question_signals if s in text)
        return score >= 2

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

    _DECISION_RE = re.compile(r"DECISION-(\d+)\s*:\s*(.+)", re.IGNORECASE)

    @staticmethod
    def _extract_admin_decisions(
        comments: list[dict], bot_login: str, analysis_comment_id: int | None = None
    ) -> tuple[dict[str, str], str]:
        """Extract admin decisions from issue comments after the analysis comment."""
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

    async def cancel_run(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        else:
            await self._storage.update_run_status(run_id, RunStatus.CANCELLED)
