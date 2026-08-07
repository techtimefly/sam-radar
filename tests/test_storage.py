from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sam_radar.storage import Store


def test_status_round_trip_and_defaults(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    default = store.get_status("abc")
    assert default["noticeId"] == "abc"
    assert default["status"] == "new"
    assert default["notes"] == ""
    assert default["priority"] == "normal"
    assert default["documents"] == []

    saved = store.set_status("abc", "Pursue", "Review attachments")
    assert saved["status"] == "pursue"
    assert saved["notes"] == "Review attachments"
    assert saved["updatedAt"]

    assert store.status_map(["abc", "missing"])["abc"]["status"] == "pursue"


def test_status_validation_rejects_unknown_values(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    try:
        store.set_status("abc", "maybe")
    except ValueError as exc:
        assert "status must be one of" in str(exc)
    else:
        raise AssertionError("Expected invalid status to raise ValueError")



def test_workflow_fields_documents_events_and_notification_dedupe(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    saved = store.set_workflow(
        "abc",
        {
            "status": "pursue",
            "notes": "Review attachments",
            "priority": "urgent",
            "owner": "Capture Lead",
            "nextAction": "Pull PWS",
            "followUpAt": "2026-08-06",
            "decisionReason": "Strong fit",
            "documents": [{"label": "PWS", "url": "https://example.test/pws.pdf", "reviewed": True}],
        },
    )

    assert saved["status"] == "pursue"
    assert saved["priority"] == "urgent"
    assert saved["owner"] == "Capture Lead"
    assert saved["nextAction"] == "Pull PWS"
    assert saved["documents"][0]["reviewed"] is True
    assert {event["type"] for event in saved["events"]} >= {"status_changed", "documents_updated"}

    mapped = store.status_map(["abc"])["abc"]
    assert mapped["followUpAt"] == "2026-08-06"
    assert mapped["documents"][0]["label"] == "PWS"

    assert store.record_notification_once("abc", "pursue", "pursue") is True
    assert store.record_notification_once("abc", "pursue", "pursue") is False


def test_no_bid_reason_validation(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    try:
        store.set_workflow("abc", {"status": "no-bid", "noBidReason": "not-a-real-reason"})
    except ValueError as exc:
        assert "no_bid_reason must be one of" in str(exc)
    else:
        raise AssertionError("Expected invalid no-bid reason to raise ValueError")


def test_manual_tracked_opportunity_prevents_duplicate_adds_without_marking_seen(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    opp = {"noticeId": "manual-1", "title": "Manual Opportunity", "url": "https://sam.gov/opp/manual-1/view"}

    workflow = store.add_manual_tracked(opp)

    assert workflow["status"] == "reviewing"
    assert workflow["decisionReason"] == "Added from manual SAM search"
    assert store.is_tracked("manual-1") is True
    assert store.unseen([{ "noticeId": "manual-1", "title": "Manual Opportunity", "responseDeadline": "" }])

    try:
        store.add_manual_tracked(opp)
    except ValueError as exc:
        assert "already tracked" in str(exc)
    else:
        raise AssertionError("Expected duplicate manual opportunity to be rejected")


def test_manual_tracked_opportunities_returns_saved_payloads(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    opp = {
        "noticeId": "manual-2",
        "title": "Manual Opportunity",
        "url": "https://sam.gov/opp/manual-2/view",
        "descriptionParagraphs": ["Fetched description text."],
    }

    store.add_manual_tracked(opp)

    saved = store.manual_tracked_opportunities()
    assert saved[0]["noticeId"] == "manual-2"
    assert saved[0]["manualTracked"] is True
    assert saved[0]["descriptionParagraphs"] == ["Fetched description text."]


def test_proposal_workspace_stage_engine_and_timeline_events(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    proposal = store.create_proposal(
        "opp-1",
        {"noticeId": "opp-1", "title": "Security Proposal", "role": "subcontractor"},
    )

    assert proposal["created"] is True
    assert proposal["role"] == "subcontractor"
    assert proposal["stage"] == "intent"
    assert proposal["stages"][0] == {"key": "intent", "label": "Intent", "state": "current"}
    assert "subcontracting" in proposal["nextAction"]

    duplicate = store.create_proposal("opp-1", {"noticeId": "opp-1", "role": "prime"})
    assert duplicate["created"] is False
    assert duplicate["role"] == "subcontractor"

    updated = store.update_proposal_stage("opp-1", {"noticeId": "opp-1", "stage": "docs", "notes": "Need attachments"})
    assert updated["stage"] == "docs"
    assert updated["stageLabel"] == "Docs"
    assert [item["state"] for item in updated["stages"][:4]] == ["complete", "complete", "current", "pending"]
    assert updated["notes"] == "Need attachments"

    workflow = store.get_status("opp-1")
    event_types = [event["type"] for event in workflow["events"]]
    assert "proposal_stage_changed" in event_types
    assert "proposal_created" in event_types
    assert store.proposal_map(["opp-1"])["opp-1"]["stage"] == "docs"
    assert store.proposals()[0]["noticeId"] == "opp-1"


def test_proposal_validation_rejects_bad_role_and_stage(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    try:
        store.create_proposal("opp-2", {"noticeId": "opp-2", "role": "maybe"})
    except ValueError as exc:
        assert "role must be prime or subcontractor" in str(exc)
    else:
        raise AssertionError("Expected invalid role to raise ValueError")

    store.create_proposal("opp-2", {"noticeId": "opp-2", "role": "prime"})
    try:
        store.update_proposal_stage("opp-2", {"noticeId": "opp-2", "stage": "magic"})
    except ValueError as exc:
        assert "stage must be one of" in str(exc)
    else:
        raise AssertionError("Expected invalid stage to raise ValueError")


def test_proposal_document_registry_and_evidence_snippets(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    document = store.add_proposal_document(
        {
            "noticeId": "opp-doc-1",
            "sourceType": "url",
            "source": "https://example.test/solicitation.txt",
            "label": "Solicitation",
        }
    )

    assert document["parseStatus"] == "pending"
    assert store.proposal_document_map(["opp-doc-1"])["opp-doc-1"][0]["label"] == "Solicitation"

    parsed = store.update_proposal_document_parse(
        document["id"],
        {"parseStatus": "parsed", "contentType": "text/plain", "sizeBytes": 120, "extractedTextPath": "/tmp/out.txt"},
    )
    assert parsed["parseStatus"] == "parsed"
    assert parsed["contentType"] == "text/plain"

    snippets = store.replace_evidence_snippets(
        "opp-doc-1",
        document["id"],
        [{"section": "Requirements", "snippet": "Offeror must provide security automation support.", "confidence": 0.8}],
    )
    assert snippets[0]["section"] == "Requirements"
    assert snippets[0]["confidence"] == 0.8

    event_types = [event["type"] for event in store.get_status("opp-doc-1")["events"]]
    assert "proposal_document_added" in event_types
    assert "proposal_document_parsed" in event_types


def test_evidence_citation_crud_verification_and_legacy_aliases(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-cite-1", "sourceType": "url", "source": "https://example.test/pws.txt", "label": "PWS"})

    citation = store.create_evidence_citation(
        {
            "noticeId": "opp-cite-1",
            "documentId": doc["id"],
            "pageSection": "PWS 3.1",
            "sourceExcerpt": "Contractor shall provide secure engineering support.",
            "extractedClaim": "Secure engineering support is required.",
            "extractionMethod": "manual",
            "confidence": 0.91,
        }
    )

    assert citation["verificationState"] == "needs-review"
    assert citation["snippet"] == "Contractor shall provide secure engineering support."
    assert citation["section"] == "PWS 3.1"
    assert store.evidence_citations("opp-cite-1")[0]["extractedClaim"] == "Secure engineering support is required."

    verified = store.verify_evidence_citation("opp-cite-1", citation["id"], "verified", "Capture Lead")
    assert verified["verificationState"] == "verified"
    assert verified["reviewed"] is True
    assert verified["verifier"] == "Capture Lead"
    assert verified["verifiedAt"]

    updated = store.update_evidence_citation(citation["id"], {"confidence": 0.5, "verificationState": "needs-review"})
    assert updated["confidence"] == 0.5
    assert updated["verifiedAt"] == ""

    deleted = store.delete_evidence_citation(citation["id"])
    assert deleted["id"] == citation["id"]
    assert store.evidence_citations("opp-cite-1") == []


def test_evidence_validation_rejects_bad_state_confidence_and_missing_excerpt(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    for payload, expected in [
        ({"noticeId": "bad", "sourceExcerpt": "x", "verificationState": "maybe"}, "verificationState must be one of"),
        ({"noticeId": "bad", "sourceExcerpt": "x", "confidence": 1.2}, "confidence must be from 0 to 1"),
        ({"noticeId": "bad"}, "sourceExcerpt is required"),
    ]:
        try:
            store.create_evidence_citation(payload)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("Expected invalid evidence payload to raise ValueError")


def test_evidence_validation_rejects_missing_and_cross_notice_references(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-ref-1", "sourceType": "url", "source": "https://example.test/ref-1.txt"})
    other_doc = store.add_proposal_document({"noticeId": "opp-ref-2", "sourceType": "url", "source": "https://example.test/ref-2.txt"})
    proposal = store.create_proposal("opp-ref-1", {"noticeId": "opp-ref-1", "role": "prime"})
    other_proposal = store.create_proposal("opp-ref-2", {"noticeId": "opp-ref-2", "role": "prime"})
    revision = store.capture_opportunity_revision({"noticeId": "opp-ref-1", "title": "Reference 1"})["revision"]
    other_revision = store.capture_opportunity_revision({"noticeId": "opp-ref-2", "title": "Reference 2"})["revision"]

    invalid_payloads = [
        ({"noticeId": "opp-ref-1", "documentId": 999_999, "sourceExcerpt": "x"}, "documentId does not exist"),
        ({"noticeId": "opp-ref-1", "documentId": other_doc["id"], "sourceExcerpt": "x"}, "documentId does not belong to noticeId"),
        ({"noticeId": "opp-ref-1", "proposalId": 999_999, "sourceExcerpt": "x"}, "proposalId does not exist"),
        ({"noticeId": "opp-ref-1", "proposalId": other_proposal["id"], "sourceExcerpt": "x"}, "proposalId does not belong to noticeId"),
        ({"noticeId": "opp-ref-1", "revisionId": "missing-revision", "sourceExcerpt": "x"}, "revisionId does not exist"),
        ({"noticeId": "opp-ref-1", "revisionId": other_revision["revisionId"], "sourceExcerpt": "x"}, "revisionId does not belong to noticeId"),
    ]
    for payload, expected in invalid_payloads:
        try:
            store.create_evidence_citation(payload)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"Expected {expected}")

    citation = store.create_evidence_citation(
        {
            "noticeId": "opp-ref-1",
            "proposalId": proposal["id"],
            "documentId": doc["id"],
            "revisionId": revision["revisionId"],
            "sourceExcerpt": "valid",
        }
    )
    assert citation["revisionId"] == revision["revisionId"]

    legacy = store.create_evidence_citation({"noticeId": "opp-ref-1", "revisionId": "", "sourceExcerpt": "legacy citation"})
    assert legacy["revisionId"] is None

    for payload, expected in [
        ({"documentId": other_doc["id"]}, "documentId does not belong to noticeId"),
        ({"proposalId": other_proposal["id"]}, "proposalId does not belong to noticeId"),
        ({"revisionId": other_revision["revisionId"]}, "revisionId does not belong to noticeId"),
        ({"noticeId": "opp-ref-2", "documentId": other_doc["id"]}, "documentId does not belong to noticeId"),
        ({"noticeId": "opp-ref-2", "proposalId": other_proposal["id"]}, "proposalId does not belong to noticeId"),
        ({"noticeId": "opp-ref-2", "revisionId": other_revision["revisionId"]}, "revisionId does not belong to noticeId"),
        ({"documentId": 999_999}, "documentId does not exist"),
        ({"proposalId": 999_999}, "proposalId does not exist"),
        ({"revisionId": "missing-revision"}, "revisionId does not exist"),
    ]:
        try:
            store.update_evidence_citation(citation["id"], payload)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"Expected {expected}")


def test_foreign_keys_enabled_and_document_delete_preserves_citations(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-fk-1", "sourceType": "url", "source": "https://example.test/fk.txt"})
    citation = store.create_evidence_citation({"noticeId": "opp-fk-1", "documentId": doc["id"], "sourceExcerpt": "Keep this citation."})

    with store.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    removed = store.remove_proposal_document(doc["id"])
    kept = store.evidence_citation(citation["id"])

    assert removed["id"] == doc["id"]
    assert kept is not None
    assert kept["documentId"] is None
    assert kept["verificationState"] == "superseded"
    with store.connect() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_replace_legacy_evidence_snippets_creates_citations_and_supersedes_old_parse(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-cite-2", "sourceType": "url", "source": "https://example.test/pws.txt", "label": "PWS"})

    first = store.replace_evidence_snippets("opp-cite-2", doc["id"], [{"section": "A", "snippet": "Offeror must submit a plan.", "confidence": 0.8}])
    second = store.replace_evidence_snippets("opp-cite-2", doc["id"], [{"section": "B", "snippet": "Evaluation will use best value.", "confidence": 0.7}])

    assert first[0]["snippet"] == "Offeror must submit a plan."
    assert second[0]["snippet"] == "Evaluation will use best value."
    citations = store.evidence_citations("opp-cite-2", include_legacy=False)
    assert {item["verificationState"] for item in citations} == {"generated", "superseded"}
    assert any(item["pageSection"] == "B" for item in citations)


def test_replace_evidence_snippets_preserves_verified_unchanged_citation_review_metadata(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-reparse-verified", "sourceType": "url", "source": "https://example.test/pws.txt"})
    store.replace_evidence_snippets(
        "opp-reparse-verified",
        doc["id"],
        [{"section": "A", "snippet": "Offeror must submit a plan.", "confidence": 0.8}],
    )
    original = store.evidence_citations("opp-reparse-verified", include_legacy=False)[0]
    verified = store.verify_evidence_citation("opp-reparse-verified", original["id"], "verified", "Capture Lead")

    store.replace_evidence_snippets(
        "opp-reparse-verified",
        doc["id"],
        [{"section": "A", "snippet": "Offeror must submit a plan.", "confidence": 0.55}],
    )

    citations = store.evidence_citations("opp-reparse-verified", include_legacy=False)
    assert len(citations) == 1
    assert citations[0]["id"] == original["id"]
    assert citations[0]["verificationState"] == "verified"
    assert citations[0]["verifier"] == "Capture Lead"
    assert citations[0]["verifiedAt"] == verified["verifiedAt"]
    assert citations[0]["confidence"] == 0.55


def test_replace_evidence_snippets_preserves_rejected_unchanged_citation_review_metadata(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-reparse-rejected", "sourceType": "url", "source": "https://example.test/pws.txt"})
    store.replace_evidence_snippets(
        "opp-reparse-rejected",
        doc["id"],
        [{"section": "A", "snippet": "Offeror must submit a plan.", "confidence": 0.8}],
    )
    original = store.evidence_citations("opp-reparse-rejected", include_legacy=False)[0]
    rejected = store.verify_evidence_citation("opp-reparse-rejected", original["id"], "rejected", "Capture Lead")

    store.replace_evidence_snippets(
        "opp-reparse-rejected",
        doc["id"],
        [{"section": "A", "snippet": "Offeror must submit a plan.", "confidence": 0.55, "reviewed": True}],
    )

    citations = store.evidence_citations("opp-reparse-rejected", include_legacy=False)
    assert len(citations) == 1
    assert citations[0]["id"] == original["id"]
    assert citations[0]["verificationState"] == "rejected"
    assert citations[0]["verifier"] == "Capture Lead"
    assert citations[0]["verifiedAt"] == rejected["verifiedAt"] == ""
    assert citations[0]["reviewed"] is False


def test_replace_evidence_snippets_supersedes_changed_citation_with_coherent_review_metadata(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-reparse-changed", "sourceType": "url", "source": "https://example.test/pws.txt"})
    store.replace_evidence_snippets(
        "opp-reparse-changed",
        doc["id"],
        [{"section": "A", "snippet": "Offeror must submit a plan.", "confidence": 0.8}],
    )
    original = store.evidence_citations("opp-reparse-changed", include_legacy=False)[0]
    verified = store.verify_evidence_citation("opp-reparse-changed", original["id"], "verified", "Capture Lead")

    store.replace_evidence_snippets(
        "opp-reparse-changed",
        doc["id"],
        [{"section": "B", "snippet": "Evaluation will use best value.", "confidence": 0.7}],
    )

    citations = store.evidence_citations("opp-reparse-changed", include_legacy=False)
    superseded = next(item for item in citations if item["id"] == original["id"])
    active = next(item for item in citations if item["verificationState"] != "superseded")
    assert superseded["verificationState"] == "superseded"
    assert superseded["verifier"] == "Capture Lead"
    assert superseded["verifiedAt"] == verified["verifiedAt"]
    assert active["pageSection"] == "B"
    assert active["verificationState"] == "generated"
    assert active["verifier"] == ""
    assert active["verifiedAt"] == ""


def test_replace_evidence_snippets_repeated_reparse_has_no_duplicate_active_citations(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-reparse-repeat", "sourceType": "url", "source": "https://example.test/pws.txt"})
    snippet = {"section": "A", "snippet": "Offeror must submit a plan.", "confidence": 0.8}

    store.replace_evidence_snippets("opp-reparse-repeat", doc["id"], [snippet])
    store.replace_evidence_snippets(
        "opp-reparse-repeat",
        doc["id"],
        [snippet, {**snippet, "section": " A ", "snippet": " Offeror must submit a plan. ", "confidence": 0.4}],
    )
    store.replace_evidence_snippets(
        "opp-reparse-repeat",
        doc["id"],
        [
            {**snippet, "reviewed": True},
            {**snippet, "section": " A ", "snippet": " Offeror must submit a plan. ", "confidence": 0.4},
        ],
    )

    legacy = store.evidence_snippets("opp-reparse-repeat")
    citations = store.evidence_citations("opp-reparse-repeat", include_legacy=False)
    active = [item for item in citations if item["verificationState"] != "superseded"]
    assert len(legacy) == 1
    assert legacy[0]["section"] == "A"
    assert legacy[0]["snippet"] == "Offeror must submit a plan."
    assert legacy[0]["confidence"] == 0.8
    assert len(active) == 1
    assert active[0]["verificationState"] == "verified"
    assert active[0]["verifiedAt"]
    assert active[0]["verifier"] == ""
    assert active[0]["confidence"] == 0.8


def test_mixed_legacy_snippets_are_preserved_without_parser_duplicate_citations(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-mixed-1", "sourceType": "url", "source": "https://example.test/mixed.txt"})
    now = "2026-08-06T00:00:00+00:00"
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO evidence_snippets (notice_id, document_id, section, snippet, confidence, reviewed, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("opp-mixed-1", doc["id"], "Legacy", "Legacy-only snippet.", 0.4, 0, now, now),
        )

    manual = store.create_evidence_citation({"noticeId": "opp-mixed-1", "sourceExcerpt": "Manual citation.", "extractionMethod": "manual"})
    mixed = store.evidence_citations("opp-mixed-1")
    assert {item["sourceExcerpt"] for item in mixed} == {"Manual citation.", "Legacy-only snippet."}
    assert any(item["legacySnippet"] for item in mixed)

    store.replace_evidence_snippets("opp-mixed-1", doc["id"], [{"section": "Legacy", "snippet": "Legacy-only snippet.", "confidence": 0.4}])
    store.replace_evidence_snippets("opp-mixed-1", doc["id"], [{"section": "Legacy", "snippet": "Legacy-only snippet.", "confidence": 0.4}])
    citations = store.evidence_citations("opp-mixed-1")
    parser_created = [item for item in citations if item["extractionMethod"] == "document-intake" and item["sourceExcerpt"] == "Legacy-only snippet."]

    assert len(parser_created) == 1
    assert all(not item["legacySnippet"] for item in citations if item["sourceExcerpt"] == "Legacy-only snippet.")
    assert any(item["id"] == manual["id"] for item in citations)
    with store.connect() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_proposal_artifact_registry_updates_versions_and_events(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    artifact = store.add_proposal_artifact(
        {
            "noticeId": "opp-artifact-1",
            "artifactType": "outline",
            "title": "Initial Outline",
            "content": "# Outline",
            "notes": "First capture draft.",
        }
    )

    assert artifact["noticeId"] == "opp-artifact-1"
    assert artifact["artifactType"] == "outline"
    assert artifact["status"] == "draft"
    assert artifact["format"] == "markdown"
    assert artifact["version"] == 1

    updated = store.update_proposal_artifact(
        artifact["id"],
        {"status": "review", "content": "# Updated Outline", "notes": "Ready for review."},
    )

    assert updated["status"] == "review"
    assert updated["content"] == "# Updated Outline"
    assert updated["version"] == 2
    assert store.proposal_artifacts("opp-artifact-1")[0]["id"] == artifact["id"]
    assert store.proposal_artifact_map(["opp-artifact-1"])["opp-artifact-1"][0]["status"] == "review"
    loaded = store.proposal_artifact(artifact["id"])
    assert loaded and loaded["title"] == "Initial Outline"
    history = store.proposal_artifact_history(artifact["id"])
    assert [item["version"] for item in history] == [2, 1]
    assert history[0]["status"] == "review"
    assert history[1]["content"] == "# Outline"

    event_types = [event["type"] for event in store.get_status("opp-artifact-1")["events"]]
    assert "proposal_artifact_created" in event_types
    assert "proposal_artifact_updated" in event_types


def test_proposal_artifact_validation_rejects_bad_values(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    try:
        store.add_proposal_artifact({"noticeId": "opp-artifact-2", "artifactType": "mystery"})
    except ValueError as exc:
        assert "artifactType must be one of" in str(exc)
    else:
        raise AssertionError("Expected invalid artifact type to raise ValueError")

    artifact = store.add_proposal_artifact({"noticeId": "opp-artifact-2", "artifactType": "notes"})
    try:
        store.update_proposal_artifact(artifact["id"], {"status": "stuck"})
    except ValueError as exc:
        assert "status must be one of" in str(exc)
    else:
        raise AssertionError("Expected invalid artifact status to raise ValueError")


def test_ai_audit_events_store_metadata_without_prompt_text(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    event = store.record_ai_audit(
        {
            "noticeId": "opp-ai-1",
            "action": "summary",
            "provider": "ollama",
            "mode": "deterministic",
            "model": "gemma4:64k",
            "result": "fallback",
            "external": False,
            "message": "Summary assist completed.",
            "prompt": "this should never be stored",
        }
    )
    events = store.ai_audit_events()

    assert event["id"]
    assert events[0]["noticeId"] == "opp-ai-1"
    assert events[0]["action"] == "summary"
    assert events[0]["external"] is False
    assert "prompt" not in events[0]
    assert "this should never" not in str(events[0])


def test_compliance_migration_crud_same_notice_and_review_states(tmp_path: Path):
    db = tmp_path / "sam-radar.sqlite3"
    Store(db)
    store = Store(db)
    doc = store.add_proposal_document({"noticeId": "opp-comp-1", "sourceType": "url", "source": "https://example.test/pws.txt"})
    citation = store.create_evidence_citation(
        {
            "noticeId": "opp-comp-1",
            "documentId": doc["id"],
            "pageSection": "L.3",
            "sourceExcerpt": "Offeror shall submit a staffing plan.",
            "extractedClaim": "Staffing plan required.",
        }
    )
    revision = store.capture_opportunity_revision({"noticeId": "opp-comp-1", "title": "A", "responseDeadline": "2026-08-20T12:00:00+00:00"})["revision"]
    other_doc = store.add_proposal_document({"noticeId": "opp-comp-2", "sourceType": "url", "source": "https://example.test/other.txt"})
    other_citation = store.create_evidence_citation({"noticeId": "opp-comp-2", "documentId": other_doc["id"], "sourceExcerpt": "Other"})
    other_revision = store.capture_opportunity_revision({"noticeId": "opp-comp-2", "title": "B"})["revision"]

    req = store.create_compliance_requirement(
        {
            "noticeId": "opp-comp-1",
            "citationId": citation["id"],
            "revisionId": revision["revisionId"],
            "category": "Submission",
            "requirementText": "Submit a staffing plan.",
            "mandatoryState": "mandatory",
            "owner": "Capture Lead",
            "dueDate": "2026-08-18",
            "responseLocation": "Volume I",
            "status": "open",
            "notes": "Draft outline.",
            "provenance": "manual",
            "generationMetadata": {"source": "test"},
        }
    )

    assert req["noticeId"] == "opp-comp-1"
    assert req["citationId"] == citation["id"]
    assert req["verificationState"] == "needs-review"
    assert req["invalidated"] is False
    assert req["generationMetadata"] == {"source": "test"}
    assert store.compliance_requirements("opp-comp-1")[0]["requirementText"] == "Submit a staffing plan."

    edited = store.update_compliance_requirement(req["id"], {"noticeId": "opp-comp-1", "requirementText": "Submit a revised staffing plan.", "status": "in-progress"})
    assert edited["humanEdited"] is True
    assert edited["status"] == "in-progress"

    verified = store.verify_compliance_requirement(req["id"], "verified", "Reviewer")
    assert verified["verificationState"] == "verified"
    assert verified["verifier"] == "Reviewer"
    assert verified["verifiedAt"]

    for payload, expected in [
        ({"noticeId": "opp-comp-1", "citationId": other_citation["id"], "requirementText": "Bad"}, "citationId does not belong to noticeId"),
        ({"noticeId": "opp-comp-1", "revisionId": other_revision["revisionId"], "requirementText": "Bad"}, "revisionId does not belong to noticeId"),
    ]:
        try:
            store.create_compliance_requirement(payload)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"Expected {expected}")

    for payload, expected in [
        ({"noticeId": "opp-comp-2"}, "noticeId does not match requirement"),
        ({"noticeId": "opp-comp-1", "citationId": other_citation["id"]}, "citationId does not belong to noticeId"),
        ({"noticeId": "opp-comp-1", "revisionId": other_revision["revisionId"]}, "revisionId does not belong to noticeId"),
    ]:
        try:
            store.update_compliance_requirement(req["id"], payload)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"Expected {expected}")

    with store.connect() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"compliance_requirements", "compliance_requirement_lineage"} <= tables
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_compliance_generation_idempotency_review_preservation_and_invalidation_precision(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    revision_a = store.capture_opportunity_revision({"noticeId": "opp-gen-1", "title": "A", "responseDeadline": "2026-08-20T12:00:00+00:00"})["revision"]
    doc = store.add_proposal_document({"noticeId": "opp-gen-1", "sourceType": "url", "source": "https://example.test/pws.txt", "label": "PWS"})
    citation = store.create_evidence_citation(
        {
            "noticeId": "opp-gen-1",
            "documentId": doc["id"],
            "revisionId": revision_a["revisionId"],
            "pageSection": "C.1",
            "sourceExcerpt": "The contractor shall provide weekly status reports. The contractor must submit a quality plan.",
            "extractedClaim": "Weekly reports and a quality plan are required.",
            "verificationState": "verified",
        }
    )
    rejected = store.create_evidence_citation(
        {
            "noticeId": "opp-gen-1",
            "documentId": doc["id"],
            "revisionId": revision_a["revisionId"],
            "sourceExcerpt": "The contractor may attend an optional site visit.",
            "extractedClaim": "Optional site visit.",
            "verificationState": "rejected",
        }
    )

    first = store.generate_compliance_requirements("opp-gen-1")
    second = store.generate_compliance_requirements("opp-gen-1")

    assert len(first["requirements"]) == 1
    assert len(second["requirements"]) == 1
    assert first["createdCount"] == 1
    assert second["createdCount"] == 0
    assert first["requirements"][0]["citationId"] == citation["id"]
    assert all(item["citationId"] != rejected["id"] for item in first["requirements"])

    req = store.update_compliance_requirement(
        first["requirements"][0]["id"],
        {"noticeId": "opp-gen-1", "requirementText": "Human reviewed status reports.", "notes": "Keep this.", "owner": "Lead"},
    )
    verified = store.verify_compliance_requirement(req["id"], "verified", "Reviewer")
    regenerated = store.generate_compliance_requirements("opp-gen-1")["requirements"][0]

    assert regenerated["id"] == req["id"]
    assert regenerated["requirementText"] == "Human reviewed status reports."
    assert regenerated["notes"] == "Keep this."
    assert regenerated["owner"] == "Lead"
    assert regenerated["verificationState"] == "verified"
    assert regenerated["verifiedAt"] == verified["verifiedAt"]

    unscoped_citation = store.create_evidence_citation({"noticeId": "opp-gen-1", "documentId": doc["id"], "sourceExcerpt": "Unscoped citation."})
    unscoped_req = store.create_compliance_requirement({"noticeId": "opp-gen-1", "citationId": unscoped_citation["id"], "requirementText": "Review unscoped citation manually."})

    store.capture_opportunity_revision({"noticeId": "opp-gen-1", "title": "A", "responseDeadline": "2026-08-21T12:00:00+00:00"})
    unchanged = store.compliance_requirement(req["id"])
    assert unchanged["invalidated"] is False

    store.capture_opportunity_revision({"noticeId": "opp-gen-1", "title": "A", "responseDeadline": "2026-08-21T12:00:00+00:00", "description": "Changed requirements"})
    invalidated = store.compliance_requirement(req["id"])
    assert invalidated["invalidated"] is True
    assert "material revision" in invalidated["invalidationReason"]
    assert store.compliance_requirement(unscoped_req["id"])["invalidated"] is False


def test_compliance_generation_deduplicates_normalized_citations_without_row_churn(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-gen-dupe", "sourceType": "url", "source": "https://example.test/pws.txt"})
    first_citation = store.create_evidence_citation(
        {
            "noticeId": "opp-gen-dupe",
            "documentId": doc["id"],
            "pageSection": "L.1",
            "sourceExcerpt": "Offeror shall submit a management plan.",
            "extractedClaim": "Offeror shall submit a management plan.",
        }
    )
    store.create_evidence_citation(
        {
            "noticeId": "opp-gen-dupe",
            "documentId": doc["id"],
            "pageSection": "L.1 copy",
            "sourceExcerpt": "  Offeror   shall submit a management plan.  ",
            "extractedClaim": "Offeror shall submit a management plan.",
        }
    )

    first = store.generate_compliance_requirements("opp-gen-dupe")
    second = store.generate_compliance_requirements("opp-gen-dupe")

    assert first["createdCount"] == 1
    assert first["updatedCount"] == 0
    assert second["createdCount"] == 0
    assert second["updatedCount"] == 0
    assert len(second["requirements"]) == 1
    assert second["requirements"][0]["citationId"] == first_citation["id"]
    assert second["requirements"][0]["updatedAt"] == first["requirements"][0]["updatedAt"]


def test_compliance_generation_is_concurrency_safe_and_preserves_review_state(tmp_path: Path):
    db = tmp_path / "sam-radar.sqlite3"
    store = Store(db)
    doc = store.add_proposal_document({"noticeId": "opp-gen-race", "sourceType": "url", "source": "https://example.test/pws.txt"})
    store.create_evidence_citation(
        {
            "noticeId": "opp-gen-race",
            "documentId": doc["id"],
            "sourceExcerpt": "Contractor shall submit monthly status reports.",
            "extractedClaim": "Contractor shall submit monthly status reports.",
        }
    )

    def generate() -> dict:
        return Store(db).generate_compliance_requirements("opp-gen-race")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: generate(), range(4)))

    requirements = store.compliance_requirements("opp-gen-race")
    assert all(result["ok"] for result in results)
    assert len(requirements) == 1

    reviewed = store.update_compliance_requirement(requirements[0]["id"], {"noticeId": "opp-gen-race", "notes": "Keep review", "owner": "Lead"})
    verified = store.verify_compliance_requirement_for_notice(reviewed["id"], "opp-gen-race", "verified", "Reviewer")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: generate(), range(4)))

    after = store.compliance_requirements("opp-gen-race")
    assert len(after) == 1
    assert after[0]["id"] == reviewed["id"]
    assert after[0]["notes"] == "Keep review"
    assert after[0]["owner"] == "Lead"
    assert after[0]["verificationState"] == "verified"
    assert after[0]["verifiedAt"] == verified["verifiedAt"]


def test_compliance_storage_update_requires_matching_notice_id(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    req = store.create_compliance_requirement({"noticeId": "opp-update-scope", "requirementText": "Submit a plan."})

    for payload, expected in [
        ({"requirementText": "Changed without scope"}, "noticeId is required"),
        ({"noticeId": "other-notice", "requirementText": "Changed across scope"}, "noticeId does not match requirement"),
    ]:
        try:
            store.update_compliance_requirement(req["id"], payload)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"Expected {expected}")

    assert store.compliance_requirement(req["id"])["requirementText"] == "Submit a plan."


def test_compliance_merge_split_lineage_and_exports_escape(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-export-1", "sourceType": "url", "source": "https://example.test/pws.csv", "label": "PWS"})
    cite1 = store.create_evidence_citation({"noticeId": "opp-export-1", "documentId": doc["id"], "pageSection": "A", "sourceExcerpt": "Contractor shall provide \"reports\".", "extractedClaim": "Reports."})
    cite2 = store.create_evidence_citation({"noticeId": "opp-export-1", "documentId": doc["id"], "pageSection": "B", "sourceExcerpt": "Contractor must provide training.", "extractedClaim": "Training."})
    req1 = store.create_compliance_requirement({"noticeId": "opp-export-1", "citationId": cite1["id"], "category": "Submission", "requirementText": "Provide \"reports\", weekly", "status": "open"})
    req2 = store.create_compliance_requirement({"noticeId": "opp-export-1", "citationId": cite2["id"], "category": "Staffing", "requirementText": "Provide training", "status": "open"})

    merged = store.merge_compliance_requirements("opp-export-1", [req1["id"], req2["id"]], {"requirementText": "Provide reports and training", "category": "Submission"})
    assert merged["requirement"]["parentRequirementIds"] == [req1["id"], req2["id"]]
    assert {item["status"] for item in store.compliance_requirements("opp-export-1") if item["id"] in {req1["id"], req2["id"]}} == {"merged"}

    split = store.split_compliance_requirement(
        "opp-export-1",
        merged["requirement"]["id"],
        [
            {"requirementText": "Provide reports", "category": "Submission"},
            {"requirementText": "Provide training", "category": "Staffing"},
        ],
    )
    assert [item["parentRequirementIds"] for item in split["requirements"]] == [[merged["requirement"]["id"]], [merged["requirement"]["id"]]]
    assert store.compliance_requirement(merged["requirement"]["id"])["status"] == "split"

    csv_text = store.export_compliance_csv("opp-export-1")
    md_text = store.export_compliance_markdown("opp-export-1")
    assert '"Provide ""reports"", weekly"' in csv_text
    assert "Source: citation #" in csv_text
    assert "# Compliance Matrix - opp-export-1" in md_text
    assert "\\|reports\\|" in store.export_compliance_markdown("opp-export-1", requirements=[{**req1, "requirementText": "Need |reports|"}])
    assert "APP_WRITE_TOKEN" not in csv_text + md_text

    markdown = store.export_compliance_markdown(
        "opp-export-1",
        requirements=[
            {
                **req1,
                "category": "Line\rCategory",
                "requirementText": "First\r\nSecond\rThird\nFourth",
                "invalidationReason": "Bad\r\nsource",
                "invalidated": True,
            }
        ],
    )
    assert "First Second Third Fourth" in markdown
    assert "Line Category" in markdown
    assert "Bad source" in markdown
    assert "\r" not in markdown


def test_compliance_split_is_atomic_and_exports_mitigate_formula_injection(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-atomic-1", "sourceType": "url", "source": "https://example.test/pws.txt"})
    citation = store.create_evidence_citation({"noticeId": "opp-atomic-1", "documentId": doc["id"], "sourceExcerpt": "Contractor shall submit reports."})
    other_doc = store.add_proposal_document({"noticeId": "opp-atomic-2", "sourceType": "url", "source": "https://example.test/other.txt"})
    other_citation = store.create_evidence_citation({"noticeId": "opp-atomic-2", "documentId": other_doc["id"], "sourceExcerpt": "Other"})
    req = store.create_compliance_requirement({"noticeId": "opp-atomic-1", "citationId": citation["id"], "requirementText": "Submit reports"})

    try:
        store.split_compliance_requirement(
            "opp-atomic-1",
            req["id"],
            [
                {"requirementText": "Submit monthly reports"},
                {"citationId": other_citation["id"], "requirementText": "Use another notice citation"},
            ],
        )
    except ValueError as exc:
        assert "citationId does not belong to noticeId" in str(exc)
    else:
        raise AssertionError("Expected split with cross-notice citation to fail")

    remaining = store.compliance_requirements("opp-atomic-1")
    assert [item["id"] for item in remaining] == [req["id"]]
    assert remaining[0]["status"] == "open"
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM compliance_requirement_lineage").fetchone()[0] == 0

    csv_text = store.export_compliance_csv(
        "opp-atomic-1",
        requirements=[
            {
                **req,
                "category": "=cmd|' /C calc'!A0",
                "requirementText": "+SUM(1,2)",
                "owner": "@owner",
                "notes": "-note",
            }
        ],
    )
    assert "'=cmd|' /C calc'!A0" in csv_text
    assert "'+SUM(1,2)" in csv_text
    assert "'@owner" in csv_text
    assert "'-note" in csv_text
