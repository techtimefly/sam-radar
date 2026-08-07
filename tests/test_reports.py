import datetime as dt
import json
import re
import subprocess
import textwrap
from pathlib import Path

from sam_radar import reports as reports_module
from sam_radar.command_center import build_command_center
from sam_radar.config import BusinessProfile, Settings
from sam_radar.reports import build_csv_report, build_html_report, build_report_payload, write_reports


def _extract_report_script(html: str) -> str:
    match = re.search(r"<script>(?P<script>.*)</script></body></html>", html, flags=re.DOTALL)
    assert match is not None
    return match.group("script")


def _extract_one_line_js_function(script: str, name: str) -> str:
    start = script.index(f"function {name}(")
    return script[start : script.index("\n", start)]


class FixedReportDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        instant = dt.datetime(2026, 8, 7, 5, 30, tzinfo=dt.UTC)
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


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


def test_report_renders_compliance_matrix_controls_and_js_syntax(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    payload = {
        "postedFrom": "08/01/2026",
        "postedTo": "08/04/2026",
        "matches": [
            {
                "noticeId": "abc-<unsafe>",
                "title": "Security Support <script>",
                "type": "Solicitation",
                "postedDate": "2026-08-04",
                "responseDeadline": "2026-08-10T09:00:00-04:00",
                "score": 10,
                "reasons": ["keywords: security"],
                "recommendation": "Pursue",
                "proposal": {"id": 1, "role": "prime", "stage": "requirements", "stageLabel": "Requirements", "stages": []},
                "complianceRequirements": [
                    {
                        "id": 12,
                        "noticeId": "abc-<unsafe>",
                        "citationId": 4,
                        "revisionId": "abc-rev",
                        "category": "Submission",
                        "requirementText": "Submit <management> plan & certify.",
                        "mandatoryState": "mandatory",
                        "owner": "Capture Lead",
                        "dueDate": "2026-08-08",
                        "responseLocation": "Volume I",
                        "status": "open",
                        "notes": "Needs review",
                        "verificationState": "needs-review",
                        "verifier": "",
                        "invalidated": True,
                        "invalidationReason": "Citation predates material revision",
                        "generationMetadata": {"source": "test"},
                    }
                ],
            }
        ],
        "errors": [],
    }
    report = build_report_payload(payload, profile, settings, unseen=[])
    html = build_html_report(report)
    script = _extract_report_script(html)

    assert "Compliance Matrix" in html
    assert "compliance-matrix" in html
    assert "compliance-filter-category" in html
    assert "compliance-filter-status" in html
    assert "compliance-filter-mandatory" in html
    assert "compliance-filter-verification" in html
    assert "compliance-filter-invalidated" in html
    assert "compliance-filter-missing-citation" in html
    assert "/api/compliance/generate" in html
    assert "/api/compliance/add" in html
    assert "/api/compliance/update" in html
    assert "/api/compliance/verify" in html
    assert "/api/compliance/merge" in html
    assert "/api/compliance/split" in html
    assert "/api/compliance-export/" in html
    assert "Submit &lt;management&gt; plan &amp; certify." in html or "\\u003cmanagement>" in html
    assert "<management>" not in html
    assert "aria-label=\"Compliance category filter\"" in html
    assert "window.confirm('Reject this compliance requirement?')" in html
    assert "window.confirm('Merge selected compliance requirements?')" in html
    assert "window.confirm('Split this compliance requirement?')" in html

    subprocess.run(["node", "--check"], input=script, text=True, check=True)


def test_report_adds_primary_command_center_view_and_safe_actions(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    report = build_report_payload(
        {
            "postedFrom": "08/01/2026",
            "postedTo": "08/04/2026",
            "matches": [
                {
                    "noticeId": "abc-123",
                    "title": "Security <script>alert(1)</script>",
                    "organization": "Unsafe <b>Agency</b>",
                    "score": 13,
                    "recommendation": "Pursue",
                    "workflowStatus": "new",
                    "responseDeadline": "2026-08-07T01:00:00Z",
                    "evidenceCitations": [{"id": 4, "verificationState": "needs-review"}],
                    "complianceRequirements": [{"id": 7, "status": "open", "verificationState": "needs-review"}],
                }
            ],
            "errors": [],
        },
        profile,
        settings,
        unseen=[],
    )
    html = build_html_report(report)
    script = _extract_report_script(html)

    assert report["commandCenter"]["metrics"]["highFitUnreviewed"] == 1
    assert 'data-view="command"' in html
    assert 'id="command-view"' in html
    assert "Pursuit Command Center" in html
    assert "Do Today" in html
    assert "Portfolio Health" in html
    assert "Recent Intelligence" in html
    assert "command-action" in html
    assert "command-quick-action" in html
    assert "command-action-message" in html
    assert "Security \\u003cscript>alert(1)\\u003c/script>" in html
    assert "Security <script>" not in html
    assert "Unsafe <b>Agency</b>" not in html
    assert "function commandCenterHtml" in html
    assert "function routeCommandTarget" in html
    assert "function runCommandQuickAction" in html
    assert "/api/status/" in html
    assert "/api/evidence/verify" in html
    assert "/api/amendments/mark-reviewed" in html
    assert "/api/compliance/verify" in html
    assert "/api/proposals/stage" in html
    assert "renderCommandCenter()" in html
    assert "@media(max-width:860px)" in html

    subprocess.run(["node", "--check"], input=script, text=True, check=True)


def test_report_command_center_js_routes_and_mutates_with_token(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    report = build_report_payload(
        {
            "postedFrom": "08/01/2026",
            "postedTo": "08/04/2026",
            "matches": [
                {
                    "noticeId": "abc",
                    "title": "Security Support",
                    "workflowStatus": "new",
                    "workflowPriority": "normal",
                    "score": 13,
                    "responseDeadline": "2026-08-07T01:00:00Z",
                    "evidenceCitations": [{"id": 4, "verificationState": "needs-review"}],
                    "amendmentSummary": {"materialChangeCount": 1, "unreadCount": 1, "staleEvidenceCount": 0},
                    "amendmentTimeline": [{"id": 5, "material": True, "readAt": ""}],
                    "complianceRequirements": [{"id": 7, "status": "open", "verificationState": "needs-review"}],
                    "proposal": {"id": 1, "stage": "requirements", "stageLabel": "Requirements"},
                }
            ],
            "errors": [],
        },
        profile,
        settings,
        unseen=[],
    )
    script = _extract_report_script(build_html_report(report))
    helpers = "\n".join(
        [
            _extract_one_line_js_function(script, "escapeHtml"),
            _extract_one_line_js_function(script, "icon"),
            _extract_one_line_js_function(script, "iconLabel"),
            _extract_one_line_js_function(script, "safeClassToken"),
            _extract_one_line_js_function(script, "commandActionHtml"),
            _extract_one_line_js_function(script, "commandCenterHtml"),
            _extract_one_line_js_function(script, "routeCommandTarget"),
            _extract_one_line_js_function(script, "writeCommandJson"),
            _extract_one_line_js_function(script, "commandKindsForQuickAction"),
            _extract_one_line_js_function(script, "rebuildCommandCenter"),
            _extract_one_line_js_function(script, "syncCommandCenterAfterAction"),
            _extract_one_line_js_function(script, "setCommandStatus"),
            _extract_one_line_js_function(script, "proposalAdjacentStage"),
            _extract_one_line_js_function(script, "runCommandQuickAction"),
            _extract_one_line_js_function(script, "renderCommandCenter"),
        ]
    )
    js = textwrap.dedent(
        f"""
        function assert(condition, message) {{ if (!condition) throw new Error(message); }}
        const report = {json.dumps(report)};
        const byId = new Map(report.matches.map(o => [String(o.noticeId), o]));
        const tokenPanel = {{classList: {{add(value) {{ this.value = value; }}}}}};
        function token() {{ return global.currentToken || ''; }}
        let opened = [];
        function openDetail(id, origin) {{ opened.push(['detail', id, origin]); }}
        function renderProposalWorkspace(id) {{ opened.push(['proposal', id]); }}
        function renderProposals() {{ opened.push(['proposals']); }}
        function repaintAmendmentPanel() {{ opened.push(['amendments']); }}
        function repaintCompliance() {{ opened.push(['compliance']); }}
        function syncAmendments(id, amendments) {{ byId.get(id).amendmentSummary = amendments.summary; }}
        function syncCompliance(id, requirements) {{ byId.get(id).complianceRequirements = requirements; }}
        let requests = [];
        global.fetch = async (url, options) => {{
          requests.push([url, JSON.parse(options.body), options.headers['X-SAM-RADAR-TOKEN']]);
          return {{ok:true, headers:{{get(){{ return 'application/json'; }}}}, json: async () => {{
            if (url === '/api/evidence/verify') return {{ok:true, evidence:{{id:4, verificationState:'verified'}}, items:[{{id:4, verificationState:'verified'}}]}};
            if (url === '/api/amendments/mark-reviewed') return {{ok:true, amendments:{{summary:{{materialChangeCount:1, unreadCount:0, staleEvidenceCount:0}}}}}};
            if (url === '/api/compliance/verify') return {{ok:true, requirements:[{{id:7, status:'satisfied', verificationState:'verified'}}]}};
            if (url === '/api/proposals/stage') return {{ok:true, proposal:{{id:1, stage:'draft', stageLabel:'Draft'}}}};
          if (url.startsWith('/api/status/')) return {{ok:true, workflow:{{status:JSON.parse(options.body).status, priority:'normal', owner:'Lead', nextAction:'', followUpAt:'2026-08-08', notes:'', decisionReason:'', noBidReason:JSON.parse(options.body).noBidReason, noBidDetail:'', documents:[]}}}};
            return {{ok:false, error:'unexpected'}};
          }}}};
        }};
        let commandHost = {{innerHTML:'', querySelectorAll() {{ return []; }}}};
        const document = {{
          getElementById(id) {{ return id === 'command-center' ? commandHost : null; }},
          querySelector(selector) {{ return {{click() {{ opened.push(['view', selector]); }}}}; }},
          querySelectorAll() {{ return []; }}
        }};
        const window = {{confirm() {{ return true; }}}};
        const CSS = {{escape(value) {{ return String(value); }}}};
        const localStorage = {{getItem() {{ return ''; }}}};
        function setStatusState(el,state,message) {{ el.state = state; el.textContent = message; }}
        async function readJsonResponse(res) {{ return await res.json(); }}
        function refreshReportInPlace() {{ opened.push(['refresh']); }}
        function saveWorkflow() {{ throw new Error('saveWorkflow should not be used by command quick actions'); }}
        function syncCard(id, workflow) {{ Object.assign(byId.get(id), {{workflowStatus:workflow.status, workflowOwner:workflow.owner, workflowFollowUpAt:workflow.followUpAt, workflowNoBidReason:workflow.noBidReason}}); }}
        {helpers}
        renderCommandCenter();
        assert(commandHost.innerHTML.includes('Pursuit Command Center'), 'command center renders');
        assert(!commandHost.innerHTML.includes('<script>'), 'unsafe title remains escaped in command center');
        routeCommandTarget({{view:'proposals', noticeId:'abc', surface:'proposal-workspace'}});
        routeCommandTarget({{view:'command', noticeId:'abc', surface:'opportunity-detail'}});
        assert(JSON.stringify(opened).includes('proposal'), 'proposal target opens workspace');
        assert(JSON.stringify(opened).includes('detail'), 'detail target opens opportunity');

        try {{
          await runCommandQuickAction({{action:'assign-owner', noticeId:'abc'}}, {{textContent:''}});
        }} catch (err) {{
          assert(err.message === 'Token required', 'missing token blocks writes');
        }}
        global.currentToken = 'secret';
        const evidenceBefore = report.commandCenter.doToday.filter(item => ['stale-evidence','evidence-unverified'].includes(item.kind)).length;
        await runCommandQuickAction({{action:'verify-evidence', noticeId:'abc'}}, {{textContent:''}});
        assert(evidenceBefore > 0, 'evidence actions exist before quick action');
        assert(report.commandCenter.doToday.every(item => !['stale-evidence','evidence-unverified'].includes(item.kind)), 'evidence actions are removed after verification');
        await runCommandQuickAction({{action:'review-amendment', noticeId:'abc'}}, {{textContent:''}});
        assert(report.commandCenter.recentIntelligence.length === 0, 'reviewed amendments are removed from recent intelligence');
        await runCommandQuickAction({{action:'open-compliance', noticeId:'abc'}}, {{textContent:''}});
        await runCommandQuickAction({{action:'advance-proposal', noticeId:'abc'}}, {{textContent:''}});
        await runCommandQuickAction({{action:'assign-owner', noticeId:'abc'}}, {{textContent:''}});
        await runCommandQuickAction({{action:'set-follow-up', noticeId:'abc'}}, {{textContent:''}});
        await runCommandQuickAction({{action:'no-bid', noticeId:'abc'}}, {{textContent:''}});
        assert(requests.every(item => item[2] === 'secret'), 'every mutation uses APP_WRITE_TOKEN header');
        assert(requests.map(item => item[0]).includes('/api/evidence/verify'), 'evidence verify endpoint used');
        assert(requests.map(item => item[0]).includes('/api/amendments/mark-reviewed'), 'amendment endpoint used');
        assert(!requests.map(item => item[0]).includes('/api/compliance/verify'), 'open compliance does not verify rows');
        assert(requests.map(item => item[0]).includes('/api/proposals/stage'), 'proposal stage endpoint used');
        assert(requests.map(item => item[0]).includes('/api/status/abc'), 'workflow endpoint used');
        assert(report.commandCenter.doToday.length === 0, 'notice command actions are removed after no-bid');
        assert(report.commandCenter.statusMessage === 'No-bid saved', 'command center carries accessible repaint status');
        """
    )
    subprocess.run(["node", "--input-type=module", "-e", js], check=True)


def test_report_command_center_follow_up_date_only_uses_configured_local_day_in_python_and_generated_js(
    tmp_path: Path, monkeypatch
):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    generated = dt.datetime(2026, 8, 7, 5, 30, tzinfo=dt.UTC)
    matches = [
        {
            "noticeId": "tomorrow-local",
            "title": "Tomorrow Local",
            "workflowStatus": "pursue",
            "workflowOwner": "Lead",
            "workflowNextAction": "Call partner",
            "workflowFollowUpAt": "2026-08-07",
            "responseDeadline": "2026-08-20T17:00:00Z",
        },
        {
            "noticeId": "today-local",
            "title": "Today Local",
            "workflowStatus": "pursue",
            "workflowOwner": "Lead",
            "workflowNextAction": "Call partner",
            "workflowFollowUpAt": "2026-08-06",
            "responseDeadline": "2026-08-20T17:00:00Z",
        },
        {
            "noticeId": "malformed-follow-up",
            "title": "Malformed Follow-Up",
            "workflowStatus": "pursue",
            "workflowOwner": "Lead",
            "workflowNextAction": "Call partner",
            "workflowFollowUpAt": "not a date",
            "responseDeadline": "2026-08-20T17:00:00Z",
        },
    ]
    python_center = build_command_center(matches, now=generated, timezone="America/Denver")
    assert [
        item["noticeId"] for item in python_center["doToday"] if item["kind"] == "follow-up-overdue"
    ] == ["today-local"]

    monkeypatch.setattr(reports_module.dt, "datetime", FixedReportDatetime)
    report = build_report_payload(
        {"postedFrom": "08/01/2026", "postedTo": "08/04/2026", "matches": matches, "errors": []},
        profile,
        settings,
        unseen=[],
    )
    assert report["summary"]["generatedAt"] == "2026-08-06T23:30:00-06:00"
    assert [
        item["noticeId"] for item in report["commandCenter"]["doToday"] if item["kind"] == "follow-up-overdue"
    ] == ["today-local"]

    script = _extract_report_script(build_html_report(report))
    js = textwrap.dedent(
        f"""
        function assert(condition, message) {{ if (!condition) throw new Error(message); }}
        const report = {json.dumps(report)};
        {_extract_one_line_js_function(script, "rebuildCommandCenter")}
        rebuildCommandCenter();
        const overdue = report.commandCenter.doToday
          .filter(item => item.kind === 'follow-up-overdue')
          .map(item => item.noticeId);
        assert(JSON.stringify(overdue) === JSON.stringify(['today-local']), 'generated JS follow-up parity failed: '+JSON.stringify(overdue));
        assert(!overdue.includes('tomorrow-local'), '2026-08-07 is not due on configured local 2026-08-06');
        assert(!overdue.includes('malformed-follow-up'), 'malformed follow-up dates remain ignored');
        """
    )
    subprocess.run(["node", "--input-type=module", "-e", js], check=True)


def test_report_command_center_quick_actions_recompute_from_current_report_state(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    stage_items = [
        {"key": "intent", "label": "Intent", "state": "complete"},
        {"key": "intake", "label": "Intake", "state": "complete"},
        {"key": "docs", "label": "Docs", "state": "complete"},
        {"key": "requirements", "label": "Requirements", "state": "current"},
        {"key": "gaps", "label": "Gaps", "state": "pending"},
        {"key": "strategy", "label": "Strategy", "state": "pending"},
        {"key": "draft", "label": "Draft", "state": "pending"},
        {"key": "review", "label": "Review", "state": "pending"},
    ]
    report = build_report_payload(
        {
            "postedFrom": "08/01/2026",
            "postedTo": "08/04/2026",
            "matches": [
                {
                    "noticeId": "abc",
                    "title": "Security Support",
                    "workflowStatus": "pursue",
                    "workflowPriority": "normal",
                    "workflowOwner": "Lead",
                    "workflowNextAction": "",
                    "score": 8,
                    "responseDeadline": "2026-08-20T17:00:00Z",
                    "staleEvidenceWarnings": {"count": 1, "items": [{"evidenceId": 4, "reason": "Citation predates revision"}]},
                    "evidenceCitations": [
                        {"id": 4, "noticeId": "abc", "verificationState": "needs-review"},
                        {"id": 5, "noticeId": "abc", "verificationState": "needs-review"},
                    ],
                    "complianceRequirements": [{"id": 7, "noticeId": "abc", "status": "open", "verificationState": "needs-review"}],
                    "proposal": {"id": 1, "stage": "requirements", "stageLabel": "Requirements", "stages": stage_items},
                }
            ],
            "errors": [],
        },
        profile,
        settings,
        unseen=[],
    )
    script = _extract_report_script(build_html_report(report))
    helpers = "\n".join(
        [
            _extract_one_line_js_function(script, "escapeHtml"),
            _extract_one_line_js_function(script, "icon"),
            _extract_one_line_js_function(script, "iconLabel"),
            _extract_one_line_js_function(script, "safeClassToken"),
            _extract_one_line_js_function(script, "commandActionHtml"),
            _extract_one_line_js_function(script, "commandCenterHtml"),
            _extract_one_line_js_function(script, "routeCommandTarget"),
            _extract_one_line_js_function(script, "writeCommandJson"),
            _extract_one_line_js_function(script, "commandKindsForQuickAction"),
            _extract_one_line_js_function(script, "rebuildCommandCenter"),
            _extract_one_line_js_function(script, "syncCommandCenterAfterAction"),
            _extract_one_line_js_function(script, "setCommandStatus"),
            _extract_one_line_js_function(script, "proposalAdjacentStage"),
            _extract_one_line_js_function(script, "runCommandQuickAction"),
            _extract_one_line_js_function(script, "renderCommandCenter"),
        ]
    )
    js = textwrap.dedent(
        f"""
        function assert(condition, message) {{ if (!condition) throw new Error(message); }}
        const report = {json.dumps(report)};
        const byId = new Map(report.matches.map(o => [String(o.noticeId), o]));
        const tokenPanel = {{classList: {{add() {{}}}}}};
        function token() {{ return 'secret'; }}
        let opened = [];
        function openDetail(id, origin) {{ opened.push(['detail', id, origin]); }}
        function renderProposalWorkspace(id) {{ opened.push(['proposal', id]); }}
        function renderProposals() {{ opened.push(['proposals']); }}
        function repaintAmendmentPanel() {{ opened.push(['amendments']); }}
        function repaintCompliance() {{ opened.push(['compliance']); }}
        function syncAmendments(id, amendments) {{ byId.get(id).amendmentSummary = amendments.summary; }}
        function syncCompliance(id, requirements) {{ byId.get(id).complianceRequirements = requirements; }}
        let requests = [];
        global.fetch = async (url, options) => {{
          const body = JSON.parse(options.body);
          requests.push([url, body, options.headers['X-SAM-RADAR-TOKEN']]);
          return {{ok:true, headers:{{get(){{ return 'application/json'; }}}}, json: async () => {{
            if (url === '/api/evidence/verify') return {{ok:true, evidence:{{id:body.evidenceId, noticeId:body.noticeId, verificationState:'verified'}}, items:[{{id:5, noticeId:'abc', verificationState:'needs-review'}},{{id:4, noticeId:'abc', verificationState:'verified'}}]}};
            if (url === '/api/proposals/stage') return {{ok:true, proposal:{{id:1, stage:body.stage, stageLabel:body.stage === 'gaps' ? 'Gaps' : body.stage, stages:{json.dumps(stage_items)}}}}};
            if (url.startsWith('/api/status/')) return {{ok:true, workflow:{{status:body.status, priority:body.priority, owner:body.owner, nextAction:body.nextAction, followUpAt:body.followUpAt, notes:body.notes, decisionReason:body.decisionReason, noBidReason:body.noBidReason, noBidDetail:body.noBidDetail, documents:body.documents}}}};
            return {{ok:false, error:'unexpected'}};
          }}}};
        }};
        let commandHost = {{innerHTML:'', querySelectorAll() {{ return []; }}}};
        const document = {{
          getElementById(id) {{ return id === 'command-center' ? commandHost : null; }},
          querySelector(selector) {{ return {{click() {{ opened.push(['view', selector]); }}}}; }},
          querySelectorAll() {{ return []; }}
        }};
        const window = {{confirm() {{ return true; }}}};
        const CSS = {{escape(value) {{ return String(value); }}}};
        const localStorage = {{getItem() {{ return ''; }}}};
        function setStatusState(el,state,message) {{ el.state = state; el.textContent = message; }}
        async function readJsonResponse(res) {{ return await res.json(); }}
        function refreshReportInPlace() {{ opened.push(['refresh']); }}
        function syncCard(id, workflow) {{
          Object.assign(byId.get(id), {{
            workflowStatus: workflow.status,
            workflowPriority: workflow.priority,
            workflowOwner: workflow.owner,
            workflowNextAction: workflow.nextAction,
            workflowFollowUpAt: workflow.followUpAt,
            workflowNoBidReason: workflow.noBidReason,
            workflowNoBidDetail: workflow.noBidDetail,
            workflowDocuments: workflow.documents
          }});
        }}
        {helpers}
        assert(report.commandCenter.metrics.totalActions === 4, 'precondition has stale, unverified, compliance, and assignment actions');

        await runCommandQuickAction({{action:'verify-evidence', noticeId:'abc'}}, {{textContent:''}});
        assert(requests[0][0] === '/api/evidence/verify', 'evidence verify endpoint used');
        assert(requests[0][1].noticeId === 'abc', 'evidence verify is notice scoped');
        assert(byId.get('abc').evidenceCitations.length === 2, 'partial verification preserves sibling citations');
        assert(report.commandCenter.doToday.some(item => item.kind === 'stale-evidence'), 'stale warning remains after citation verify');
        assert(report.commandCenter.doToday.some(item => item.kind === 'evidence-unverified'), 'unverified sibling keeps evidence action');
        assert(report.commandCenter.doToday.some(item => item.kind === 'compliance-gap'), 'open compliance row remains');
        assert(report.commandCenter.metrics.totalActions === report.commandCenter.doToday.length, 'metrics repaint from recomputed actions');

        const beforeOpenRequests = requests.length;
        await runCommandQuickAction({{action:'open-compliance', noticeId:'abc'}}, {{textContent:''}});
        assert(requests.length === beforeOpenRequests, 'open compliance is navigation only');
        assert(report.commandCenter.doToday.some(item => item.kind === 'compliance-gap'), 'open compliance does not clear matrix action');

        await runCommandQuickAction({{action:'advance-proposal', noticeId:'abc'}}, {{textContent:''}});
        const stageRequest = requests.find(item => item[0] === '/api/proposals/stage');
        assert(stageRequest[1].stage === 'gaps', 'advance stage uses next ordered current stage');
        byId.get('abc').proposal = {{id:1, stage:'review', stageLabel:'Review', stages:{json.dumps(stage_items[:-1] + [{"key": "review", "label": "Review", "state": "current"}])}}};
        const beforeTerminalStage = requests.length;
        const terminalMessage = await runCommandQuickAction({{action:'advance-proposal', noticeId:'abc'}}, {{textContent:''}});
        assert(requests.length === beforeTerminalStage, 'terminal stage does not mutate');
        assert(terminalMessage === 'Proposal is already at Review', 'terminal stage message is accurate');

        await runCommandQuickAction({{action:'no-bid', noticeId:'abc'}}, {{textContent:''}});
        const workflowRequest = requests.filter(item => item[0] === '/api/status/abc').at(-1);
        assert(workflowRequest[1].status === 'no-bid', 'no-bid status saved');
        assert(workflowRequest[1].noBidReason === '', 'no-bid does not fabricate a deadline-too-short reason');
        assert(report.commandCenter.doToday.length === 0, 'terminal no-bid clears all actions via recompute');
        assert(report.commandCenter.recentIntelligence.length === 0, 'terminal no-bid has no recent intelligence');
        assert(report.commandCenter.metrics.totalActions === 0, 'terminal no-bid metrics repaint');
        assert(report.commandCenter.portfolioHealth.riskLevel === 'low', 'health repaint reflects terminal portfolio');
        """
    )
    subprocess.run(["node", "--input-type=module", "-e", js], check=True)


def test_report_compliance_success_messages_and_add_button_target_repainted_dom(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    report = build_report_payload(
        {
            "postedFrom": "08/01/2026",
            "postedTo": "08/04/2026",
            "matches": [{"noticeId": "abc", "title": "Security Support", "proposal": {"id": 1}, "complianceRequirements": []}],
            "errors": [],
        },
        profile,
        settings,
        unseen=[],
    )
    script = _extract_report_script(build_html_report(report))
    helpers = "\n".join(
        [
            _extract_one_line_js_function(script, "escapeHtml"),
            _extract_one_line_js_function(script, "safeClassToken"),
            _extract_one_line_js_function(script, "icon"),
            _extract_one_line_js_function(script, "iconLabel"),
            _extract_one_line_js_function(script, "complianceOptions"),
            _extract_one_line_js_function(script, "complianceRowHtml"),
            _extract_one_line_js_function(script, "complianceMatrixHtml"),
            _extract_one_line_js_function(script, "syncCompliance"),
            _extract_one_line_js_function(script, "complianceRowPayload"),
            _extract_one_line_js_function(script, "repaintCompliance"),
            _extract_one_line_js_function(script, "setComplianceMessage"),
            _extract_one_line_js_function(script, "revealComplianceNewRow"),
            _extract_one_line_js_function(script, "applyComplianceFilters"),
            _extract_one_line_js_function(script, "wireComplianceMatrix"),
        ]
    )
    js = textwrap.dedent(
        f"""
        function assert(condition, message) {{ if (!condition) throw new Error(message); }}
        const byId = new Map([['abc', {{noticeId:'abc', complianceRequirements:[]}}]]);
        const tokenPanel = {{classList: {{add() {{}}}}}};
        const localStorage = {{getItem() {{ return ''; }}}};
        const window = {{confirm() {{ return true; }}}};
        function token() {{ return 'secret'; }}
        async function readJsonResponse() {{ return {{ok:true}}; }}
        let complianceWrite;
        {helpers}
        complianceWrite = async (endpoint, body, action) => {{
          if (endpoint === '/api/compliance/generate') {{
            return {{ok:true, requirements:[{{id:1, noticeId:'abc', citationId:7, category:'Submission', requirementText:'Submit a plan.', mandatoryState:'mandatory', status:'open', verificationState:'needs-review'}}]}};
          }}
          if (endpoint === '/api/compliance/add') {{
            return {{ok:true, requirements:[{{id:2, noticeId:'abc', citationId:8, category:body.category, requirementText:body.requirementText, mandatoryState:'mandatory', status:'open', verificationState:'needs-review'}}]}};
          }}
          throw new Error(action);
        }};
        class FakeClassList {{
          constructor() {{ this.values = new Set(); }}
          add(value) {{ this.values.add(value); }}
          remove(value) {{ this.values.delete(value); }}
          contains(value) {{ return this.values.has(value); }}
        }}
        class FakeElement {{
          constructor(className = '') {{
            this.className = className;
            this.textContent = '';
            this.value = '';
            this.checked = false;
            this.style = {{}};
            this.dataset = {{}};
            this.listeners = {{}};
            this.classList = new FakeClassList();
          }}
          addEventListener(name, fn) {{ this.listeners[name] = fn; }}
          async click() {{ await this.listeners.click?.({{target:this}}); }}
          focus() {{ this.focused = true; }}
          scrollIntoView() {{ this.scrolled = true; }}
          closest() {{ return this; }}
          querySelector() {{ return null; }}
          querySelectorAll() {{ return []; }}
        }}
        class FakePanel extends FakeElement {{
          constructor(html = '') {{
            super('compliance-matrix');
            this.html = html;
            this.message = new FakeElement('compliance-message');
            this.generate = new FakeElement('compliance-generate');
            this.add = new FakeElement('compliance-add');
            this.create = new FakeElement('compliance-create');
            this.newRow = new FakeElement('compliance-new-row');
            this.newCategory = new FakeElement('compliance-new-category');
            this.newText = new FakeElement('compliance-new-text');
            this.newCategory.value = 'Submission';
            this.newText.value = 'Manual requirement';
          }}
          set outerHTML(value) {{ this.root.panel = new FakePanel(value); this.root.panel.root = this.root; }}
          querySelector(selector) {{
            return {{
              '.compliance-message': this.message,
              '.compliance-generate': this.generate,
              '.compliance-add': this.add,
              '.compliance-create': this.create,
              '.compliance-new-row': this.newRow,
              '.compliance-new-category': this.newCategory,
              '.compliance-new-text': this.newText,
              '.compliance-merge': new FakeElement('compliance-merge')
            }}[selector] || null;
          }}
          querySelectorAll(selector) {{ return selector === '.compliance-filters select,.compliance-filters input' ? [] : []; }}
        }}
        const root = {{
          panel: new FakePanel(complianceMatrixHtml(byId.get('abc'))),
          querySelector(selector) {{ return selector === '.compliance-matrix' ? this.panel : null; }},
          querySelectorAll(selector) {{ return selector === '.compliance-matrix' ? [this.panel] : []; }}
        }};
        root.panel.root = root;
        wireComplianceMatrix(root, 'abc');
        await root.panel.generate.click();
        assert(root.panel.message.textContent === 'Compliance matrix generated', 'generate success must be written into repaint panel');
        await root.panel.add.click();
        assert(root.panel.newRow.classList.contains('active'), 'visible Add button reveals the new requirement row');
        assert(root.panel.newText.focused === true, 'visible Add button focuses the requirement textarea');
        root.panel.newText.value = 'Created requirement';
        await root.panel.create.click();
        assert(root.panel.message.textContent === 'Requirement created', 'create success must be written into repaint panel');
        """
    )
    subprocess.run(["node", "--input-type=module", "-e", js], check=True)


def test_report_compliance_notes_render_edit_send_and_survive_repaint(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    report = build_report_payload(
        {
            "postedFrom": "08/01/2026",
            "postedTo": "08/04/2026",
            "matches": [
                {
                    "noticeId": "abc",
                    "title": "Security Support",
                    "proposal": {"id": 1},
                    "complianceRequirements": [
                        {
                            "id": 3,
                            "noticeId": "abc",
                            "category": "Submission",
                            "requirementText": "Submit a plan.",
                            "notes": 'Review <img src=x onerror="alert(1)"> before final.',
                            "mandatoryState": "mandatory",
                            "status": "open",
                            "verificationState": "needs-review",
                        }
                    ],
                }
            ],
            "errors": [],
        },
        profile,
        settings,
        unseen=[],
    )
    script = _extract_report_script(build_html_report(report))
    helpers = "\n".join(
        [
            _extract_one_line_js_function(script, "escapeHtml"),
            _extract_one_line_js_function(script, "safeClassToken"),
            _extract_one_line_js_function(script, "icon"),
            _extract_one_line_js_function(script, "iconLabel"),
            _extract_one_line_js_function(script, "complianceOptions"),
            _extract_one_line_js_function(script, "complianceRowHtml"),
            _extract_one_line_js_function(script, "complianceMatrixHtml"),
            _extract_one_line_js_function(script, "syncCompliance"),
            _extract_one_line_js_function(script, "complianceRowPayload"),
            _extract_one_line_js_function(script, "repaintCompliance"),
            _extract_one_line_js_function(script, "setComplianceMessage"),
            _extract_one_line_js_function(script, "revealComplianceNewRow"),
            _extract_one_line_js_function(script, "applyComplianceFilters"),
            _extract_one_line_js_function(script, "wireComplianceMatrix"),
        ]
    )
    js = textwrap.dedent(
        f"""
        function assert(condition, message) {{ if (!condition) throw new Error(message); }}
        const byId = new Map([['abc', {{noticeId:'abc', complianceRequirements:[{{id:3, noticeId:'abc', category:'Submission', requirementText:'Submit a plan.', notes:'Review <img src=x onerror="alert(1)"> before final.', mandatoryState:'mandatory', status:'open', verificationState:'needs-review'}}]}}]]);
        const tokenPanel = {{classList: {{add() {{}}}}}};
        const localStorage = {{getItem() {{ return ''; }}}};
        const window = {{confirm() {{ return true; }}}};
        function token() {{ return 'secret'; }}
        async function readJsonResponse() {{ return {{ok:true}}; }}
        let savedBody;
        let complianceWrite;
        {helpers}
        complianceWrite = async (endpoint, body, action) => {{
          if (endpoint === '/api/compliance/update') {{
            savedBody = body;
            return {{ok:true, requirements:[{{id:3, noticeId:'abc', category:'Submission', requirementText:body.requirementText, notes:body.notes, mandatoryState:body.mandatoryState, status:body.status, verificationState:body.verificationState}}]}};
          }}
          throw new Error(action);
        }};
        class FakeClassList {{ add() {{}} remove() {{}} contains() {{ return false; }} }}
        class FakeElement {{
          constructor(selector = '') {{ this.selector = selector; this.value = ''; this.checked = false; this.style = {{}}; this.dataset = {{requirementId:'3'}}; this.listeners = {{}}; this.classList = new FakeClassList(); }}
          addEventListener(name, fn) {{ this.listeners[name] = fn; }}
          async click() {{ await this.listeners.click?.({{target:this}}); }}
          closest() {{ return this.row || this; }}
          querySelector(selector) {{ return this.fields?.[selector] || null; }}
          querySelectorAll() {{ return []; }}
        }}
        class FakePanel extends FakeElement {{
          constructor(html = '') {{
            super('compliance-matrix');
            this.html = html;
            this.message = new FakeElement('compliance-message');
            this.save = new FakeElement('compliance-save');
            this.row = new FakeElement('tr');
            this.save.row = this.row;
            const fields = {{
              '.compliance-category': Object.assign(new FakeElement(), {{value:'Submission'}}),
              '.compliance-mandatory': Object.assign(new FakeElement(), {{value:'mandatory'}}),
              '.compliance-status': Object.assign(new FakeElement(), {{value:'open'}}),
              '.compliance-verification': Object.assign(new FakeElement(), {{value:'needs-review'}}),
              '.compliance-text': Object.assign(new FakeElement(), {{value:'Submit a plan.'}}),
              '.compliance-owner': Object.assign(new FakeElement(), {{value:''}}),
              '.compliance-due-date': Object.assign(new FakeElement(), {{value:''}}),
              '.compliance-response-location': Object.assign(new FakeElement(), {{value:''}}),
              '.compliance-notes': Object.assign(new FakeElement(), {{value:'Edited compliance notes'}})
            }};
            this.row.fields = fields;
          }}
          set outerHTML(value) {{ this.root.panel = new FakePanel(value); this.root.panel.root = this.root; }}
          querySelector(selector) {{ return {{'.compliance-message': this.message, '.compliance-merge': new FakeElement('compliance-merge')}}[selector] || null; }}
          querySelectorAll(selector) {{
            if (selector === '.compliance-save') return [this.save];
            if (selector === '.compliance-filters select,.compliance-filters input') return [];
            return [];
          }}
        }}
        const rowHtml = complianceRowHtml(byId.get('abc').complianceRequirements[0]);
        assert(rowHtml.includes('class="compliance-notes"'), 'notes must render as an editable control');
        assert(rowHtml.includes('Review &lt;img src=x onerror=&quot;alert(1)&quot;&gt; before final.'), 'existing notes must be escaped');
        assert(!rowHtml.includes('<img src=x'), 'notes must not render raw HTML');
        const root = {{
          panel: new FakePanel(complianceMatrixHtml(byId.get('abc'))),
          querySelector(selector) {{ return selector === '.compliance-matrix' ? this.panel : null; }},
          querySelectorAll(selector) {{ return selector === '.compliance-matrix' ? [this.panel] : []; }}
        }};
        root.panel.root = root;
        wireComplianceMatrix(root, 'abc');
        await root.panel.save.click();
        assert(savedBody.notes === 'Edited compliance notes', 'edited notes must be sent in compliance update payload');
        assert(byId.get('abc').complianceRequirements[0].notes === 'Edited compliance notes', 'synced notes must update report state');
        const repainted = repaintCompliance(root, 'abc');
        assert(repainted.html.includes('Edited compliance notes'), 'saved notes must survive repaint');
        """
    )
    subprocess.run(["node", "--input-type=module", "-e", js], check=True)


def test_report_includes_professional_design_system_primitives_and_showcase(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    payload = {
        "postedFrom": "08/01/2026",
        "postedTo": "08/04/2026",
        "matches": [],
        "errors": [],
    }
    report = build_report_payload(payload, profile, settings, unseen=[])
    html = build_html_report(report)

    assert "--font-sans:" in html
    assert "--space-1:4px" in html
    assert "--radius-card:8px" in html
    assert "--shadow-card:" in html
    assert "--surface-page:var(--bg)" in html
    assert "--status-success:" in html
    assert "--status-warning:" in html
    assert "--status-danger:" in html
    assert "--status-info:" in html
    assert "--density-card-padding:" in html
    assert '[data-theme=dark]{color-scheme:dark' in html
    assert "button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible" in html
    assert "@media(prefers-reduced-motion:reduce)" in html
    assert ".btn-primary,.refresh,.token-save,.save-detail,.save-status,.sam" in html
    assert ".btn-secondary,.small-ghost,.archive-toggle,.manual-add" in html
    assert ".form-control,input,select,textarea" in html
    assert ".card,.panel,.opp,.empty,.lane,.modal-card,.resource-card,.manual-card" in html
    assert ".table-wrap" in html
    assert ".dialog-confirm" in html
    assert ".badge,.pill" in html
    assert ".state-message" in html
    assert ".state-loading" in html
    assert ".state-success" in html
    assert ".state-error" in html
    assert ".state-empty" in html
    assert ".confirmable[data-confirming=true]" in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'id="component-showcase"' in html
    assert "Design System Showcase" in html
    assert "Tokens" in html
    assert "Buttons" in html
    assert "Forms" in html
    assert "Cards" in html
    assert "Tables" in html
    assert "Dialogs" in html
    assert "Feedback States" in html
    assert "renderComponentShowcase()" in html
    assert "componentShowcaseHtml()" in html
    assert "function setStatusState" in html


def test_confirmation_and_status_state_helpers_restore_archive_button_on_confirmed_failure(tmp_path: Path):
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
            }
        ],
        "errors": [],
    }
    report = build_report_payload(payload, profile, settings, unseen=[])
    html = build_html_report(report)
    script = _extract_report_script(html)
    helpers = "\n".join(
        [
            _extract_one_line_js_function(script, "escapeHtml"),
            _extract_one_line_js_function(script, "icon"),
            _extract_one_line_js_function(script, "iconLabel"),
            _extract_one_line_js_function(script, "setStatusState"),
            _extract_one_line_js_function(script, "requestConfirmation"),
        ]
    )
    js = textwrap.dedent(
        f"""
        const timers = [];
        global.setTimeout = (fn, ms) => {{ timers.push({{ fn, ms }}); return 1; }};
        class FakeClassList {{
          constructor(values) {{ this.values = new Set(values); }}
          contains(value) {{ return this.values.has(value); }}
        }}
        function assert(condition, message) {{ if (!condition) throw new Error(message); }}
        {helpers}

        const archiveButton = {{
          classList: new FakeClassList(['archive-toggle', 'confirmable']),
          dataset: {{}},
          innerHTML: '<svg></svg> Archive'
        }};
        assert(requestConfirmation(archiveButton, 'Confirm archive') === false, 'first archive click waits');
        assert(archiveButton.dataset.confirming === 'true', 'confirmation flag is set');
        assert(archiveButton.innerHTML.includes('Confirm archive'), 'confirmation label is shown');
        assert(requestConfirmation(archiveButton, 'Confirm archive') === true, 'second archive click proceeds');
        assert(archiveButton.dataset.confirming === 'false', 'confirmation flag is cleared before save');
        assert(archiveButton.innerHTML === '<svg></svg> Archive', 'original archive label is restored before save failure');
        timers[0].fn();
        assert(archiveButton.innerHTML === '<svg></svg> Archive', 'timeout does not reapply confirmation label');

        const unarchiveButton = {{
          classList: new FakeClassList(['archive-toggle']),
          dataset: {{}},
          innerHTML: '<svg></svg> Unarchive'
        }};
        assert(requestConfirmation(unarchiveButton, 'Confirm archive') === true, 'unarchive stays immediate');
        assert(unarchiveButton.innerHTML === '<svg></svg> Unarchive', 'unarchive label is untouched');

        const message = {{
          dataset: {{}},
          className: 'workflow-message muted',
          attributes: {{}},
          setAttribute(name, value) {{ this.attributes[name] = value; }}
        }};
        setStatusState(message, 'loading', 'Archiving...');
        assert(message.className === 'workflow-message muted state-message state-loading', 'loading class preserves base classes');
        assert(message.attributes.role === 'status', 'status role is set');
        assert(message.attributes['aria-live'] === 'polite', 'status updates are polite');
        assert(message.textContent === 'Archiving...', 'loading message is set');
        setStatusState(message, 'error', 'Token required');
        assert(message.className === 'workflow-message muted state-message state-error', 'error state replaces loading state');
        assert(message.textContent === 'Token required', 'error message is set');
        """
    )
    subprocess.run(["node", "-e", js], check=True)


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
                "proposalDocuments": [
                    {
                        "id": 7,
                        "noticeId": "abc-123",
                        "sourceType": "url",
                        "source": "https://example.test/solicitation.txt",
                        "label": "Solicitation",
                        "filename": "solicitation.txt",
                        "contentType": "text/plain",
                        "sizeBytes": 120,
                        "parseStatus": "parsed",
                        "parseError": "",
                    }
                ],
                "evidenceSnippets": [
                    {"id": 3, "documentId": 7, "section": "Requirements", "snippet": "Offeror must provide secure engineering support."}
                ],
                "evidenceCitations": [
                    {
                        "id": 4,
                        "documentId": 7,
                        "pageSection": "PWS 3.1",
                        "sourceExcerpt": "Offeror must provide secure engineering support.",
                        "extractedClaim": "Secure engineering support is required.",
                        "extractionMethod": "document-intake",
                        "confidence": 0.82,
                        "verificationState": "needs-review",
                    }
                ],
                "proposalArtifacts": [
                    {
                        "id": 5,
                        "noticeId": "abc-123",
                        "artifactType": "outline",
                        "title": "Initial Outline",
                        "status": "draft",
                        "format": "markdown",
                        "content": "# Initial Outline",
                        "notes": "Use parsed requirements.",
                        "version": 1,
                        "updatedAt": "2026-08-05T01:00:00+00:00",
                    }
                ],
                "proposal": {
                    "id": 1,
                    "noticeId": "abc-123",
                    "title": "Security Support",
                    "role": "prime",
                    "stage": "docs",
                    "stageLabel": "Docs",
                    "status": "active",
                    "nextAction": "Download attachments",
                    "stages": [
                        {"key": "intent", "label": "Intent", "state": "complete"},
                        {"key": "intake", "label": "Intake", "state": "complete"},
                        {"key": "docs", "label": "Docs", "state": "current"},
                    ],
                },
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
    assert 'data-view="proposals"' in html
    assert 'id="proposals-view"' in html
    assert 'id="proposal-list"' in html
    assert 'class="proposal-create"' in html
    assert 'class="proposal-slot"' in html
    assert '.proposal-stages{display:flex;flex-wrap:wrap' in html
    assert '.proposal-stage{flex:1 1 86px;min-width:0' in html
    assert '.proposal-actions button,.proposal-card button{max-width:100%;white-space:normal}' in html
    assert '/api/proposals/create' in html
    assert '/api/proposals/stage' in html
    assert 'function proposalPanelHtml' in html
    assert 'Summary Assist' in html
    assert 'function runAiSummary' in html
    assert '/api/ai/summary' in html
    assert 'Requirements Assist' in html
    assert 'function runRequirementsAssist' in html
    assert '/api/ai/requirements' in html
    assert 'Gap Assist' in html
    assert 'function runGapAssist' in html
    assert '/api/ai/gaps' in html
    assert 'function renderProposals' in html
    assert 'function proposalWorkspaceHtml' in html
    assert 'function readinessHtml' in html
    assert 'function proposalAdjacentStage' in html
    assert 'Document Intake' in html
    assert 'class="proposal-workspace-header"' in html
    assert 'class="proposal-workspace-body"' in html
    assert 'class="proposal-workspace-section"' in html
    assert 'class="document-intake-form"' in html
    assert 'class="source-mode"' in html
    assert 'class="proposal-doc-mode active" data-mode="url"' in html
    assert 'class="proposal-doc-url active"' in html
    assert 'class="proposal-doc-upload" type="file"' in html
    assert 'function setDocumentSourceMode' in html
    assert 'function syncProposalDocuments' in html
    assert '.document-intake-form .proposal-doc-file,.document-intake-form .proposal-doc-url{display:none}' in html
    assert 'function readFileAsBase64(file)' in html
    assert 'contentBase64' in html
    assert 'class="proposal-doc-add refresh"' in html
    assert 'Add Document' in html
    assert 'class="proposal-doc-parse"' in html
    assert 'class="proposal-doc-remove"' in html
    assert 'function removeProposalDocument' in html
    assert '/api/proposal-documents/add' in html
    assert '/api/proposal-documents/parse' in html
    assert '/api/proposal-documents/remove' in html
    assert '/api/proposal-documents/' in html
    assert 'Evidence & Citations' in html
    assert 'class="citation-card citation-state-' in html
    assert 'function citationCardHtml' in html
    assert 'function verifyCitation' in html
    assert '/api/evidence/verify' in html
    assert 'View source excerpt' in html
    assert 'Secure engineering support is required.' in html
    assert 'Artifact Drafts' in html
    assert 'Initial Outline' in html
    assert 'class="proposal-artifacts"' in html
    assert 'function proposalArtifactsHtml' in html
    assert 'function wireArtifacts' in html
    assert '/api/proposal-artifacts/add' in html
    assert '/api/proposal-artifacts/update' in html
    assert '/api/proposal-artifacts/' in html
    assert 'Version History' in html
    assert 'class="artifact-history"' in html
    assert 'function loadArtifactHistory' in html
    assert '/api/proposal-artifact-history/' in html
    assert 'Export MD' in html
    assert 'class="sam artifact-export"' in html
    assert '/api/proposal-artifact-export/' in html
    assert 'Prime Templates' in html
    assert 'function generatePrimeArtifacts' in html
    assert '/api/ai/prime-templates' in html
    assert 'Sub Templates' in html
    assert 'function generateSubcontractorArtifacts' in html
    assert '/api/ai/subcontractor-templates' in html
    assert 'Offeror must provide secure engineering support.' in html
    assert 'id="proposal-workspace"' in html
    assert 'class="readiness"' in html
    assert 'class="readiness-bar"' in html
    assert 'class="missing-list"' in html
    assert 'class="proposal-workspace-open"' in html
    assert '.proposal-meta span{border:1px solid var(--line);border-radius:6px' in html
    assert '.proposal-meta b{display:block;color:var(--ink);font-size:11px' in html
    assert 'class="proposal-stage-jump"' in html
    assert 'Proposal: Docs' in html
    assert 'Document review pending' in html
    assert 'Advance beyond Docs' in html
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
    assert 'data-view="command" role="tab"' in html
    assert 'id="command-view" class="view active"' in html
    assert 'data-view="executive" role="tab"' in html
    assert 'id="executive-view" class="view"' in html
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
    assert 'Add External Opportunity' in html
    assert 'external-intake-form' in html
    assert '/api/manual-add' in html
    assert 'Added. Reloading cached report...' in html
    assert 'manual-field-setaside' in html
    assert 'id="manual-clear"' in html
    assert 'Manual search fields cleared.' in html
    assert '.manual-field-action{grid-column:span 1' in html
    assert 'manual-search-head' in html
    assert 'class="small-ghost"' in html
    assert 'id="lookup-status"' in html
    assert 'id="coach-status"' in html
    assert 'id="ai-settings-card"' in html
    assert 'id="ai-settings-details"' in html
    assert 'id="ai-test"' in html
    assert '/api/ai/settings' in html
    assert '/api/ai/test' in html
    assert 'id="ai-audit-card"' in html
    assert 'AI Audit' in html
    assert '/api/ai/audit' in html
    assert 'function loadAiAudit' in html
    assert 'No external text transfer' in html
    assert 'function aiSettingsHtml' in html
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


def test_report_writes_csv_export_and_toolbar_link(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(
        sam_gov_api_key="test",
        reports_dir=tmp_path,
        app_base_url="https://sam-radar.example.test",
        timezone="America/Denver",
    )
    payload = {
        "postedFrom": "08/01/2026",
        "postedTo": "08/04/2026",
        "matches": [
            {
                "noticeId": "csv-1",
                "title": "Security, Support",
                "organization": "Example Agency",
                "type": "Solicitation",
                "postedDate": "2026-08-04",
                "responseDeadline": "2026-08-10T09:00:00-04:00",
                "score": 10,
                "reasons": ["keywords: security"],
                "recommendation": "Pursue",
                "uiLink": "https://sam.gov/opp/csv-1/view",
            }
        ],
        "errors": [],
    }
    report = build_report_payload(payload, profile, settings, unseen=payload["matches"])
    csv_text = build_csv_report(report)
    paths = write_reports(report, settings)
    html = (tmp_path / "latest.html").read_text()

    assert "notice_id,title,agency" in csv_text
    assert "source,source_name,estimated_value" in csv_text
    assert 'csv-1,"Security, Support",Example Agency' in csv_text
    assert (tmp_path / "latest.csv").exists()
    assert paths["latestCsvUrl"] == "https://sam-radar.example.test/reports/latest.csv"
    assert paths["csvPath"].endswith(".csv")
    assert 'href="/reports/latest.csv"' in html


def test_citation_card_escapes_user_content_and_sanitizes_state_class(tmp_path: Path):
    profile = BusinessProfile(name="Example Technology Services LLC", dba="ExampleTech", capabilities=["security"])
    settings = Settings(sam_gov_api_key="test", reports_dir=tmp_path, timezone="America/Denver")
    report = build_report_payload({"postedFrom": "08/01/2026", "postedTo": "08/04/2026", "matches": [], "errors": []}, profile, settings, unseen=[])
    script = _extract_report_script(build_html_report(report))
    helpers = "\n".join(
        [
            _extract_one_line_js_function(script, "escapeHtml"),
            _extract_one_line_js_function(script, "safeClassToken"),
            _extract_one_line_js_function(script, "icon"),
            _extract_one_line_js_function(script, "iconLabel"),
            _extract_one_line_js_function(script, "citationCardHtml"),
        ]
    )
    js = textwrap.dedent(
        f"""
        function assert(condition, message) {{ if (!condition) throw new Error(message); }}
        {helpers}
        const html = citationCardHtml({{
          id: '7" onclick="alert(1)',
          pageSection: '<img src=x onerror=alert(1)>',
          sourceExcerpt: '</script><script>alert(1)</script>',
          extractedClaim: 'Claim <b onclick=alert(1)>unsafe</b>',
          extractionMethod: 'manual"><img src=x>',
          confidence: 0.82,
          verificationState: 'needs-review" onclick="alert(1)'
        }});
        assert(html.includes('citation-state-needs-review-onclick-alert-1'), 'state class is sanitized');
        assert(!html.includes('<img'), 'raw image tag is not rendered');
        assert(!html.includes('<script>'), 'raw script tag is not rendered');
        assert(!html.includes('onclick="alert(1)'), 'raw event handler is not rendered');
        assert(html.includes('&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;'), 'source excerpt is escaped');
        """
    )
    subprocess.run(["node", "-e", js], check=True)
