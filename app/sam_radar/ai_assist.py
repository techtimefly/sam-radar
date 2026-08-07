from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .config import Settings
from .llm import LLMClient, settings_summary
from .storage import Store


def _sentences(text: str, limit: int = 2) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "").strip())
    return [part for part in parts if part][:limit]


def _latest_match(settings: Settings, notice_id: str) -> dict[str, Any]:
    path = settings.reports_dir / "latest.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    for item in data.get("matches") or []:
        if str(item.get("noticeId") or "") == str(notice_id):
            return item
    return {}


def _source_text(opp: dict[str, Any], evidence: list[dict[str, Any]]) -> tuple[str, list[str]]:
    sources: list[str] = []
    chunks: list[str] = []
    for key, label in (("descriptionParagraphs", "SAM.gov description"), ("fitReason", "Fit rationale"), ("workflowNotes", "Capture notes")):
        value = opp.get(key)
        if isinstance(value, list):
            text = "\n".join(str(item) for item in value if item)
        else:
            text = str(value or "")
        if text.strip():
            sources.append(label)
            chunks.append(text.strip())
    if evidence:
        sources.append("Evidence citations")
        chunks.extend(str(item.get("sourceExcerpt") or item.get("snippet") or "") for item in evidence[:8] if item.get("sourceExcerpt") or item.get("snippet"))
    return "\n".join(chunks), sources


def _source_items(opp: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for key, label in (("descriptionParagraphs", "SAM.gov description"), ("fitReason", "Fit rationale"), ("workflowNotes", "Capture notes")):
        value = opp.get(key)
        if isinstance(value, list):
            for entry in value:
                text = str(entry or "").strip()
                if text:
                    items.append({"source": label, "text": text})
        else:
            text = str(value or "").strip()
            if text:
                items.append({"source": label, "text": text})
    for item in evidence[:16]:
        text = str(item.get("sourceExcerpt") or item.get("snippet") or "").strip()
        if text:
            section = str(item.get("pageSection") or item.get("section") or "Evidence").strip()
            state = str(item.get("verificationState") or "generated").strip()
            confidence = str(item.get("confidence") or 0)
            items.append({"source": f"Evidence citation: {section} ({state}, confidence {confidence})", "text": text})
    return items


_REQUIREMENT_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("evaluationCriteria", "Evaluation", ("evaluation", "evaluate", "basis for award", "best value", "lowest price", "technical approach", "past performance")),
    ("requiredForms", "Forms", ("form", "sf 1449", "sf1449", "attachment", "attachments", "certification", "representation", "provisions", "clauses")),
    ("submissionInstructions", "Submission", ("submit", "submission", "proposal", "quote", "offer", "due", "email", "portal", "instructions", "upload")),
    ("requirements", "Requirements", ("must", "shall", "required", "requirement", "contractor will", "contractor shall", "provide", "perform")),
)


def _requirement_entry(text: str, source: str, label: str, confidence: float) -> dict[str, Any]:
    category = "source-fact" if source.startswith("Evidence citation") or source == "SAM.gov description" else "business-assumption"
    return {"text": text[:360], "source": source, "label": label, "confidence": confidence, "category": category}


def deterministic_requirements(opp: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in _REQUIREMENT_RULES}
    seen: set[tuple[str, str]] = set()
    for item in _source_items(opp, evidence):
        source = item["source"]
        for sentence in _sentences(item["text"], 12):
            lowered = sentence.lower()
            for key, label, terms in _REQUIREMENT_RULES:
                if any(term in lowered for term in terms):
                    marker = (key, sentence.lower()[:180])
                    if marker in seen:
                        continue
                    seen.add(marker)
                    confidence = 0.86 if source.startswith("Evidence citation") else 0.72
                    buckets[key].append(_requirement_entry(sentence, source, label, confidence))
                    break
    if not any(buckets.values()):
        title = str(opp.get("title") or "Opportunity").strip()
        buckets["requirements"].append(_requirement_entry(f"Review the solicitation package for explicit requirements for {title}.", "Latest report", "Requirements", 0.42))
    return {key: values[:6] for key, values in buckets.items()}


