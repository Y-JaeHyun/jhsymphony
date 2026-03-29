from __future__ import annotations
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/issues", tags=["issues"])

@router.get("")
async def list_issues(request: Request):
    storage = request.app.state.storage
    issues = await storage.list_issues()
    return [i.model_dump(mode="json") for i in issues]

@router.get("/{issue_id}")
async def get_issue(issue_id: str, request: Request):
    storage = request.app.state.storage
    issue = await storage.get_issue(issue_id)
    if issue is None:
        return {"error": "not found"}, 404
    return issue.model_dump(mode="json")
