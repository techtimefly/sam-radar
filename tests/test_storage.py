from pathlib import Path

from sam_radar.storage import Store


def test_status_round_trip_and_defaults(tmp_path: Path):
    store = Store(tmp_path / "sam-radar.sqlite3")

    assert store.get_status("abc") == {"noticeId": "abc", "status": "new", "notes": "", "updatedAt": ""}

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