def opportunity_requirements(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    notice_id = str(payload.get("noticeId") or "")
    if not notice_id:
        raise ValueError("noticeId is required")
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    opp = _latest_match(settings, notice_id)
    if not opp:
        raise ValueError("Opportunity not found in latest report")
    evidence = store.evidence_citations(notice_id)
    requirements = deterministic_requirements(opp, evidence)
    ai = settings_summary(settings)
    mode = "deterministic"
    provider = ai["provider"]
    warning = ""
    ai_notes = ""
    if ai["enabled"]:
        source_text, _ = _source_text(opp, evidence)
        prompt = "\n".join([
            "Extract proposal-critical requirements from this opportunity.",
            "Return concise bullets grouped as Requirements, Submission Instructions, Evaluation Criteria, and Required Forms.",
            "Cite source phrases when possible and do not invent missing requirements.",
            f"Title: {opp.get('title') or ''}",
            f"Agency: {opp.get('organization') or ''}",
            f"Deadline: {opp.get('dueDisplay') or opp.get('responseDeadline') or 'n/a'}",
            "Source text:",
            source_text[:7000],
        ])
        response = LLMClient(settings).complete(prompt, system="You support government proposal intake with careful requirement extraction.", max_tokens=700)
        if response.ok and response.text.strip():
            ai_notes = response.text.strip()
            mode = "ai"
        else:
            warning = response.error or "AI provider returned no requirements; deterministic extraction used."
    _record_ai_audit(
        settings,
        notice_id=notice_id,
        action="requirements",
        mode=mode,
        provider=provider,
        model=str(ai.get("model") or ""),
        result="success" if mode == "ai" else "fallback",
        message=warning or "Requirements assist completed.",
    )
    source_facts = [
        str(item.get("sourceExcerpt") or item.get("snippet") or "")[:260]
        for item in evidence[:6]
        if item.get("sourceExcerpt") or item.get("snippet")
    ]
    return {
        "ok": True,
        "mode": mode,
        "provider": provider,
        "warning": warning,
        "requirements": requirements,
        "sourceFacts": source_facts,
        "businessAssumptions": [str(opp.get("fitReason") or "Requirements are inferred from available report context.")],
        "aiRecommendations": ["Confirm extracted requirements against the authoritative solicitation before proposal use."],
        "aiNotes": ai_notes,
        "ai": ai,
    }


def _has_text(terms: tuple[str, ...], *values: Any) -> bool:
    text = "\n".join(str(value or "") for value in values).lower()
    return any(term in text for term in terms)


def _gap(severity: str, title: str, detail: str, action: str, source: str) -> dict[str, str]:
    return {"severity": severity, "title": title, "detail": detail, "action": action, "source": source}


def deterministic_gap_analysis(opp: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    requirements = deterministic_requirements(opp, evidence)
    gaps: list[dict[str, str]] = []
    strengths: list[str] = []
    docs = list(opp.get("proposalDocuments") or [])
    workflow_docs = list(opp.get("workflowDocuments") or [])
    proposal = opp.get("proposal") or {}
    source_text, sources = _source_text(opp, evidence)
    fit_reason = str(opp.get("fitReason") or "")
    score = int(opp.get("score") or 0)

    if score >= 8:
        strengths.append(f"High initial fit score: {score}.")
    if fit_reason:
        strengths.append(f"Fit rationale is present: {fit_reason[:180]}")
    if evidence:
        strengths.append(f"Parsed evidence available: {len(evidence)} snippet(s).")

    if not proposal.get("id"):
        gaps.append(_gap("high", "Proposal workspace not started", "No proposal pipeline exists for this opportunity yet.", "Create a prime or subcontractor proposal workspace before drafting.", "Local pipeline"))
    if not (opp.get("workflowOwner") or ""):
        gaps.append(_gap("medium", "Owner unassigned", "No capture owner is assigned.", "Assign an owner for document review, bid/no-bid, and partner follow-up.", "Local workflow"))
    if not docs and not workflow_docs:
        gaps.append(_gap("high", "Solicitation package not registered", "No proposal documents or workflow document links are attached.", "Add the SAM.gov listing, solicitation, PWS, Q&A, and amendment files or URLs.", "Document intake"))
    elif docs and not evidence:
        gaps.append(_gap("medium", "Documents not parsed into evidence", "Documents are registered, but no parsed evidence snippets are available.", "Parse the most authoritative solicitation document and confirm evidence snippets.", "Document intake"))
    if not requirements.get("evaluationCriteria"):
        gaps.append(_gap("medium", "Evaluation criteria unclear", "No clear evaluation criteria were found in the available text.", "Review the solicitation attachments for basis of award and technical evaluation factors.", "Requirements assist"))
    if requirements.get("requiredForms"):
        gaps.append(_gap("medium", "Forms checklist needed", "Required forms or attachments were detected, but no checklist artifact exists yet.", "Create a forms checklist before draft generation.", "Requirements assist"))
    if _has_text(("cmmc", "nist", "fedramp", "fisma", "ato", "security clearance", "clearance"), source_text, fit_reason):
        gaps.append(_gap("high", "Compliance evidence needed", "The opportunity references security or compliance expectations.", "Collect proof points for security controls, CMMC/NIST posture, cleared staffing, or compliant delivery approach as applicable.", "SAM.gov description"))
    if _has_text(("past performance", "similar experience", "relevant experience"), source_text):
        gaps.append(_gap("medium", "Past performance mapping needed", "Relevant experience or past performance language was detected.", "Map 2-3 qualifying projects to the stated work and evaluation language.", "SAM.gov description"))
    if _has_text(("due within 48 hours", "due this week"), " ".join(str(item) for item in opp.get("followUpReasons") or [])) or str(opp.get("urgency") or "").lower() == "high":
        gaps.append(_gap("high", "Compressed response window", "The opportunity has deadline pressure.", "Confirm whether there is enough time for attachments, teaming, pricing, and review before pursuing.", "Deadline analysis"))
    for change in (opp.get("amendmentTimeline") or [])[:4]:
        impact = str(change.get("impact") or "medium")
        title = f"Review amendment: {change.get('field') or change.get('machineType') or 'change'}"
        detail = str(change.get("explanation") or "A material opportunity revision was detected.")
        action = f"Confirm source facts and update capture plan for {change.get('afterSummary') or 'the latest revision'}."
        gaps.append(_gap("critical" if impact == "critical" else "high" if impact == "high" else "medium", title, detail, action, "Amendment intelligence"))
    if not (opp.get("workflowNextAction") or opp.get("nextAction") or ""):
        gaps.append(_gap("low", "Next action missing", "The opportunity has no recorded next action.", "Add the next concrete capture action and follow-up date.", "Local workflow"))

    if not gaps:
        gaps.append(_gap("low", "No major deterministic gaps found", "Available report data does not show obvious blockers.", "Review source attachments manually before final bid/no-bid.", "Gap assist"))
    return {
        "gaps": gaps[:8],
        "strengths": strengths[:5],
        "sources": sources,
        "requirements": requirements,
        "sourceFacts": [
            str(item.get("sourceExcerpt") or item.get("snippet") or "")[:260]
            for item in evidence[:6]
            if item.get("sourceExcerpt") or item.get("snippet")
        ],
        "businessAssumptions": strengths[:5],
        "aiRecommendations": [item["action"] for item in gaps[:8]],
    }


def opportunity_gaps(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    notice_id = str(payload.get("noticeId") or "")
    if not notice_id:
        raise ValueError("noticeId is required")
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    opp = _latest_match(settings, notice_id)
    if not opp:
        raise ValueError("Opportunity not found in latest report")
    evidence = store.evidence_citations(notice_id)
    analysis = deterministic_gap_analysis(opp, evidence)
    ai = settings_summary(settings)
    mode = "deterministic"
    provider = ai["provider"]
    warning = ""
    ai_notes = ""
    if ai["enabled"]:
        source_text, _ = _source_text(opp, evidence)
        prompt = "\n".join([
            "Analyze capture and proposal gaps for this opportunity.",
            "Focus on blockers, missing evidence, compliance proof, submission artifacts, evaluation unknowns, and deadline risk.",
            "Return concise practical notes only; do not invent facts not supported by the source text.",
            f"Title: {opp.get('title') or ''}",
            f"Agency: {opp.get('organization') or ''}",
            f"Fit: {opp.get('fitReason') or ''}",
            f"Deadline: {opp.get('dueDisplay') or opp.get('responseDeadline') or 'n/a'}",
            "Source text:",
            source_text[:7000],
        ])
        response = LLMClient(settings).complete(prompt, system="You identify practical government contracting capture gaps with conservative evidence handling.", max_tokens=700)
        if response.ok and response.text.strip():
            ai_notes = response.text.strip()
            mode = "ai"
        else:
            warning = response.error or "AI provider returned no gap analysis; deterministic analysis used."
    _record_ai_audit(
        settings,
        notice_id=notice_id,
        action="gaps",
        mode=mode,
        provider=provider,
        model=str(ai.get("model") or ""),
        result="success" if mode == "ai" else "fallback",
        message=warning or "Gap assist completed.",
    )
    return {
        "ok": True,
        "mode": mode,
        "provider": provider,
        "warning": warning,
        "analysis": analysis,
        "sourceFacts": analysis.get("sourceFacts", []),
        "businessAssumptions": analysis.get("businessAssumptions", []),
        "aiRecommendations": analysis.get("aiRecommendations", []),
        "aiNotes": ai_notes,
        "ai": ai,
    }


def _bullet_lines(items: list[dict[str, Any]], fallback: str) -> str:
    lines = [f"- {item.get('text') or item.get('detail') or item.get('title')}" for item in items if item.get('text') or item.get('detail') or item.get('title')]
    return "\n".join(lines[:8]) if lines else f"- {fallback}"


def _requirement_rows(requirements: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for bucket, label in (
        ("requirements", "Requirement"),
        ("submissionInstructions", "Submission"),
        ("evaluationCriteria", "Evaluation"),
        ("requiredForms", "Form"),
    ):
        for item in requirements.get(bucket) or []:
            rows.append({"category": label, "text": str(item.get("text") or ""), "source": str(item.get("source") or "Latest report")})
    return rows[:16]


def deterministic_prime_templates(opp: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    title = str(opp.get("title") or "Opportunity").strip()
    agency = str(opp.get("organization") or "Agency not listed").strip()
    deadline = str(opp.get("dueDisplay") or opp.get("responseDeadline") or "n/a").strip()
    fit = str(opp.get("fitReason") or "Review the fit rationale and source documents.").strip()
    requirements = deterministic_requirements(opp, evidence)
    gaps = deterministic_gap_analysis({**opp, "proposal": {"id": 1, "role": "prime"}}, evidence)
    rows = _requirement_rows(requirements)
    req_bullets = _bullet_lines(requirements.get("requirements") or [], "Confirm scope requirements from the solicitation package.")
    eval_bullets = _bullet_lines(requirements.get("evaluationCriteria") or [], "Identify evaluation criteria from attachments.")
    submission_bullets = _bullet_lines(requirements.get("submissionInstructions") or [], "Confirm submission instructions and portal/email details.")
    form_bullets = _bullet_lines(requirements.get("requiredForms") or [], "Confirm required forms, clauses, and representations.")
    evidence_bullets = _bullet_lines(evidence[:8], "Parse solicitation documents to attach source evidence.")
    matrix_rows = "\n".join(
        f"| {row['category']} | {row['text']} | {row['source']} | TBD | Open |" for row in rows
    ) or "| Requirement | Confirm requirements from solicitation package. | Latest report | TBD | Open |"
    question_lines = "\n".join(
        f"- {item['title']}: {item['action']}" for item in gaps.get("gaps", [])[:8]
    ) or "- Confirm whether all attachments, amendments, and Q&A have been reviewed."
    return [
        {
            "artifactType": "prime-proposal",
            "title": "Prime Proposal Draft Template",
            "notes": "Generated deterministic prime template; edit before use.",
            "content": f"""# Prime Proposal Draft Template\n\n## Opportunity\n- Title: {title}\n- Agency: {agency}\n- Response deadline: {deadline}\n- Initial fit: {fit}\n\n## Executive Summary\nDraft a concise summary of the agency need, proposed outcome, and why the prime offer is low-risk.\n\n## Technical Approach\n{req_bullets}\n\n## Security and Compliance Approach\nDescribe controls, compliance posture, secure delivery workflow, and required certifications or proof points.\n\n## Management and Staffing\nIdentify the capture owner, delivery lead, key roles, teaming needs, schedule, and quality controls.\n\n## Past Performance\nMap relevant projects to the requirement language and evaluation criteria.\n\n## Evaluation Alignment\n{eval_bullets}\n\n## Submission Plan\n{submission_bullets}\n\n## Source Evidence To Review\n{evidence_bullets}\n""",
        },
        {
            "artifactType": "compliance-matrix",
            "title": "Prime Compliance Matrix",
            "notes": "Generated from available report text and parsed evidence.",
            "content": f"""# Prime Compliance Matrix\n\n| Category | Requirement | Source | Response Owner | Status |\n| --- | --- | --- | --- | --- |\n{matrix_rows}\n""",
        },
        {
            "artifactType": "forms-checklist",
            "title": "Prime Forms and Submission Checklist",
            "notes": "Use this to track forms, attachments, and submission readiness.",
            "content": f"""# Prime Forms and Submission Checklist\n\n## Required Forms / Attachments\n{form_bullets}\n\n## Submission Instructions\n{submission_bullets}\n\n## Internal Review Gates\n- Bid/no-bid confirmed\n- Solicitation, amendments, and Q&A reviewed\n- Compliance matrix reviewed\n- Pricing assumptions reviewed\n- Final package owner assigned\n""",
        },
        {
            "artifactType": "questions",
            "title": "Prime Capture Questions",
            "notes": "Questions generated from current gaps and unknowns.",
            "content": f"""# Prime Capture Questions\n\n## Questions / Follow-Ups\n{question_lines}\n\n## Decision Notes\n- Confirm prime responsibility and subcontracting needs.\n- Confirm whether the response window supports a compliant submission.\n- Confirm any certifications, facility, clearance, or past performance gaps.\n""",
        },
    ]


def deterministic_subcontractor_templates(opp: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    title = str(opp.get("title") or "Opportunity").strip()
    agency = str(opp.get("organization") or "Agency not listed").strip()
    deadline = str(opp.get("dueDisplay") or opp.get("responseDeadline") or "n/a").strip()
    fit = str(opp.get("fitReason") or "Review the fit rationale and source documents.").strip()
    requirements = deterministic_requirements(opp, evidence)
    gaps = deterministic_gap_analysis({**opp, "proposal": {"id": 1, "role": "subcontractor"}}, evidence)
    req_bullets = _bullet_lines(requirements.get("requirements") or [], "Confirm which scope elements are realistic for a subcontractor role.")
    eval_bullets = _bullet_lines(requirements.get("evaluationCriteria") or [], "Ask the prime how evaluation factors map to subcontractor inputs.")
    submission_bullets = _bullet_lines(requirements.get("submissionInstructions") or [], "Confirm prime-required internal due dates and format.")
    evidence_bullets = _bullet_lines(evidence[:8], "Parse source documents to attach supporting evidence.")
    question_lines = "\n".join(
        f"- {item['title']}: {item['action']}" for item in gaps.get("gaps", [])[:8]
    ) or "- Confirm workshare, pricing model, and required partner inputs with the prime."
    matrix_rows = "\n".join(
        f"| {row['category']} | {row['text']} | Prime/TBD | Sub/TBD | Open |" for row in _requirement_rows(requirements)
    ) or "| Requirement | Confirm requirements from solicitation package. | Prime/TBD | Sub/TBD | Open |"
    return [
        {
            "artifactType": "subcontractor",
            "title": "Subcontractor Capability Response Template",
            "notes": "Generated deterministic subcontractor template; tailor for the target prime.",
            "content": f"""# Subcontractor Capability Response Template\n\n## Opportunity\n- Title: {title}\n- Agency: {agency}\n- Response deadline: {deadline}\n- Initial fit: {fit}\n\n## Partner Positioning\nDescribe the specific workshare, niche technical value, and low-friction teaming model offered to the prime.\n\n## Relevant Capabilities\n{req_bullets}\n\n## Security / Compliance Support\nSummarize certifications, controls, tooling, secure delivery practices, and documentation the prime can reuse.\n\n## Past Performance Inputs\nList relevant project summaries, roles performed, outcomes, and references the prime may cite.\n\n## Evaluation Support\n{eval_bullets}\n\n## Prime Input Needed\n{submission_bullets}\n\n## Evidence To Attach\n{evidence_bullets}\n""",
        },
        {
            "artifactType": "compliance-matrix",
            "title": "Subcontractor Responsibility Matrix",
            "notes": "Use with a prime to clarify ownership and handoffs.",
            "content": f"""# Subcontractor Responsibility Matrix\n\n| Category | Requirement | Prime Owner | Subcontractor Owner | Status |\n| --- | --- | --- | --- | --- |\n{matrix_rows}\n""",
        },
        {
            "artifactType": "forms-checklist",
            "title": "Subcontractor Partner Checklist",
            "notes": "Track documents and inputs commonly requested by primes.",
            "content": """# Subcontractor Partner Checklist\n\n## Prime-Facing Materials\n- Capability statement\n- Socio-economic certification summary\n- NAICS/PSC fit notes\n- Past performance summaries\n- Security/compliance proof points\n- Labor categories or rate assumptions\n- Conflict of interest check\n\n## Internal Readiness\n- Confirm desired workshare\n- Confirm pricing constraints\n- Confirm subcontract terms review owner\n- Confirm response due date to prime\n""",
        },
        {
            "artifactType": "questions",
            "title": "Subcontractor Teaming Questions",
            "notes": "Questions for prime outreach and teaming qualification.",
            "content": f"""# Subcontractor Teaming Questions\n\n## Questions For Prime / Capture Lead\n{question_lines}\n\n## Partner Fit Checks\n- What scope does the prime want covered by a subcontractor?\n- What evidence or past performance format does the prime need?\n- What pricing detail is required before proposal submission?\n- Who owns final compliance matrix updates?\n""",
        },
    ]


def generate_subcontractor_proposal_templates(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    notice_id = str(payload.get("noticeId") or "")
    if not notice_id:
        raise ValueError("noticeId is required")
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    opp = _latest_match(settings, notice_id)
    if not opp:
        raise ValueError("Opportunity not found in latest report")
    proposal = store.proposal_map([notice_id]).get(notice_id) or {}
    if proposal and proposal.get("role") != "subcontractor":
        raise ValueError("Subcontractor templates require a subcontractor proposal workspace")
    if not proposal:
        proposal = store.create_proposal(notice_id, {"noticeId": notice_id, "title": opp.get("title") or notice_id, "role": "subcontractor"})
    evidence = store.evidence_citations(notice_id)
    generated: list[dict[str, Any]] = []
    existing = store.proposal_artifacts(notice_id)
    by_title = {str(item.get("title") or ""): item for item in existing}
    for template in deterministic_subcontractor_templates({**opp, "proposal": proposal}, evidence):
        artifact_payload = {"noticeId": notice_id, "status": "draft", "format": "markdown", **template}
        current = by_title.get(template["title"])
        if current:
            generated.append(store.update_proposal_artifact(int(current["id"]), artifact_payload))
        else:
            generated.append(store.add_proposal_artifact(artifact_payload))
    ai = settings_summary(settings)
    _record_ai_audit(
        settings,
        notice_id=notice_id,
        action="subcontractor_templates",
        mode="deterministic",
        provider=ai["provider"],
        model=str(ai.get("model") or ""),
        result="success",
        message=f"Generated {len(generated)} subcontractor proposal artifact templates.",
    )
    return {"ok": True, "mode": "deterministic", "proposal": proposal, "generated": generated, "artifacts": store.proposal_artifacts(notice_id)}


def generate_prime_proposal_templates(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    notice_id = str(payload.get("noticeId") or "")
    if not notice_id:
        raise ValueError("noticeId is required")
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    opp = _latest_match(settings, notice_id)
    if not opp:
        raise ValueError("Opportunity not found in latest report")
    proposal = store.proposal_map([notice_id]).get(notice_id) or {}
    if proposal and proposal.get("role") != "prime":
        raise ValueError("Prime templates require a prime proposal workspace")
    if not proposal:
        proposal = store.create_proposal(notice_id, {"noticeId": notice_id, "title": opp.get("title") or notice_id, "role": "prime"})
    evidence = store.evidence_citations(notice_id)
    generated: list[dict[str, Any]] = []
    existing = store.proposal_artifacts(notice_id)
    by_title = {str(item.get("title") or ""): item for item in existing}
    for template in deterministic_prime_templates({**opp, "proposal": proposal}, evidence):
        payload = {"noticeId": notice_id, "status": "draft", "format": "markdown", **template}
        current = by_title.get(template["title"])
        if current:
            generated.append(store.update_proposal_artifact(int(current["id"]), payload))
        else:
            generated.append(store.add_proposal_artifact(payload))
    _record_ai_audit(
        settings,
        notice_id=notice_id,
        action="prime_templates",
        mode="deterministic",
        provider=settings_summary(settings)["provider"],
        model=str(settings_summary(settings).get("model") or ""),
        result="success",
        message=f"Generated {len(generated)} prime proposal artifact templates.",
    )
    return {"ok": True, "mode": "deterministic", "proposal": proposal, "generated": generated, "artifacts": store.proposal_artifacts(notice_id)}


def _record_ai_audit(settings: Settings, *, notice_id: str, action: str, mode: str, provider: str, model: str, result: str, message: str = "") -> None:
    try:
        Store(settings.data_dir / "sam-radar.sqlite3").record_ai_audit(
            {
                "noticeId": notice_id,
                "action": action,
                "provider": provider,
                "mode": mode,
                "model": model,
                "result": result,
                "external": mode == "ai" and provider == "openai-compatible",
                "message": message,
            }
        )
    except (OSError, sqlite3.Error):
        return


def deterministic_summary(opp: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    text, sources = _source_text(opp, evidence)
    evidence_bullets = [str(item.get("sourceExcerpt") or item.get("snippet") or "")[:260] for item in evidence[:5] if item.get("sourceExcerpt") or item.get("snippet")]
    overview_parts = _sentences(text, 2) or [str(opp.get("title") or "Opportunity summary is not available.")]
    fit = str(opp.get("fitReason") or "Review capability, NAICS/PSC, set-aside, deadline, and parsed document evidence.")
    action = str(opp.get("nextAction") or opp.get("workflowNextAction") or "Review the opportunity and confirm bid/no-bid posture.")
    amendment_facts = [
        f"{item.get('field') or item.get('machineType') or 'Amendment'!s} changed: {item.get('explanation') or ''!s} {item.get('afterSummary') or ''!s}".strip()
        for item in (opp.get("amendmentTimeline") or [])[:5]
    ]
    amendment_actions = ["Review amendment impact and refresh proposal evidence before relying on prior citations."] if amendment_facts else []
    return {
        "overview": " ".join(overview_parts),
        "fit": fit,
        "deadline": str(opp.get("dueDisplay") or opp.get("responseDeadline") or "n/a"),
        "recommendedAction": action,
        "evidence": evidence_bullets,
        "sources": sources,
        "sourceFacts": evidence_bullets + amendment_facts,
        "businessAssumptions": [fit],
        "aiRecommendations": [action] + amendment_actions,
    }


def opportunity_summary(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    notice_id = str(payload.get("noticeId") or "")
    if not notice_id:
        raise ValueError("noticeId is required")
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    opp = _latest_match(settings, notice_id)
    if not opp:
        raise ValueError("Opportunity not found in latest report")
    evidence = store.evidence_citations(notice_id)
    base = deterministic_summary(opp, evidence)
    ai = settings_summary(settings)
    mode = "deterministic"
    provider = ai["provider"]
    warning = ""
    if ai["enabled"]:
        source_text, sources = _source_text(opp, evidence)
        prompt = "\n".join([
            "Create a concise capture-oriented opportunity summary.",
            "Return four short sections: Overview, Fit, Deadline/Action, Evidence.",
            f"Title: {opp.get('title') or ''}",
            f"Agency: {opp.get('organization') or ''}",
            f"Deadline: {opp.get('dueDisplay') or opp.get('responseDeadline') or 'n/a'}",
            "Source text:",
            source_text[:6000],
        ])
        response = LLMClient(settings).complete(prompt, system="You summarize government contracting opportunities for capture review.", max_tokens=500)
        if response.ok and response.text.strip():
            base["aiSummary"] = response.text.strip()
            base["sources"] = sources
            mode = "ai"
        else:
            warning = response.error or "AI provider returned no summary; deterministic fallback used."
    _record_ai_audit(
        settings,
        notice_id=notice_id,
        action="summary",
        mode=mode,
        provider=provider,
        model=str(ai.get("model") or ""),
        result="success" if mode == "ai" else "fallback",
        message=warning or "Summary assist completed.",
    )
    return {"ok": True, "mode": mode, "provider": provider, "warning": warning, "summary": base, "ai": ai}
