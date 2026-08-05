from pathlib import Path

from sam_radar.config import BusinessProfile, Settings
from sam_radar.reports import build_html_report, build_report_payload


def test_report_uses_configured_business_name_and_normalized_time(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    payload = {
        "postedFrom": "08/01/2026",
        "postedTo": "08/04/2026",
        "matches": [
            {
                "noticeId": "abc",
                "title": "Security Support",
                "type": "Sources Sought",
                "postedDate": "2026-08-04",
                "responseDeadline": "2026-08-10T09:00:00-04:00",
                "score": 10,
                "reasons": ["keywords: security"],
                "recommendation": "Pursue",
            }
        ],
        "errors": [],
    }
    report = build_report_payload(payload, profile, settings, unseen=payload["matches"])
    html = build_html_report(report)
    assert "ExampleTech" in html
    assert "MDT" in html
    assert "Example Technology Services LLC" not in html


def test_report_renders_workflow_controls_and_safe_status_api_hooks(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    payload = {
        "postedFrom": "08/01/2026",
        "postedTo": "08/04/2026",
        "matches": [
            {
                "noticeId": "abc-123",
                "title": "Security Support",
                "type": "Sources Sought",
                "postedDate": "2026-08-04",
                "responseDeadline": "2026-08-10T09:00:00-04:00",
                "score": 10,
                "reasons": ["keywords: security"],
                "recommendation": "Pursue",
                "workflowStatus": "teaming",
                "workflowNotes": "Ask partner about FedRAMP past performance.",
                "workflowUpdatedAt": "2026-08-05T00:54:39+00:00",
                "workflowPriority": "urgent",
                "workflowOwner": "Capture Lead",
                "workflowNextAction": "Pull the PWS",
                "workflowFollowUpAt": "2026-08-06",
                "workflowDecisionReason": "Strong fit",
                "workflowDocuments": [{"label": "PWS", "url": "https://example.test/pws.pdf", "reviewed": False}],
                "workflowEvents": [{"type": "status_changed", "message": "Status changed", "createdAt": "2026-08-05T00:54:39+00:00"}],
            }
        ],
        "errors": [],
    }
    report = build_report_payload(payload, profile, settings, unseen=[])
    html = build_html_report(report)

    assert 'data-id="abc-123"' in html
    assert 'class="workflow-status"' in html
    assert 'value="teaming"' in html
    assert 'Ask partner about FedRAMP past performance.' in html
    assert '/api/status/' in html
    assert 'X-SAM-RADAR-TOKEN' in html
    assert 'samRadarWriteToken' in html
    assert 'data-view="board"' in html
    assert 'data-view="queue"' in html
    assert 'id="detail-modal"' in html
    assert 'name="decisionReason"' in html
    assert 'class="documents"' in html
    assert 'class="timeline"' in html
    assert 'max-width:min(1760px,calc(100vw - 48px))' in html
    assert 'class="tool-group view-group"' in html
    assert 'class="tool-group filter-group"' in html
    assert 'class="tool-group action-group"' in html
    assert 'width:min(1320px,calc(100vw - 40px))' in html
    assert 'overflow-wrap:anywhere;overflow-x:hidden' in html
    assert '.detail-fit a{overflow-wrap:anywhere;word-break:break-word}' in html
    assert '.detail-sections>section,.documents,.doc-row,.doc-row label{min-width:0}' in html
    assert 'grid-template-columns:minmax(120px,.8fr) minmax(180px,1.5fr) minmax(92px,auto) auto' in html
    assert '.doc-row input{width:100%;min-width:0}' in html
    assert 'grid-template-columns:repeat(8,minmax(210px,1fr))' in html
    assert 'id="theme-button"' in html
    assert 'Use your local APP_WRITE_TOKEN here. This is separate from your SAM.gov API key.' in html
    assert 'Updated: Aug 4, 2026 6:54 PM MDT' in html
    assert '<script id="report-data" type="application/json">{"summary"' in html
    assert '&quot;summary&quot;' not in html
