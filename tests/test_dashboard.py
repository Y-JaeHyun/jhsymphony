from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from jhsymphony.dashboard.app import create_app
from jhsymphony.models import Issue, IssueState, Run, RunStatus, UsageRecord
from jhsymphony.storage.sqlite import SQLiteStorage

@pytest.fixture
async def storage(tmp_dir: Path):
    store = SQLiteStorage(str(tmp_dir / "test.sqlite"))
    await store.initialize()
    yield store
    await store.close()

@pytest.fixture
def client(storage):
    app = create_app(storage)
    return TestClient(app)

def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

async def test_list_issues(client, storage):
    await storage.upsert_issue(Issue(id="gh-1", number=1, repo="o/r", title="Bug"))
    resp = client.get("/api/issues")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1

async def test_list_runs(client, storage):
    await storage.insert_run(Run(id="r1", issue_id="gh-1", provider="claude", status=RunStatus.RUNNING))
    resp = client.get("/api/runs")
    assert resp.status_code == 200

async def test_get_stats(client, storage):
    await storage.record_usage(UsageRecord(
        run_id="r1", provider="claude", input_tokens=1000, output_tokens=500, estimated_cost_usd=0.05
    ))
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert "daily_cost" in resp.json()

async def test_get_run_events(client, storage):
    await storage.insert_event("r1", 1, "message.delta", {"text": "hi"})
    resp = client.get("/api/runs/r1/events")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
