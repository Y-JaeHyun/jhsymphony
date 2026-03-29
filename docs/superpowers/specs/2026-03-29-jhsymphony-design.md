# JHSymphony Design Spec

**Date**: 2026-03-29
**Status**: Approved
**Approach**: Hybrid (Monolith MVP → Progressive Decomposition)

## Overview

JHSymphony is a Python-based autonomous agent orchestration system for internal development automation. It monitors GitHub Issues, dispatches multi-model coding agents (Claude, Codex, Gemini), and provides a full-featured real-time dashboard.

Inspired by OpenAI's Symphony (Elixir/BEAM), JHSymphony improves upon the original with:
- Multi-model support instead of Codex-only
- GitHub Issues instead of Linear-only
- Durable DB state instead of in-memory only
- Built-in cost controls and real-time dashboard

## Requirements

| Item | Decision |
|------|----------|
| Name | JHSymphony |
| Purpose | Personal/team internal development automation |
| Issue Tracker | GitHub Issues |
| Scope | Coding + Auto review/test verification |
| Agent Runtime | Multi-model (Claude, Codex, Gemini) |
| Concurrency | Flexible scale up/down |
| UI | Full dashboard (real-time monitoring, thought visualization, cost analysis) |
| Language | Python 3.12+ |
| Phase | Phase 1 MVP (Monolith + SQLite) |

## Architecture

```
+-----------------------------------------------------------+
|                    JHSymphony (Phase 1)                    |
+----------+----------+----------+----------+---------------+
| CLI      | Dashboard| Orchestr.| Providers| Tracker       |
| (typer)  | (FastAPI | (asyncio | (Claude, | (GitHub       |
|          |  + WS +  |  poller  |  Codex,  |  Issues       |
|          |  React)  |  + lease)|  Gemini) |  + Webhook)   |
+----------+----------+----------+----------+---------------+
| Storage (SQLite + aiosqlite)                               |
+------------------------------------------------------------+
| Workspace (git worktree + subprocess)                      |
+------------------------------------------------------------+
```

### Core Flow

1. GitHub Issues with configured label (e.g., `jhsymphony`) detected via polling/webhook
2. Orchestrator evaluates priority/concurrency, dispatches agent
3. Agent works in isolated git worktree
4. On completion, PR is created automatically
5. Review agent validates the PR (code review + test verification)
6. Dashboard streams the entire process in real-time

## Components

### Orchestrator

```
Orchestrator
+-- Scheduler      -- 30s polling + GitHub Webhook receiver
+-- Dispatcher     -- Assigns agents within concurrency budget
+-- Reconciler     -- Detects mid-run issue state changes (closed/cancelled)
+-- LeaseManager   -- DB-based lease to prevent duplicate runs
```

**State Machine:**

```
pending -> leased -> preparing -> running -> reviewing -> completed
                                    |           |
                                retry_wait    failed
                                    |
                                cancelled
```

- `pending`: Issue detected, waiting
- `leased`: Dispatch decided, lease acquired
- `preparing`: Worktree creation + branch checkout
- `running`: Coding agent executing
- `reviewing`: PR created, review agent running
- `completed/failed/cancelled`: Terminal states

### Agent Providers

```python
class AgentProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    async def start_session(self, ctx: RunContext) -> Session: ...
    async def run_turn(self, session: Session, prompt: str) -> AsyncIterator[AgentEvent]: ...
    async def cancel(self, session: Session) -> None: ...
```

**AgentEvent types:**
- `session.started` — Session initialized
- `message.delta` — Streaming text output
- `tool.call` — Tool invocation
- `tool.result` — Tool result
- `usage` — Token count update
- `completed` — Run finished (done/blocked/cancelled)
- `error` — Error occurred

**Provider defaults (configurable):**

| Provider | Default Role | Execution |
|----------|-------------|-----------|
| Claude Code | Coding, complex refactoring | subprocess (CLI) |
| Codex | Coding, fast tasks | API (app-server) |
| Gemini | Code review, analysis | API |

**Routing:** Issue labels (`use-claude`, `use-codex`) select provider; fallback to config default.

### GitHub Issues Tracker

```python
class GitHubTracker:
    async def fetch_candidates(self) -> list[Issue]    # Label filtering
    async def update_status(self, issue, status)       # Comment + label change
    async def create_pr(self, issue, branch) -> PR     # PR creation
    async def add_review_comment(self, pr, body)       # Post review results
```

- **Trigger**: Configured label (e.g., `jhsymphony`, `auto-fix`)
- **Status reporting**: Label changes + issue comments for progress updates

### Workspace Manager

```
issue-123/
+-- worktree/          <- git worktree (isolated branch)
+-- logs/              <- agent execution logs
+-- metadata.json      <- run state, token usage
```

- Git worktree-based branch isolation
- Per-agent subprocess execution
- Cleanup on success, preserve on failure

### Dashboard

- **Backend**: FastAPI + WebSocket real-time streaming
- **Frontend**: React (Vite)
- **Screens**:
  - Running agents list + real-time logs
  - Agent thought process (token stream) visualization
  - Token cost tracking (per provider, per issue)
  - Run history + success/failure statistics

### CLI

- Built with Typer
- Commands: `start`, `stop`, `status`, `run <issue>`, `config`, `dashboard`

## Data Model

### SQLite Schema

