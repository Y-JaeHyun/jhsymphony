from __future__ import annotations
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/stats", tags=["stats"])

@router.get("")
async def get_stats(request: Request):
    storage = request.app.state.storage
    active_runs = await storage.list_active_runs()
    daily_cost = await storage.sum_daily_cost()
    return {"active_runs": len(active_runs), "daily_cost": daily_cost}
