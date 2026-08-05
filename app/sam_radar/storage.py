from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

VALID_STATUSES = {"new", "reviewing", "pursue", "teaming", "monitor", "no-bid", "submitted", "archived"}
VALID_PRIORITIES = {"", "low", "normal", "high", "urgent"}
NO_BID_REASONS = {
    "",
    "poor-fit",
    "deadline-too-short",
    "incumbent-likely",
    "too-large",
    "certification-gap",
    "clearance-gap",
    "geography",
    "staffing-gap",
    "past-performance-gap",
    "not-it-security",
    "duplicate-noise",
    "other",
}
WORKFLOW_COLUMNS = {
    "priority": "TEXT NOT NULL DEFAULT 'normal'",
    "owner": "TEXT NOT NULL DEFAULT ''",
    "next_action": "TEXT NOT NULL DEFAULT ''",
    "follow_up_at": "TEXT NOT NULL DEFAULT ''",
    "decision_reason": "TEXT NOT NULL DEFAULT ''",
    "no_bid_reason": "TEXT NOT NULL DEFAULT ''",
    "no_bid_detail": "TEXT NOT NULL DEFAULT ''",
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def title_key(opp: dict) -> str:
    title = re.sub(r"\s+", " ", str(opp.get("title") or "").lower()).strip()
    deadline = str(opp.get("responseDeadline") or "")
    return f"{title}|{deadline}"


def clean_text(value: Any, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def parse_documents(raw: Any) -> list[dict[str, Any]]:
    docs = raw if isinstance(raw, list) else []
    parsed = []
    for idx, doc in enumerate(docs[:20], 1):
        if not isinstance(doc, dict):
            continue
        url = clean_text(doc.get("url"), 1000)
        label = clean_text(doc.get("label"), 200) or f"Document {idx}"
        if not url:
            continue
        parsed.append({"label": label, "url": url, "reviewed": bool(doc.get("reviewed"))})
    return parsed


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
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(opportunity_status)")}
            for column, ddl in WORKFLOW_COLUMNS.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE opportunity_status ADD COLUMN {column} {ddl}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS opportunity_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  old_value TEXT NOT NULL DEFAULT '',
                  new_value TEXT NOT NULL DEFAULT '',
                  message TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opportunity_events_notice
                ON opportunity_events (notice_id, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS opportunity_documents (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL,
                  label TEXT NOT NULL,
                  url TEXT NOT NULL,
                  reviewed INTEGER NOT NULL DEFAULT 0,
                  updated_at TEXT NOT NULL,
                  UNIQUE(notice_id, url)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_notifications (
                  notice_id TEXT NOT NULL,
                  notification_type TEXT NOT NULL,
                  notification_key TEXT NOT NULL,
                  sent_at TEXT NOT NULL,
                  PRIMARY KEY (notice_id, notification_type, notification_key)
                )
                """
            )

    def _add_event(
        self,
        conn: sqlite3.Connection,
        notice_id: str,
        event_type: str,
        old_value: str = "",
        new_value: str = "",
        message: str = "",
        created_at: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO opportunity_events (notice_id, event_type, old_value, new_value, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (notice_id, event_type, clean_text(old_value), clean_text(new_value), clean_text(message), created_at or utc_now()),
        )

    def unseen(self, matches: list[dict]) -> list[dict]:
        with self.connect() as conn:
            ids = {row["notice_id"] for row in conn.execute("SELECT notice_id FROM seen_opportunities")}
            keys = {row["title_key"] for row in conn.execute("SELECT title_key FROM seen_opportunities")}
        return [opp for opp in matches if opp.get("noticeId") not in ids and title_key(opp) not in keys]

    def mark_seen(self, matches: list[dict], *, notified: bool = False) -> None:
        now = utc_now()
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
                self._add_event(conn, notice_id, "first_seen", message="Opportunity first seen", created_at=now)

    def _workflow_from_row(self, row: sqlite3.Row | None, notice_id: str) -> dict[str, Any]:
        if not row:
            return {
                "noticeId": notice_id,
                "status": "new",
                "notes": "",
                "priority": "normal",
                "owner": "",
                "nextAction": "",
                "followUpAt": "",
                "decisionReason": "",
                "noBidReason": "",
                "noBidDetail": "",
                "updatedAt": "",
                "documents": [],
                "events": [],
            }
        return {
            "noticeId": row["notice_id"],
            "status": row["status"],
            "notes": row["notes"],
            "priority": row["priority"],
            "owner": row["owner"],
            "nextAction": row["next_action"],
            "followUpAt": row["follow_up_at"],
            "decisionReason": row["decision_reason"],
            "noBidReason": row["no_bid_reason"],
            "noBidDetail": row["no_bid_detail"],
            "updatedAt": row["updated_at"],
            "documents": [],
            "events": [],
        }

    def _documents(self, conn: sqlite3.Connection, notice_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not notice_ids:
            return {}
        placeholders = ",".join("?" for _ in notice_ids)
        rows = conn.execute(
            f"SELECT notice_id, label, url, reviewed, updated_at FROM opportunity_documents WHERE notice_id IN ({placeholders}) ORDER BY id",
            notice_ids,
        ).fetchall()
        docs: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            docs.setdefault(row["notice_id"], []).append(
                {"label": row["label"], "url": row["url"], "reviewed": bool(row["reviewed"]), "updatedAt": row["updated_at"]}
            )
        return docs

    def _events(self, conn: sqlite3.Connection, notice_ids: list[str], limit: int = 12) -> dict[str, list[dict[str, str]]]:
        if not notice_ids:
            return {}
        events: dict[str, list[dict[str, str]]] = {}
        for notice_id in notice_ids:
            rows = conn.execute(
                """
                SELECT event_type, old_value, new_value, message, created_at
                FROM opportunity_events
                WHERE notice_id = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
                """,
                (notice_id, limit),
            ).fetchall()
            events[notice_id] = [
                {
                    "type": row["event_type"],
                    "oldValue": row["old_value"],
                    "newValue": row["new_value"],
                    "message": row["message"],
                    "createdAt": row["created_at"],
                }
                for row in rows
            ]
        return events

    def status_map(self, notice_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not notice_ids:
            return {}
        placeholders = ",".join("?" for _ in notice_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT notice_id, status, notes, priority, owner, next_action, follow_up_at,
                       decision_reason, no_bid_reason, no_bid_detail, updated_at
                FROM opportunity_status WHERE notice_id IN ({placeholders})
                """,
                notice_ids,
            ).fetchall()
            docs = self._documents(conn, notice_ids)
            events = self._events(conn, notice_ids)
        mapped = {row["notice_id"]: self._workflow_from_row(row, row["notice_id"]) for row in rows}
        for notice_id, workflow in mapped.items():
            workflow["documents"] = docs.get(notice_id, [])
            workflow["events"] = events.get(notice_id, [])
        return mapped

    def set_status(self, notice_id: str, status: str, notes: str = "") -> dict[str, Any]:
        return self.set_workflow(notice_id, {"status": status, "notes": notes})

    def set_workflow(self, notice_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not notice_id:
            raise ValueError("notice_id is required")
        status = clean_text(payload.get("status") or "new", 80).lower()
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")
        priority = clean_text(payload.get("priority") or "normal", 40).lower()
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"priority must be one of: {', '.join(sorted(VALID_PRIORITIES))}")
        no_bid_reason = clean_text(payload.get("noBidReason") or payload.get("no_bid_reason") or "", 80).lower()
        if no_bid_reason not in NO_BID_REASONS:
            raise ValueError(f"no_bid_reason must be one of: {', '.join(sorted(NO_BID_REASONS))}")
        now = utc_now()
        docs = parse_documents(payload.get("documents"))
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT notice_id, status, notes, priority, owner, next_action, follow_up_at,
                       decision_reason, no_bid_reason, no_bid_detail, updated_at
                FROM opportunity_status WHERE notice_id = ?
                """,
                (notice_id,),
            ).fetchone()
            previous = self._workflow_from_row(existing, notice_id)
            values = {
                "status": status,
                "notes": clean_text(payload.get("notes"), 5000) if "notes" in payload else previous["notes"],
                "priority": priority,
                "owner": clean_text(payload.get("owner"), 120) if "owner" in payload else previous["owner"],
                "next_action": clean_text(payload.get("nextAction") or payload.get("next_action"), 1000)
                if ("nextAction" in payload or "next_action" in payload)
                else previous["nextAction"],
                "follow_up_at": clean_text(payload.get("followUpAt") or payload.get("follow_up_at"), 120)
                if ("followUpAt" in payload or "follow_up_at" in payload)
                else previous["followUpAt"],
                "decision_reason": clean_text(payload.get("decisionReason") or payload.get("decision_reason"), 1000)
                if ("decisionReason" in payload or "decision_reason" in payload)
                else previous["decisionReason"],
                "no_bid_reason": no_bid_reason,
                "no_bid_detail": clean_text(payload.get("noBidDetail") or payload.get("no_bid_detail"), 1000)
                if ("noBidDetail" in payload or "no_bid_detail" in payload)
                else previous["noBidDetail"],
            }
            conn.execute(
                """
                INSERT INTO opportunity_status
                  (notice_id, status, notes, priority, owner, next_action, follow_up_at,
                   decision_reason, no_bid_reason, no_bid_detail, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(notice_id) DO UPDATE SET
                  status = excluded.status,
                  notes = excluded.notes,
                  priority = excluded.priority,
                  owner = excluded.owner,
                  next_action = excluded.next_action,
                  follow_up_at = excluded.follow_up_at,
                  decision_reason = excluded.decision_reason,
                  no_bid_reason = excluded.no_bid_reason,
                  no_bid_detail = excluded.no_bid_detail,
                  updated_at = excluded.updated_at
                """,
                (
                    notice_id,
                    values["status"],
                    values["notes"],
                    values["priority"],
                    values["owner"],
                    values["next_action"],
                    values["follow_up_at"],
                    values["decision_reason"],
                    values["no_bid_reason"],
                    values["no_bid_detail"],
                    now,
                ),
            )
            comparisons = [
                ("status_changed", previous["status"], values["status"], f"Status changed to {values['status']}"),
                ("note_updated", previous["notes"], values["notes"], "Notes updated"),
                ("priority_changed", previous["priority"], values["priority"], f"Priority changed to {values['priority']}"),
                ("owner_changed", previous["owner"], values["owner"], f"Owner changed to {values['owner'] or 'unassigned'}"),
                ("next_action_changed", previous["nextAction"], values["next_action"], "Next action updated"),
                ("follow_up_changed", previous["followUpAt"], values["follow_up_at"], "Follow-up date updated"),
                ("decision_reason_changed", previous["decisionReason"], values["decision_reason"], "Decision reason updated"),
                ("no_bid_reason_changed", previous["noBidReason"], values["no_bid_reason"], "No-bid reason updated"),
            ]
            for event_type, old, new, message in comparisons:
                if str(old or "") != str(new or ""):
                    self._add_event(conn, notice_id, event_type, str(old or ""), str(new or ""), message, now)
            if values["status"] == "submitted" and previous["status"] != "submitted":
                self._add_event(conn, notice_id, "submitted", previous["status"], "submitted", "Opportunity marked submitted", now)
            if values["status"] == "archived" and previous["status"] != "archived":
                self._add_event(conn, notice_id, "archived", previous["status"], "archived", "Opportunity archived", now)
            if "documents" in payload:
                old_docs = self._documents(conn, [notice_id]).get(notice_id, [])
                conn.execute("DELETE FROM opportunity_documents WHERE notice_id = ?", (notice_id,))
                for doc in docs:
                    conn.execute(
                        """
                        INSERT INTO opportunity_documents (notice_id, label, url, reviewed, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (notice_id, doc["label"], doc["url"], int(doc["reviewed"]), now),
                    )
                if old_docs != docs:
                    self._add_event(conn, notice_id, "documents_updated", json.dumps(old_docs), json.dumps(docs), "Documents updated", now)
            workflow = self._workflow_from_row(
                conn.execute(
                    """
                    SELECT notice_id, status, notes, priority, owner, next_action, follow_up_at,
                           decision_reason, no_bid_reason, no_bid_detail, updated_at
                    FROM opportunity_status WHERE notice_id = ?
                    """,
                    (notice_id,),
                ).fetchone(),
                notice_id,
            )
            workflow["documents"] = self._documents(conn, [notice_id]).get(notice_id, [])
            workflow["events"] = self._events(conn, [notice_id]).get(notice_id, [])
        return workflow

    def get_status(self, notice_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT notice_id, status, notes, priority, owner, next_action, follow_up_at,
                       decision_reason, no_bid_reason, no_bid_detail, updated_at
                FROM opportunity_status WHERE notice_id = ?
                """,
                (notice_id,),
            ).fetchone()
            workflow = self._workflow_from_row(row, notice_id)
            workflow["documents"] = self._documents(conn, [notice_id]).get(notice_id, [])
            workflow["events"] = self._events(conn, [notice_id]).get(notice_id, [])
        return workflow

    def record_notification_once(self, notice_id: str, notification_type: str, notification_key: str) -> bool:
        now = utc_now()
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO workflow_notifications (notice_id, notification_type, notification_key, sent_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (notice_id, notification_type, notification_key, now),
                )
                self._add_event(conn, notice_id, "slack_posted", "", notification_type, f"Slack notification posted: {notification_type}", now)
            return True
        except sqlite3.IntegrityError:
            return False
