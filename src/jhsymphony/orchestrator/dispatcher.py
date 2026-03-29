from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from jhsymphony.models import Issue, IssueState, Run, RunStatus, UsageRecord
from jhsymphony.orchestrator.lease import LeaseManager
from jhsymphony.providers.base import EventType, RunContext
from jhsymphony.storage.base import Storage

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
    ) -> None:
        self._storage = storage
        self._lease_manager = lease_manager
        self._workspace_manager = workspace_manager
        self._router = provider_router
        self._tracker = tracker
        self._max_concurrent = max_concurrent
        self._budget_daily_limit = budget_daily_limit
        self._budget_per_run_limit = budget_per_run_limit
        self._tasks: dict[str, asyncio.Task] = {}

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    async def can_dispatch(self, issue: Issue) -> bool:
        active_runs = await self._storage.list_active_runs()
        if len(active_runs) >= self._max_concurrent:
            logger.debug(
                "Cannot dispatch issue %s: concurrency limit %d reached (%d active)",
                issue.id,
                self._max_concurrent,
                len(active_runs),
            )
            return False

        if await self._lease_manager.is_held(issue.id):
            logger.debug("Cannot dispatch issue %s: lease already held", issue.id)
            return False

        daily_cost = await self._storage.sum_daily_cost()
        if daily_cost >= self._budget_daily_limit:
            logger.warning(
                "Cannot dispatch issue %s: daily budget limit %.2f reached (spent %.2f)",
                issue.id,
                self._budget_daily_limit,
                daily_cost,
            )
            return False

        return True

    async def dispatch(self, issue: Issue) -> str | None:
        if not await self.can_dispatch(issue):
            return None

        acquired = await self._lease_manager.try_acquire(issue.id)
        if not acquired:
            logger.debug("Failed to acquire lease for issue %s", issue.id)
            return None

        run_id = str(uuid.uuid4())
        provider = self._router.select(issue.labels)
        _name_attr = getattr(provider, "name", None)
        provider_name = _name_attr if isinstance(_name_attr, str) else type(provider).__name__

        run = Run(
            id=run_id,
            issue_id=issue.id,
            provider=provider_name,
            status=RunStatus.STARTING,
        )
        await self._storage.insert_run(run)
        await self._storage.update_issue_state(issue.id, IssueState.LEASED)

        task = asyncio.create_task(self._execute_run(run_id, issue, provider))
        self._tasks[run_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(run_id, None))

        logger.info("Dispatched run %s for issue %s", run_id, issue.id)
        return run_id

    async def _execute_run(self, run_id: str, issue: Issue, provider: Any) -> None:
        seq = 0
        try:
            await self._storage.update_issue_state(issue.id, IssueState.PREPARING)
            await self._storage.update_run_status(run_id, RunStatus.RUNNING)

            # Create workspace
            workspace = await self._workspace_manager.create(issue.id)

            await self._storage.update_issue_state(issue.id, IssueState.RUNNING)

            # Post GitHub comment on start
            try:
                await self._tracker.post_comment(
                    issue.number,
                    f"JHSymphony is working on this issue (run `{run_id}`).",
                )
            except Exception:
                logger.debug("Failed to post start comment for issue %s", issue.id, exc_info=True)

            # Build run context
            ctx = RunContext(
                workspace_path=str(workspace.path),
                branch=workspace.branch,
                issue_title=issue.title,
                issue_body=issue.body,
            )

            # Start session and run agent
            session = await provider.start_session(ctx)
            prompt = (
                f"You are working on GitHub issue #{issue.number}: {issue.title}\n\n"
                f"{issue.body}\n\n"
                f"Instructions:\n"
                f"1. Read the existing code to understand the codebase\n"
                f"2. Implement the requested changes\n"
                f"3. Write or update tests\n"
                f"4. Run the tests to make sure they pass\n"
                f"5. Commit your changes with a descriptive message\n\n"
                f"Work in the current directory. Do not ask questions — just implement."
            )

            async for event in provider.run_turn(session, prompt):
                await self._storage.insert_event(
                    run_id=run_id,
                    seq=seq,
                    event_type=event.type,
                    payload=event.data,
                )
                seq += 1

                if event.type == EventType.USAGE:
                    usage = UsageRecord(
                        run_id=run_id,
                        provider=getattr(provider, "name", type(provider).__name__),
                        input_tokens=event.data.get("input_tokens", 0),
                        output_tokens=event.data.get("output_tokens", 0),
                        estimated_cost_usd=event.data.get("cost_usd", 0.0),
                    )
                    await self._storage.record_usage(usage)

                    run_cost = await self._storage.sum_run_cost(run_id)
                    if run_cost >= self._budget_per_run_limit:
                        logger.warning(
                            "Run %s exceeded per-run budget limit %.2f (spent %.2f), stopping",
                            run_id,
                            self._budget_per_run_limit,
                            run_cost,
                        )
                        break

            await self._storage.update_run_status(run_id, RunStatus.COMPLETED)
            await self._storage.update_issue_state(issue.id, IssueState.COMPLETED)
            logger.info("Run %s completed for issue %s", run_id, issue.id)

            # Post-completion: commit uncommitted changes, push, create PR, close issue
            try:
                # Auto-commit any uncommitted changes (some providers can't git commit in sandbox)
                from jhsymphony.workspace.isolation import run_subprocess
                ws_path = str(workspace.path)
                await run_subprocess(["git", "add", "-A"], cwd=ws_path, env=None, timeout_sec=10)
                diff_result = await run_subprocess(["git", "diff", "--cached", "--quiet"], cwd=ws_path, env=None, timeout_sec=10)
                if diff_result.returncode != 0:  # there are staged changes
                    await run_subprocess(
                        ["git", "commit", "-m", f"feat: {issue.title} (#{issue.number})\n\nAutomatically implemented by JHSymphony"],
                        cwd=ws_path, env=None, timeout_sec=10,
                    )
                await self._tracker.push_branch(ws_path, workspace.branch)
                pr = await self._tracker.create_pr(
                    title=f"fix: {issue.title} (#{issue.number})",
                    head=workspace.branch,
                    base="main",
                    body=f"Automatically resolved by JHSymphony.\n\nCloses #{issue.number}",
                )
                pr_url = pr.get("html_url", "")
                await self._tracker.post_comment(
                    issue.number,
                    f"**JHSymphony** completed this issue.\n- PR: {pr_url}\n- Run: `{run_id}`",
                )
                await self._tracker.close_issue(issue.number)
                logger.info("Created PR and closed issue #%d", issue.number)
            except Exception:
                logger.warning("Post-completion actions failed for issue #%d", issue.number, exc_info=True)

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

    async def cancel_run(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        else:
            # Run may not have a live task (e.g. already finished or from a previous process)
            await self._storage.update_run_status(run_id, RunStatus.CANCELLED)
