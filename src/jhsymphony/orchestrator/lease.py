from __future__ import annotations

from jhsymphony.storage.base import Storage


class LeaseManager:
    def __init__(self, storage: Storage, owner_id: str, ttl_sec: int = 3600) -> None:
        self._storage = storage
        self._owner_id = owner_id
        self._ttl_sec = ttl_sec

    async def try_acquire(self, issue_id: str) -> bool:
        return await self._storage.acquire_lease(issue_id, self._owner_id, self._ttl_sec)

    async def release(self, issue_id: str) -> None:
        await self._storage.release_lease(issue_id)

    async def is_held(self, issue_id: str) -> bool:
        leases = await self._storage.list_active_leases()
        return any(l["issue_id"] == issue_id for l in leases)
