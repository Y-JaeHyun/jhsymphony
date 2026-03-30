from __future__ import annotations

import logging
from typing import Any

from jhsymphony.models import Issue, IssueState, RunStatus
from jhsymphony.storage.base import Storage

logger = logging.getLogger(__name__)


class Reconciler:
    def __init__(self, storage: Storage, tracker: Any, dispatcher: Any) -> None:
        self._storage = storage
        self._tracker = tracker
        self._dispatcher = dispatcher

    async def reconcile(self, current_candidates: list[Issue], active_issue_ids: set[str]) -> None:
        candidate_ids = {c.id for c in current_candidates}
        # Only reconcile issues that belong to the same repo as our candidates
        # This prevents cross-repo cancellation in multi-repo setups
        if current_candidates:
            repo = current_candidates[0].repo
        else:
            repo = None
        for issue_id in active_issue_ids:
            if issue_id not in candidate_ids:
                # Only cancel if the issue belongs to the same repo we're tracking
                issue = await self._storage.get_issue(issue_id)
                if issue and repo and issue.repo != repo:
                    continue  # Skip issues from other repos
                logger.info("Issue %s no longer a candidate, cancelling", issue_id)
                await self._cancel_issue(issue_id)

    async def _cancel_issue(self, issue_id: str) -> None:
        active_runs = await self._storage.list_active_runs()
        for run in active_runs:
            if run.issue_id == issue_id:
                await self._dispatcher.cancel_run(run.id)
                await self._storage.update_run_status(run.id, RunStatus.CANCELLED)
        await self._storage.update_issue_state(issue_id, IssueState.CANCELLED)
