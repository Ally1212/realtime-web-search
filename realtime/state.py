from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  query TEXT NOT NULL,
  status TEXT NOT NULL,
  pages INTEGER NOT NULL,
  workers INTEGER NOT NULL,
  discovered INTEGER NOT NULL DEFAULT 0,
  fetched INTEGER NOT NULL DEFAULT 0,
  indexed INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  blocked INTEGER NOT NULL DEFAULT 0,
  engine_errors TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fetch_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT,
  status TEXT NOT NULL,
  http_status INTEGER,
  error TEXT,
  fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fetch_events_job_id ON fetch_events(job_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    FIELDS = {"status", "discovered", "fetched", "indexed", "failed", "blocked", "engine_errors", "error"}

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "UPDATE jobs SET status='interrupted', updated_at=? WHERE status IN ('queued','running')",
                (_now(),),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def create_job(self, query: str, pages: int, workers: int) -> str:
        job_id = uuid4().hex
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs(id,query,status,pages,workers,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (job_id, query, "queued", pages, workers, now, now),
            )
        return job_id

    def update_job(self, job_id: str, **values: Any) -> None:
        unknown = set(values) - self.FIELDS
        if unknown:
            raise ValueError(f"unknown job fields: {sorted(unknown)}")
        values["updated_at"] = _now()
        assignments = ",".join(f"{field}=?" for field in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?",  # fields are allow-listed
                (*values.values(), job_id),
            )

    def add_event(
        self, job_id: str, url: str, title: str, status: str,
        http_status: int | None = None, error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO fetch_events(job_id,url,title,status,http_status,error,fetched_at) VALUES(?,?,?,?,?,?,?)",
                (job_id, url, title[:300], status, http_status, error, _now()),
            )

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            totals = connection.execute(
                "SELECT COUNT(*) jobs, COALESCE(SUM(discovered),0) discovered, "
                "COALESCE(SUM(fetched),0) fetched, COALESCE(SUM(indexed),0) AS indexed_total, "
                "COALESCE(SUM(failed),0) failed, COALESCE(SUM(blocked),0) blocked FROM jobs"
            ).fetchone()
            jobs = connection.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 10").fetchall()
            events = connection.execute(
                "SELECT url,title,status,http_status,error,fetched_at FROM fetch_events ORDER BY id DESC LIMIT 12"
            ).fetchall()
        return {"totals": dict(totals), "jobs": [dict(row) for row in jobs], "events": [dict(row) for row in events]}
