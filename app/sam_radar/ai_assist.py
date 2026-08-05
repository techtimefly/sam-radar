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
