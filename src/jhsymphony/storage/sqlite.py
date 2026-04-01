from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from jhsymphony.models import Issue, IssueState, Run, RunStatus, UsageRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id          TEXT PRIMARY KEY,
    number      INTEGER NOT NULL,
    repo        TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    labels      TEXT NOT NULL DEFAULT '[]',
    state       TEXT NOT NULL DEFAULT 'pending',
    priority    INTEGER NOT NULL DEFAULT 0,
    provider    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_issues_state ON issues (state);

CREATE TABLE IF NOT EXISTS runs (
    id                    TEXT PRIMARY KEY,
    issue_id              TEXT NOT NULL,
    provider              TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'starting',
    attempt               INTEGER NOT NULL DEFAULT 1,
    branch                TEXT,
    pr_number             INTEGER,
    analysis_comment_id   INTEGER,
    started_at            TEXT NOT NULL,
    ended_at              TEXT,
    error                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_issue_id ON runs (issue_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status);

CREATE TABLE IF NOT EXISTS leases (
    issue_id    TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL,
    UNIQUE (run_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_events_run_id ON events (run_id);

CREATE TABLE IF NOT EXISTS usage (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL,
    provider            TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd  REAL NOT NULL DEFAULT 0.0,
    recorded_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_run_id ON usage (run_id);
CREATE INDEX IF NOT EXISTS idx_usage_recorded_at ON usage (recorded_at);

CREATE TABLE IF NOT EXISTS qa_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo            TEXT NOT NULL,
    question_hash   TEXT NOT NULL,
    category_major  TEXT NOT NULL DEFAULT '',
    category_mid    TEXT NOT NULL DEFAULT '',
    category_minor  TEXT NOT NULL DEFAULT '',
    subject         TEXT NOT NULL,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    issue_number    INTEGER,
    created_at      TEXT NOT NULL,
    hit_count       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_qa_repo ON qa_cache (repo);
CREATE INDEX IF NOT EXISTS idx_qa_hash ON qa_cache (question_hash);

CREATE VIRTUAL TABLE IF NOT EXISTS qa_cache_fts USING fts5(
    subject, question, answer,
    content='qa_cache', content_rowid='id'
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _row_to_issue(row: aiosqlite.Row) -> Issue:
    return Issue(
        id=row["id"],
        number=row["number"],
        repo=row["repo"],
        title=row["title"],
        body=row["body"],
        labels=json.loads(row["labels"]),
        state=IssueState(row["state"]),
        priority=row["priority"],
        provider=row["provider"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _row_to_run(row: aiosqlite.Row) -> Run:
    return Run(
        id=row["id"],
        issue_id=row["issue_id"],
        provider=row["provider"],
        status=RunStatus(row["status"]),
        attempt=row["attempt"],
        branch=row["branch"],
        pr_number=row["pr_number"],
        analysis_comment_id=row["analysis_comment_id"],
        started_at=_parse_dt(row["started_at"]),
        ended_at=_parse_dt(row["ended_at"]),
        error=row["error"],
    )


class SQLiteStorage:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------ issues

    async def upsert_issue(self, issue: Issue) -> None:
        await self._db.execute(
            """
            INSERT INTO issues (id, number, repo, title, body, labels, state, priority, provider, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                number     = excluded.number,
                repo       = excluded.repo,
                title      = excluded.title,
                body       = excluded.body,
                labels     = excluded.labels,
                state      = excluded.state,
                priority   = excluded.priority,
                provider   = excluded.provider,
                updated_at = excluded.updated_at
            """,
            (
                issue.id,
                issue.number,
                issue.repo,
                issue.title,
                issue.body,
                json.dumps(issue.labels),
                issue.state.value,
                issue.priority,
                issue.provider,
                issue.created_at.isoformat(),
                issue.updated_at.isoformat(),
            ),
        )
        await self._db.commit()

    async def get_issue(self, issue_id: str) -> Issue | None:
        async with self._db.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_issue(row) if row else None

    async def list_issues(self, state: IssueState | None = None) -> list[Issue]:
        if state is None:
            async with self._db.execute("SELECT * FROM issues ORDER BY priority DESC, created_at ASC") as cur:
                rows = await cur.fetchall()
        else:
            async with self._db.execute(
                "SELECT * FROM issues WHERE state = ? ORDER BY priority DESC, created_at ASC",
                (state.value,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_issue(r) for r in rows]

    async def update_issue_state(self, issue_id: str, state: IssueState) -> None:
        await self._db.execute(
            "UPDATE issues SET state = ?, updated_at = ? WHERE id = ?",
            (state.value, _now_iso(), issue_id),
        )
        await self._db.commit()

    # -------------------------------------------------------------------- runs

    async def insert_run(self, run: Run) -> None:
        await self._db.execute(
            """
            INSERT INTO runs (id, issue_id, provider, status, attempt, branch, pr_number, analysis_comment_id, started_at, ended_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.id,
                run.issue_id,
                run.provider,
                run.status.value,
                run.attempt,
                run.branch,
                run.pr_number,
                run.analysis_comment_id,
                run.started_at.isoformat(),
                run.ended_at.isoformat() if run.ended_at else None,
                run.error,
            ),
        )
        await self._db.commit()

    async def get_run(self, run_id: str) -> Run | None:
        async with self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_run(row) if row else None

    async def update_run_status(self, run_id: str, status: RunStatus, error: str | None = None) -> None:
        ended_at = _now_iso() if status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED) else None
        await self._db.execute(
            "UPDATE runs SET status = ?, error = ?, ended_at = ? WHERE id = ?",
            (status.value, error, ended_at, run_id),
        )
        await self._db.commit()

    async def list_active_runs(self) -> list[Run]:
        async with self._db.execute(
            "SELECT * FROM runs WHERE status IN ('starting', 'running', 'reviewing') ORDER BY started_at ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_run(r) for r in rows]

    async def get_run_count_for_issue(self, issue_id: str) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM runs WHERE issue_id = ?", (issue_id,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def get_analysis_run(self, issue_id: str) -> Run | None:
        """Return the first completed run for an issue (Phase 1 analysis)."""
        async with self._db.execute(
            "SELECT * FROM runs WHERE issue_id = ? AND status = 'completed' ORDER BY started_at ASC LIMIT 1",
            (issue_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_run(row) if row else None

    async def update_run_analysis_comment_id(self, run_id: str, comment_id: int) -> None:
        await self._db.execute(
            "UPDATE runs SET analysis_comment_id = ? WHERE id = ?",
            (comment_id, run_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------------ events

    async def insert_event(self, run_id: str, seq: int, event_type: str, payload: dict) -> None:
        await self._db.execute(
            "INSERT INTO events (run_id, seq, event_type, payload, recorded_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, event_type, json.dumps(payload), _now_iso()),
        )
        await self._db.commit()

    async def list_events(self, run_id: str, since_seq: int = 0) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq ASC",
            (run_id, since_seq),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "run_id": r["run_id"],
                "seq": r["seq"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload"]),
                "recorded_at": r["recorded_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------- usage

    async def record_usage(self, record: UsageRecord) -> None:
        await self._db.execute(
            """
            INSERT INTO usage (run_id, provider, input_tokens, output_tokens, estimated_cost_usd, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.provider,
                record.input_tokens,
                record.output_tokens,
                record.estimated_cost_usd,
                record.recorded_at.isoformat(),
            ),
        )
        await self._db.commit()

    async def sum_daily_cost(self) -> float:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        async with self._db.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0.0) FROM usage WHERE recorded_at >= ?",
            (today_start,),
        ) as cur:
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def sum_run_cost(self, run_id: str) -> float:
        async with self._db.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0.0) FROM usage WHERE run_id = ?",
            (run_id,),
        ) as cur:
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    # ------------------------------------------------------------------ leases

    async def acquire_lease(self, issue_id: str, owner_id: str, ttl_sec: int) -> bool:
        now = _now_iso()
        # Remove any expired leases for this issue first
        await self._db.execute(
            "DELETE FROM leases WHERE issue_id = ? AND expires_at <= ?",
            (issue_id, now),
        )
        expires_at = datetime.now(timezone.utc).astimezone()
        from datetime import timedelta
        expires_dt = datetime.now(timezone.utc) + timedelta(seconds=ttl_sec)
        try:
            await self._db.execute(
                "INSERT INTO leases (issue_id, owner_id, expires_at) VALUES (?, ?, ?)",
                (issue_id, owner_id, expires_dt.isoformat()),
            )
            await self._db.commit()
            return True
        except Exception:
            await self._db.rollback()
            return False

    async def release_lease(self, issue_id: str) -> None:
        await self._db.execute("DELETE FROM leases WHERE issue_id = ?", (issue_id,))
        await self._db.commit()

    async def list_active_leases(self) -> list[dict]:
        now = _now_iso()
        async with self._db.execute(
            "SELECT * FROM leases WHERE expires_at > ? ORDER BY expires_at ASC",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "issue_id": r["issue_id"],
                "owner_id": r["owner_id"],
                "expires_at": r["expires_at"],
            }
            for r in rows
        ]

    # --------------------------------------------------------------- qa_cache

    @staticmethod
    def _qa_row_to_dict(row) -> dict:
        return {
            "id": row["id"],
            "repo": row["repo"],
            "category_major": row["category_major"],
            "category_mid": row["category_mid"],
            "category_minor": row["category_minor"],
            "subject": row["subject"],
            "question": row["question"],
            "answer": row["answer"],
            "issue_number": row["issue_number"],
            "hit_count": row["hit_count"],
            "created_at": row["created_at"],
        }

    async def search_qa_cache(self, repo: str, query: str, limit: int = 3) -> list[dict]:
        """Search Q&A cache using FTS5. Returns top matches."""
        import hashlib
        query_hash = hashlib.sha256(query.strip().lower().encode()).hexdigest()[:16]

        # Exact hash match first
        async with self._db.execute(
            "SELECT * FROM qa_cache WHERE repo = ? AND question_hash = ? LIMIT 1",
            (repo, query_hash),
        ) as cur:
            row = await cur.fetchone()
        if row:
            await self._db.execute("UPDATE qa_cache SET hit_count = hit_count + 1 WHERE id = ?", (row["id"],))
            await self._db.commit()
            return [self._qa_row_to_dict(row)]

        # FTS5 search
        try:
            async with self._db.execute(
                """SELECT qa_cache.*, rank FROM qa_cache_fts
                   JOIN qa_cache ON qa_cache.id = qa_cache_fts.rowid
                   WHERE qa_cache.repo = ? AND qa_cache_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (repo, query, limit),
            ) as cur:
                rows = await cur.fetchall()
            results = [self._qa_row_to_dict(r) for r in rows]
            for r in results:
                await self._db.execute("UPDATE qa_cache SET hit_count = hit_count + 1 WHERE id = ?", (r["id"],))
            if results:
                await self._db.commit()
            return results
        except Exception:
            return []

    async def insert_qa_cache(
        self, repo: str, question: str, answer: str,
        category_major: str = "", category_mid: str = "", category_minor: str = "",
        subject: str = "", issue_number: int | None = None,
    ) -> int:
        """Insert a Q&A entry and update FTS index."""
        import hashlib
        question_hash = hashlib.sha256(question.strip().lower().encode()).hexdigest()[:16]
        if not subject:
            subject = question[:100]

        async with self._db.execute(
            """INSERT INTO qa_cache
               (repo, question_hash, category_major, category_mid, category_minor, subject, question, answer, issue_number, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (repo, question_hash, category_major, category_mid, category_minor,
             subject, question, answer, issue_number, _now_iso()),
        ) as cur:
            row_id = cur.lastrowid

        # Update FTS index
        await self._db.execute(
            "INSERT INTO qa_cache_fts(rowid, subject, question, answer) VALUES (?, ?, ?, ?)",
            (row_id, subject, question, answer),
        )
        await self._db.commit()
        return row_id
