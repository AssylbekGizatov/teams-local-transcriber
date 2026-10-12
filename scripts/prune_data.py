#!/usr/bin/env python3
"""Review and explicitly purge expired LOCAL terminal jobs; never touches Vexa or active jobs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Permanently delete matching local transcripts/audit rows/audio",
    )
    parser.add_argument("--days", type=int, help="Override RETENTION_DAYS")
    args = parser.parse_args()
    settings = Settings()
    days = args.days if args.days is not None else settings.retention_days
    if days < 1:
        parser.error("--days must be at least 1")
    if not settings.db_path.is_file():
        print("No local database; nothing to purge.")
        return
    database = Database(settings.db_path)
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            "SELECT id,status,audio_path FROM meetings WHERE status IN ('completed','failed','cancelled') AND updated_at < ?",
            (cutoff,),
        ).fetchall()
        print(
            json.dumps(
                {
                    "apply": args.apply,
                    "cutoff": cutoff,
                    "local_jobs": [{"id": row["id"], "status": row["status"]} for row in rows],
                },
                indent=2,
            )
        )
        if args.apply:
            audio_root = (settings.data_dir / "audio").resolve()
            for row in rows:
                if row["audio_path"]:
                    path = Path(row["audio_path"]).resolve()
                    if path.is_relative_to(audio_root):
                        path.unlink(missing_ok=True)
                connection.execute("DELETE FROM meetings WHERE id=?", (row["id"],))
    print(
        "Vexa transcripts, Redis, logs, filesystem snapshots and backups are separate; this command does not erase them."
    )


if __name__ == "__main__":
    main()
