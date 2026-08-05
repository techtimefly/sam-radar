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
