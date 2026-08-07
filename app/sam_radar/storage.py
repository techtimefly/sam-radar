from __future__ import annotations

import datetime as dt
import hashlib
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
EVIDENCE_STATES = {"generated", "needs-review", "verified", "rejected", "superseded"}
EVIDENCE_METHODS = {"manual", "document-intake", "ai-assisted", "imported", "legacy-snippet"}
AMENDMENT_TASK_STATUSES = {"open", "in-progress", "done", "blocked", "dismissed"}
COMPLIANCE_MANDATORY_STATES = {"mandatory", "optional", "conditional", "unknown"}
COMPLIANCE_STATUSES = {"open", "in-progress", "addressed", "not-applicable", "rejected", "merged", "split"}
COMPLIANCE_VERIFICATION_STATES = {"needs-review", "verified", "rejected"}
COMPLIANCE_PROVENANCE = {"manual", "generated", "human-edited", "merged", "split"}


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


def compact_text(value: Any, limit: int = 8000) -> str:
    if isinstance(value, list):
        value = " ".join(str(item or "") for item in value)
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def content_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def normalize_deadline(value: Any) -> str:
    raw = str(value or "").strip().replace(" ", "T", 1)
    if not raw:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC).replace(microsecond=0).isoformat()
    except ValueError:
        return compact_text(raw, 120).lower()


