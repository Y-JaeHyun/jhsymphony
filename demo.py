"""JHSymphony Dashboard Demo - seeds sample data and starts the server."""
import asyncio
import sys
sys.path.insert(0, "src")

from pathlib import Path
from jhsymphony.models import Issue, IssueState, Run, RunStatus, UsageRecord
from jhsymphony.storage.sqlite import SQLiteStorage
from jhsymphony.dashboard.app import create_app

DB_PATH = "/tmp/jhsymphony-demo.sqlite"


async def seed_data():
    storage = SQLiteStorage(DB_PATH)
    await storage.initialize()

    # Sample issues
    issues = [
        Issue(id="gh-101", number=101, repo="myteam/backend", title="Fix login timeout bug", labels=["jhsymphony", "bug"], state=IssueState.COMPLETED, priority=2, provider="claude"),
        Issue(id="gh-102", number=102, repo="myteam/backend", title="Add rate limiting to API", labels=["jhsymphony", "use-codex"], state=IssueState.RUNNING, priority=1, provider="codex"),
        Issue(id="gh-103", number=103, repo="myteam/backend", title="Refactor auth middleware", labels=["jhsymphony", "use-claude"], state=IssueState.REVIEWING, priority=0, provider="claude"),
        Issue(id="gh-104", number=104, repo="myteam/backend", title="Update dependencies", labels=["jhsymphony"], state=IssueState.PENDING, priority=0),
        Issue(id="gh-105", number=105, repo="myteam/backend", title="Fix CORS headers", labels=["jhsymphony", "bug"], state=IssueState.RUNNING, priority=3, provider="gemini"),
    ]
    for issue in issues:
        await storage.upsert_issue(issue)

    # Sample runs
    runs = [
        Run(id="run-a1b2c3d4e5f6", issue_id="gh-101", provider="claude", status=RunStatus.COMPLETED, attempt=1, branch="jhsymphony/issue-101", pr_number=42),
        Run(id="run-b2c3d4e5f6a7", issue_id="gh-102", provider="codex", status=RunStatus.RUNNING, attempt=1, branch="jhsymphony/issue-102"),
        Run(id="run-c3d4e5f6a7b8", issue_id="gh-103", provider="claude", status=RunStatus.REVIEWING, attempt=1, branch="jhsymphony/issue-103", pr_number=43),
        Run(id="run-d4e5f6a7b8c9", issue_id="gh-105", provider="gemini", status=RunStatus.RUNNING, attempt=2, branch="jhsymphony/issue-105"),
    ]
    for run in runs:
        await storage.insert_run(run)

    # Sample events for running issue #102
    events_102 = [
        (1, "session.started", {"pid": 12345}),
        (2, "message.delta", {"text": "Analyzing the API endpoints that need rate limiting..."}),
        (3, "tool.call", {"name": "read_file", "input": "src/api/routes.py"}),
        (4, "tool.result", {"output": "Found 12 endpoints without rate limiting"}),
        (5, "message.delta", {"text": "I'll add a rate limiter middleware using a token bucket algorithm."}),
        (6, "tool.call", {"name": "write_file", "input": "src/middleware/rate_limit.py"}),
        (7, "tool.result", {"output": "File created successfully"}),
        (8, "message.delta", {"text": "Now writing tests for the rate limiter..."}),
        (9, "tool.call", {"name": "write_file", "input": "tests/test_rate_limit.py"}),
        (10, "tool.result", {"output": "Test file created"}),
        (11, "tool.call", {"name": "run_command", "input": "pytest tests/test_rate_limit.py"}),
        (12, "tool.result", {"output": "5 passed in 0.3s"}),
        (13, "usage", {"input_tokens": 15000, "output_tokens": 8000}),
        (14, "message.delta", {"text": "Rate limiting middleware implemented and all tests passing."}),
    ]
    for seq, etype, payload in events_102:
        await storage.insert_event("run-b2c3d4e5f6a7", seq, etype, payload)

    # Sample events for running issue #105
    events_105 = [
        (1, "session.started", {"pid": 12346}),
        (2, "message.delta", {"text": "Investigating CORS header configuration..."}),
        (3, "tool.call", {"name": "read_file", "input": "src/server.py"}),
        (4, "tool.result", {"output": "Found misconfigured Access-Control-Allow-Origin"}),
        (5, "message.delta", {"text": "The issue is in the CORS middleware setup. Fixing now..."}),
        (6, "usage", {"input_tokens": 5000, "output_tokens": 2000}),
    ]
    for seq, etype, payload in events_105:
        await storage.insert_event("run-d4e5f6a7b8c9", seq, etype, payload)

    # Sample events for completed #101
    events_101 = [
        (1, "session.started", {"pid": 12340}),
        (2, "message.delta", {"text": "Found the timeout issue in session management."}),
        (3, "tool.call", {"name": "edit_file", "input": "src/auth/session.py"}),
        (4, "tool.result", {"output": "Fixed timeout from 30s to 300s"}),
        (5, "usage", {"input_tokens": 8000, "output_tokens": 3000}),
        (6, "completed", {"reason": "done"}),
    ]
    for seq, etype, payload in events_101:
        await storage.insert_event("run-a1b2c3d4e5f6", seq, etype, payload)

    # Sample usage records
    usage_records = [
        UsageRecord(run_id="run-a1b2c3d4e5f6", provider="claude", input_tokens=8000, output_tokens=3000, estimated_cost_usd=0.33),
        UsageRecord(run_id="run-b2c3d4e5f6a7", provider="codex", input_tokens=15000, output_tokens=8000, estimated_cost_usd=0.46),
        UsageRecord(run_id="run-c3d4e5f6a7b8", provider="claude", input_tokens=12000, output_tokens=5000, estimated_cost_usd=0.51),
        UsageRecord(run_id="run-d4e5f6a7b8c9", provider="gemini", input_tokens=5000, output_tokens=2000, estimated_cost_usd=0.07),
    ]
    for record in usage_records:
        await storage.record_usage(record)

    print(f"Seeded: {len(issues)} issues, {len(runs)} runs, {sum(len(e) for e in [events_101, events_102, events_105])} events, {len(usage_records)} usage records")
    return storage


async def main():
    storage = await seed_data()

    # Serve static files from the built frontend
    from fastapi.staticfiles import StaticFiles
    app = create_app(storage)

    static_dir = Path(__file__).parent / "src" / "jhsymphony" / "dashboard" / "static"
    if static_dir.exists():
        from fastapi.responses import FileResponse

        @app.get("/")
        async def serve_index():
            return FileResponse(static_dir / "index.html")

        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="static")

    import uvicorn
    config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="info")
    server = uvicorn.Server(config)

    print("\n" + "=" * 50)
    print("  JHSymphony Dashboard Demo")
    print("=" * 50)
    print(f"  URL: http://localhost:8080")
    print(f"  API: http://localhost:8080/api/health")
    print(f"  Issues: http://localhost:8080/api/issues")
    print(f"  Runs: http://localhost:8080/api/runs")
    print(f"  Stats: http://localhost:8080/api/stats")
    print("=" * 50 + "\n")

    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
