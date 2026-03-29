from pathlib import Path

import pytest

from jhsymphony.orchestrator.lease import LeaseManager
from jhsymphony.storage.sqlite import SQLiteStorage


@pytest.fixture
async def storage(tmp_dir: Path):
    store = SQLiteStorage(str(tmp_dir / "test.sqlite"))
    await store.initialize()
    yield store
    await store.close()


@pytest.fixture
def lease_mgr(storage):
    return LeaseManager(storage=storage, owner_id="worker-1", ttl_sec=60)


async def test_try_acquire(lease_mgr):
    assert await lease_mgr.try_acquire("issue-1") is True


async def test_try_acquire_already_leased(lease_mgr):
    await lease_mgr.try_acquire("issue-1")
    assert await lease_mgr.try_acquire("issue-1") is False


async def test_release(lease_mgr):
    await lease_mgr.try_acquire("issue-1")
    await lease_mgr.release("issue-1")
    assert await lease_mgr.try_acquire("issue-1") is True


async def test_is_held(lease_mgr):
    assert await lease_mgr.is_held("issue-1") is False
    await lease_mgr.try_acquire("issue-1")
    assert await lease_mgr.is_held("issue-1") is True
