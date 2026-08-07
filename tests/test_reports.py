import re
import subprocess
import textwrap
from pathlib import Path

from sam_radar.config import BusinessProfile, Settings
from sam_radar.reports import build_csv_report, build_html_report, build_report_payload, write_reports


def _extract_report_script(html: str) -> str:
    match = re.search(r"<script>(?P<script>.*)</script></body></html>", html, flags=re.DOTALL)
    assert match is not None
    return match.group("script")


def _extract_one_line_js_function(script: str, name: str) -> str:
    start = script.index(f"function {name}(")
    return script[start : script.index("\n", start)]


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
