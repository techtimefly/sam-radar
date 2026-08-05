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
