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

PROPOSAL_ROLES = {"prime", "subcontractor"}
PROPOSAL_STAGES = ["intent", "intake", "docs", "requirements", "gaps", "strategy", "draft", "review"]
PROPOSAL_STAGE_LABELS = {
    "intent": "Intent",
    "intake": "Intake",
    "docs": "Docs",
    "requirements": "Requirements",
    "gaps": "Gaps",
    "strategy": "Strategy",
    "draft": "Draft",
    "review": "Review",
}
DOCUMENT_SOURCE_TYPES = {"url", "local-path", "upload"}
DOCUMENT_PARSE_STATUSES = {"pending", "parsed", "failed", "unsupported"}
ARTIFACT_TYPES = {"outline", "prime-proposal", "subcontractor", "compliance-matrix", "forms-checklist", "questions", "notes"}
ARTIFACT_STATUSES = {"draft", "review", "approved", "archived"}
ARTIFACT_FORMATS = {"markdown", "text"}


def proposal_stage_items(current_stage: str) -> list[dict[str, str]]:
    stage = current_stage if current_stage in PROPOSAL_STAGES else "intent"
    current_index = PROPOSAL_STAGES.index(stage)
    items = []
    for idx, key in enumerate(PROPOSAL_STAGES):
        state = "complete" if idx < current_index else "current" if idx == current_index else "pending"
        items.append({"key": key, "label": PROPOSAL_STAGE_LABELS[key], "state": state})
    return items


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS manual_tracked_opportunities (
                  notice_id TEXT PRIMARY KEY,
                  title TEXT NOT NULL DEFAULT '',
                  url TEXT NOT NULL DEFAULT '',
                  source TEXT NOT NULL DEFAULT 'manual-search',
                  payload_json TEXT NOT NULL DEFAULT '{}',
                  added_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_reference_codes (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  kind TEXT NOT NULL,
                  code TEXT NOT NULL,
                  title TEXT NOT NULL DEFAULT '',
                  description TEXT NOT NULL DEFAULT '',
                  notes TEXT NOT NULL DEFAULT '',
                  active INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(kind, code)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_search_profiles (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL UNIQUE,
                  description TEXT NOT NULL DEFAULT '',
                  payload_json TEXT NOT NULL DEFAULT '{}',
                  active INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS search_feedback (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  profile_name TEXT NOT NULL DEFAULT '',
                  notes TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS proposal_workspaces (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL UNIQUE,
                  title TEXT NOT NULL DEFAULT '',
                  role TEXT NOT NULL DEFAULT 'prime',
                  stage TEXT NOT NULL DEFAULT 'intent',
                  status TEXT NOT NULL DEFAULT 'active',
                  payload_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_proposal_workspaces_stage
                ON proposal_workspaces (stage, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS proposal_documents (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL,
                  source_type TEXT NOT NULL DEFAULT 'url',
                  source TEXT NOT NULL,
                  label TEXT NOT NULL DEFAULT '',
                  filename TEXT NOT NULL DEFAULT '',
                  content_type TEXT NOT NULL DEFAULT '',
                  size_bytes INTEGER NOT NULL DEFAULT 0,
                  local_path TEXT NOT NULL DEFAULT '',
                  parse_status TEXT NOT NULL DEFAULT 'pending',
                  parse_error TEXT NOT NULL DEFAULT '',
                  extracted_text_path TEXT NOT NULL DEFAULT '',
                  reviewed INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  UNIQUE(notice_id, source)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_proposal_documents_notice
                ON proposal_documents (notice_id, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence_snippets (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL,
                  document_id INTEGER NOT NULL,
                  section TEXT NOT NULL DEFAULT '',
                  snippet TEXT NOT NULL,
                  confidence REAL NOT NULL DEFAULT 0,
                  reviewed INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY(document_id) REFERENCES proposal_documents(id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_evidence_snippets_notice
                ON evidence_snippets (notice_id, document_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS proposal_artifacts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL,
                  artifact_type TEXT NOT NULL DEFAULT 'outline',
                  title TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'draft',
                  format TEXT NOT NULL DEFAULT 'markdown',
                  content TEXT NOT NULL DEFAULT '',
                  notes TEXT NOT NULL DEFAULT '',
                  version INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_proposal_artifacts_notice
                ON proposal_artifacts (notice_id, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_audit_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL DEFAULT '',
                  action TEXT NOT NULL,
                  provider TEXT NOT NULL DEFAULT 'none',
                  mode TEXT NOT NULL DEFAULT 'disabled',
                  model TEXT NOT NULL DEFAULT '',
                  result TEXT NOT NULL DEFAULT '',
                  external INTEGER NOT NULL DEFAULT 0,
                  message TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ai_audit_events_created
                ON ai_audit_events (created_at DESC)
                """
            )

    def record_ai_audit(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        values = {
            "notice_id": clean_text(payload.get("noticeId") or payload.get("notice_id"), 160),
            "action": clean_text(payload.get("action"), 80) or "ai_action",
            "provider": clean_text(payload.get("provider"), 80) or "none",
            "mode": clean_text(payload.get("mode"), 40) or "disabled",
            "model": clean_text(payload.get("model"), 160),
            "result": clean_text(payload.get("result"), 80),
            "external": 1 if payload.get("external") else 0,
            "message": clean_text(payload.get("message"), 600),
            "created_at": now,
        }
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO ai_audit_events
                  (notice_id, action, provider, mode, model, result, external, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["notice_id"],
                    values["action"],
                    values["provider"],
                    values["mode"],
                    values["model"],
                    values["result"],
                    values["external"],
                    values["message"],
                    values["created_at"],
                ),
            )
            values["id"] = cur.lastrowid
        return values

    def ai_audit_events(self, limit: int = 25) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit or 25), 100))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, notice_id, action, provider, mode, model, result, external, message, created_at
                FROM ai_audit_events
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
                """,
                (capped,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "noticeId": row["notice_id"],
                "action": row["action"],
                "provider": row["provider"],
                "mode": row["mode"],
                "model": row["model"],
                "result": row["result"],
                "external": bool(row["external"]),
                "message": row["message"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

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



    def save_reference_code(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = clean_text(payload.get("kind"), 20).lower()
        if kind not in {"naics", "psc"}:
            raise ValueError("kind must be naics or psc")
        code = clean_text(payload.get("code"), 20).upper()
        if not code:
            raise ValueError("code is required")
        now = utc_now()
        values = (
            kind,
            code,
            clean_text(payload.get("title"), 300),
            clean_text(payload.get("description"), 1200),
            clean_text(payload.get("notes"), 1200),
            1 if payload.get("active", True) else 0,
            now,
            now,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO saved_reference_codes
                  (kind, code, title, description, notes, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, code) DO UPDATE SET
                  title=excluded.title,
                  description=excluded.description,
                  notes=excluded.notes,
                  active=excluded.active,
                  updated_at=excluded.updated_at
                """,
                values,
            )
        return self.saved_reference_code(kind, code)

    def saved_reference_code(self, kind: str, code: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, kind, code, title, description, notes, active, created_at, updated_at
                FROM saved_reference_codes WHERE kind = ? AND code = ?
                """,
                (kind, code),
            ).fetchone()
        if not row:
            return {}
        return {
            "id": row["id"],
            "kind": row["kind"],
            "code": row["code"],
            "title": row["title"],
            "description": row["description"],
            "notes": row["notes"],
            "active": bool(row["active"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def saved_reference_codes(self, kind: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT id, kind, code, title, description, notes, active, created_at, updated_at
            FROM saved_reference_codes
        """
        params: tuple[Any, ...] = ()
        if kind:
            sql += " WHERE kind = ?"
            params = (kind,)
        sql += " ORDER BY kind, code"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "code": row["code"],
                "title": row["title"],
                "description": row["description"],
                "notes": row["notes"],
                "active": bool(row["active"]),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def delete_reference_code(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = clean_text(payload.get("kind"), 20).lower()
        if kind not in {"naics", "psc"}:
            raise ValueError("kind must be naics or psc")
        code = clean_text(payload.get("code"), 20).upper()
        if not code:
            raise ValueError("code is required")
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM saved_reference_codes WHERE kind = ? AND code = ?", (kind, code))
        return {"kind": kind, "code": code, "deleted": cur.rowcount > 0}

    def save_search_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = clean_text(payload.get("name"), 160)
        if not name:
            raise ValueError("name is required")
        now = utc_now()
        profile = {
            "name": name,
            "description": clean_text(payload.get("description"), 1200),
            "keywords": [clean_text(item, 100) for item in payload.get("keywords", []) if clean_text(item, 100)],
            "naics": [clean_text(item, 20).upper() for item in payload.get("naics", []) if clean_text(item, 20)],
            "psc": [clean_text(item, 20).upper() for item in payload.get("psc", []) if clean_text(item, 20)],
            "setAsides": [clean_text(item, 40).upper() for item in payload.get("setAsides", []) if clean_text(item, 40)],
            "noticeTypes": [clean_text(item, 10).lower() for item in payload.get("noticeTypes", []) if clean_text(item, 10)],
            "exclusions": [clean_text(item, 120) for item in payload.get("exclusions", []) if clean_text(item, 120)],
            "days": int(payload.get("days") or 7),
            "limit": int(payload.get("limit") or 25),
            "active": bool(payload.get("active", False)),
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO saved_search_profiles (name, description, payload_json, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                  description=excluded.description,
                  payload_json=excluded.payload_json,
                  active=excluded.active,
                  updated_at=excluded.updated_at
                """,
                (name, profile["description"], json.dumps(profile, sort_keys=True), 1 if profile["active"] else 0, now, now),
            )
        return self.saved_search_profile(name)

    def saved_search_profile(self, name: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, description, payload_json, active, created_at, updated_at
                FROM saved_search_profiles WHERE name = ?
                """,
                (name,),
            ).fetchone()
        if not row:
            return {}
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload.update({"id": row["id"], "name": row["name"], "active": bool(row["active"]), "createdAt": row["created_at"], "updatedAt": row["updated_at"]})
        return payload

    def saved_search_profiles(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, description, payload_json, active, created_at, updated_at
                FROM saved_search_profiles ORDER BY active DESC, name
                """
            ).fetchall()
        profiles = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            payload.update({"id": row["id"], "name": row["name"], "active": bool(row["active"]), "createdAt": row["created_at"], "updatedAt": row["updated_at"]})
            profiles.append(payload)
        return profiles

    def add_search_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        notice_id = clean_text(payload.get("noticeId"), 200)
        reason = clean_text(payload.get("reason"), 120)
        if not notice_id or not reason:
            raise ValueError("noticeId and reason are required")
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO search_feedback (notice_id, reason, profile_name, notes, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (notice_id, reason, clean_text(payload.get("profileName"), 160), clean_text(payload.get("notes"), 1200), now),
            )
            feedback_id = cur.lastrowid
        return {"id": feedback_id, "noticeId": notice_id, "reason": reason, "createdAt": now}

    def search_feedback_summary(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT reason, COUNT(*) AS count, MAX(created_at) AS last_seen
                FROM search_feedback GROUP BY reason ORDER BY count DESC, reason
                """
            ).fetchall()
        return [{"reason": row["reason"], "count": row["count"], "lastSeen": row["last_seen"]} for row in rows]

    def profile_quality(self) -> list[dict[str, Any]]:
        profiles = self.saved_search_profiles()
        feedback = self.search_feedback_summary()
        feedback_count = sum(item["count"] for item in feedback)
        return [
            {
                "name": profile["name"],
                "active": profile.get("active", False),
                "naicsCount": len(profile.get("naics") or []),
                "pscCount": len(profile.get("psc") or []),
                "keywordCount": len(profile.get("keywords") or []),
                "setAsideCount": len(profile.get("setAsides") or []),
                "feedbackCount": feedback_count,
                "recommendation": "Tune" if feedback_count >= 5 else "Monitor" if profile.get("active") else "Draft",
            }
            for profile in profiles
        ]

    def _proposal_from_row(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if not row:
            return {}
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        stage = row["stage"] if row["stage"] in PROPOSAL_STAGES else "intent"
        role = row["role"] if row["role"] in PROPOSAL_ROLES else "prime"
        return {
            "id": row["id"],
            "noticeId": row["notice_id"],
            "title": row["title"],
            "role": role,
            "stage": stage,
            "stageLabel": PROPOSAL_STAGE_LABELS[stage],
            "status": row["status"],
            "stages": proposal_stage_items(stage),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "nextAction": payload.get("nextAction") or self._proposal_next_action(role, stage),
            "notes": payload.get("notes", ""),
        }

    def _proposal_next_action(self, role: str, stage: str) -> str:
        if stage == "intent":
            return "Confirm prime pursuit and start intake." if role == "prime" else "Confirm subcontracting angle and identify likely prime partners."
        if stage == "intake":
            return "Collect solicitation links, deadlines, contacts, and submission instructions."
        if stage == "docs":
            return "Download or attach solicitation documents for review."
        if stage == "requirements":
            return "Extract must-have requirements, evaluation factors, and compliance items."
        if stage == "gaps":
            return "Identify capability, staffing, certification, and past-performance gaps."
        if stage == "strategy":
            return "Draft win theme, teaming plan, pricing posture, and response outline."
        if stage == "draft":
            return "Build proposal sections and assign reviewers."
        return "Review final package, compliance matrix, and submission checklist."

    def proposal_map(self, notice_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not notice_ids:
            return {}
        placeholders = ",".join("?" for _ in notice_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, notice_id, title, role, stage, status, payload_json, created_at, updated_at
                FROM proposal_workspaces WHERE notice_id IN ({placeholders})
                """,
                notice_ids,
            ).fetchall()
        return {row["notice_id"]: self._proposal_from_row(row) for row in rows}

    def proposals(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, notice_id, title, role, stage, status, payload_json, created_at, updated_at
                FROM proposal_workspaces ORDER BY datetime(updated_at) DESC, id DESC
                """
            ).fetchall()
        return [self._proposal_from_row(row) for row in rows]

    def _proposal_document_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "noticeId": row["notice_id"],
            "sourceType": row["source_type"],
            "source": row["source"],
            "label": row["label"],
            "filename": row["filename"],
            "contentType": row["content_type"],
            "sizeBytes": row["size_bytes"],
            "localPath": row["local_path"],
            "parseStatus": row["parse_status"],
            "parseError": row["parse_error"],
            "extractedTextPath": row["extracted_text_path"],
            "reviewed": bool(row["reviewed"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def add_proposal_document(self, payload: dict[str, Any]) -> dict[str, Any]:
        notice_id = clean_text(payload.get("noticeId"), 200)
        if not notice_id:
            raise ValueError("noticeId is required")
        source_type = clean_text(payload.get("sourceType") or payload.get("source_type") or "url", 40).lower()
        if source_type not in DOCUMENT_SOURCE_TYPES:
            raise ValueError("sourceType must be url, local-path, or upload")
        source = clean_text(payload.get("source") or payload.get("url") or payload.get("localPath") or payload.get("path"), 1200)
        if not source:
            raise ValueError("source is required")
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO proposal_documents
                  (notice_id, source_type, source, label, filename, content_type, size_bytes, local_path,
                   parse_status, parse_error, extracted_text_path, reviewed, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', '', ?, ?, ?)
                ON CONFLICT(notice_id, source) DO UPDATE SET
                  source_type=excluded.source_type,
                  label=excluded.label,
                  filename=excluded.filename,
                  content_type=excluded.content_type,
                  local_path=excluded.local_path,
                  updated_at=excluded.updated_at
                """,
                (
                    notice_id,
                    source_type,
                    source,
                    clean_text(payload.get("label"), 220),
                    clean_text(payload.get("filename"), 260),
                    clean_text(payload.get("contentType") or payload.get("content_type"), 120),
                    int(payload.get("sizeBytes") or payload.get("size_bytes") or 0),
                    clean_text(payload.get("localPath") or payload.get("local_path"), 1200),
                    1 if payload.get("reviewed", False) else 0,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM proposal_documents WHERE notice_id = ? AND source = ?", (notice_id, source)).fetchone()
            self._add_event(conn, notice_id, "proposal_document_added", "", source, "Proposal document registered", now)
        return self._proposal_document_from_row(row)

    def proposal_documents(self, notice_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM proposal_documents"
        params: tuple[Any, ...] = ()
        if notice_id:
            sql += " WHERE notice_id = ?"
            params = (notice_id,)
        sql += " ORDER BY datetime(updated_at) DESC, id DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._proposal_document_from_row(row) for row in rows]

    def proposal_document(self, document_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM proposal_documents WHERE id = ?", (document_id,)).fetchone()
        return self._proposal_document_from_row(row) if row else {}

    def remove_proposal_document(self, document_id: int) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM proposal_documents WHERE id = ?", (document_id,)).fetchone()
            if not row:
                raise ValueError("document does not exist")
            document = self._proposal_document_from_row(row)
            conn.execute("DELETE FROM evidence_snippets WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM proposal_documents WHERE id = ?", (document_id,))
            self._add_event(
                conn,
                document["noticeId"],
                "proposal_document_removed",
                document.get("label") or document.get("filename") or "",
                "",
                "Proposal document source removed",
                now,
            )
        return document

    def proposal_document_map(self, notice_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not notice_ids:
            return {}
        placeholders = ",".join("?" for _ in notice_ids)
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM proposal_documents WHERE notice_id IN ({placeholders}) ORDER BY id", notice_ids).fetchall()
        mapped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            mapped.setdefault(row["notice_id"], []).append(self._proposal_document_from_row(row))
        return mapped

    def update_proposal_document_parse(self, document_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        status = clean_text(payload.get("parseStatus") or payload.get("parse_status") or "pending", 40).lower()
        if status not in DOCUMENT_PARSE_STATUSES:
            raise ValueError(f"parseStatus must be one of: {', '.join(sorted(DOCUMENT_PARSE_STATUSES))}")
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute("SELECT * FROM proposal_documents WHERE id = ?", (document_id,)).fetchone()
            if not existing:
                raise ValueError("document does not exist")
            conn.execute(
                """
                UPDATE proposal_documents
                SET parse_status = ?, parse_error = ?, extracted_text_path = ?, content_type = ?,
                    size_bytes = ?, local_path = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    clean_text(payload.get("parseError") or payload.get("parse_error"), 1000),
                    clean_text(payload.get("extractedTextPath") or payload.get("extracted_text_path"), 1200),
                    clean_text(payload.get("contentType") or payload.get("content_type") or existing["content_type"], 120),
                    int(payload.get("sizeBytes") or payload.get("size_bytes") or existing["size_bytes"] or 0),
                    clean_text(payload.get("localPath") or payload.get("local_path") or existing["local_path"], 1200),
                    now,
                    document_id,
                ),
            )
            row = conn.execute("SELECT * FROM proposal_documents WHERE id = ?", (document_id,)).fetchone()
            self._add_event(conn, row["notice_id"], "proposal_document_parsed", existing["parse_status"], status, f"Document parse {status}", now)
        return self._proposal_document_from_row(row)

    def replace_evidence_snippets(self, notice_id: str, document_id: int, snippets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        notice_id = clean_text(notice_id, 200)
        now = utc_now()
        with self.connect() as conn:
            conn.execute("DELETE FROM evidence_snippets WHERE document_id = ?", (document_id,))
            for item in snippets[:40]:
                snippet = clean_text(item.get("snippet"), 1200)
                if not snippet:
                    continue
                conn.execute(
                    """
                    INSERT INTO evidence_snippets (notice_id, document_id, section, snippet, confidence, reviewed, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        notice_id,
                        document_id,
                        clean_text(item.get("section"), 160),
                        snippet,
                        float(item.get("confidence") or 0),
                        1 if item.get("reviewed", False) else 0,
                        now,
                        now,
                    ),
                )
        return self.evidence_snippets(notice_id)

    def evidence_snippets(self, notice_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, notice_id, document_id, section, snippet, confidence, reviewed, created_at, updated_at
                FROM evidence_snippets
                WHERE notice_id = ?
                ORDER BY document_id, id
                """,
                (notice_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "noticeId": row["notice_id"],
                "documentId": row["document_id"],
                "section": row["section"],
                "snippet": row["snippet"],
                "confidence": row["confidence"],
                "reviewed": bool(row["reviewed"]),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def create_proposal(self, notice_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        notice_id = clean_text(notice_id or payload.get("noticeId"), 200)
        if not notice_id:
            raise ValueError("noticeId is required")
        role = clean_text(payload.get("role") or "prime", 40).lower()
        if role not in PROPOSAL_ROLES:
            raise ValueError("role must be prime or subcontractor")
        title = clean_text(payload.get("title"), 500) or notice_id
        now = utc_now()
        proposal_payload = {
            "nextAction": clean_text(payload.get("nextAction"), 1200) or self._proposal_next_action(role, "intent"),
            "notes": clean_text(payload.get("notes"), 5000),
        }
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT id, notice_id, title, role, stage, status, payload_json, created_at, updated_at
                FROM proposal_workspaces WHERE notice_id = ?
                """,
                (notice_id,),
            ).fetchone()
            if existing:
                return self._proposal_from_row(existing) | {"created": False}
            conn.execute(
                """
                INSERT INTO proposal_workspaces (notice_id, title, role, stage, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, 'intent', 'active', ?, ?, ?)
                """,
                (notice_id, title, role, json.dumps(proposal_payload, sort_keys=True), now, now),
            )
            self._add_event(conn, notice_id, "proposal_created", "", role, f"Proposal workspace created as {role}", now)
            row = conn.execute(
                """
                SELECT id, notice_id, title, role, stage, status, payload_json, created_at, updated_at
                FROM proposal_workspaces WHERE notice_id = ?
                """,
                (notice_id,),
            ).fetchone()
        return self._proposal_from_row(row) | {"created": True}

    def update_proposal_stage(self, notice_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        notice_id = clean_text(notice_id or payload.get("noticeId"), 200)
        stage = clean_text(payload.get("stage"), 40).lower()
        if not notice_id:
            raise ValueError("noticeId is required")
        if stage not in PROPOSAL_STAGES:
            raise ValueError(f"stage must be one of: {', '.join(PROPOSAL_STAGES)}")
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT id, notice_id, title, role, stage, status, payload_json, created_at, updated_at
                FROM proposal_workspaces WHERE notice_id = ?
                """,
                (notice_id,),
            ).fetchone()
            if not existing:
                raise ValueError("proposal workspace does not exist")
            old_stage = existing["stage"]
            payload_json = json.loads(existing["payload_json"] or "{}")
            if "notes" in payload:
                payload_json["notes"] = clean_text(payload.get("notes"), 5000)
            if "nextAction" in payload:
                payload_json["nextAction"] = clean_text(payload.get("nextAction"), 1200)
            conn.execute(
                """
                UPDATE proposal_workspaces
                SET stage = ?, payload_json = ?, updated_at = ?
                WHERE notice_id = ?
                """,
                (stage, json.dumps(payload_json, sort_keys=True), now, notice_id),
            )
            if old_stage != stage:
                self._add_event(conn, notice_id, "proposal_stage_changed", old_stage, stage, f"Proposal stage changed to {PROPOSAL_STAGE_LABELS[stage]}", now)
            row = conn.execute(
                """
                SELECT id, notice_id, title, role, stage, status, payload_json, created_at, updated_at
                FROM proposal_workspaces WHERE notice_id = ?
                """,
                (notice_id,),
            ).fetchone()
        return self._proposal_from_row(row)

    def _proposal_artifact_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "noticeId": row["notice_id"],
            "artifactType": row["artifact_type"],
            "title": row["title"],
            "status": row["status"],
            "format": row["format"],
            "content": row["content"],
            "notes": row["notes"],
            "version": row["version"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def add_proposal_artifact(self, payload: dict[str, Any]) -> dict[str, Any]:
        notice_id = clean_text(payload.get("noticeId"), 200)
        if not notice_id:
            raise ValueError("noticeId is required")
        artifact_type = clean_text(payload.get("artifactType") or payload.get("artifact_type") or "outline", 60).lower()
        if artifact_type not in ARTIFACT_TYPES:
            raise ValueError(f"artifactType must be one of: {', '.join(sorted(ARTIFACT_TYPES))}")
        status = clean_text(payload.get("status") or "draft", 40).lower()
        if status not in ARTIFACT_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(ARTIFACT_STATUSES))}")
        fmt = clean_text(payload.get("format") or "markdown", 40).lower()
        if fmt not in ARTIFACT_FORMATS:
            raise ValueError(f"format must be one of: {', '.join(sorted(ARTIFACT_FORMATS))}")
        title = clean_text(payload.get("title"), 300) or artifact_type.replace("-", " ").title()
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO proposal_artifacts
                  (notice_id, artifact_type, title, status, format, content, notes, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    notice_id,
                    artifact_type,
                    title,
                    status,
                    fmt,
                    clean_text(payload.get("content"), 20000),
                    clean_text(payload.get("notes"), 5000),
                    now,
                    now,
                ),
            )
            self._add_event(conn, notice_id, "proposal_artifact_created", "", title, "Proposal artifact created", now)
            row = conn.execute("SELECT * FROM proposal_artifacts WHERE id = ?", (cur.lastrowid,)).fetchone()
        return self._proposal_artifact_from_row(row)

    def update_proposal_artifact(self, artifact_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if not artifact_id:
            raise ValueError("artifactId is required")
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute("SELECT * FROM proposal_artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if not existing:
                raise ValueError("artifact does not exist")
            artifact_type = clean_text(payload.get("artifactType") or existing["artifact_type"], 60).lower()
            if artifact_type not in ARTIFACT_TYPES:
                raise ValueError(f"artifactType must be one of: {', '.join(sorted(ARTIFACT_TYPES))}")
            status = clean_text(payload.get("status") or existing["status"], 40).lower()
            if status not in ARTIFACT_STATUSES:
                raise ValueError(f"status must be one of: {', '.join(sorted(ARTIFACT_STATUSES))}")
            fmt = clean_text(payload.get("format") or existing["format"], 40).lower()
            if fmt not in ARTIFACT_FORMATS:
                raise ValueError(f"format must be one of: {', '.join(sorted(ARTIFACT_FORMATS))}")
            content = clean_text(payload.get("content"), 20000) if "content" in payload else existing["content"]
            notes = clean_text(payload.get("notes"), 5000) if "notes" in payload else existing["notes"]
            title = clean_text(payload.get("title"), 300) if "title" in payload else existing["title"]
            changed = any(
                str(existing[key] or "") != str(value or "")
                for key, value in (("artifact_type", artifact_type), ("status", status), ("format", fmt), ("content", content), ("notes", notes), ("title", title))
            )
            version = int(existing["version"] or 1) + (1 if changed else 0)
            conn.execute(
                """
                UPDATE proposal_artifacts
                SET artifact_type = ?, title = ?, status = ?, format = ?, content = ?, notes = ?, version = ?, updated_at = ?
                WHERE id = ?
                """,
                (artifact_type, title, status, fmt, content, notes, version, now, artifact_id),
            )
            if changed:
                self._add_event(conn, existing["notice_id"], "proposal_artifact_updated", str(existing["version"]), str(version), "Proposal artifact updated", now)
            row = conn.execute("SELECT * FROM proposal_artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return self._proposal_artifact_from_row(row)

    def proposal_artifacts(self, notice_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM proposal_artifacts"
        params: tuple[Any, ...] = ()
        if notice_id:
            sql += " WHERE notice_id = ?"
            params = (notice_id,)
        sql += " ORDER BY datetime(updated_at) DESC, id DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._proposal_artifact_from_row(row) for row in rows]

    def proposal_artifact_map(self, notice_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not notice_ids:
            return {}
        placeholders = ",".join("?" for _ in notice_ids)
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM proposal_artifacts WHERE notice_id IN ({placeholders}) ORDER BY id", notice_ids).fetchall()
        mapped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            mapped.setdefault(row["notice_id"], []).append(self._proposal_artifact_from_row(row))
        return mapped

    def manual_tracked_opportunities(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT notice_id, title, url, source, payload_json, added_at
                FROM manual_tracked_opportunities
                ORDER BY added_at DESC
                """
            ).fetchall()
        opportunities: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload.setdefault("noticeId", row["notice_id"])
            payload.setdefault("title", row["title"])
            payload.setdefault("url", row["url"])
            payload.setdefault("uiLink", row["url"])
            payload["manualTracked"] = True
            payload["manualTrackedAt"] = row["added_at"]
            payload["source"] = row["source"]
            opportunities.append(payload)
        return opportunities

    def tracked_notice_ids(self) -> set[str]:
        with self.connect() as conn:
            ids = {row["notice_id"] for row in conn.execute("SELECT notice_id FROM opportunity_status")}
            ids.update(row["notice_id"] for row in conn.execute("SELECT notice_id FROM manual_tracked_opportunities"))
            ids.update(row["notice_id"] for row in conn.execute("SELECT notice_id FROM seen_opportunities"))
        return ids

    def is_tracked(self, notice_id: str) -> bool:
        return bool(notice_id and notice_id in self.tracked_notice_ids())

    def add_manual_tracked(self, opp: dict[str, Any]) -> dict[str, Any]:
        notice_id = clean_text(opp.get("noticeId") or opp.get("notice_id"), 200)
        if not notice_id:
            raise ValueError("noticeId is required")
        now = utc_now()
        with self.connect() as conn:
            duplicate = conn.execute(
                """
                SELECT notice_id FROM manual_tracked_opportunities WHERE notice_id = ?
                UNION SELECT notice_id FROM seen_opportunities WHERE notice_id = ?
                UNION SELECT notice_id FROM opportunity_status WHERE notice_id = ?
                """,
                (notice_id, notice_id, notice_id),
            ).fetchone()
            if duplicate:
                raise ValueError("already tracked")
            conn.execute(
                """
                INSERT INTO manual_tracked_opportunities (notice_id, title, url, source, payload_json, added_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    notice_id,
                    clean_text(opp.get("title"), 500),
                    clean_text(opp.get("url") or opp.get("uiLink"), 1000),
                    clean_text(opp.get("source") or "manual-search", 80),
                    json.dumps(opp, sort_keys=True),
                    now,
                ),
            )
            self._add_event(conn, notice_id, "manual_tracked", "", "manual-search", "Added from manual SAM search", now)
        return self.set_workflow(notice_id, {"status": "reviewing", "decisionReason": "Added from manual SAM search"})

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
