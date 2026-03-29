from __future__ import annotations
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/runs", tags=["runs"])

@router.get("")
async def list_runs(request: Request, active_only: bool = False):
    storage = request.app.state.storage
    runs = await storage.list_active_runs()
    return [r.model_dump(mode="json") for r in runs]

@router.get("/{run_id}")
async def get_run(run_id: str, request: Request):
    storage = request.app.state.storage
    run = await storage.get_run(run_id)
    if run is None:
        return {"error": "not found"}, 404
    return run.model_dump(mode="json")

@router.get("/{run_id}/events")
async def get_run_events(run_id: str, request: Request, since_seq: int = 0):
    storage = request.app.state.storage
    events = await storage.list_events(run_id, since_seq=since_seq)
    return events
