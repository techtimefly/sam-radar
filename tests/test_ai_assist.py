import json
from pathlib import Path

from sam_radar.ai_assist import (
    deterministic_gap_analysis,
    deterministic_requirements,
    deterministic_summary,
    opportunity_requirements,
    opportunity_summary,
)
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


def test_deterministic_requirements_groups_source_aware_items():
    opp = {
        "title": "CMMC Support",
        "descriptionParagraphs": [
            "The contractor shall provide secure engineering support. Submit a technical proposal by the due date. Evaluation will consider technical approach and past performance. Include SF 1449 and all attachments."
        ],
    }
    evidence = [{"section": "PWS", "snippet": "Offeror must provide CMMC documentation before award."}]

    result = deterministic_requirements(opp, evidence)

    assert "shall provide secure engineering" in result["requirements"][0]["text"]
    assert any("Submit a technical proposal" in item["text"] for item in result["submissionInstructions"])
    assert any("Evaluation will consider" in item["text"] for item in result["evaluationCriteria"])
    assert any("SF 1449" in item["text"] for item in result["requiredForms"])
    assert any(
        item["source"].startswith("Parsed document evidence")
        for items in result.values()
        for item in items
    )


def test_opportunity_requirements_falls_back_without_ai(tmp_path: Path):
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
                "descriptionParagraphs": ["Contractor shall provide DevSecOps support. Submit quote by email."],
            }
        ]
    }
    (reports / "latest.json").write_text(json.dumps(report))
    settings = Settings(sam_gov_api_key="test", reports_dir=reports, data_dir=data)
    store = Store(data / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "abc", "sourceType": "url", "source": "https://example.test/doc.txt", "label": "Doc"})
    store.replace_evidence_snippets("abc", doc["id"], [{"section": "Instructions", "snippet": "Evaluation will use best value."}])

    result = opportunity_requirements(settings, {"noticeId": "abc"})

    assert result["ok"] is True
    assert result["mode"] == "deterministic"
    assert result["provider"] == "none"
    assert result["requirements"]["requirements"]
    assert result["requirements"]["evaluationCriteria"]


def test_deterministic_gap_analysis_flags_capture_blockers():
    opp = {
        "title": "CMMC Support",
        "score": 9,
        "fitReason": "Strong DevSecOps and CMMC fit.",
        "descriptionParagraphs": [
            "Contractor shall provide secure engineering support. Evaluation will consider technical approach and past performance. Include SF 1449."
        ],
        "urgency": "high",
    }
    result = deterministic_gap_analysis(opp, [])
    titles = [item["title"] for item in result["gaps"]]

    assert "Proposal workspace not started" in titles
    assert "Solicitation package not registered" in titles
    assert "Compliance evidence needed" in titles
    assert "Compressed response window" in titles
    assert result["strengths"]


def test_opportunity_gaps_falls_back_without_ai(tmp_path: Path):
    reports = tmp_path / "reports"
    data = tmp_path / "data"
    reports.mkdir()
    report = {
        "matches": [
            {
                "noticeId": "abc",
                "title": "Security Support",
                "score": 8,
                "fitReason": "Security fit based on CMMC.",
                "descriptionParagraphs": ["Contractor shall provide DevSecOps support."],
            }
        ]
    }
    (reports / "latest.json").write_text(json.dumps(report))
    settings = Settings(sam_gov_api_key="test", reports_dir=reports, data_dir=data)

    from sam_radar.ai_assist import opportunity_gaps

    result = opportunity_gaps(settings, {"noticeId": "abc"})

    assert result["ok"] is True
    assert result["mode"] == "deterministic"
    assert result["provider"] == "none"
    assert result["analysis"]["gaps"]