def normalize_code(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def normalize_label(value: Any) -> str:
    return compact_text(value, 1000).lower()


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
        conn.execute("PRAGMA foreign_keys = ON")
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
                CREATE TABLE IF NOT EXISTS evidence_citations (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL,
                  proposal_id INTEGER,
                  document_id INTEGER,
                  revision_id TEXT,
                  page_section TEXT NOT NULL DEFAULT '',
                  source_excerpt TEXT NOT NULL,
                  extracted_claim TEXT NOT NULL DEFAULT '',
                  extraction_method TEXT NOT NULL DEFAULT 'manual',
                  confidence REAL NOT NULL DEFAULT 0,
                  verification_state TEXT NOT NULL DEFAULT 'needs-review',
                  verifier TEXT NOT NULL DEFAULT '',
                  verified_at TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY(proposal_id) REFERENCES proposal_workspaces(id) ON DELETE SET NULL,
                  FOREIGN KEY(document_id) REFERENCES proposal_documents(id) ON DELETE SET NULL,
                  FOREIGN KEY(revision_id) REFERENCES opportunity_revisions(revision_id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_evidence_citations_notice
                ON evidence_citations (notice_id, verification_state, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_evidence_citations_document
                ON evidence_citations (document_id, id)
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
                CREATE TABLE IF NOT EXISTS proposal_artifact_versions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  artifact_id INTEGER NOT NULL,
                  notice_id TEXT NOT NULL,
                  artifact_type TEXT NOT NULL,
                  title TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'draft',
                  format TEXT NOT NULL DEFAULT 'markdown',
                  content TEXT NOT NULL DEFAULT '',
                  notes TEXT NOT NULL DEFAULT '',
                  version INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(artifact_id) REFERENCES proposal_artifacts(id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_proposal_artifact_versions_artifact
                ON proposal_artifact_versions (artifact_id, version DESC)
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS opportunity_revisions (
                  revision_id TEXT PRIMARY KEY,
                  notice_id TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  canonical_json TEXT NOT NULL,
                  raw_summary_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  UNIQUE(notice_id, content_hash)
                )
                """
            )
            evidence_columns = {row["name"] for row in conn.execute("PRAGMA table_info(evidence_citations)")}
            if "revision_id" not in evidence_columns:
                try:
                    conn.execute(
                        """
                        ALTER TABLE evidence_citations
                        ADD COLUMN revision_id TEXT REFERENCES opportunity_revisions(revision_id) ON DELETE SET NULL
                        """
                    )
                except sqlite3.OperationalError:
                    conn.execute("ALTER TABLE evidence_citations ADD COLUMN revision_id TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opportunity_revisions_notice
                ON opportunity_revisions (notice_id, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_evidence_citations_revision
                ON evidence_citations (notice_id, revision_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS attachment_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  revision_id TEXT NOT NULL,
                  notice_id TEXT NOT NULL,
                  attachment_key TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  UNIQUE(revision_id, attachment_key),
                  FOREIGN KEY(revision_id) REFERENCES opportunity_revisions(revision_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attachment_snapshots_notice
                ON attachment_snapshots (notice_id, attachment_key)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS amendment_changes (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL,
                  from_revision_id TEXT NOT NULL,
                  to_revision_id TEXT NOT NULL,
                  field TEXT NOT NULL,
                  change_type TEXT NOT NULL,
                  machine_type TEXT NOT NULL,
                  impact TEXT NOT NULL,
                  before_summary TEXT NOT NULL DEFAULT '',
                  after_summary TEXT NOT NULL DEFAULT '',
                  detected_at TEXT NOT NULL,
                  explanation TEXT NOT NULL DEFAULT '',
                  material INTEGER NOT NULL DEFAULT 1,
                  read_at TEXT NOT NULL DEFAULT '',
                  UNIQUE(to_revision_id, machine_type, before_summary, after_summary),
                  FOREIGN KEY(from_revision_id) REFERENCES opportunity_revisions(revision_id) ON DELETE CASCADE,
                  FOREIGN KEY(to_revision_id) REFERENCES opportunity_revisions(revision_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_amendment_changes_notice
                ON amendment_changes (notice_id, detected_at DESC, id DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS amendment_review_tasks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL,
                  revision_id TEXT NOT NULL,
                  change_id INTEGER,
                  assignee TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'open',
                  due_date TEXT NOT NULL DEFAULT '',
                  notes TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY(revision_id) REFERENCES opportunity_revisions(revision_id) ON DELETE CASCADE,
                  FOREIGN KEY(change_id) REFERENCES amendment_changes(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_amendment_review_tasks_notice
                ON amendment_review_tasks (notice_id, status, due_date, id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS compliance_requirements (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL,
                  citation_id INTEGER,
                  revision_id TEXT,
                  category TEXT NOT NULL DEFAULT 'General',
                  requirement_text TEXT NOT NULL,
                  mandatory_state TEXT NOT NULL DEFAULT 'unknown',
                  owner TEXT NOT NULL DEFAULT '',
                  due_date TEXT NOT NULL DEFAULT '',
                  response_location TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'open',
                  notes TEXT NOT NULL DEFAULT '',
                  verification_state TEXT NOT NULL DEFAULT 'needs-review',
                  verifier TEXT NOT NULL DEFAULT '',
                  verified_at TEXT NOT NULL DEFAULT '',
                  provenance TEXT NOT NULL DEFAULT 'manual',
                  generation_key TEXT NOT NULL DEFAULT '',
                  generation_metadata_json TEXT NOT NULL DEFAULT '{}',
                  human_edited INTEGER NOT NULL DEFAULT 0,
                  invalidated INTEGER NOT NULL DEFAULT 0,
                  invalidation_reason TEXT NOT NULL DEFAULT '',
                  invalidated_at TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY(citation_id) REFERENCES evidence_citations(id) ON DELETE SET NULL,
                  FOREIGN KEY(revision_id) REFERENCES opportunity_revisions(revision_id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_compliance_requirements_notice
                ON compliance_requirements (notice_id, status, mandatory_state, verification_state, id)
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_compliance_requirements_generation
                ON compliance_requirements (notice_id, generation_key)
                WHERE generation_key != ''
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS compliance_requirement_lineage (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  notice_id TEXT NOT NULL,
                  child_requirement_id INTEGER NOT NULL,
                  parent_requirement_id INTEGER NOT NULL,
                  relation TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(child_requirement_id) REFERENCES compliance_requirements(id) ON DELETE CASCADE,
                  FOREIGN KEY(parent_requirement_id) REFERENCES compliance_requirements(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_compliance_lineage_child
                ON compliance_requirement_lineage (child_requirement_id, parent_requirement_id)
                """
            )

    def _canonical_attachment(self, doc: dict[str, Any], idx: int) -> dict[str, str]:
        url = clean_text(doc.get("url") or doc.get("href") or doc.get("link") or doc.get("resourceUrl") or doc.get("source"), 1200)
        title = compact_text(doc.get("title") or doc.get("label") or doc.get("name") or doc.get("filename") or f"Attachment {idx}", 300)
        key_source = (url or f"{title}#{idx}").lower().rstrip("/")
        return {
            "key": hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:24],
            "url": url,
            "title": title,
            "type": compact_text(doc.get("type") or doc.get("contentType") or doc.get("mimeType"), 120).lower(),
            "size": str(doc.get("size") or doc.get("sizeBytes") or ""),
            "hash": clean_text(doc.get("hash") or doc.get("contentHash") or doc.get("sha256"), 160),
        }

    def _canonical_opportunity(self, opp: dict[str, Any]) -> dict[str, Any]:
        raw_attachments: list[Any] = []
        for key in ("resourceLinks", "attachments", "links", "documents"):
            value = opp.get(key)
            if isinstance(value, list):
                raw_attachments.extend(value)
        attachments = [
            self._canonical_attachment(item, idx)
            for idx, item in enumerate(raw_attachments[:50], 1)
            if isinstance(item, dict) and (item.get("url") or item.get("href") or item.get("source") or item.get("title") or item.get("label"))
        ]
        attachments.sort(key=lambda item: item["key"])
        contacts = opp.get("contacts") if isinstance(opp.get("contacts"), list) else []
        normalized_contacts = [
            {
                "name": normalize_label(item.get("name") or item.get("fullName")) if isinstance(item, dict) else normalize_label(item),
                "email": normalize_label(item.get("email")) if isinstance(item, dict) else "",
                "phone": normalize_label(item.get("phone")) if isinstance(item, dict) else "",
            }
            for item in contacts[:20]
        ]
        normalized_contacts.sort(key=lambda item: (item["email"], item["name"], item["phone"]))
        notice_type = compact_text(opp.get("type") or opp.get("noticeType"), 160)
        status = compact_text(opp.get("status") or opp.get("active") or "", 160)
        if "cancel" in notice_type.lower() or "cancel" in status.lower():
            status = "cancelled"
        return {
            "noticeId": clean_text(opp.get("noticeId") or opp.get("notice_id"), 200),
            "deadline": normalize_deadline(opp.get("responseDeadline") or opp.get("responseDeadLine") or opp.get("reponseDeadLine")),
            "status": normalize_label(status or "active"),
            "notice_type": normalize_label(notice_type),
            "set_aside": normalize_code(opp.get("setAsideCode") or opp.get("typeOfSetAside") or opp.get("setAside")),
            "naics": normalize_code(opp.get("naicsCode") or opp.get("naics")),
            "psc": normalize_code(opp.get("classificationCode") or opp.get("psc")),
            "description": normalize_label(opp.get("description") or opp.get("descriptionText") or opp.get("descriptionParagraphs")),
            "contacts": normalized_contacts,
            "attachments": attachments,
        }

    def _revision_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        canonical = json.loads(row["canonical_json"] or "{}")
        return {
            "revisionId": row["revision_id"],
            "noticeId": row["notice_id"],
            "contentHash": row["content_hash"],
            "createdAt": row["created_at"],
            "canonical": canonical,
        }

    def _summarize_value(self, field: str, value: Any) -> str:
        if field == "attachments":
            return f"{len(value or [])} attachment(s)"
        if field == "contacts":
            return f"{len(value or [])} contact(s)"
        return compact_text(value, 240)

    def _parse_normalized_deadline(self, value: Any) -> dt.datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC)

    def _deadline_change_spec(self, before: Any, after: Any) -> dict[str, str]:
        before_text = str(before or "").strip()
        after_text = str(after or "").strip()
        before_dt = self._parse_normalized_deadline(before_text)
        after_dt = self._parse_normalized_deadline(after_text)
        if not before_text and after_text:
            machine = "deadline_added"
            impact = "high"
            explanation = "Response deadline was added."
        elif before_text and not after_text:
            machine = "deadline_removed"
            impact = "high"
            explanation = "Response deadline was removed."
        elif before_dt and after_dt:
            contracted = after_dt < before_dt
            machine = "deadline_contracted" if contracted else "deadline_extended"
            impact = "critical" if contracted else "medium"
            explanation = "Response deadline moved earlier." if contracted else "Response deadline moved later."
        elif before_text != after_text:
            machine = "deadline_unparseable_changed"
            impact = "medium"
            explanation = "Response deadline changed but could not be parsed into comparable instants."
        else:
            machine = "deadline_changed"
            impact = "medium"
            explanation = "Response deadline changed."
        return {
            "field": "deadline",
            "change_type": machine,
            "machine_type": machine,
            "impact": impact,
            "before_summary": self._summarize_value("deadline", before),
            "after_summary": self._summarize_value("deadline", after),
            "explanation": explanation,
        }

    def _attachment_identity(self, item: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
        return (
            clean_text(item.get("key"), 80),
            compact_text(item.get("url"), 1200).lower().rstrip("/"),
            compact_text(item.get("title"), 300).lower(),
            clean_text(item.get("hash"), 160).lower(),
            clean_text(item.get("size"), 80),
            compact_text(item.get("type"), 120).lower(),
        )

    def _unique_attachment_pairs(
        self,
        old_items: list[dict[str, Any]],
        new_items: list[dict[str, Any]],
        attr: str,
    ) -> list[tuple[int, int]]:
        old_values: dict[str, list[int]] = {}
        new_values: dict[str, list[int]] = {}
        for idx, item in enumerate(old_items):
            value = (
                compact_text(item.get(attr), 1200).lower().rstrip("/")
                if attr == "url"
                else compact_text(item.get(attr), 300).lower()
            )
            if value:
                old_values.setdefault(value, []).append(idx)
        for idx, item in enumerate(new_items):
            value = (
                compact_text(item.get(attr), 1200).lower().rstrip("/")
                if attr == "url"
                else compact_text(item.get(attr), 300).lower()
            )
            if value:
                new_values.setdefault(value, []).append(idx)
        pairs = []
        for value in sorted(set(old_values) & set(new_values)):
            if len(old_values[value]) == 1 and len(new_values[value]) == 1:
                pairs.append((old_values[value][0], new_values[value][0]))
        return pairs

    def _match_attachments(
        self,
        old_items: list[dict[str, Any]],
        new_items: list[dict[str, Any]],
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
        matched_old: set[int] = set()
        matched_new: set[int] = set()
        pairs: list[tuple[int, int]] = []

        def add_pair(old_idx: int, new_idx: int) -> None:
            if old_idx in matched_old or new_idx in matched_new:
                return
            matched_old.add(old_idx)
            matched_new.add(new_idx)
            pairs.append((old_idx, new_idx))

        exact_old: dict[tuple[str, str, str, str, str, str], list[int]] = {}
        exact_new: dict[tuple[str, str, str, str, str, str], list[int]] = {}
        for idx, item in enumerate(old_items):
            exact_old.setdefault(self._attachment_identity(item), []).append(idx)
        for idx, item in enumerate(new_items):
            exact_new.setdefault(self._attachment_identity(item), []).append(idx)
        for identity in sorted(set(exact_old) & set(exact_new)):
            old_candidates = exact_old[identity]
            new_candidates = exact_new[identity]
            for old_idx, new_idx in zip(old_candidates, new_candidates, strict=False):
                add_pair(old_idx, new_idx)

        for attr in ("url", "title", "hash"):
            for old_idx, new_idx in self._unique_attachment_pairs(old_items, new_items, attr):
                add_pair(old_idx, new_idx)

        changed_pairs = [(old_items[old_idx], new_items[new_idx]) for old_idx, new_idx in sorted(pairs)]
        removed = [item for idx, item in enumerate(old_items) if idx not in matched_old]
        added = [item for idx, item in enumerate(new_items) if idx not in matched_new]
        return changed_pairs, removed, added

    def _change_specs(self, old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, str]]:
        specs: list[dict[str, str]] = []
        fields = ["deadline", "status", "notice_type", "set_aside", "naics", "psc", "description", "contacts"]
        for field in fields:
            before = old.get(field)
            after = new.get(field)
            if before == after:
                continue
            machine = f"{field}_changed"
            impact = "low"
            explanation = f"{field.replace('_', ' ').title()} changed."
            if field == "deadline":
                specs.append(self._deadline_change_spec(before, after))
                continue
            elif field == "status" and "cancel" in str(after):
                machine = "status_cancelled"
                impact = "critical"
                explanation = "Opportunity appears to be cancelled."
            elif field in {"set_aside", "naics"}:
                impact = "high"
                explanation = f"{field.replace('_', ' ').upper()} changed and may affect eligibility or fit."
            elif field in {"psc", "notice_type", "description"}:
                impact = "medium"
            specs.append(
                {
                    "field": field,
                    "change_type": machine,
                    "machine_type": machine,
                    "impact": impact,
                    "before_summary": self._summarize_value(field, before),
                    "after_summary": self._summarize_value(field, after),
                    "explanation": explanation,
                }
            )
        changed_attachments, removed_attachments, added_attachments = self._match_attachments(
            old.get("attachments") or [],
            new.get("attachments") or [],
        )
        for item in sorted(
            added_attachments,
            key=lambda candidate: (candidate.get("title") or "", candidate.get("url") or "", candidate.get("key") or ""),
        ):
            specs.append(
                {
                    "field": "attachments",
                    "change_type": "attachment_added",
                    "machine_type": "attachment_added",
                    "impact": "high",
                    "before_summary": "",
                    "after_summary": item.get("title") or item.get("url") or item.get("key") or "",
                    "explanation": "New attachment appeared; review for amendment instructions or changed requirements.",
                }
            )
        for item in sorted(
            removed_attachments,
            key=lambda candidate: (candidate.get("title") or "", candidate.get("url") or "", candidate.get("key") or ""),
        ):
            specs.append(
                {
                    "field": "attachments",
                    "change_type": "attachment_removed",
                    "machine_type": "attachment_removed",
                    "impact": "high",
                    "before_summary": item.get("title") or item.get("url") or item.get("key") or "",
                    "after_summary": "",
                    "explanation": "Attachment was removed or superseded.",
                }
            )
        for old_item, new_item in changed_attachments:
            if old_item != new_item:
                specs.append(
                    {
                        "field": "attachments",
                        "change_type": "attachment_changed",
                        "machine_type": "attachment_changed",
                        "impact": "medium",
                        "before_summary": old_item.get("title") or old_item.get("url") or old_item.get("key") or "",
                        "after_summary": new_item.get("title") or new_item.get("url") or new_item.get("key") or "",
                        "explanation": "Attachment metadata changed and may represent a revised document.",
                    }
                )
        return specs

    def capture_opportunity_revision(self, opp: dict[str, Any]) -> dict[str, Any]:
        canonical = self._canonical_opportunity(opp)
        notice_id = canonical["noticeId"]
        if not notice_id:
            raise ValueError("noticeId is required")
        digest = content_digest(canonical)
        revision_id = f"{notice_id}:{digest[:16]}"
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM opportunity_revisions WHERE notice_id = ? AND content_hash = ?", (notice_id, digest)).fetchone()
            if existing:
                return {"created": False, "revision": self._revision_from_row(existing), "changes": []}
            previous = conn.execute(
                """
                SELECT * FROM opportunity_revisions
                WHERE notice_id = ?
                ORDER BY datetime(created_at) DESC, rowid DESC
                LIMIT 1
                """,
                (notice_id,),
            ).fetchone()
            raw_summary = {
                "title": clean_text(opp.get("title"), 500),
                "url": clean_text(opp.get("url") or opp.get("uiLink"), 1200),
                "source": "sam.gov",
            }
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO opportunity_revisions (revision_id, notice_id, content_hash, canonical_json, raw_summary_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (revision_id, notice_id, digest, json.dumps(canonical, sort_keys=True), json.dumps(raw_summary, sort_keys=True), now),
            )
            if not cur.rowcount:
                existing = conn.execute("SELECT * FROM opportunity_revisions WHERE notice_id = ? AND content_hash = ?", (notice_id, digest)).fetchone()
                return {"created": False, "revision": self._revision_from_row(existing), "changes": []}
            for attachment in canonical["attachments"]:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO attachment_snapshots
                      (revision_id, notice_id, attachment_key, content_hash, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (revision_id, notice_id, attachment["key"], content_digest(attachment), json.dumps(attachment, sort_keys=True), now),
                )
            changes: list[dict[str, Any]] = []
            if previous:
                old = json.loads(previous["canonical_json"] or "{}")
                for spec in self._change_specs(old, canonical):
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO amendment_changes
                          (notice_id, from_revision_id, to_revision_id, field, change_type, machine_type, impact,
                           before_summary, after_summary, detected_at, explanation, material)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            notice_id,
                            previous["revision_id"],
                            revision_id,
                            spec["field"],
                            spec["change_type"],
                            spec["machine_type"],
                            spec["impact"],
                            spec["before_summary"],
                            spec["after_summary"],
                            now,
                            spec["explanation"],
                            1 if spec["impact"] in {"critical", "high", "medium"} else 0,
                        ),
                    )
                    if cur.rowcount:
                        changes.append({**spec, "id": cur.lastrowid, "noticeId": notice_id, "detectedAt": now})
                invalidating_changes = [item for item in changes if item.get("impact") in {"critical", "high", "medium"} and item.get("field") != "deadline"]
                if invalidating_changes:
                    conn.execute(
                        """
                        UPDATE compliance_requirements
                        SET invalidated = 1,
                            invalidation_reason = 'Citation predates material revision',
                            invalidated_at = CASE WHEN invalidated = 0 THEN ? ELSE invalidated_at END,
                            updated_at = ?
                        WHERE notice_id = ? AND invalidated = 0 AND citation_id IS NOT NULL
                          AND revision_id IS NOT NULL AND revision_id != ?
                        """,
                        (now, now, notice_id, revision_id),
                    )
            row = conn.execute("SELECT * FROM opportunity_revisions WHERE revision_id = ?", (revision_id,)).fetchone()
        return {"created": True, "revision": self._revision_from_row(row), "changes": changes}

    def opportunity_revisions(self, notice_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM opportunity_revisions WHERE notice_id = ? ORDER BY datetime(created_at) DESC, rowid DESC",
                (notice_id,),
            ).fetchall()
        return [self._revision_from_row(row) for row in rows]

    def _change_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "noticeId": row["notice_id"],
            "fromRevisionId": row["from_revision_id"],
            "toRevisionId": row["to_revision_id"],
            "field": row["field"],
            "changeType": row["change_type"],
            "machineType": row["machine_type"],
            "impact": row["impact"],
            "beforeSummary": row["before_summary"],
            "afterSummary": row["after_summary"],
            "detectedAt": row["detected_at"],
            "explanation": row["explanation"],
            "material": bool(row["material"]),
            "readAt": row["read_at"],
        }

    def amendment_changes(self, notice_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM amendment_changes
                WHERE notice_id = ?
                ORDER BY datetime(detected_at) DESC, id DESC
                LIMIT ?
                """,
                (notice_id, max(1, min(int(limit or 100), 500))),
            ).fetchall()
        return [self._change_from_row(row) for row in rows]

    def mark_amendment_changes_reviewed(self, notice_id: str, change_ids: list[int] | None = None) -> dict[str, Any]:
        notice_id = clean_text(notice_id, 200)
        if not notice_id:
            raise ValueError("noticeId is required")
        now = utc_now()
        with self.connect() as conn:
            if change_ids is None:
                cur = conn.execute(
                    """
                    UPDATE amendment_changes
                    SET read_at = ?
                    WHERE notice_id = ? AND material = 1 AND read_at = ''
                    """,
                    (now, notice_id),
                )
            else:
                ids = [int(item) for item in change_ids]
                if not ids:
                    raise ValueError("changeIds must not be empty")
                placeholders = ",".join("?" for _ in ids)
                rows = conn.execute(
                    f"SELECT id, notice_id FROM amendment_changes WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()
                if len(rows) != len(set(ids)):
                    raise ValueError("changeId does not exist")
                if any(row["notice_id"] != notice_id for row in rows):
                    raise ValueError("changeId does not belong to noticeId")
                cur = conn.execute(
                    f"""
                    UPDATE amendment_changes
                    SET read_at = ?
                    WHERE notice_id = ? AND material = 1 AND read_at = '' AND id IN ({placeholders})
                    """,
                    [now, notice_id, *ids],
                )
        return {"noticeId": notice_id, "reviewedCount": cur.rowcount, "readAt": now, "summary": self.amendment_summary(notice_id), "changes": self.amendment_changes(notice_id)}

    def _attachment_document_tokens(self, row: sqlite3.Row) -> set[str]:
        tokens = set()
        values = dict(row)
        for column in ("document_source", "document_label", "document_filename"):
            value = compact_text(values.get(column, ""), 1200).lower().rstrip("/")
            if value:
                tokens.add(value)
        return tokens

    def _document_matches_attachment(self, document_tokens: set[str], attachment: dict[str, Any]) -> bool:
        if not document_tokens:
            return False
        attachment_tokens = {
            compact_text(attachment.get("url"), 1200).lower().rstrip("/"),
            compact_text(attachment.get("title"), 300).lower(),
        }
        attachment_tokens = {token for token in attachment_tokens if token}
        return bool(document_tokens & attachment_tokens)

    def _citation_attachment_removed(self, row: sqlite3.Row, revisions: list[dict[str, Any]], latest_revision_id: str) -> bool:
        document_tokens = self._attachment_document_tokens(row)
        if not document_tokens:
            return False
        latest = next((item for item in revisions if item["revisionId"] == latest_revision_id), None)
        latest_attachments = (latest or {}).get("canonical", {}).get("attachments") or []
        latest_keys = {item.get("key") for item in latest_attachments}
        for revision in revisions:
            for attachment in revision.get("canonical", {}).get("attachments") or []:
                if not self._document_matches_attachment(document_tokens, attachment):
                    continue
                if attachment.get("key") not in latest_keys:
                    return True
        return False

    def stale_evidence_warnings(self, notice_id: str) -> dict[str, Any]:
        revisions = list(reversed(self.opportunity_revisions(notice_id)))
        latest = revisions[-1]["revisionId"] if revisions else ""
        revision_index = {item["revisionId"]: idx for idx, item in enumerate(revisions)}
        changes = self.amendment_changes(notice_id, limit=500)
        with self.connect() as conn:
            citations = conn.execute(
                """
                SELECT c.id, c.revision_id, c.document_id, c.page_section,
                       d.source AS document_source, d.label AS document_label, d.filename AS document_filename
                FROM evidence_citations c
                LEFT JOIN proposal_documents d ON d.id = c.document_id
                WHERE c.notice_id = ? AND c.verification_state != 'superseded'
                """,
                (notice_id,),
            ).fetchall()
        items = []
        for row in citations:
            reasons = []
            rev = row["revision_id"]
            attachment_removed = self._citation_attachment_removed(row, revisions, latest)
            if latest and rev and rev != latest:
                cited_idx = revision_index.get(rev, -1)
                predating_changes = [
                    change
                    for change in changes
                    if revision_index.get(change["toRevisionId"], 10**6) > cited_idx and change["material"]
                ]
                if any(change["field"] != "attachments" or change["machineType"] != "attachment_removed" or attachment_removed for change in predating_changes):
                    reasons.append("Citation predates a material revision")
            elif latest and not rev and changes:
                reasons.append("Citation is not tied to the latest immutable revision")
            if attachment_removed:
                reasons.append("cited document may have been removed or superseded")
            if reasons:
                items.append({"citationId": row["id"], "revisionId": rev, "reason": "; ".join(reasons), "pageSection": row["page_section"]})
        return {"count": len(items), "items": items}

    def amendment_summary(self, notice_id: str) -> dict[str, Any]:
        changes = self.amendment_changes(notice_id)
        stale = self.stale_evidence_warnings(notice_id)
        return {
            "revisionCount": len(self.opportunity_revisions(notice_id)),
            "changeCount": len(changes),
            "materialChangeCount": sum(1 for item in changes if item["material"]),
            "unreadCount": sum(1 for item in changes if item["material"] and not item["readAt"]),
            "staleEvidenceCount": stale["count"],
        }

    def amendment_timeline(self, notice_id: str) -> dict[str, Any]:
        return {
            "noticeId": notice_id,
            "summary": self.amendment_summary(notice_id),
            "revisions": self.opportunity_revisions(notice_id),
            "changes": self.amendment_changes(notice_id),
            "staleEvidenceWarnings": self.stale_evidence_warnings(notice_id),
            "tasks": self.amendment_tasks(notice_id),
        }

    def _task_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "noticeId": row["notice_id"],
            "revisionId": row["revision_id"],
            "changeId": row["change_id"],
            "assignee": row["assignee"],
            "status": row["status"],
            "dueDate": row["due_date"],
            "notes": row["notes"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _validate_amendment_task_refs(self, conn: sqlite3.Connection, notice_id: str, revision_id: str, change_id: int | None = None) -> None:
        revision = conn.execute("SELECT notice_id FROM opportunity_revisions WHERE revision_id = ?", (revision_id,)).fetchone()
        if not revision:
            raise ValueError("revisionId does not exist")
        if revision["notice_id"] != notice_id:
            raise ValueError("revisionId does not belong to noticeId")
        if change_id:
            change = conn.execute("SELECT notice_id, to_revision_id FROM amendment_changes WHERE id = ?", (change_id,)).fetchone()
            if not change:
                raise ValueError("changeId does not exist")
            if change["notice_id"] != notice_id or change["to_revision_id"] != revision_id:
                raise ValueError("changeId does not belong to noticeId/revisionId")

    def create_amendment_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        notice_id = clean_text(payload.get("noticeId") or payload.get("notice_id"), 200)
        revision_id = clean_text(payload.get("revisionId") or payload.get("revision_id"), 260)
        if not notice_id or not revision_id:
            raise ValueError("noticeId and revisionId are required")
        status = clean_text(payload.get("status") or "open", 40).lower()
        if status not in AMENDMENT_TASK_STATUSES:
            raise ValueError("status is invalid")
        change_id = payload.get("changeId") or payload.get("change_id")
        change_id = int(change_id) if change_id not in (None, "") else None
        now = utc_now()
        with self.connect() as conn:
            self._validate_amendment_task_refs(conn, notice_id, revision_id, change_id)
            cur = conn.execute(
                """
                INSERT INTO amendment_review_tasks
                  (notice_id, revision_id, change_id, assignee, status, due_date, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notice_id,
                    revision_id,
                    change_id,
                    clean_text(payload.get("assignee"), 200),
                    status,
                    clean_text(payload.get("dueDate") or payload.get("due_date"), 40),
                    clean_text(payload.get("notes"), 3000),
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM amendment_review_tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return self._task_from_row(row)

    def update_amendment_task(self, task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute("SELECT * FROM amendment_review_tasks WHERE id = ?", (int(task_id),)).fetchone()
            if not existing:
                raise ValueError("task does not exist")
            notice_id = clean_text(payload.get("noticeId") or payload.get("notice_id") or existing["notice_id"], 200)
            if notice_id != existing["notice_id"]:
                raise ValueError("task does not belong to noticeId")
            revision_id = clean_text(payload.get("revisionId") or payload.get("revision_id") or existing["revision_id"], 260)
            change_id = payload.get("changeId") or payload.get("change_id")
            change_id = int(change_id) if change_id not in (None, "") else existing["change_id"]
            self._validate_amendment_task_refs(conn, notice_id, revision_id, change_id)
            status = clean_text(payload.get("status") or existing["status"], 40).lower()
            if status not in AMENDMENT_TASK_STATUSES:
                raise ValueError("status is invalid")
            conn.execute(
                """
                UPDATE amendment_review_tasks
                SET revision_id = ?, change_id = ?, assignee = ?, status = ?, due_date = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    revision_id,
                    change_id,
                    clean_text(payload.get("assignee"), 200) if "assignee" in payload else existing["assignee"],
                    status,
                    clean_text(payload.get("dueDate") or payload.get("due_date"), 40) if ("dueDate" in payload or "due_date" in payload) else existing["due_date"],
                    clean_text(payload.get("notes"), 3000) if "notes" in payload else existing["notes"],
                    now,
                    int(task_id),
                ),
            )
            row = conn.execute("SELECT * FROM amendment_review_tasks WHERE id = ?", (int(task_id),)).fetchone()
        return self._task_from_row(row)

    def delete_amendment_task(self, task_id: int, notice_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM amendment_review_tasks WHERE id = ?", (int(task_id),)).fetchone()
            if not row:
                raise ValueError("task does not exist")
            if row["notice_id"] != notice_id:
                raise ValueError("task does not belong to noticeId")
            task = self._task_from_row(row)
            conn.execute("DELETE FROM amendment_review_tasks WHERE id = ?", (int(task_id),))
        return task

    def amendment_tasks(self, notice_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM amendment_review_tasks WHERE notice_id = ? ORDER BY status, due_date, id",
                (notice_id,),
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def _validate_evidence_payload(self, payload: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
        notice_id = clean_text(payload.get("noticeId") or payload.get("notice_id"), 200)
        if not partial and not notice_id:
            raise ValueError("noticeId is required")
        state = clean_text(payload.get("verificationState") or payload.get("verification_state") or "needs-review", 40).lower()
        if state not in EVIDENCE_STATES:
            raise ValueError(f"verificationState must be one of {', '.join(sorted(EVIDENCE_STATES))}")
        method = clean_text(payload.get("extractionMethod") or payload.get("extraction_method") or "manual", 40).lower()
        if method not in EVIDENCE_METHODS:
            raise ValueError(f"extractionMethod must be one of {', '.join(sorted(EVIDENCE_METHODS))}")
        try:
            confidence = float(payload.get("confidence", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("confidence must be a number from 0 to 1") from exc
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be from 0 to 1")
        excerpt = clean_text(payload.get("sourceExcerpt") or payload.get("source_excerpt") or payload.get("snippet"), 4000)
        if not partial and not excerpt:
            raise ValueError("sourceExcerpt is required")
        proposal_id = payload.get("proposalId") or payload.get("proposal_id")
        document_id = payload.get("documentId") or payload.get("document_id")
        return {
            "notice_id": notice_id,
            "proposal_id": int(proposal_id) if proposal_id not in (None, "") else None,
            "document_id": int(document_id) if document_id not in (None, "") else None,
            "revision_id": clean_text(payload.get("revisionId") or payload.get("revision_id"), 160) or None,
            "page_section": clean_text(payload.get("pageSection") or payload.get("page_section") or payload.get("section"), 300),
            "source_excerpt": excerpt,
            "extracted_claim": clean_text(payload.get("extractedClaim") or payload.get("extracted_claim"), 2000),
            "extraction_method": method,
            "confidence": confidence,
            "verification_state": state,
            "verifier": clean_text(payload.get("verifier"), 200),
        }

    def _validate_evidence_references(self, conn: sqlite3.Connection, values: dict[str, Any]) -> None:
        if values["revision_id"] is not None:
            revision = conn.execute("SELECT notice_id FROM opportunity_revisions WHERE revision_id = ?", (values["revision_id"],)).fetchone()
            if not revision:
                raise ValueError("revisionId does not exist")
            if revision["notice_id"] != values["notice_id"]:
                raise ValueError("revisionId does not belong to noticeId")
        if values["proposal_id"] is not None:
            proposal = conn.execute("SELECT notice_id FROM proposal_workspaces WHERE id = ?", (values["proposal_id"],)).fetchone()
            if not proposal:
                raise ValueError("proposalId does not exist")
            if proposal["notice_id"] != values["notice_id"]:
                raise ValueError("proposalId does not belong to noticeId")
        if values["document_id"] is not None:
            document = conn.execute("SELECT notice_id FROM proposal_documents WHERE id = ?", (values["document_id"],)).fetchone()
            if not document:
                raise ValueError("documentId does not exist")
            if document["notice_id"] != values["notice_id"]:
                raise ValueError("documentId does not belong to noticeId")

    def _evidence_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "noticeId": row["notice_id"],
            "proposalId": row["proposal_id"],
            "documentId": row["document_id"],
            "revisionId": row["revision_id"],
            "pageSection": row["page_section"],
            "section": row["page_section"],
            "sourceExcerpt": row["source_excerpt"],
            "snippet": row["source_excerpt"],
            "extractedClaim": row["extracted_claim"],
            "extractionMethod": row["extraction_method"],
            "confidence": row["confidence"],
            "verificationState": row["verification_state"],
            "reviewed": row["verification_state"] == "verified",
            "verifier": row["verifier"],
            "verifiedAt": row["verified_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "legacySnippet": False,
        }

    def _json_metadata(self, value: Any) -> str:
        if isinstance(value, str):
            try:
                parsed = json.loads(value or "{}")
            except json.JSONDecodeError:
                parsed = {"value": clean_text(value, 2000)}
        elif isinstance(value, dict):
            parsed = value
        else:
            parsed = {}
        return json.dumps(parsed, sort_keys=True)

    def _validate_compliance_refs(self, conn: sqlite3.Connection, values: dict[str, Any]) -> None:
        if values["citation_id"] is not None:
            citation = conn.execute("SELECT notice_id FROM evidence_citations WHERE id = ?", (values["citation_id"],)).fetchone()
            if not citation:
                raise ValueError("citationId does not exist")
            if citation["notice_id"] != values["notice_id"]:
                raise ValueError("citationId does not belong to noticeId")
        if values["revision_id"]:
            revision = conn.execute("SELECT notice_id FROM opportunity_revisions WHERE revision_id = ?", (values["revision_id"],)).fetchone()
            if not revision:
                raise ValueError("revisionId does not exist")
            if revision["notice_id"] != values["notice_id"]:
                raise ValueError("revisionId does not belong to noticeId")

    def _insert_compliance_requirement(self, conn: sqlite3.Connection, values: dict[str, Any], now: str) -> int:
        self._validate_compliance_refs(conn, values)
        conn.execute(
            """
            INSERT INTO compliance_requirements (
              notice_id, citation_id, revision_id, category, requirement_text, mandatory_state,
              owner, due_date, response_location, status, notes, verification_state, verifier,
              verified_at, provenance, generation_key, generation_metadata_json, human_edited,
              invalidated, invalidation_reason, invalidated_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["notice_id"],
                values["citation_id"],
                values["revision_id"],
                values["category"],
                values["requirement_text"],
                values["mandatory_state"],
                values["owner"],
                values["due_date"],
                values["response_location"],
                values["status"],
                values["notes"],
                values["verification_state"],
                values["verifier"],
                now if values["verification_state"] == "verified" else "",
                values["provenance"],
                values["generation_key"],
                values["generation_metadata_json"],
                values["human_edited"],
                values["invalidated"],
                values["invalidation_reason"],
                now if values["invalidated"] else "",
                now,
                now,
            ),
        )
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def _validate_compliance_payload(self, payload: dict[str, Any], existing: sqlite3.Row | None = None) -> dict[str, Any]:
        payload_notice_id = clean_text(payload.get("noticeId") or payload.get("notice_id"), 200)
        if existing and not payload_notice_id:
            raise ValueError("noticeId is required")
        notice_id = payload_notice_id or clean_text(existing["notice_id"] if existing else "", 200)
        if not notice_id:
            raise ValueError("noticeId is required")
        if existing and notice_id != existing["notice_id"]:
            raise ValueError("noticeId does not match requirement")
        citation_raw = payload.get("citationId", payload.get("citation_id", existing["citation_id"] if existing else None))
        revision_id = clean_text(payload.get("revisionId") or payload.get("revision_id") or (existing["revision_id"] if existing else ""), 240)
        category = clean_text(payload.get("category", existing["category"] if existing else "General"), 160) or "General"
        requirement_text = clean_text(payload.get("requirementText") or payload.get("requirement_text") or (existing["requirement_text"] if existing else ""), 5000)
        if not requirement_text:
            raise ValueError("requirementText is required")
        mandatory_state = clean_text(payload.get("mandatoryState") or payload.get("mandatory_state") or (existing["mandatory_state"] if existing else "unknown"), 40).lower()
        if mandatory_state not in COMPLIANCE_MANDATORY_STATES:
            raise ValueError(f"mandatoryState must be one of: {', '.join(sorted(COMPLIANCE_MANDATORY_STATES))}")
        status = clean_text(payload.get("status", existing["status"] if existing else "open"), 60).lower()
        if status not in COMPLIANCE_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(COMPLIANCE_STATUSES))}")
        verification_state = clean_text(payload.get("verificationState") or payload.get("verification_state") or (existing["verification_state"] if existing else "needs-review"), 60).lower()
        if verification_state not in COMPLIANCE_VERIFICATION_STATES:
            raise ValueError(f"verificationState must be one of: {', '.join(sorted(COMPLIANCE_VERIFICATION_STATES))}")
        provenance = clean_text(payload.get("provenance", existing["provenance"] if existing else "manual"), 60).lower()
        if provenance not in COMPLIANCE_PROVENANCE:
            provenance = "manual"
        return {
            "notice_id": notice_id,
            "citation_id": int(citation_raw) if citation_raw not in (None, "") else None,
            "revision_id": revision_id or None,
            "category": category,
            "requirement_text": requirement_text,
            "mandatory_state": mandatory_state,
            "owner": clean_text(payload.get("owner", existing["owner"] if existing else ""), 160),
            "due_date": clean_text(payload.get("dueDate") or payload.get("due_date") or (existing["due_date"] if existing else ""), 80),
            "response_location": clean_text(payload.get("responseLocation") or payload.get("response_location") or (existing["response_location"] if existing else ""), 300),
            "status": status,
            "notes": clean_text(payload.get("notes", existing["notes"] if existing else ""), 5000),
            "verification_state": verification_state,
            "verifier": clean_text(payload.get("verifier", existing["verifier"] if existing else ""), 160),
            "provenance": provenance,
            "generation_key": clean_text(payload.get("generationKey") or payload.get("generation_key") or (existing["generation_key"] if existing else ""), 128),
            "generation_metadata_json": self._json_metadata(payload.get("generationMetadata") or payload.get("generation_metadata") or (existing["generation_metadata_json"] if existing else {})),
            "human_edited": 1 if payload.get("humanEdited", existing["human_edited"] if existing else False) else 0,
            "invalidated": 1 if payload.get("invalidated", existing["invalidated"] if existing else False) else 0,
            "invalidation_reason": clean_text(payload.get("invalidationReason") or payload.get("invalidation_reason") or (existing["invalidation_reason"] if existing else ""), 1000),
        }

    def _compliance_from_row(self, row: sqlite3.Row, parent_ids: list[int] | None = None) -> dict[str, Any]:
        try:
            metadata = json.loads(row["generation_metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        return {
            "id": row["id"],
            "noticeId": row["notice_id"],
            "citationId": row["citation_id"],
            "revisionId": row["revision_id"],
            "category": row["category"],
            "requirementText": row["requirement_text"],
            "mandatoryState": row["mandatory_state"],
            "owner": row["owner"],
            "dueDate": row["due_date"],
            "responseLocation": row["response_location"],
            "status": row["status"],
            "notes": row["notes"],
            "verificationState": row["verification_state"],
            "verifier": row["verifier"],
            "verifiedAt": row["verified_at"],
            "provenance": row["provenance"],
            "generationKey": row["generation_key"],
            "generationMetadata": metadata,
            "humanEdited": bool(row["human_edited"]),
            "invalidated": bool(row["invalidated"]),
            "invalidationReason": row["invalidation_reason"],
            "invalidatedAt": row["invalidated_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "parentRequirementIds": parent_ids or [],
        }

    def _lineage_map(self, conn: sqlite3.Connection, requirement_ids: list[int]) -> dict[int, list[int]]:
        if not requirement_ids:
            return {}
        placeholders = ",".join("?" for _ in requirement_ids)
        rows = conn.execute(
            f"SELECT child_requirement_id, parent_requirement_id FROM compliance_requirement_lineage WHERE child_requirement_id IN ({placeholders}) ORDER BY id",
            requirement_ids,
        ).fetchall()
        mapped: dict[int, list[int]] = {}
        for row in rows:
            mapped.setdefault(row["child_requirement_id"], []).append(row["parent_requirement_id"])
        return mapped

    def compliance_requirement(self, requirement_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM compliance_requirements WHERE id = ?", (int(requirement_id),)).fetchone()
            if not row:
                return None
            lineage = self._lineage_map(conn, [int(requirement_id)])
        return self._compliance_from_row(row, lineage.get(int(requirement_id), []))

    def compliance_requirements(self, notice_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM compliance_requirements
                WHERE notice_id = ?
                ORDER BY category COLLATE NOCASE, mandatory_state, status, id
                """,
                (notice_id,),
            ).fetchall()
            lineage = self._lineage_map(conn, [row["id"] for row in rows])
        return [self._compliance_from_row(row, lineage.get(row["id"], [])) for row in rows]

    def create_compliance_requirement(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = self._validate_compliance_payload(payload)
        now = utc_now()
        with self.connect() as conn:
            requirement_id = self._insert_compliance_requirement(conn, values, now)
            self._add_event(conn, values["notice_id"], "compliance_created", "", values["status"], "Compliance requirement created", now)
        return self.compliance_requirement(requirement_id) or {}

    def update_compliance_requirement(self, requirement_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute("SELECT * FROM compliance_requirements WHERE id = ?", (int(requirement_id),)).fetchone()
            if not existing:
                raise ValueError("requirement does not exist")
            values = self._validate_compliance_payload(payload, existing)
            self._validate_compliance_refs(conn, values)
            verified_at = existing["verified_at"]
            if values["verification_state"] == "verified" and existing["verification_state"] != "verified":
                verified_at = now
            elif values["verification_state"] != "verified":
                verified_at = ""
            human_fields = {"category", "requirementText", "requirement_text", "mandatoryState", "mandatory_state", "owner", "dueDate", "due_date", "responseLocation", "response_location", "status", "notes"}
            human_edited = 1 if existing["human_edited"] or any(key in payload for key in human_fields) else 0
            invalidated_at = existing["invalidated_at"]
            if values["invalidated"] and not existing["invalidated"]:
                invalidated_at = now
            elif not values["invalidated"]:
                invalidated_at = ""
            conn.execute(
                """
                UPDATE compliance_requirements
                SET citation_id = ?, revision_id = ?, category = ?, requirement_text = ?, mandatory_state = ?,
                    owner = ?, due_date = ?, response_location = ?, status = ?, notes = ?,
                    verification_state = ?, verifier = ?, verified_at = ?, provenance = ?,
                    generation_key = ?, generation_metadata_json = ?, human_edited = ?, invalidated = ?,
                    invalidation_reason = ?, invalidated_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    values["citation_id"],
                    values["revision_id"],
                    values["category"],
                    values["requirement_text"],
                    values["mandatory_state"],
                    values["owner"],
                    values["due_date"],
                    values["response_location"],
                    values["status"],
                    values["notes"],
                    values["verification_state"],
                    values["verifier"],
                    verified_at,
                    values["provenance"],
                    values["generation_key"],
                    values["generation_metadata_json"],
                    human_edited,
                    values["invalidated"],
                    values["invalidation_reason"],
                    invalidated_at,
                    now,
                    int(requirement_id),
                ),
            )
            self._add_event(conn, existing["notice_id"], "compliance_updated", existing["status"], values["status"], "Compliance requirement updated", now)
        return self.compliance_requirement(requirement_id) or {}

    def verify_compliance_requirement(self, requirement_id: int, state: str, verifier: str = "") -> dict[str, Any]:
        existing = self.compliance_requirement(requirement_id)
        if not existing:
            raise ValueError("requirement does not exist")
        return self.update_compliance_requirement(requirement_id, {"noticeId": existing["noticeId"], "verificationState": state, "verifier": verifier})

    def verify_compliance_requirement_for_notice(self, requirement_id: int, notice_id: str, state: str, verifier: str = "") -> dict[str, Any]:
        existing = self.compliance_requirement(requirement_id)
        if not existing:
            raise ValueError("requirement does not exist")
        if existing["noticeId"] != notice_id:
            raise ValueError("requirementId does not belong to noticeId")
        return self.update_compliance_requirement(requirement_id, {"noticeId": notice_id, "verificationState": state, "verifier": verifier})

    def reject_compliance_requirement(self, requirement_id: int, notice_id: str) -> dict[str, Any]:
        existing = self.compliance_requirement(requirement_id)
        if not existing:
            raise ValueError("requirement does not exist")
        if existing["noticeId"] != notice_id:
            raise ValueError("requirementId does not belong to noticeId")
        return self.update_compliance_requirement(requirement_id, {"noticeId": notice_id, "status": "rejected", "verificationState": "rejected"})

    def _generated_requirement_from_citation(self, citation: sqlite3.Row) -> dict[str, Any] | None:
        text = compact_text(citation["extracted_claim"] or citation["source_excerpt"], 1200)
        if not text:
            return None
        lower = text.lower()
        if not any(token in lower for token in ("shall", "must", "required", "requirement", "submit", "provide", "deliver", "comply")):
            return None
        category = "Submission" if any(token in lower for token in ("submit", "proposal", "volume")) else "Performance"
        if "report" in lower:
            category = "Reporting"
        if any(token in lower for token in ("quality", "management plan", "staffing")):
            category = "Management"
        mandatory = "mandatory" if any(token in lower for token in ("shall", "must", "required")) else "conditional"
        normalized_text = compact_text(text, 1200).casefold()
        generation_key = hashlib.sha256(f"citation-requirement:{normalized_text}".encode()).hexdigest()
        return {
            "noticeId": citation["notice_id"],
            "citationId": citation["id"],
            "revisionId": citation["revision_id"],
            "category": category,
            "requirementText": text,
            "mandatoryState": mandatory,
            "status": "open",
            "verificationState": "needs-review",
            "provenance": "generated",
            "generationKey": generation_key,
            "generationMetadata": {
                "method": "deterministic-citation",
                "citationId": citation["id"],
                "pageSection": citation["page_section"],
            },
        }

    def generate_compliance_requirements(self, notice_id: str) -> dict[str, Any]:
        notice_id = clean_text(notice_id, 200)
        now = utc_now()
        created = 0
        updated = 0
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM evidence_citations
                WHERE notice_id = ? AND verification_state NOT IN ('rejected', 'superseded')
                ORDER BY document_id, page_section COLLATE NOCASE, id
                """,
                (notice_id,),
            ).fetchall()
            generated: dict[str, dict[str, Any]] = {}
            for citation in rows:
                payload = self._generated_requirement_from_citation(citation)
                if not payload:
                    continue
                values = self._validate_compliance_payload(payload)
                generated.setdefault(values["generation_key"], values)
            for values in generated.values():
                existing = conn.execute(
                    "SELECT * FROM compliance_requirements WHERE notice_id = ? AND generation_key = ?",
                    (notice_id, values["generation_key"]),
                ).fetchone()
                if existing:
                    if not existing["human_edited"]:
                        changed = (
                            existing["citation_id"] != values["citation_id"]
                            or existing["revision_id"] != values["revision_id"]
                            or existing["category"] != values["category"]
                            or existing["requirement_text"] != values["requirement_text"]
                            or existing["mandatory_state"] != values["mandatory_state"]
                            or existing["generation_metadata_json"] != values["generation_metadata_json"]
                            or existing["invalidated"]
                            or existing["invalidation_reason"]
                            or existing["invalidated_at"]
                        )
                        if changed:
                            conn.execute(
                                """
                                UPDATE compliance_requirements
                                SET citation_id = ?, revision_id = ?, category = ?, requirement_text = ?,
                                    mandatory_state = ?, generation_metadata_json = ?, invalidated = 0,
                                    invalidation_reason = '', invalidated_at = '', updated_at = ?
                                WHERE id = ?
                                """,
                                (
                                    values["citation_id"],
                                    values["revision_id"],
                                    values["category"],
                                    values["requirement_text"],
                                    values["mandatory_state"],
                                    values["generation_metadata_json"],
                                    now,
                                    existing["id"],
                                ),
                            )
                            updated += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO compliance_requirements (
                      notice_id, citation_id, revision_id, category, requirement_text, mandatory_state,
                      status, verification_state, provenance, generation_key, generation_metadata_json,
                      created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'open', 'needs-review', 'generated', ?, ?, ?, ?)
                    """,
                    (
                        values["notice_id"],
                        values["citation_id"],
                        values["revision_id"],
                        values["category"],
                        values["requirement_text"],
                        values["mandatory_state"],
                        values["generation_key"],
                        values["generation_metadata_json"],
                        now,
                        now,
                    ),
                )
                created += 1
            self._invalidate_compliance_for_stale_citations(conn, notice_id, now)
        return {"ok": True, "noticeId": notice_id, "createdCount": created, "updatedCount": updated, "requirements": self.compliance_requirements(notice_id)}

    def _invalidate_compliance_for_stale_citations(self, conn: sqlite3.Connection, notice_id: str, now: str) -> None:
        stale_ids = {int(item["citationId"]) for item in self.stale_evidence_warnings(notice_id).get("items", []) if item.get("citationId")}
        if not stale_ids:
            return
        placeholders = ",".join("?" for _ in stale_ids)
        conn.execute(
            f"""
            UPDATE compliance_requirements
            SET invalidated = 1,
                invalidation_reason = 'Citation predates material revision or stale source',
                invalidated_at = CASE WHEN invalidated = 0 THEN ? ELSE invalidated_at END,
                updated_at = ?
            WHERE notice_id = ? AND citation_id IN ({placeholders}) AND invalidated = 0
            """,
            [now, now, notice_id, *stale_ids],
        )

    def merge_compliance_requirements(self, notice_id: str, requirement_ids: list[int], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        notice_id = clean_text(notice_id, 200)
        ids = [int(item) for item in requirement_ids]
        if len(set(ids)) < 2:
            raise ValueError("at least two requirementIds are required")
        payload = payload or {}
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(f"SELECT * FROM compliance_requirements WHERE id IN ({placeholders}) ORDER BY id", ids).fetchall()
            if len(rows) != len(set(ids)):
                raise ValueError("requirementId does not exist")
            if any(row["notice_id"] != notice_id for row in rows):
                raise ValueError("requirementId does not belong to noticeId")
            merged_text = clean_text(payload.get("requirementText"), 5000) or " / ".join(row["requirement_text"] for row in rows)
            values = self._validate_compliance_payload(
                {
                    "noticeId": notice_id,
                    "citationId": rows[0]["citation_id"],
                    "revisionId": rows[0]["revision_id"],
                    "category": clean_text(payload.get("category"), 160) or rows[0]["category"],
                    "requirementText": merged_text,
                    "mandatoryState": rows[0]["mandatory_state"],
                    "status": "open",
                    "provenance": "merged",
                    "humanEdited": True,
                    "generationMetadata": {"mergedRequirementIds": ids},
                }
            )
            merged_id = self._insert_compliance_requirement(conn, values, now)
            self._add_event(conn, notice_id, "compliance_created", "", values["status"], "Compliance requirement created", now)
            for parent_id in ids:
                conn.execute(
                    """
                    INSERT INTO compliance_requirement_lineage (notice_id, child_requirement_id, parent_requirement_id, relation, created_at)
                    VALUES (?, ?, ?, 'merge', ?)
                    """,
                    (notice_id, merged_id, parent_id, now),
                )
                conn.execute("UPDATE compliance_requirements SET status = 'merged', updated_at = ? WHERE id = ?", (now, parent_id))
            row = conn.execute("SELECT * FROM compliance_requirements WHERE id = ?", (merged_id,)).fetchone()
            lineage = self._lineage_map(conn, [merged_id])
            requirement = self._compliance_from_row(row, lineage.get(merged_id, []))
        return {"ok": True, "requirement": requirement, "requirements": self.compliance_requirements(notice_id)}

    def split_compliance_requirement(self, notice_id: str, requirement_id: int, parts: list[dict[str, Any]]) -> dict[str, Any]:
        notice_id = clean_text(notice_id, 200)
        if len(parts) < 2:
            raise ValueError("at least two split parts are required")
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            parent_row = conn.execute("SELECT * FROM compliance_requirements WHERE id = ?", (int(requirement_id),)).fetchone()
            if not parent_row:
                raise ValueError("requirement does not exist")
            if parent_row["notice_id"] != notice_id:
                raise ValueError("requirementId does not belong to noticeId")
            values_list = []
            for part in parts:
                values = self._validate_compliance_payload(
                    {
                        "noticeId": notice_id,
                        "citationId": part.get("citationId", parent_row["citation_id"]),
                        "revisionId": part.get("revisionId", parent_row["revision_id"]),
                        "category": part.get("category", parent_row["category"]),
                        "requirementText": part.get("requirementText") or part.get("requirement_text"),
                        "mandatoryState": part.get("mandatoryState", parent_row["mandatory_state"]),
                        "status": part.get("status", "open"),
                        "provenance": "split",
                        "humanEdited": True,
                        "generationMetadata": {"splitFromRequirementId": int(requirement_id)},
                    }
                )
                self._validate_compliance_refs(conn, values)
                values_list.append(values)
            created_ids = []
            for values in values_list:
                child_id = self._insert_compliance_requirement(conn, values, now)
                created_ids.append(child_id)
                self._add_event(conn, notice_id, "compliance_created", "", values["status"], "Compliance requirement created", now)
                conn.execute(
                    """
                    INSERT INTO compliance_requirement_lineage (notice_id, child_requirement_id, parent_requirement_id, relation, created_at)
                    VALUES (?, ?, ?, 'split', ?)
                    """,
                    (notice_id, child_id, int(requirement_id), now),
                )
            conn.execute("UPDATE compliance_requirements SET status = 'split', updated_at = ? WHERE id = ?", (now, int(requirement_id)))
            self._add_event(conn, notice_id, "compliance_updated", parent_row["status"], "split", "Compliance requirement updated", now)
            placeholders = ",".join("?" for _ in created_ids)
            rows = conn.execute(f"SELECT * FROM compliance_requirements WHERE id IN ({placeholders}) ORDER BY id", created_ids).fetchall()
            lineage = self._lineage_map(conn, created_ids)
            created = [self._compliance_from_row(row, lineage.get(row["id"], [])) for row in rows]
        return {"ok": True, "requirements": created, "items": self.compliance_requirements(notice_id)}

    def export_compliance_csv(self, notice_id: str, requirements: list[dict[str, Any]] | None = None) -> str:
        import csv
        import io

        def safe_cell(value: Any) -> Any:
            if value is None:
                return ""
            text = str(value)
            return f"'{text}" if text[:1] in {"=", "+", "-", "@", "\t", "\r"} else text

        output = io.StringIO(newline="")
        fields = [
            "notice_id",
            "requirement_id",
            "category",
            "mandatory_state",
            "status",
            "verification_state",
            "owner",
            "due_date",
            "response_location",
            "requirement_text",
            "citation_id",
            "revision_id",
            "source_trace",
            "invalidated",
            "invalidation_reason",
            "notes",
        ]
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\r\n")
        writer.writeheader()
        for item in requirements or self.compliance_requirements(notice_id):
            writer.writerow(
                {
                    "notice_id": safe_cell(item.get("noticeId") or notice_id),
                    "requirement_id": item.get("id") or "",
                    "category": safe_cell(item.get("category") or ""),
                    "mandatory_state": safe_cell(item.get("mandatoryState") or ""),
                    "status": safe_cell(item.get("status") or ""),
                    "verification_state": safe_cell(item.get("verificationState") or ""),
                    "owner": safe_cell(item.get("owner") or ""),
                    "due_date": safe_cell(item.get("dueDate") or ""),
                    "response_location": safe_cell(item.get("responseLocation") or ""),
                    "requirement_text": safe_cell(item.get("requirementText") or ""),
                    "citation_id": item.get("citationId") or "",
                    "revision_id": safe_cell(item.get("revisionId") or ""),
                    "source_trace": safe_cell(f"Source: citation #{item.get('citationId') or 'missing'}; revision {item.get('revisionId') or 'unscoped'}"),
                    "invalidated": "yes" if item.get("invalidated") else "no",
                    "invalidation_reason": safe_cell(item.get("invalidationReason") or ""),
                    "notes": safe_cell(item.get("notes") or ""),
                }
            )
        return output.getvalue()

    def export_compliance_markdown(self, notice_id: str, requirements: list[dict[str, Any]] | None = None) -> str:
        import html

        def cell(value: Any) -> str:
            flattened = re.sub(r"[\r\n]+", " ", str(value or ""))
            return html.escape(flattened, quote=False).replace("|", "\\|").strip()

        rows = requirements or self.compliance_requirements(notice_id)
        lines = [
            f"# Compliance Matrix - {cell(notice_id)}",
            "",
            "| ID | Category | Mandatory | Status | Verification | Requirement | Source | Invalidated |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for item in rows:
            source = f"citation #{item.get('citationId') or 'missing'} / revision {item.get('revisionId') or 'unscoped'}"
            lines.append(
                "| "
                + " | ".join(
                    [
                        cell(item.get("id")),
                        cell(item.get("category")),
                        cell(item.get("mandatoryState")),
                        cell(item.get("status")),
                        cell(item.get("verificationState")),
                        cell(item.get("requirementText")),
                        cell(source),
                        cell(item.get("invalidationReason") if item.get("invalidated") else ""),
                    ]
                )
                + " |"
            )
        lines.append("")
        return "\n".join(lines)

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
            conn.execute(
                """
                UPDATE evidence_citations
                SET document_id = NULL, verification_state = 'superseded', updated_at = ?
                WHERE document_id = ?
                """,
                (now, document_id),
            )
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
        normalized_snippets: list[dict[str, Any]] = []
        seen_input: set[tuple[str, str]] = set()
        for item in snippets[:40]:
            snippet = clean_text(item.get("snippet"), 1200)
            if not snippet:
                continue
            section = clean_text(item.get("section"), 300)
            key = (section, snippet)
            if key in seen_input:
                continue
            seen_input.add(key)
            normalized_snippets.append(
                {
                    "key": key,
                    "section": section,
                    "legacy_section": clean_text(section, 160),
                    "snippet": snippet,
                    "confidence": float(item.get("confidence") or 0),
                    "claim": clean_text(item.get("extractedClaim") or item.get("claim") or snippet, 2000),
                    "parser_state": "verified" if item.get("reviewed", False) else "generated",
                    "reviewed": bool(item.get("reviewed", False)),
                }
            )
        with self.connect() as conn:
            document = conn.execute("SELECT notice_id FROM proposal_documents WHERE id = ?", (document_id,)).fetchone()
            if not document:
                raise ValueError("documentId does not exist")
            if document["notice_id"] != notice_id:
                raise ValueError("documentId does not belong to noticeId")
            conn.execute("DELETE FROM evidence_snippets WHERE document_id = ?", (document_id,))
            existing_rows = conn.execute(
                """
                SELECT *
                FROM evidence_citations
                WHERE notice_id = ?
                  AND document_id = ?
                  AND extraction_method = 'document-intake'
                ORDER BY
                  CASE WHEN verification_state = 'superseded' THEN 1 ELSE 0 END,
                  id DESC
                """,
                (notice_id, document_id),
            ).fetchall()
            existing_by_identity: dict[tuple[str, str], list[sqlite3.Row]] = {}
            for row in existing_rows:
                key = (row["page_section"], row["source_excerpt"])
                existing_by_identity.setdefault(key, []).append(row)
            seen: set[tuple[str, str]] = set()
            for item in normalized_snippets:
                key = item["key"]
                seen.add(key)
                conn.execute(
                    """
                    INSERT INTO evidence_snippets (notice_id, document_id, section, snippet, confidence, reviewed, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        notice_id,
                        document_id,
                        item["legacy_section"],
                        item["snippet"],
                        item["confidence"],
                        1 if item["reviewed"] else 0,
                        now,
                        now,
                    ),
                )
                existing = existing_by_identity.get(key, [None])[0]
                if existing:
                    state = existing["verification_state"]
                    verifier = existing["verifier"]
                    verified_at = existing["verified_at"]
                    if state in {"generated", "superseded"}:
                        state = item["parser_state"]
                        verifier = ""
                        verified_at = now if item["parser_state"] == "verified" else ""
                    conn.execute(
                        """
                        UPDATE evidence_citations
                        SET extracted_claim = ?, confidence = ?, verification_state = ?,
                            verifier = ?, verified_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (item["claim"], item["confidence"], state, verifier, verified_at, now, existing["id"]),
                    )
                    for duplicate in existing_by_identity.get(key, [])[1:]:
                        if duplicate["verification_state"] != "superseded":
                            conn.execute(
                                """
                                UPDATE evidence_citations
                                SET verification_state = 'superseded', updated_at = ?
                                WHERE id = ?
                                """,
                                (now, duplicate["id"]),
                            )
                else:
                    conn.execute(
                        """
                        INSERT INTO evidence_citations (
                          notice_id, document_id, page_section, source_excerpt, extracted_claim,
                          extraction_method, confidence, verification_state, verified_at, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, 'document-intake', ?, ?, ?, ?, ?)
                        """,
                        (
                            notice_id,
                            document_id,
                            item["section"],
                            item["snippet"],
                            item["claim"],
                            item["confidence"],
                            item["parser_state"],
                            now if item["parser_state"] == "verified" else "",
                            now,
                            now,
                        ),
                    )
            for key, rows in existing_by_identity.items():
                if key in seen:
                    continue
                for row in rows:
                    if row["verification_state"] != "superseded":
                        conn.execute(
                            """
                            UPDATE evidence_citations
                            SET verification_state = 'superseded', updated_at = ?
                            WHERE id = ?
                            """,
                            (now, row["id"]),
                        )
        return self.evidence_snippets(notice_id)

    def create_evidence_citation(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = self._validate_evidence_payload(payload)
        now = utc_now()
        with self.connect() as conn:
            self._validate_evidence_references(conn, values)
            conn.execute(
                """
                INSERT INTO evidence_citations (
                  notice_id, proposal_id, document_id, revision_id, page_section, source_excerpt,
                  extracted_claim, extraction_method, confidence, verification_state, verifier,
                  verified_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["notice_id"],
                    values["proposal_id"],
                    values["document_id"],
                    values["revision_id"],
                    values["page_section"],
                    values["source_excerpt"],
                    values["extracted_claim"],
                    values["extraction_method"],
                    values["confidence"],
                    values["verification_state"],
                    values["verifier"],
                    now if values["verification_state"] == "verified" else "",
                    now,
                    now,
                ),
            )
            citation_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            self._add_event(conn, values["notice_id"], "evidence_created", "", values["verification_state"], "Evidence citation created", now)
        return self.evidence_citation(citation_id) or {}

    def update_evidence_citation(self, evidence_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute("SELECT * FROM evidence_citations WHERE id = ?", (evidence_id,)).fetchone()
            if not existing:
                raise ValueError("evidence citation does not exist")
            merged = {
                "noticeId": existing["notice_id"],
                "proposalId": existing["proposal_id"],
                "documentId": existing["document_id"],
                "revisionId": existing["revision_id"],
                "pageSection": existing["page_section"],
                "sourceExcerpt": existing["source_excerpt"],
                "extractedClaim": existing["extracted_claim"],
                "extractionMethod": existing["extraction_method"],
                "confidence": existing["confidence"],
                "verificationState": existing["verification_state"],
                "verifier": existing["verifier"],
                **payload,
            }
            merged["noticeId"] = existing["notice_id"]
            values = self._validate_evidence_payload(merged)
            self._validate_evidence_references(conn, values)
            verified_at = existing["verified_at"]
            if values["verification_state"] == "verified" and existing["verification_state"] != "verified":
                verified_at = now
            elif values["verification_state"] != "verified":
                verified_at = ""
            conn.execute(
                """
                UPDATE evidence_citations
                SET proposal_id = ?, document_id = ?, revision_id = ?, page_section = ?,
                    source_excerpt = ?, extracted_claim = ?, extraction_method = ?, confidence = ?,
                    verification_state = ?, verifier = ?, verified_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    values["proposal_id"],
                    values["document_id"],
                    values["revision_id"],
                    values["page_section"],
                    values["source_excerpt"],
                    values["extracted_claim"],
                    values["extraction_method"],
                    values["confidence"],
                    values["verification_state"],
                    values["verifier"],
                    verified_at,
                    now,
                    evidence_id,
                ),
            )
            if existing["verification_state"] != values["verification_state"]:
                self._add_event(conn, existing["notice_id"], "evidence_verified", existing["verification_state"], values["verification_state"], "Evidence verification state changed", now)
        return self.evidence_citation(evidence_id) or {}

    def verify_evidence_citation(self, evidence_id: int, state: str, verifier: str = "") -> dict[str, Any]:
        return self.update_evidence_citation(evidence_id, {"verificationState": state, "verifier": verifier})

    def delete_evidence_citation(self, evidence_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM evidence_citations WHERE id = ?", (evidence_id,)).fetchone()
            if not row:
                raise ValueError("evidence citation does not exist")
            conn.execute("DELETE FROM evidence_citations WHERE id = ?", (evidence_id,))
        return self._evidence_from_row(row)

    def evidence_citation(self, evidence_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM evidence_citations WHERE id = ?", (evidence_id,)).fetchone()
        return self._evidence_from_row(row) if row else None

    def evidence_citations(self, notice_id: str, *, include_legacy: bool = True) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM evidence_citations
                WHERE notice_id = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (notice_id,),
            ).fetchall()
        citations = [self._evidence_from_row(row) for row in rows]
        citations = self._dedupe_evidence(citations)
        if include_legacy:
            citations = self._merge_legacy_evidence(citations, self._legacy_evidence_as_citations(notice_id))
        return citations

    def _evidence_identity(self, item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            item.get("documentId"),
            item.get("pageSection") or item.get("section") or "",
            item.get("sourceExcerpt") or item.get("snippet") or "",
            item.get("extractionMethod") or "",
        )

    def _dedupe_evidence(self, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: dict[tuple[Any, ...], dict[str, Any]] = {}
        order: list[tuple[Any, ...]] = []
        for item in citations:
            key = self._evidence_identity(item)
            existing = selected.get(key)
            if existing is None:
                selected[key] = item
                order.append(key)
                continue
            if existing.get("verificationState") == "superseded" and item.get("verificationState") != "superseded":
                selected[key] = item
        return [selected[key] for key in order]

    def _merge_legacy_evidence(self, citations: list[dict[str, Any]], legacy: list[dict[str, Any]]) -> list[dict[str, Any]]:
        citation_keys = {
            (
                item.get("documentId"),
                item.get("pageSection") or item.get("section") or "",
                item.get("sourceExcerpt") or item.get("snippet") or "",
            )
            for item in citations
        }
        merged = list(citations)
        for item in legacy:
            key = (
                item.get("documentId"),
                item.get("pageSection") or item.get("section") or "",
                item.get("sourceExcerpt") or item.get("snippet") or "",
            )
            if key not in citation_keys:
                merged.append(item)
                citation_keys.add(key)
        return merged

    def _legacy_evidence_as_citations(self, notice_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": item["id"],
                "noticeId": item["noticeId"],
                "proposalId": None,
                "documentId": item["documentId"],
                "revisionId": "",
                "pageSection": item["section"],
                "section": item["section"],
                "sourceExcerpt": item["snippet"],
                "snippet": item["snippet"],
                "extractedClaim": item["snippet"],
                "extractionMethod": "legacy-snippet",
                "confidence": item["confidence"],
                "verificationState": "verified" if item["reviewed"] else "generated",
                "reviewed": item["reviewed"],
                "verifier": "",
                "verifiedAt": "",
                "createdAt": item["createdAt"],
                "updatedAt": item["updatedAt"],
                "legacySnippet": True,
            }
            for item in self.evidence_snippets(notice_id)
        ]

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


    def _proposal_artifact_version_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "artifactId": row["artifact_id"],
            "noticeId": row["notice_id"],
            "artifactType": row["artifact_type"],
            "title": row["title"],
            "status": row["status"],
            "format": row["format"],
            "content": row["content"],
            "notes": row["notes"],
            "version": row["version"],
            "createdAt": row["created_at"],
        }

    def _add_proposal_artifact_version(self, conn: sqlite3.Connection, row: sqlite3.Row, created_at: str) -> None:
        conn.execute(
            """
            INSERT INTO proposal_artifact_versions
              (artifact_id, notice_id, artifact_type, title, status, format, content, notes, version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["notice_id"],
                row["artifact_type"],
                row["title"],
                row["status"],
                row["format"],
                row["content"],
                row["notes"],
                row["version"],
                created_at,
            ),
        )

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
            row = conn.execute("SELECT * FROM proposal_artifacts WHERE id = ?", (cur.lastrowid,)).fetchone()
            self._add_proposal_artifact_version(conn, row, now)
            self._add_event(conn, notice_id, "proposal_artifact_created", "", title, "Proposal artifact created", now)
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
            row = conn.execute("SELECT * FROM proposal_artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if changed:
                self._add_proposal_artifact_version(conn, row, now)
                self._add_event(conn, existing["notice_id"], "proposal_artifact_updated", str(existing["version"]), str(version), "Proposal artifact updated", now)
        return self._proposal_artifact_from_row(row)


    def proposal_artifact(self, artifact_id: int) -> dict[str, Any] | None:
        if not artifact_id:
            raise ValueError("artifactId is required")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM proposal_artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return self._proposal_artifact_from_row(row) if row else None

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


    def proposal_artifact_history(self, artifact_id: int) -> list[dict[str, Any]]:
        if not artifact_id:
            raise ValueError("artifactId is required")
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM proposal_artifact_versions
                WHERE artifact_id = ?
                ORDER BY version DESC, id DESC
                """,
                (artifact_id,),
            ).fetchall()
        return [self._proposal_artifact_version_from_row(row) for row in rows]

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
        source = clean_text(opp.get("source") or "manual-search", 80)
        reason = "Added from manual external intake" if source == "manual-external" or opp.get("manualExternal") else "Added from manual SAM search"
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
                    source,
                    json.dumps(opp, sort_keys=True),
                    now,
                ),
            )
            self._add_event(conn, notice_id, "manual_tracked", "", source, reason, now)
        return self.set_workflow(notice_id, {"status": "reviewing", "decisionReason": reason})

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
