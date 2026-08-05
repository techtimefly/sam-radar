from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

VALID_STATUSES = {"new", "reviewing", "pursue", "teaming", "monitor", "no-bid", "submitted", "archived"}


def title_key(opp: dict) -> str:
    title = re.sub(r"\s+", " ", str(opp.get("title") or "").lower()).strip()
    deadline = str(opp.get("responseDeadline") or "")
    return f"{title}|{deadline}"


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_opportunities (
                  notice_id TEXT PRIMARY KEY,
                  title_key TEXT UNIQUE,
                  title TEXT,
                  url TEXT,
                  recommendation TEXT,
                  score INTEGER,
                  payload_json TEXT NOT NULL,
                  first_seen TEXT NOT NULL,
                  last_seen TEXT NOT NULL,
                  notified_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS opportunity_status (
                  notice_id TEXT PRIMARY KEY,
                  status TEXT NOT NULL DEFAULT 'new',
                  notes TEXT NOT NULL DEFAULT '',
                  updated_at TEXT NOT NULL
                )
                """
            )

    def unseen(self, matches: list[dict]) -> list[dict]:
        with self.connect() as conn:
            ids = {row["notice_id"] for row in conn.execute("SELECT notice_id FROM seen_opportunities")}
            keys = {row["title_key"] for row in conn.execute("SELECT title_key FROM seen_opportunities")}
        return [opp for opp in matches if opp.get("noticeId") not in ids and title_key(opp) not in keys]

    def mark_seen(self, matches: list[dict], *, notified: bool = False) -> None:
        now = dt.datetime.now(dt.UTC).isoformat()
        with self.connect() as conn:
            for opp in matches:
                notice_id = opp.get("noticeId")
                if not notice_id:
                    continue
                existing = conn.execute("SELECT notice_id FROM seen_opportunities WHERE notice_id = ?", (notice_id,)).fetchone()
                if existing:
                    conn.execute("UPDATE seen_opportunities SET last_seen = ? WHERE notice_id = ?", (now, notice_id))
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO seen_opportunities
                    (notice_id, title_key, title, url, recommendation, score, payload_json, first_seen, last_seen, notified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        notice_id,
                        title_key(opp),
                        opp.get("title"),
                        opp.get("url") or opp.get("uiLink"),
                        opp.get("recommendation"),
                        opp.get("score"),
                        json.dumps(opp, sort_keys=True),
                        now,
                        now,
                        now if notified else None,
                    ),
                )


    def status_map(self, notice_ids: list[str]) -> dict[str, dict]:
        if not notice_ids:
            return {}
        placeholders = ",".join("?" for _ in notice_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT notice_id, status, notes, updated_at FROM opportunity_status WHERE notice_id IN ({placeholders})",
                notice_ids,
            ).fetchall()
        return {
            row["notice_id"]: {"status": row["status"], "notes": row["notes"], "updatedAt": row["updated_at"]}
            for row in rows
        }

    def set_status(self, notice_id: str, status: str, notes: str = "") -> dict:
        if not notice_id:
            raise ValueError("notice_id is required")
        status = status.strip().lower()
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")
        now = dt.datetime.now(dt.UTC).isoformat()
        with self.connect() as conn:
            existing = conn.execute("SELECT notes FROM opportunity_status WHERE notice_id = ?", (notice_id,)).fetchone()
            final_notes = notes if notes or not existing else existing["notes"]
            conn.execute(
                """
                INSERT INTO opportunity_status (notice_id, status, notes, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(notice_id) DO UPDATE SET
                  status = excluded.status,
                  notes = excluded.notes,
                  updated_at = excluded.updated_at
                """,
                (notice_id, status, final_notes, now),
            )
        return {"noticeId": notice_id, "status": status, "notes": final_notes, "updatedAt": now}

    def get_status(self, notice_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT notice_id, status, notes, updated_at FROM opportunity_status WHERE notice_id = ?",
                (notice_id,),
            ).fetchone()
        if not row:
            return {"noticeId": notice_id, "status": "new", "notes": "", "updatedAt": ""}
        return {"noticeId": row["notice_id"], "status": row["status"], "notes": row["notes"], "updatedAt": row["updated_at"]}