```sql
-- Issue tracking
CREATE TABLE issues (
    id TEXT PRIMARY KEY,
    number INTEGER NOT NULL,
    repo TEXT NOT NULL,
    title TEXT,
    labels JSON,
    state TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER DEFAULT 0,
    provider TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Run history
CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(id),
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'starting',
    attempt INTEGER DEFAULT 1,
    branch TEXT,
    pr_number INTEGER,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    error TEXT
);

-- Lease (duplicate execution prevention)
CREATE TABLE leases (
    issue_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL
);

-- Agent event stream (dashboard + debugging)
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Cost tracking
CREATE TABLE usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    provider TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    estimated_cost_usd REAL DEFAULT 0.0,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Configuration

```yaml
project:
  name: "my-project"
  repo: "owner/repo"

tracker:
  kind: github
  label: "jhsymphony"
  poll_interval_sec: 30
  webhook_secret: $WEBHOOK_SECRET

orchestrator:
  max_concurrent_agents: 5
  max_retries: 3
  retry_backoff_sec: 60
  lease_ttl_sec: 3600

providers:
  default: claude
  claude:
    command: "claude"
    model: "opus"
    max_turns: 30
  codex:
    command: "codex"
    model: "gpt-5.4"
    sandbox: "read-only"
  gemini:
    command: "gemini"
    model: "gemini-2.5-pro"

routing:
  - label: "use-claude"
    provider: claude
  - label: "use-codex"
    provider: codex
  - label: "review-only"
    provider: gemini
    role: reviewer

workspace:
  root: "~/.jhsymphony/workspaces"
  cleanup_on_success: true
  keep_on_failure: true

review:
  enabled: true
  provider: gemini
  auto_approve: false

budget:
  daily_limit_usd: 50.0
  per_run_limit_usd: 10.0
  alert_threshold_pct: 80

dashboard:
  port: 8080
  host: "0.0.0.0"

storage:
  backend: sqlite
  path: "~/.jhsymphony/db.sqlite"
```

## Error Handling & Safety

### Retry Strategy

- Failure -> attempt < max_retries? -> Wait (retry_backoff_sec * 2^(attempt-1)) -> Retry
- Max retries exceeded -> Mark failed + post failure comment on issue
- Workspace preserved across retries for context continuity
- Issue closed mid-run -> Reconciler detects -> Immediate cancellation

### Safety Guards

| Scenario | Response |
|----------|----------|
| Agent infinite loop | max_turns per run + global timeout (default 30min) |
| Concurrency explosion | max_concurrent_agents hard cap |
| Duplicate execution | Lease-based locking (auto-release on TTL expiry) |
| API cost explosion | Per-provider daily token limit |
| Abnormal process exit | On restart, expired leases in running state -> auto-retry |

### Logging

- Structured JSON logs -> file + dashboard WebSocket
- Event types: `run.started`, `run.turn`, `run.tool_call`, `run.completed`, `run.failed`
- All events tagged with `run_id`, `issue_id`, `provider`

## Directory Structure

```
jhsymphony/
+-- pyproject.toml
+-- jhsymphony.yaml.example
+-- src/
|   +-- jhsymphony/
|       +-- __init__.py
|       +-- main.py                 # Entrypoint
|       +-- config.py               # YAML config loader
|       +-- models.py               # Pydantic models (Issue, Run, Event, etc.)
|       +-- orchestrator/
|       |   +-- scheduler.py        # Polling loop + webhook receiver
|       |   +-- dispatcher.py       # Concurrency + agent assignment
|       |   +-- reconciler.py       # Mid-run state change detection
|       |   +-- lease.py            # DB-based lease management
|       +-- providers/
|       |   +-- base.py             # AgentProvider Protocol + AgentEvent
|       |   +-- claude.py           # Claude Code CLI adapter
|       |   +-- codex.py            # Codex API/CLI adapter
|       |   +-- gemini.py           # Gemini API adapter
|       |   +-- router.py           # Label-based provider routing
|       +-- tracker/
|       |   +-- base.py             # Tracker Protocol
|       |   +-- github.py           # GitHub Issues + PR client
|       +-- workspace/
|       |   +-- manager.py          # Worktree creation/cleanup
|       |   +-- isolation.py        # Subprocess execution environment
|       +-- storage/
|       |   +-- base.py             # Storage Protocol
|       |   +-- sqlite.py           # aiosqlite implementation
|       |   +-- migrations/         # Schema migrations
|       +-- review/
|       |   +-- reviewer.py         # PR review agent execution
|       +-- dashboard/
|       |   +-- app.py              # FastAPI app
|       |   +-- ws.py               # WebSocket event streaming
|       |   +-- routes/             # REST API endpoints
|       |   +-- frontend/           # React (Vite) build
|       +-- cli/
|           +-- app.py              # Typer CLI
+-- tests/
    +-- test_orchestrator.py
    +-- test_providers.py
    +-- test_tracker.py
    +-- test_workspace.py
```

## Evolution Path

| Phase | Scope | Trigger |
|-------|-------|---------|
| Phase 1 (MVP) | Monolith + SQLite + embedded dashboard | Now |
| Phase 2 | PostgreSQL + worker process separation | 10+ concurrent agents needed |
| Phase 3 | Dashboard -> separate Next.js app | Team collaboration features needed |

## Consensus Record

3-way consensus (Claude + Gemini + Codex) conducted on 2026-03-29:

| Topic | Agreement | Notes |
|-------|-----------|-------|
| Language | 3:0 Python | All three recommended Python for LLM ecosystem |
| Architecture | 3:0 Polling + DB lease | Gemini suggested Temporal but agreed MVP should be simpler |
| Issue Tracker | 3:0 Adapter pattern | Multi-tracker support via protocol abstraction |
| Agent Runtime | 3:0 Multi-model | Provider protocol with capability model |
| State Management | 3:0 SQLite MVP | Upgrade path to PostgreSQL |
| Process Isolation | 2:1 worktree + subprocess | Gemini focused on queue-based, Claude+Codex on worktree |
