import json
from pathlib import Path

from sam_radar.ai_assist import (
    deterministic_gap_analysis,
    deterministic_requirements,
    deterministic_summary,
    generate_prime_proposal_templates,
    generate_subcontractor_proposal_templates,
    opportunity_requirements,
    opportunity_summary,
)
from sam_radar.config import Settings
from sam_radar.storage import Store


def test_export_proposal_artifact_markdown_includes_metadata(tmp_path: Path):
    from sam_radar.core import export_proposal_artifact_markdown

    data = tmp_path / "data"
    settings = Settings(sam_gov_api_key="test", data_dir=data)
    store = Store(data / "sam-radar.sqlite3")
    artifact = store.add_proposal_artifact(
        {
            "noticeId": "export-1",
            "artifactType": "prime-proposal",
            "title": "Prime Draft",
            "status": "review",
            "content": "## Technical Approach\nDraft text.",
            "notes": "Ready for capture review.",
        }
    )

    exported = export_proposal_artifact_markdown(settings, artifact["id"])

    assert exported["ok"] is True
    assert exported["filename"] == "Prime-Draft.md"
    assert "# Prime Draft" in exported["content"]
    assert "- Notice ID: export-1" in exported["content"]
    assert "## Technical Approach" in exported["content"]


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
    assert summary["sourceFacts"] == ["Offeror must provide CMMC documentation before award."]
    assert summary["businessAssumptions"] == ["Security fit based on DJ01."]
    assert summary["aiRecommendations"] == ["Review the opportunity and confirm bid/no-bid posture."]


def test_deterministic_prime_templates_builds_editable_artifacts():
    from sam_radar.ai_assist import deterministic_prime_templates

    opp = {
        "title": "CMMC Support",
        "organization": "Example Agency",
        "dueDisplay": "Aug 9, 2026 5:00 PM MDT",
        "fitReason": "Strong DevSecOps and CMMC fit.",
        "descriptionParagraphs": [
            "Contractor shall provide secure engineering support. Submit a technical proposal by email. Evaluation will consider technical approach. Include SF 1449."
        ],
    }
    artifacts = deterministic_prime_templates(opp, [{"section": "Security", "snippet": "Offeror must provide CMMC documentation before award."}])

    assert [item["artifactType"] for item in artifacts] == ["prime-proposal", "compliance-matrix", "forms-checklist", "questions"]
    assert "# Prime Proposal Draft Template" in artifacts[0]["content"]
    assert "| Category | Requirement | Source | Response Owner | Status |" in artifacts[1]["content"]
    assert "SF 1449" in artifacts[2]["content"]
    assert "Compliance evidence needed" in artifacts[3]["content"]


def test_generate_prime_proposal_templates_creates_and_updates_artifacts(tmp_path: Path):
    reports = tmp_path / "reports"
    data = tmp_path / "data"
    reports.mkdir()
    report = {
        "matches": [
            {
                "noticeId": "abc",
                "title": "Security Support",
                "organization": "Example Agency",
                "score": 9,
                "dueDisplay": "Aug 9, 2026 5:00 PM MDT",
                "fitReason": "Strong DevSecOps fit.",
                "descriptionParagraphs": ["Contractor shall provide DevSecOps support. Submit quote by email. Include attachments."],
            }
        ]
    }
    (reports / "latest.json").write_text(json.dumps(report))
    settings = Settings(sam_gov_api_key="test", reports_dir=reports, data_dir=data)
    store = Store(data / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "abc", "sourceType": "url", "source": "https://example.test/doc.txt", "label": "Doc"})
    store.replace_evidence_snippets("abc", doc["id"], [{"section": "Evaluation", "snippet": "Evaluation will use best value and technical approach."}])

    first = generate_prime_proposal_templates(settings, {"noticeId": "abc"})
    second = generate_prime_proposal_templates(settings, {"noticeId": "abc"})

    assert first["ok"] is True
    assert first["proposal"]["role"] == "prime"
    assert len(first["generated"]) == 4
    assert len(second["artifacts"]) == 4
    assert all(item["version"] == 1 for item in second["generated"])
    assert {event["type"] for event in store.get_status("abc")["events"]} >= {"proposal_created", "proposal_artifact_created"}


def test_deterministic_subcontractor_templates_builds_partner_artifacts():
    from sam_radar.ai_assist import deterministic_subcontractor_templates

    opp = {
        "title": "Security Support",
        "organization": "Example Agency",
        "dueDisplay": "Aug 9, 2026 5:00 PM MDT",
        "fitReason": "Strong DevSecOps fit.",
        "descriptionParagraphs": [
            "Contractor shall provide secure engineering support. Submit a technical proposal by email. Evaluation will consider technical approach."
        ],
    }
    artifacts = deterministic_subcontractor_templates(opp, [{"section": "Security", "snippet": "Offeror must provide CMMC documentation before award."}])

    assert [item["artifactType"] for item in artifacts] == ["subcontractor", "compliance-matrix", "forms-checklist", "questions"]
    assert "# Subcontractor Capability Response Template" in artifacts[0]["content"]
    assert "| Category | Requirement | Prime Owner | Subcontractor Owner | Status |" in artifacts[1]["content"]
    assert "Capability statement" in artifacts[2]["content"]
    assert "Questions For Prime" in artifacts[3]["content"]


def test_generate_subcontractor_templates_creates_partner_workspace(tmp_path: Path):
    reports = tmp_path / "reports"
    data = tmp_path / "data"
    reports.mkdir()
    report = {
        "matches": [
            {
                "noticeId": "sub-abc",
                "title": "Security Support",
                "organization": "Example Agency",
                "score": 9,
                "dueDisplay": "Aug 9, 2026 5:00 PM MDT",
                "fitReason": "Strong DevSecOps fit.",
                "descriptionParagraphs": ["Contractor shall provide DevSecOps support. Submit quote by email."],
            }
        ]
    }
    (reports / "latest.json").write_text(json.dumps(report))
    settings = Settings(sam_gov_api_key="test", reports_dir=reports, data_dir=data)

    result = generate_subcontractor_proposal_templates(settings, {"noticeId": "sub-abc"})

    assert result["ok"] is True
    assert result["proposal"]["role"] == "subcontractor"
    assert len(result["generated"]) == 4
    assert any(item["title"] == "Subcontractor Capability Response Template" for item in result["artifacts"])


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
        item["source"].startswith("Evidence citation")
        for items in result.values()
        for item in items
    )
    assert any(item["category"] == "source-fact" for items in result.values() for item in items)


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
    assert result["sourceFacts"] == ["Evaluation will use best value."]
    assert result["aiRecommendations"]


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
    assert result["sourceFacts"] == []
    assert result["businessAssumptions"]
    assert result["aiRecommendations"]
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
