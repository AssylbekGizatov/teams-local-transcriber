"""Small durable single-node store. WAL transactions guard claims and editing revisions."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL = {"completed", "failed", "cancelled", "needs_review"}
ACTIVE = {"queued", "joining", "waiting_room", "recording", "transcribing", "stopping"}


def now() -> str:
    return datetime.now(UTC).isoformat()


class ConflictError(Exception):
    pass


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meetings (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                    language TEXT NOT NULL, meeting_url TEXT, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    audio_path TEXT, handle TEXT, error TEXT, version INTEGER NOT NULL DEFAULT 1,
                    consent_at TEXT NOT NULL, authorization_reference TEXT,
                    stop_requested INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS segments (
                    id TEXT NOT NULL, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                    start REAL NOT NULL, end REAL NOT NULL, text TEXT NOT NULL,
                    speaker TEXT, edited INTEGER NOT NULL DEFAULT 0, is_final INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (meeting_id,id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                    at TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
                CREATE INDEX IF NOT EXISTS idx_segments_time ON segments(meeting_id,start);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(segments)")}
            if "is_final" not in columns:
                db.execute("ALTER TABLE segments ADD COLUMN is_final INTEGER NOT NULL DEFAULT 1")
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def event(db, meeting_id: str, action: str, details: dict | None = None):
        db.execute(
            "INSERT INTO events(meeting_id,at,action,details) VALUES (?,?,?,?)",
            (meeting_id, now(), action, json.dumps(details or {}, ensure_ascii=False)),
        )

    def create(
        self,
        *,
        title: str,
        source: str,
        language: str,
        meeting_url: str | None = None,
        audio_path: str | None = None,
        authorization_reference: str = "",
        max_pending: int = 32,
    ) -> dict:
        meeting_id = str(uuid.uuid4())
        timestamp = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM meetings WHERE status IN ('queued','joining','waiting_room','recording','transcribing','stopping')"
            ).fetchone()[0]
            if count >= max_pending:
                raise ConflictError("The job queue is full")
            db.execute(
                "INSERT INTO meetings (id,title,source,language,meeting_url,status,created_at,updated_at,audio_path,consent_at,authorization_reference) VALUES (?,?,?,?,?,'queued',?,?,?,?,?)",
                (
                    meeting_id,
                    title,
                    source,
                    language,
                    meeting_url,
                    timestamp,
                    timestamp,
                    audio_path,
                    timestamp,
                    authorization_reference,
                ),
            )
            self.event(db, meeting_id, "created", {"source": source, "consent_confirmed": True})
        return self.get(meeting_id)

    def get(self, meeting_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["handle"] = json.loads(data["handle"]) if data["handle"] else None
            data["segments"] = [
                dict(x)
                for x in db.execute(
                    "SELECT * FROM segments WHERE meeting_id=? ORDER BY start,id", (meeting_id,)
                )
            ]
            for segment in data["segments"]:
                segment["final"] = bool(segment.pop("is_final"))
            data["events"] = [
                dict(x)
                for x in db.execute(
                    "SELECT at,action,details FROM events WHERE meeting_id=? ORDER BY id DESC LIMIT 50",
                    (meeting_id,),
                )
            ]
            return data

    def list(self, limit: int = 100, offset: int = 0) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,title,source,language,status,created_at,updated_at,error,version FROM meetings ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def active(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id FROM meetings WHERE status IN ('queued','joining','waiting_room','recording','transcribing','stopping') ORDER BY created_at"
            ).fetchall()
        return [self.get(row["id"]) for row in rows]

    def update(self, meeting_id: str, **fields: Any):
        allowed = {"status", "error", "handle", "audio_path", "stop_requested", "attempts"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("Unsupported meeting update")
        if "handle" in fields:
            fields["handle"] = json.dumps(fields["handle"]) if fields["handle"] else None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT status FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if old is None:
                raise KeyError(meeting_id)
            assignments = ",".join(f"{key}=?" for key in fields)
            db.execute(
                f"UPDATE meetings SET {assignments},updated_at=?,version=version+1 WHERE id=?",
                (*fields.values(), now(), meeting_id),
            )
            if "status" in fields and fields["status"] != old["status"]:
                self.event(
                    db,
                    meeting_id,
                    "status_changed",
                    {"from": old["status"], "to": fields["status"]},
                )

    def claim(self, meeting_id: str, next_status: str) -> bool:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE meetings SET status=?,updated_at=?,attempts=attempts+1,version=version+1 WHERE id=? AND status='queued' AND stop_requested=0",
                (next_status, now(), meeting_id),
            ).rowcount
            if changed:
                self.event(db, meeting_id, "started", {"status": next_status})
            return bool(changed)

    def put_segments(
        self, meeting_id: str, segments: list[dict], *, replace_snapshot: bool = False
    ):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = False
            if replace_snapshot:
                incoming = {str(segment.get("id", index)) for index, segment in enumerate(segments)}
                existing_rows = db.execute(
                    "SELECT id FROM segments WHERE meeting_id=? AND edited=0", (meeting_id,)
                ).fetchall()
                for row in existing_rows:
                    if row["id"] not in incoming:
                        db.execute(
                            "DELETE FROM segments WHERE meeting_id=? AND id=? AND edited=0",
                            (meeting_id, row["id"]),
                        )
                        changed = True
            for index, segment in enumerate(segments):
                segment_id = str(segment.get("id", index))
                existing = db.execute(
                    "SELECT edited,start,end,text,speaker,is_final FROM segments WHERE meeting_id=? AND id=?",
                    (meeting_id, segment_id),
                ).fetchone()
                values = (
                    float(segment["start"]),
                    float(segment["end"]),
                    str(segment["text"]),
                    segment.get("speaker"),
                    int(segment.get("final", True)),
                )
                if existing and (existing["edited"] or tuple(existing)[1:] == values):
                    continue
                db.execute(
                    "INSERT INTO segments(id,meeting_id,start,end,text,speaker,is_final) VALUES (?,?,?,?,?,?,?) ON CONFLICT(meeting_id,id) DO UPDATE SET start=excluded.start,end=excluded.end,text=excluded.text,speaker=excluded.speaker,is_final=excluded.is_final WHERE segments.edited=0",
                    (segment_id, meeting_id, *values),
                )
                changed = True
            if changed:
                db.execute(
                    "UPDATE meetings SET updated_at=?,version=version+1 WHERE id=?",
                    (now(), meeting_id),
                )

    def edit_segment(
        self, meeting_id: str, segment_id: str, text: str, speaker: str | None, version: int
    ) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            meeting = db.execute(
                "SELECT version FROM meetings WHERE id=?", (meeting_id,)
            ).fetchone()
            if not meeting:
                raise KeyError(meeting_id)
            if meeting["version"] != version:
                raise ConflictError("The transcript has changed. Reload before saving.")
            if not db.execute(
                "UPDATE segments SET text=?,speaker=?,edited=1 WHERE meeting_id=? AND id=?",
                (text, speaker, meeting_id, segment_id),
            ).rowcount:
                raise KeyError(segment_id)
            db.execute(
                "UPDATE meetings SET version=version+1,updated_at=? WHERE id=?", (now(), meeting_id)
            )
            self.event(db, meeting_id, "segment_edited", {"segment_id": segment_id})
        return self.get(meeting_id)

    def recover(self):
        """Audio work is retryable. Uncertain external joins require operator reconciliation."""
        for meeting in self.active():
            if meeting["source"] == "upload" and meeting["status"] == "transcribing":
                self.update(meeting["id"], status="queued", error="Recovered after worker restart")
            elif (
                meeting["source"] == "teams"
                and meeting["status"] == "joining"
                and not meeting["handle"]
            ):
                self.update(
                    meeting["id"],
                    status="needs_review",
                    error="Join outcome is uncertain after restart. Check Vexa/Teams before starting another bot.",
                )
