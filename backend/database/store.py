"""Small, transactional SQLite repository. Connections never cross threads."""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone

from typing import Iterator

from backend.core.config import DATABASE_PATH, init_directories
from backend.models.schemas import Settings


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


@contextlib.contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(str(DATABASE_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize() -> None:
    init_directories()
    with connection() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS highlights (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS highlights_project ON highlights(project_id);
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, data TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,created_at);
            CREATE INDEX IF NOT EXISTS jobs_project ON jobs(project_id);
            CREATE TABLE IF NOT EXISTS exports (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS benchmarks (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
        """)
        db.execute("INSERT OR IGNORE INTO settings VALUES(1,?)", (encode(Settings().model_dump()),))


_TABLES = {"projects", "highlights", "jobs", "exports", "benchmarks"}


def get(table: str, identifier: str) -> dict | None:
    assert table in _TABLES
    with connection() as db:
        row = db.execute(f"SELECT data FROM {table} WHERE id=?", (identifier,)).fetchone()
    return json.loads(row["data"]) if row else None


def list_items(table: str, project_id: str | None = None, limit: int = 5000) -> list[dict]:
    assert table in _TABLES
    sql, args = f"SELECT data FROM {table}", []
    if project_id is not None:
        assert table in {"jobs", "highlights", "exports"}
        sql += " WHERE project_id=?"
        args.append(project_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with connection() as db:
        rows = db.execute(sql, args).fetchall()
    return [json.loads(row["data"]) for row in rows]


def insert(table: str, data: dict, payload: dict | None = None, db=None) -> None:
    assert table in _TABLES
    columns = {"id": data["id"], "created_at": data.get("created_at", now()), "data": encode(data)}
    if table in {"jobs", "highlights", "exports"}:
        columns["project_id"] = data["project_id"]
    if table == "jobs":
        columns.update(kind=data["kind"], status=data["status"], updated_at=data["updated_at"], payload=encode(payload or {}))
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
    if db is not None:
        db.execute(sql, list(columns.values()))
    else:
        with connection() as conn:
            conn.execute(sql, list(columns.values()))


def update(table: str, identifier: str, patch: dict) -> dict:
    assert table in _TABLES
    with connection() as db:
        # Acquire the write lock BEFORE reading to avoid lost read-modify-writes.
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(f"SELECT data FROM {table} WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise KeyError(identifier)
        data = json.loads(row["data"])
        data.update(patch)
        if table == "jobs":
            data["updated_at"] = now()
            db.execute("UPDATE jobs SET data=?,status=?,updated_at=? WHERE id=?",
                       (encode(data), data["status"], data["updated_at"], identifier))
        else:
            db.execute(f"UPDATE {table} SET data=? WHERE id=?", (encode(data), identifier))
    return data


def delete_project(identifier: str) -> None:
    with connection() as db:
        db.execute("DELETE FROM projects WHERE id=?", (identifier,))


def delete_highlight(identifier: str) -> None:
    with connection() as db:
        db.execute("DELETE FROM highlights WHERE id=?", (identifier,))


def delete_export(identifier: str) -> None:
    with connection() as db:
        db.execute("DELETE FROM exports WHERE id=?", (identifier,))


def replace_highlights(project_id: str, highlights: list[dict]) -> None:
    with connection() as db:
        # Replace only once analysis succeeded, preserving earlier results on error/cancel.
        old = {row["id"]: json.loads(row["data"]) for row in db.execute(
            "SELECT id,data FROM highlights WHERE project_id=?", (project_id,))}
        db.execute("DELETE FROM highlights WHERE project_id=?", (project_id,))
        for item in highlights:
            prior = old.get(item["id"], {})
            if prior:
                item["selected"] = prior.get("selected", True)
                item["transcript"] = prior.get("transcript") or item.get("transcript", [])
            insert("highlights", item, db=db)


def settings() -> dict:
    with connection() as db:
        row = db.execute("SELECT data FROM settings WHERE id=1").fetchone()
    return Settings.model_validate(json.loads(row["data"])).model_dump()


def save_settings(value: dict) -> dict:
    validated = Settings.model_validate(value).model_dump()
    with connection() as db:
        db.execute("UPDATE settings SET data=? WHERE id=1", (encode(validated),))
    return validated


def queued_job() -> tuple[dict, dict] | None:
    with connection() as db:
        row = db.execute("SELECT data,payload FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
    return (json.loads(row["data"]), json.loads(row["payload"])) if row else None


def job_payload(identifier: str) -> dict:
    with connection() as db:
        row = db.execute("SELECT payload FROM jobs WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise KeyError(identifier)
    return json.loads(row["payload"])


def complete_export(export_id: str, job_id: str, rendered: dict, elapsed: float) -> None:
    """Publish both DB records in one transaction after the atomic file rename."""
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        export_row = db.execute("SELECT data FROM exports WHERE id=?", (export_id,)).fetchone()
        job_row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not export_row or not job_row:
            raise KeyError("Export or job no longer exists")
        item, job = json.loads(export_row["data"]), json.loads(job_row["data"])
        item.update(rendered, status="completed", error=None, download_url=f"/api/exports/{export_id}/file")
        total = float(job.get("total_seconds") or 0)
        job.update(status="completed", stage="rendering", processed_seconds=total,
                   progress=1.0 if total else None, error=None, detail="Completed", eta_seconds=0.0,
                   elapsed_seconds=max(0.0, elapsed), updated_at=now(),
                   result={"export_id": export_id, **rendered})
        db.execute("UPDATE exports SET data=? WHERE id=?", (encode(item), export_id))
        db.execute("UPDATE jobs SET data=?,status='completed',updated_at=? WHERE id=?",
                   (encode(job), job["updated_at"], job_id))


def recent_jobs(terminal_limit: int = 100) -> list[dict]:
    # Never hide the running job behind a long queue of newer submissions.
    with connection() as db:
        rows = db.execute("SELECT data FROM jobs WHERE status IN ('queued','running') ORDER BY created_at DESC").fetchall()
        terminal = db.execute("SELECT data FROM jobs WHERE status NOT IN ('queued','running') ORDER BY created_at DESC LIMIT ?", (terminal_limit,)).fetchall()
    return sorted([json.loads(row["data"]) for row in [*rows, *terminal]], key=lambda item: item["created_at"], reverse=True)


def active_jobs(project_id: str | None = None) -> list[dict]:
    query = "SELECT data FROM jobs WHERE status IN ('queued','running')"
    args = ()
    if project_id:
        query += " AND project_id=?"
        args = (project_id,)
    with connection() as db:
        rows = db.execute(query, args).fetchall()
    return [json.loads(row["data"]) for row in rows]
