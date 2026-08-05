from pathlib import Path

from sam_radar.config import Settings
from sam_radar.core import add_manual_opportunity, manual_search, refresh_report


def write_profile(path: Path) -> None:
    path.write_text(
        """
business:
  name: Example Technology Services LLC
  dba: ExampleTech
capabilities:
  - security
naics:
  primary:
    - "541512"
psc:
  - DJ01
set_asides:
  - SDVOSBC
keywords:
  strong:
    - security
""".lstrip()
    )


def test_manual_search_marks_current_report_duplicates_and_does_not_overwrite_report(tmp_path: Path, monkeypatch):
    profile = tmp_path / "business.yaml"
    reports = tmp_path / "reports"
    reports.mkdir()
    data_dir = tmp_path / "data"
    write_profile(profile)
    latest = reports / "latest.json"
    original = '{"matches":[{"noticeId":"dup-1","title":"Already in report"}]}\n'
    latest.write_text(original)
    settings = Settings(sam_gov_api_key="test-key", profile_path=profile, reports_dir=reports, data_dir=data_dir)

    def fake_fetch(params, timeout=8):
        return {
            "opportunitiesData": [
                {
                    "noticeId": "dup-1",
                    "title": "Security Engineering Support",
                    "type": "Sources Sought",
                    "postedDate": "2099-01-01",
                    "responseDeadLine": "2099-02-01T17:00:00+00:00",
                    "naicsCode": "541512",
                    "classificationCode": "DJ01",
                    "fullParentPathName": "Example Agency",
                    "uiLink": "https://sam.gov/opp/dup-1/view",
                },
                {
                    "noticeId": "new-1",
                    "title": "Security Automation Support",
                    "type": "Solicitation",
                    "postedDate": "2099-01-01",
                    "responseDeadLine": "2099-02-01T17:00:00+00:00",
                    "naicsCode": "541512",
                    "classificationCode": "DJ01",
                    "fullParentPathName": "Example Agency",
                    "uiLink": "https://sam.gov/opp/new-1/view",
                },
            ]
        }

    monkeypatch.setattr("sam_radar.core.fetch_json", fake_fetch)

    result = manual_search(settings, {"keyword": "security", "days": 7, "limit": 2})

    assert result["ok"] is True
    assert result["reportsUnchanged"] is True
    assert latest.read_text() == original
    by_id = {item["noticeId"]: item for item in result["matches"]}
    assert by_id["dup-1"]["alreadyTracked"] is True
    assert by_id["new-1"]["alreadyTracked"] is False

    duplicate = add_manual_opportunity(settings, by_id["dup-1"])
    assert duplicate == {"ok": False, "duplicate": True, "error": "Already tracked"}


def test_refresh_report_includes_manual_tracked_opportunities(tmp_path: Path, monkeypatch):
    profile = tmp_path / "business.yaml"
    reports = tmp_path / "reports"
    data_dir = tmp_path / "data"
    write_profile(profile)
    settings = Settings(
        sam_gov_api_key="test-key",
        profile_path=profile,
        reports_dir=reports,
        data_dir=data_dir,
        enable_descriptions=False,
    )
    manual = {
        "noticeId": "manual-report-1",
        "title": "Tracked Manual Opportunity",
        "type": "Solicitation",
        "postedDate": "2099-01-01",
        "responseDeadline": "2099-02-01T17:00:00+00:00",
        "naicsCode": "541512",
        "classificationCode": "DJ01",
        "organization": "Example Agency",
        "url": "https://sam.gov/opp/manual-report-1/view",
        "score": 9,
        "reasons": ["manual fit"],
        "recommendation": "Monitor",
    }
    add_manual_opportunity(settings, manual)

    monkeypatch.setattr(
        "sam_radar.core.search_opportunities",
        lambda *args, **kwargs: {
            "matches": [],
            "postedFrom": "01/01/2099",
            "postedTo": "01/08/2099",
            "errors": [],
        },
    )

    result = refresh_report(settings)

    assert result["ok"] is True
    latest = (reports / "latest.json").read_text()
    assert "Tracked Manual Opportunity" in latest
    assert "manual-report-1" in latest
