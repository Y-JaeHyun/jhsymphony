from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any

from jhsymphony.models import Issue, IssueState
from jhsymphony.storage.base import Storage

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        storage: Storage,
        tracker: Any,
        dispatcher: Any,
        reconciler: Any,
        poll_interval_sec: int = 30,
    ) -> None:
        self._storage = storage
        self._tracker = tracker
        self._dispatcher = dispatcher
        self._reconciler = reconciler
        self._poll_interval = poll_interval_sec
        self._running = False

    async def tick(self) -> None:
        try:
            candidates = await self._tracker.fetch_candidates()
            active_issues = await self._storage.list_issues()
            active_ids = {i.id for i in active_issues if i.state.is_active()}
            await self._reconciler.reconcile(current_candidates=candidates, active_issue_ids=active_ids)
            for candidate in candidates:
                if candidate.id in active_ids:
                    continue
                existing = await self._storage.get_issue(candidate.id)
                if existing:
                    # Skip if already active OR already completed/failed
                    if existing.state.is_active() or existing.state in (
                        IssueState.COMPLETED, IssueState.FAILED, IssueState.CANCELLED,
                    ):
                        continue
                await self._storage.upsert_issue(candidate)
                await self._dispatcher.dispatch(candidate)
        except Exception:
            logger.exception("Error in scheduler tick")

    async def run(self) -> None:
        self._running = True
        next_tick = monotonic()
        while self._running:
            now = monotonic()
            if now < next_tick:
                await asyncio.sleep(next_tick - now)
            next_tick = monotonic() + self._poll_interval
            await self.tick()

    async def stop(self) -> None:
        self._running = False
