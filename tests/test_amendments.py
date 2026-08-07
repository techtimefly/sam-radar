import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from sam_radar.ai_assist import deterministic_gap_analysis, deterministic_summary
from sam_radar.config import BusinessProfile, Settings
from sam_radar.core import attach_amendment_context
from sam_radar.reports import build_html_report, build_report_payload
from sam_radar.storage import Store


def _opp(**overrides):
    base = {
        "noticeId": "opp-amd-1",
        "title": "Security Support",
        "type": "Solicitation",
        "postedDate": "2026-08-01",
        "responseDeadline": "2026-08-20T17:00:00-04:00",
        "naicsCode": "541512",
        "classificationCode": "DJ01",
        "setAsideCode": "SDVOSBC",
        "setAside": "Service-Disabled Veteran-Owned Small Business",
        "organization": "Example Agency",
        "descriptionParagraphs": ["Contractor shall provide security support."],
        "contacts": [{"name": "Jane CO", "email": "jane@example.test"}],
        "resourceLinks": [{"rel": "package", "href": "https://example.test/sol.pdf", "title": "Solicitation"}],
    }
    base.update(overrides)
    return base


def test_revision_capture_is_idempotent_and_normalizes_no_change(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    first = store.capture_opportunity_revision(_opp(responseDeadline="2026-08-20T17:00:00-04:00"))
    second = store.capture_opportunity_revision(_opp(responseDeadline="2026-08-20 21:00:00+00:00"))

    assert first["created"] is True
    assert second["created"] is False
    assert len(store.opportunity_revisions("opp-amd-1")) == 1
    assert store.amendment_changes("opp-amd-1") == []


def test_revision_change_classes_and_impacts(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    store.capture_opportunity_revision(_opp())
    store.capture_opportunity_revision(
        _opp(
            responseDeadline="2026-08-12T17:00:00-04:00",
            type="Special Notice",
            naicsCode="541519",
            classificationCode="R425",
            setAsideCode="SBA",
            setAside="Total Small Business Set-Aside",
            descriptionParagraphs=["Contractor shall provide revised cyber support."],
            contacts=[{"name": "New CO", "email": "new@example.test"}],
        )
    )

    changes = store.amendment_changes("opp-amd-1")
    by_field = {item["field"]: item for item in changes}
    assert by_field["deadline"]["changeType"] == "deadline_contracted"
    assert by_field["deadline"]["impact"] == "critical"
    assert by_field["notice_type"]["impact"] == "medium"
    assert by_field["set_aside"]["impact"] == "high"
    assert by_field["naics"]["impact"] == "high"
    assert by_field["psc"]["impact"] == "medium"
    assert by_field["description"]["impact"] == "medium"
    assert by_field["contacts"]["impact"] == "low"
    assert all(item["machineType"] for item in changes)
    assert all(item["explanation"] for item in changes)


def test_status_cancellation_deadline_extension_and_attachment_add_remove_change(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    store.capture_opportunity_revision(_opp(resourceLinks=[{"href": "https://example.test/a.pdf", "title": "A"}]))
    store.capture_opportunity_revision(
        _opp(
            responseDeadline="2026-09-01T17:00:00-04:00",
            type="Cancellation",
            resourceLinks=[
                {"href": "https://example.test/a.pdf", "title": "A revised", "size": 12},
                {"href": "https://example.test/b.pdf", "title": "B"},
            ],
        )
    )
    store.capture_opportunity_revision(_opp(type="Cancellation", responseDeadline="2026-09-01T17:00:00-04:00", resourceLinks=[]))

    changes = store.amendment_changes("opp-amd-1")
    machine_types = {item["machineType"] for item in changes}
    assert "deadline_extended" in machine_types
    assert "status_cancelled" in machine_types
    assert "attachment_added" in machine_types
    assert "attachment_removed" in machine_types
    assert "attachment_changed" in machine_types
    assert any(item["impact"] == "critical" for item in changes if item["machineType"] == "status_cancelled")


def test_attachment_content_hash_change_is_classified_as_attachment_changed(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    store.capture_opportunity_revision(_opp(resourceLinks=[{"href": "https://example.test/a.pdf", "title": "A", "hash": "old"}]))
    store.capture_opportunity_revision(_opp(resourceLinks=[{"href": "https://example.test/a.pdf", "title": "A", "hash": "new"}]))

    changes = store.amendment_changes("opp-amd-1")
    assert [item["machineType"] for item in changes] == ["attachment_changed"]
    assert changes[0]["impact"] == "medium"


def test_attachment_url_title_and_hash_fallbacks_are_stable_one_to_one(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    cases = [
        ([{"href": "https://example.test/a-v1.pdf", "title": "Package A"}], [{"href": "https://example.test/a-v2.pdf", "title": "Package A"}]),
        ([{"href": "https://example.test/b.pdf", "title": "Old B"}], [{"href": "https://example.test/b.pdf", "title": "New B"}]),
        ([{"href": "https://example.test/c-v1.pdf", "title": "C", "hash": "same"}], [{"href": "https://example.test/c-v2.pdf", "title": "Renamed C", "hash": "same"}]),
    ]
    for idx, (before, after) in enumerate(cases, 1):
        notice_id = f"opp-attach-{idx}"
        store.capture_opportunity_revision(_opp(noticeId=notice_id, resourceLinks=before))
        store.capture_opportunity_revision(_opp(noticeId=notice_id, resourceLinks=after))
        changes = store.amendment_changes(notice_id)
        assert [item["machineType"] for item in changes] == ["attachment_changed"]


def test_attachment_ambiguous_same_title_duplicates_are_not_conflated(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    store.capture_opportunity_revision(
        _opp(
            resourceLinks=[
                {"title": "Attachment"},
                {"title": "Attachment"},
            ]
        )
    )
    store.capture_opportunity_revision(
        _opp(
            resourceLinks=[
                {"title": "Attachment", "hash": "revised"},
                {"title": "Attachment"},
            ]
        )
    )
    changes = store.amendment_changes("opp-amd-1")
    assert [item["machineType"] for item in changes].count("attachment_changed") == 0
    assert [item["machineType"] for item in changes].count("attachment_added") == 1
    assert [item["machineType"] for item in changes].count("attachment_removed") == 1


def test_existing_v09_evidence_schema_migrates_and_foreign_keys_are_checked(tmp_path: Path):
    db = tmp_path / "sam-radar.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE seen_opportunities (notice_id TEXT PRIMARY KEY, title_key TEXT UNIQUE, title TEXT, url TEXT, recommendation TEXT, score INTEGER, payload_json TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, notified_at TEXT)")
        conn.execute(
            """
            CREATE TABLE proposal_documents (
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
            CREATE TABLE evidence_citations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              notice_id TEXT NOT NULL,
              proposal_id INTEGER,
              document_id INTEGER,
              page_section TEXT NOT NULL DEFAULT '',
              source_excerpt TEXT NOT NULL,
              extracted_claim TEXT NOT NULL DEFAULT '',
              extraction_method TEXT NOT NULL DEFAULT 'manual',
              confidence REAL NOT NULL DEFAULT 0,
              verification_state TEXT NOT NULL DEFAULT 'needs-review',
              verifier TEXT NOT NULL DEFAULT '',
              verified_at TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO proposal_documents
              (notice_id, source, label, filename, created_at, updated_at)
            VALUES ('legacy-1', 'https://example.test/old.pdf', 'Old Package', 'old.pdf', '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO evidence_citations
              (notice_id, document_id, page_section, source_excerpt, created_at, updated_at)
            VALUES ('legacy-1', 1, 'deadline', 'Legacy citation text', '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')
            """
        )

    store = Store(db)
    store.capture_opportunity_revision(_opp(noticeId="legacy-1", resourceLinks=[{"href": "https://example.test/old.pdf", "title": "Old Package"}]))
    captured = store.capture_opportunity_revision(
        _opp(noticeId="legacy-1", responseDeadline="2026-08-12T17:00:00-04:00", resourceLinks=[{"href": "https://example.test/old.pdf", "title": "Old Package"}])
    )
    assert captured["created"] is True
    warnings = store.stale_evidence_warnings("legacy-1")
    assert warnings["count"] == 1
    assert "not tied" in warnings["items"][0]["reason"]
    with store.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(evidence_citations)")}
        assert "revision_id" in columns
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_deadline_transition_machine_types_and_valid_comparison(tmp_path: Path):
    scenarios = [
        ("", "2026-08-20T17:00:00-04:00", "deadline_added"),
        ("2026-08-20T17:00:00-04:00", "", "deadline_removed"),
        ("not a date", "2026-08-20T17:00:00-04:00", "deadline_unparseable_changed"),
        ("2026-08-20T17:00:00-04:00", "not a date", "deadline_unparseable_changed"),
        ("not a date", "still not a date", "deadline_unparseable_changed"),
        ("2026-08-20T17:00:00-04:00", "2026-08-20T21:00:00+00:00", None),
    ]
    for idx, (before, after, expected) in enumerate(scenarios, 1):
        notice_id = f"opp-deadline-{idx}"
        store = Store(tmp_path / f"{notice_id}.sqlite3")
        store.capture_opportunity_revision(_opp(noticeId=notice_id, responseDeadline=before))
        store.capture_opportunity_revision(_opp(noticeId=notice_id, responseDeadline=after))
        changes = store.amendment_changes(notice_id)
        if expected is None:
            assert changes == []
        else:
            assert [item["machineType"] for item in changes] == [expected]
            assert expected not in {"deadline_extended", "deadline_contracted"}


def test_capture_opportunity_revision_parallel_identical_insert_is_race_idempotent(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    barrier = Barrier(8)

    def capture_once():
        barrier.wait(timeout=5)
        return store.capture_opportunity_revision(_opp(noticeId="opp-race"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: capture_once(), range(8)))

    assert [item["created"] for item in results].count(True) == 1
    assert [item["created"] for item in results].count(False) == 7
    assert len(store.opportunity_revisions("opp-race")) == 1
    assert store.amendment_changes("opp-race") == []


def test_stale_citation_detection_for_predating_revision_and_removed_document_is_precise(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    first = store.capture_opportunity_revision(
        _opp(
            resourceLinks=[
                {"href": "https://example.test/a.pdf", "title": "A"},
                {"href": "https://example.test/b.pdf", "title": "B"},
            ]
        )
    )
    doc_a = store.add_proposal_document({"noticeId": "opp-amd-1", "source": "https://example.test/a.pdf", "label": "A"})
    doc_b = store.add_proposal_document({"noticeId": "opp-amd-1", "source": "https://example.test/b.pdf", "label": "B"})
    citation_a = store.create_evidence_citation(
        {
            "noticeId": "opp-amd-1",
            "documentId": doc_a["id"],
            "revisionId": first["revision"]["revisionId"],
            "sourceExcerpt": "Original package says submit by Aug 20.",
            "extractionMethod": "manual",
        }
    )
    citation_b = store.create_evidence_citation(
        {
            "noticeId": "opp-amd-1",
            "documentId": doc_b["id"],
            "revisionId": first["revision"]["revisionId"],
            "sourceExcerpt": "Unrelated package evidence remains current.",
            "extractionMethod": "manual",
        }
    )
    store.capture_opportunity_revision(_opp(resourceLinks=[{"href": "https://example.test/b.pdf", "title": "B"}]))

    warnings = store.stale_evidence_warnings("opp-amd-1")
    assert warnings["count"] == 1
    assert warnings["items"][0]["citationId"] == citation_a["id"]
    assert citation_b["id"] not in {item["citationId"] for item in warnings["items"]}
    assert "material revision" in warnings["items"][0]["reason"]
    assert any("removed" in item["reason"] for item in warnings["items"])


def test_deadline_revision_stales_all_older_citations(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    first = store.capture_opportunity_revision(_opp(resourceLinks=[{"href": "https://example.test/a.pdf", "title": "A"}]))
    doc = store.add_proposal_document({"noticeId": "opp-amd-1", "source": "https://example.test/a.pdf", "label": "A"})
    citation = store.create_evidence_citation(
        {
            "noticeId": "opp-amd-1",
            "documentId": doc["id"],
            "revisionId": first["revision"]["revisionId"],
            "sourceExcerpt": "Original package says submit by Aug 20.",
        }
    )
    store.capture_opportunity_revision(_opp(responseDeadline="2026-08-12T17:00:00-04:00", resourceLinks=[{"href": "https://example.test/a.pdf", "title": "A"}]))

    warnings = store.stale_evidence_warnings("opp-amd-1")
    assert warnings["items"][0]["citationId"] == citation["id"]
    assert warnings["items"][0]["reason"] == "Citation predates a material revision"


def test_review_task_crud_cross_notice_integrity(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    change = store.capture_opportunity_revision(_opp())["revision"]
    task = store.create_amendment_task(
        {
            "noticeId": "opp-amd-1",
            "revisionId": change["revisionId"],
            "assignee": "Capture Lead",
            "status": "open",
            "dueDate": "2026-08-08",
            "notes": "Review amendment.",
        }
    )
    updated = store.update_amendment_task(task["id"], {"noticeId": "opp-amd-1", "status": "done", "notes": "Reviewed."})
    assert updated["status"] == "done"
    assert store.amendment_tasks("opp-amd-1")[0]["notes"] == "Reviewed."
    try:
        store.update_amendment_task(task["id"], {"noticeId": "other", "status": "open"})
    except ValueError as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("Expected cross-notice task update to fail")


def test_mark_amendment_changes_reviewed_specific_all_and_cross_notice(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    store.capture_opportunity_revision(_opp())
    store.capture_opportunity_revision(_opp(responseDeadline="2026-08-12T17:00:00-04:00"))
    change = store.amendment_changes("opp-amd-1")[0]
    assert store.amendment_summary("opp-amd-1")["unreadCount"] == 1

    result = store.mark_amendment_changes_reviewed("opp-amd-1", [change["id"]])
    assert result["reviewedCount"] == 1
    assert result["summary"]["unreadCount"] == 0
    assert store.amendment_changes("opp-amd-1")[0]["readAt"]

    store.capture_opportunity_revision(_opp(noticeId="other", responseDeadline="2026-08-20T17:00:00-04:00"))
    store.capture_opportunity_revision(_opp(noticeId="other", responseDeadline="2026-08-12T17:00:00-04:00"))
    other_change = store.amendment_changes("other")[0]
    try:
        store.mark_amendment_changes_reviewed("opp-amd-1", [other_change["id"]])
    except ValueError as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("Expected cross-notice review to fail")

    result = store.mark_amendment_changes_reviewed("other")
    assert result["summary"]["unreadCount"] == 0


def test_review_task_rejects_missing_or_cross_notice_references(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    rev = store.capture_opportunity_revision(_opp())["revision"]
    store.capture_opportunity_revision(_opp(noticeId="other"))
    store.capture_opportunity_revision(_opp(noticeId="other", responseDeadline="2026-08-12T17:00:00-04:00"))
    other_change = store.amendment_changes("other")[0]

    for payload in (
        {"noticeId": "opp-amd-1", "revisionId": "missing"},
        {"noticeId": "opp-amd-1", "revisionId": rev["revisionId"], "changeId": other_change["id"]},
    ):
        try:
            store.create_amendment_task(payload)
        except ValueError as exc:
            assert "does not" in str(exc)
        else:
            raise AssertionError("Expected invalid amendment task references to fail")


def test_attach_amendment_context_for_refresh_payload_calls_capture_once_and_preserves_manual(tmp_path: Path, monkeypatch):
    settings = Settings(sam_gov_api_key="test", data_dir=tmp_path / "data")
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    calls = []
    original = store.capture_opportunity_revision

    def capture_once(opp):
        calls.append(opp.get("noticeId"))
        return original(opp)

    monkeypatch.setattr(store, "capture_opportunity_revision", capture_once)
    matches = [
        _opp(),
        _opp(noticeId="manual-local", manualTracked=True),
        _opp(noticeId="external-local", manualExternal=True),
        _opp(noticeId="manual-prefix"),
    ]
    attach_amendment_context(store, matches)
    assert calls == ["opp-amd-1"]
    assert store.opportunity_revisions("manual-local") == []
    assert store.opportunity_revisions("external-local") == []
    assert store.opportunity_revisions("manual-prefix") == []

    attach_amendment_context(store, [_opp(responseDeadline="2026-08-12T17:00:00-04:00")])

    enriched = [_opp(responseDeadline="2026-08-12T17:00:00-04:00")]
    attach_amendment_context(store, enriched)
    assert enriched[0]["amendmentSummary"]["materialChangeCount"] == 1
    assert enriched[0]["amendmentTimeline"][0]["impact"] == "critical"


def test_report_renders_amendments_safely_and_mobile_surface(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    payload = {
        "postedFrom": "08/01/2026",
        "postedTo": "08/04/2026",
        "matches": [
            _opp(
                title="<img src=x onerror=alert(1)>",
                amendmentSummary={"unreadCount": 1, "materialChangeCount": 1, "staleEvidenceCount": 1},
                amendmentTimeline=[
                    {
                        "field": "deadline",
                        "impact": "critical<script>",
                        "machineType": "deadline_contracted",
                        "beforeSummary": "Aug 20",
                        "afterSummary": "Aug 12 <script>",
                        "detectedAt": "2026-08-06T00:00:00+00:00",
                        "explanation": "Deadline moved earlier.",
                    }
                ],
                staleEvidenceWarnings={"count": 1, "items": [{"reason": "Citation predates material revision <script>"}]},
                amendmentTasks=[],
            )
        ],
        "errors": [],
    }
    report = build_report_payload(payload, profile, settings, unseen=[])
    html = build_html_report(report)
    assert "Amendments" in html
    assert "amendment-timeline" in html
    assert "amendment-mobile" in html
    assert "deadline_contracted" in html
    assert "<img src=x" not in html
    assert "critical&lt;script&gt;" not in html
    assert "impact-critical" in html
    assert "/api/amendments/task" in html
    assert "/api/amendments/mark-reviewed" in html
    assert "amendment-mark-reviewed" in html
    assert "amendment-task-update" in html
    assert "amendment-task-complete" in html
    assert "amendment-task-delete" in html
    assert "window.confirm" in html


def test_report_json_embedding_escapes_script_breakout(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    report = build_report_payload(
        {
            "postedFrom": "08/01/2026",
            "postedTo": "08/04/2026",
            "matches": [_opp(title="</script><script>alert(1)</script>")],
            "errors": [],
        },
        profile,
        settings,
        unseen=[],
    )
    html = build_html_report(report)
    data_block = html.split('<script id="report-data" type="application/json">', 1)[1].split("</script><script>", 1)[0]
    assert "</script>" not in data_block.lower()
    assert "\\u003c/script>" in data_block


def test_ai_assist_includes_amendment_context_without_audit_source_text(tmp_path: Path):
    opp = _opp(
        amendmentSummary={"materialChangeCount": 1},
        amendmentTimeline=[{"impact": "critical", "field": "deadline", "explanation": "Deadline contracted.", "afterSummary": "Aug 12"}],
    )
    summary = deterministic_summary(opp, [])
    gaps = deterministic_gap_analysis(opp, [])

    assert any("Deadline contracted" in item for item in summary["sourceFacts"])
    assert any("Review amendment" in item for item in summary["aiRecommendations"])
    assert any(gap["source"] == "Amendment intelligence" for gap in gaps["gaps"])
