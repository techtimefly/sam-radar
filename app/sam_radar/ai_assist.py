from __future__ import annotations

import json
import re
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
        sources.append("Parsed document evidence")
        chunks.extend(str(item.get("snippet") or "") for item in evidence[:8] if item.get("snippet"))
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
        text = str(item.get("snippet") or "").strip()
        if text:
            section = str(item.get("section") or "Parsed evidence").strip()
            items.append({"source": f"Parsed document evidence: {section}", "text": text})
    return items


_REQUIREMENT_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("evaluationCriteria", "Evaluation", ("evaluation", "evaluate", "basis for award", "best value", "lowest price", "technical approach", "past performance")),
    ("requiredForms", "Forms", ("form", "sf 1449", "sf1449", "attachment", "attachments", "certification", "representation", "provisions", "clauses")),
    ("submissionInstructions", "Submission", ("submit", "submission", "proposal", "quote", "offer", "due", "email", "portal", "instructions", "upload")),
    ("requirements", "Requirements", ("must", "shall", "required", "requirement", "contractor will", "contractor shall", "provide", "perform")),
)


def _requirement_entry(text: str, source: str, label: str, confidence: float) -> dict[str, Any]:
    return {"text": text[:360], "source": source, "label": label, "confidence": confidence}


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
                    confidence = 0.86 if source.startswith("Parsed document") else 0.72
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
    evidence = store.evidence_snippets(notice_id)
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
    return {"ok": True, "mode": mode, "provider": provider, "warning": warning, "requirements": requirements, "aiNotes": ai_notes, "ai": ai}


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
    if not (opp.get("workflowNextAction") or opp.get("nextAction") or ""):
        gaps.append(_gap("low", "Next action missing", "The opportunity has no recorded next action.", "Add the next concrete capture action and follow-up date.", "Local workflow"))

    if not gaps:
        gaps.append(_gap("low", "No major deterministic gaps found", "Available report data does not show obvious blockers.", "Review source attachments manually before final bid/no-bid.", "Gap assist"))
    return {"gaps": gaps[:8], "strengths": strengths[:5], "sources": sources, "requirements": requirements}


def opportunity_gaps(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    notice_id = str(payload.get("noticeId") or "")
    if not notice_id:
        raise ValueError("noticeId is required")
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    opp = _latest_match(settings, notice_id)
    if not opp:
        raise ValueError("Opportunity not found in latest report")
    evidence = store.evidence_snippets(notice_id)
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
    return {"ok": True, "mode": mode, "provider": provider, "warning": warning, "analysis": analysis, "aiNotes": ai_notes, "ai": ai}


def deterministic_summary(opp: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    text, sources = _source_text(opp, evidence)
    evidence_bullets = [str(item.get("snippet") or "")[:260] for item in evidence[:5] if item.get("snippet")]
    overview_parts = _sentences(text, 2) or [str(opp.get("title") or "Opportunity summary is not available.")]
    fit = str(opp.get("fitReason") or "Review capability, NAICS/PSC, set-aside, deadline, and parsed document evidence.")
    action = str(opp.get("nextAction") or opp.get("workflowNextAction") or "Review the opportunity and confirm bid/no-bid posture.")
    return {
        "overview": " ".join(overview_parts),
        "fit": fit,
        "deadline": str(opp.get("dueDisplay") or opp.get("responseDeadline") or "n/a"),
        "recommendedAction": action,
        "evidence": evidence_bullets,
        "sources": sources,
    }


def opportunity_summary(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    notice_id = str(payload.get("noticeId") or "")
    if not notice_id:
        raise ValueError("noticeId is required")
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    opp = _latest_match(settings, notice_id)
    if not opp:
        raise ValueError("Opportunity not found in latest report")
    evidence = store.evidence_snippets(notice_id)
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
    return {"ok": True, "mode": mode, "provider": provider, "warning": warning, "summary": base, "ai": ai}
