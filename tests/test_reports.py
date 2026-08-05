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
                "descriptionUrl": "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid=abc-123",
                "descriptionParagraphs": ["The agency needs secure engineering support.", "Work includes training and compliance automation."],
                "descriptionStatus": "available",
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
    assert 'class="tab-row"' in html
    assert 'class="command-row"' in html
    assert 'class="tool-group view-group" role="tablist" aria-label="Report views"' in html
    assert 'class="tool-group filter-group"' in html
    assert 'class="tool-group action-group"' in html
    assert 'data-view="executive" role="tab"' in html
    assert 'id="executive-view" class="view active"' in html
    assert 'id="list-view" class="view active"' not in html
    assert 'width:min(1320px,calc(100vw - 40px))' in html
    assert 'overflow-wrap:anywhere;overflow-x:hidden' in html
    assert '.detail-fit a{overflow-wrap:anywhere;word-break:break-word}' in html
    assert '.detail-sections>section,.documents,.doc-row,.doc-row label{min-width:0}' in html
    assert 'grid-template-columns:minmax(120px,.8fr) minmax(180px,1.5fr) minmax(92px,auto) auto' in html
    assert '.doc-row input{width:100%;min-width:0}' in html
    assert 'grid-template-columns:repeat(var(--lane-count,8),minmax(210px,1fr))' in html
    assert '<nav class="toolbar commandbar" aria-label="Report controls">' in html
    assert 'id="lane-button"' in html
    assert 'id="lane-panel" class="lane-popover"' in html
    assert 'id="lane-active"' in html
    assert 'id="lane-hide-empty"' in html
    assert 'id="back-to-top" class="back-to-top"' in html
    assert 'samRadarLanePrefs' in html
    assert "const statusOrder=['new','reviewing','pursue','teaming','submitted','monitor','no-bid','archived']" in html
    assert 'grid-template-columns:repeat(var(--lane-count,8),minmax(210px,1fr))' in html
    assert '.back-to-top.visible' in html
    assert 'class="lane-head"' in html
    assert 'class="lane-cards"' in html
    assert '.lane{min-height:360px;padding:10px;display:grid;grid-template-rows:auto 1fr' in html
    assert '.lane-head{position:relative;z-index:1' in html
    assert '.lane-head{position:sticky' not in html
    assert '.lane-cards{display:grid;align-content:start;gap:8px;min-width:0}' in html
    assert '.toolbar{position:static}' not in html
    assert 'id="filter-menu-button"' in html
    assert 'id="tools-menu-button"' in html
    assert 'id="more-view-button"' in html
    assert 'class="list-controls"' in html
    assert 'id="expand-all"' in html
    assert 'id="collapse-all"' in html
    assert 'class="card-toggle"' in html
    assert 'function setCardCollapsed(card,collapsed)' in html
    assert 'initializeListDensity()' in html
    assert '.mobile-menu-row{display:none}' in html
    assert '.filter-group.open,.action-group.open{display:flex}' in html
    assert '.opp.collapsed .follow-row,.opp.collapsed .analysis,.opp.collapsed .description-preview,.opp.collapsed .workflow,.opp.collapsed .dims{display:none}' in html
    assert '.view-group.more-open button{display:inline-flex}' in html
    assert 'function configureDetailSurface(surface,origin)' in html
    assert 'Back to ${returnLabel(origin)}' in html
    assert '.modal.mobile-record.open{display:block}' in html
    assert '.modal.mobile-record .modal-close span{position:static' in html
    assert 'class="manual-controls"' in html
    assert 'id="manual-expand-all"' in html
    assert 'id="manual-collapse-all"' in html
    assert 'class="manual-toggle"' in html
    assert 'function setManualCollapsed(card,collapsed)' in html
    assert "openDetail(el.dataset.id,'board')" in html
    assert "openManualDetail(btn.dataset.id,'manual')" in html
    assert '.board-view-cta' in html
    assert '#list-view .opp .facts,#list-view .opp .follow-row' in html
    assert 'class="description-preview"' in html
    assert 'class="detail-description"' in html
    assert 'class="manual-detail-description"' in html
    assert 'class="manual-description"' in html
    assert 'function refreshAfterManualTrack(msg)' in html
    assert 'descriptionPreviewHtml(opp)' in html
    assert 'Search Intelligence Studio' in html
    assert 'id="search-studio-view"' in html
    assert 'id="manual-profile"' in html
    assert 'id="manual-setaside"' in html
    assert 'list="manual-naics-codes"' in html
    assert 'id="manual-naics-codes"' in html
    assert 'data-unsave-code' in html
    assert "iconLabel('check','Saved')" in html
    assert '/api/search-reference/delete' in html
    assert 'manual-search-form' in html
    assert 'manual-field-setaside' in html
    assert 'id="manual-clear"' in html
    assert 'Manual search fields cleared.' in html
    assert '.manual-field-action{grid-column:span 1' in html
    assert 'manual-search-head' in html
    assert 'class="small-ghost"' in html
    assert 'id="lookup-status"' in html
    assert 'id="coach-status"' in html
    assert 'Saving code...' in html
    assert 'Draft profile saved.' in html
    assert 'function renderSearchIntel(data)' in html
    assert 'function loadSearchIntel' in html
    assert '/api/search-coach' in html
    assert 'function descriptionHtml(opp)' in html
    assert 'The agency needs secure engineering support.' in html
    assert 'DESCRIPTION' in html
    assert 'SAM.gov Description' not in html
    assert 'id="theme-button"' in html
    assert 'id="theme-button" class="theme-toggle"' in html
    assert ".theme-toggle{min-width:92px;white-space:nowrap}" in html
    assert "laneButton?.addEventListener('click',event=>{event.stopPropagation()" in html
    assert "!laneButton?.contains(event.target)" in html
    assert "!tokenButton.contains(event.target)" in html
    assert 'Use your local APP_WRITE_TOKEN here. This is separate from your SAM.gov API key.' in html
    assert 'class="icon-sprite"' in html
    assert 'id="icon-refresh"' in html
    assert 'id="icon-archive"' in html
    assert "--text-lg:16px" in html
    assert "font-variant-numeric:tabular-nums" in html
    assert 'class="icon" aria-hidden="true"><use href="#icon-refresh"' in html
    assert 'class="icon" aria-hidden="true"><use href="#icon-kanban"' in html
    assert 'class="icon" aria-hidden="true"><use href="#icon-external"' in html
    assert 'id="manual-detail-modal"' in html
    assert 'class="manual-detail"' in html
    assert 'class="manual-detail-track refresh"' in html
    assert 'class="close-modal modal-close" type="button" aria-label="Close"' in html
    assert 'class="close-manual-modal modal-close" type="button" aria-label="Close"' in html
    assert '.modal-close{width:40px;height:40px;min-width:40px;padding:0' in html
    assert '<option value="7" selected>7 days</option>' in html
    assert '<option value="25" selected>25</option>' in html
    assert ".lane-grid label,.lane-empty{display:grid;grid-template-columns:18px 1fr" in html
    assert 'Updated: Aug 4, 2026 6:54 PM MDT' in html
    assert '<script id="report-data" type="application/json">{"summary"' in html
    assert '&quot;summary&quot;' not in html
