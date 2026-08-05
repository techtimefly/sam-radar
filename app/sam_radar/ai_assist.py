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
