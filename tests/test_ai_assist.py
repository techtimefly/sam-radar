import json
from pathlib import Path

from sam_radar.ai_assist import deterministic_summary, opportunity_summary
from sam_radar.config import Settings
from sam_radar.storage import Store


def test_deterministic_summary_uses_description_and_evidence():
    opp = {
        "title": "Security Support",
        "fitReason": "Security fit based on DJ01.",
        "dueDisplay": "Aug 9, 2026 5:00 PM MDT",
        "descriptionParagraphs": ["The agency needs secure engineering support. Work includes compliance automation."],
    }
    evidence = [{"snippet": "Offeror must provide CMMC documentation before award."}]

    summary = deterministic_summary(opp, evidence)

    assert "secure engineering" in summary["overview"]
    assert summary["fit"] == "Security fit based on DJ01."
    assert summary["evidence"] == ["Offeror must provide CMMC documentation before award."]
    assert "SAM.gov description" in summary["sources"]


def test_opportunity_summary_falls_back_without_ai(tmp_path: Path):
    reports = tmp_path / "reports"
    data = tmp_path / "data"
    reports.mkdir()
    report = {
        "matches": [
            {
                "noticeId": "abc",
                "title": "Security Support",
                "organization": "Example Agency",
                "dueDisplay": "Aug 9, 2026 5:00 PM MDT",
                "fitReason": "Strong DevSecOps fit.",
                "descriptionParagraphs": ["The agency needs secure engineering support."],
            }
        ]
    }
    (reports / "latest.json").write_text(json.dumps(report))
    settings = Settings(sam_gov_api_key="test", reports_dir=reports, data_dir=data)
    store = Store(data / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "abc", "sourceType": "url", "source": "https://example.test/doc.txt", "label": "Doc"})
    store.replace_evidence_snippets("abc", doc["id"], [{"section": "Requirement", "snippet": "Submit a compliance matrix."}])

    result = opportunity_summary(settings, {"noticeId": "abc"})

    assert result["ok"] is True
    assert result["mode"] == "deterministic"
    assert result["provider"] == "none"
    assert "secure engineering" in result["summary"]["overview"]
    assert result["summary"]["evidence"] == ["Submit a compliance matrix."]
