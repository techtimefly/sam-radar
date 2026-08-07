from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .ai_assist import (
    generate_prime_proposal_templates,
    generate_subcontractor_proposal_templates,
    opportunity_gaps,
    opportunity_requirements,
    opportunity_summary,
)
from .config import BusinessProfile, Settings, load_business_profile
from .descriptions import enrich_descriptions
from .digest import build_digest, build_no_new_digest
from .document_intake import MAX_DOCUMENT_BYTES, parse_registered_document, safe_filename
from .llm import LLMClient, settings_summary
from .notifications.slack import send_slack_webhook
from .notifications.telegram import send_telegram
from .reports import build_report_payload, days_until, write_reports
from .sam_api import fetch_json, mmddyyyy
from .scoring import DEFAULT_EXCLUDED_TYPES, is_expired, normalize_opp, score_opp, search_opportunities
from .search_intel import SET_ASIDES, search_reference, set_asides_for_status, suggest_profiles
from .storage import Store


def send_notifications(settings: Settings, message: str) -> list[str]:
    sent: list[str] = []
    if settings.enable_slack and settings.slack_webhook_url:
        send_slack_webhook(settings.slack_webhook_url, message)
        sent.append("slack")
    if settings.enable_telegram and settings.telegram_bot_token and settings.telegram_chat_id:
        send_telegram(settings.telegram_bot_token, settings.telegram_chat_id, message)
        sent.append("telegram")
    return sent


def workflow_notification_text(event_type: str, opp: dict, report_url: str) -> str:
    title = opp.get("title") or "(untitled)"
    status = opp.get("workflowStatus") or "new"
    due = opp.get("dueDisplay") or "n/a"
    score = opp.get("score") or "n/a"
    url = opp.get("url") or opp.get("uiLink") or "n/a"
    next_action = opp.get("workflowNextAction") or opp.get("nextAction") or "Review opportunity."
    return "\n".join(
        [
            f"SAM Radar workflow: {event_type}",
            title,
            f"Status: {status} | Score: {score} | Due: {due}",
            f"Next: {next_action}",
            f"SAM.gov: {url}",
            f"Pipeline: {report_url}",
        ]
    )



def notification_preview(settings: Settings, payload: dict | None = None) -> dict:
    payload = payload or {}
    profile = load_business_profile(settings.profile_path)
    report_url = settings.latest_report_url
    report: dict = {"summary": {}, "matches": []}
    latest_json = settings.reports_dir / "latest.json"
    if latest_json.exists():
        report = json.loads(latest_json.read_text() or "{}")
    matches = list(report.get("matches") or [])
    notice_id = str(payload.get("noticeId") or "").strip()
    event_type = str(payload.get("eventType") or "").strip()
    if notice_id and event_type:
        opp = next((match for match in matches if str(match.get("noticeId") or "") == notice_id), None)
        if not opp:
            raise ValueError("noticeId not found in latest report")
        message = workflow_notification_text(event_type, opp, report_url)
        preview_type = "workflow"
    elif matches:
        summary = report.get("summary") or {}
        max_items = max(1, min(int(payload.get("maxItems") or 7), 15))
        message = build_digest(
            matches,
            profile,
            settings,
            str(summary.get("postedFrom") or ""),
            str(summary.get("postedTo") or ""),
            report_url,
            max_items=max_items,
        )
        preview_type = "digest"
    else:
        summary = report.get("summary") or {}
        message = build_no_new_digest(
            profile,
            str(summary.get("postedFrom") or ""),
            str(summary.get("postedTo") or ""),
            report_url,
        )
        preview_type = "no-new"
    return {
        "ok": True,
        "type": preview_type,
        "message": message,
        "length": len(message),
        "slackConfigured": bool(settings.enable_slack and settings.slack_webhook_url),
        "telegramConfigured": bool(settings.enable_telegram and settings.telegram_bot_token and settings.telegram_chat_id),
    }


def send_workflow_notifications(settings: Settings, store: Store, report: dict, report_url: str) -> list[str]:
    if not (settings.enable_slack and settings.enable_slack_workflow and settings.slack_webhook_url):
        return []
    sent: list[str] = []
    enabled = set(settings.slack_workflow_events)
    today_key = report["summary"].get("generatedAt", "")[:10]
    for opp in report.get("matches") or []:
        notice_id = str(opp.get("noticeId") or "")
        if not notice_id:
            continue
        status = str(opp.get("workflowStatus") or "new")
        candidates: list[str] = []
        if status in {"pursue", "submitted"}:
            candidates.append(status)
        due_days = days_until(opp.get("responseDeadline") or "")
        if due_days is not None and 0 <= due_days <= 2:
            candidates.append("due-soon")
        follow_up = str(opp.get("workflowFollowUpAt") or "")[:10]
        if follow_up and follow_up <= today_key:
            candidates.append("follow-up-due")
        for event_type in candidates:
            if event_type not in enabled:
                continue
            key = today_key if event_type in {"due-soon", "follow-up-due"} else status
            if store.record_notification_once(notice_id, event_type, key):
                send_slack_webhook(settings.slack_webhook_url, workflow_notification_text(event_type, opp, report_url))
                sent.append(f"slack:{event_type}")
    return sent



def _manual_date(value: str, fallback: dt.date) -> str:
    if not value:
        return mmddyyyy(fallback)
    try:
        return mmddyyyy(dt.date.fromisoformat(str(value)[:10]))
    except ValueError:
        return mmddyyyy(fallback)


def _current_report_ids(settings: Settings) -> set[str]:
    latest = settings.reports_dir / "latest.json"
    if not latest.exists():
        return set()
    try:
        import json

        data = json.loads(latest.read_text())
    except Exception:  # noqa: BLE001
        return set()
    return {str(match.get("noticeId")) for match in data.get("matches", []) if match.get("noticeId")}


def _clean_manual_text(value: object, limit: int = 1000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _normalize_manual_url(value: object) -> str:
    url = _clean_manual_text(value, 1000)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url
    if "." in url and " " not in url:
        return f"https://{url}"
    return url


def _manual_notice_id(payload: dict) -> str:
    explicit = _clean_manual_text(payload.get("noticeId") or payload.get("notice_id"), 200)
    if explicit:
        return explicit
    url = _normalize_manual_url(payload.get("sourceUrl") or payload.get("url") or payload.get("uiLink"))
    if url:
        fingerprint = f"url:{url.lower().rstrip('/')}"
    else:
        parts = [
            _clean_manual_text(payload.get("title"), 500).lower(),
            _clean_manual_text(payload.get("organization") or payload.get("agency"), 300).lower(),
            _clean_manual_text(payload.get("responseDeadline") or payload.get("dueDate"), 80).lower(),
        ]
        fingerprint = "manual:" + "|".join(parts)
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"manual-{digest}"


def _manual_opportunity_payload(payload: dict) -> dict:
    title = _clean_manual_text(payload.get("title"), 500)
    source_url = _normalize_manual_url(payload.get("sourceUrl") or payload.get("url") or payload.get("uiLink"))
    source_name = _clean_manual_text(payload.get("sourceName") or payload.get("source") or payload.get("sourceType") or "External source", 120)
    context = _clean_manual_text(payload.get("notes") or payload.get("context") or payload.get("fitReason"), 2000)
    organization = _clean_manual_text(payload.get("organization") or payload.get("agency") or payload.get("customer"), 500)
    if not title:
        raise ValueError("Title is required")
    if not any([source_url, source_name, context, organization]):
        raise ValueError("Add a source URL, source name, customer/agency, or context note")
    today = dt.datetime.now(dt.UTC).date().isoformat()
    notice_id = _manual_notice_id({**payload, "title": title, "url": source_url, "organization": organization})
    try:
        score = max(0, min(int(payload.get("score")), 10)) if payload.get("score") not in (None, "") else 0
    except (TypeError, ValueError):
        score = 0
    recommendation = _clean_manual_text(payload.get("recommendation") or payload.get("priority") or "Review", 80)
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    reasons = [_clean_manual_text(reason, 240) for reason in reasons if _clean_manual_text(reason, 240)]
    if source_name:
        reasons.insert(0, f"manual source: {source_name}")
    if context:
        reasons.append(context)
    return {
        "noticeId": notice_id,
        "title": title,
        "type": _clean_manual_text(payload.get("type") or payload.get("noticeType") or "External Opportunity", 120),
        "postedDate": _clean_manual_text(payload.get("postedDate") or payload.get("discoveredDate") or today, 80),
        "responseDeadline": _clean_manual_text(payload.get("responseDeadline") or payload.get("dueDate") or "", 120),
        "naicsCode": _clean_manual_text(payload.get("naicsCode") or payload.get("naics"), 80),
        "classificationCode": _clean_manual_text(payload.get("classificationCode") or payload.get("psc"), 80),
        "setAsideCode": _clean_manual_text(payload.get("setAsideCode") or payload.get("setAside"), 80),
        "setAside": _clean_manual_text(payload.get("setAside") or payload.get("setAsideCode"), 160),
        "organization": organization or "External source",
        "url": source_url,
        "uiLink": source_url,
        "source": "manual-external",
        "sourceName": source_name,
        "sourceUrl": source_url,
        "manualExternal": True,
        "manualTracked": True,
        "estimatedValue": _clean_manual_text(payload.get("estimatedValue") or payload.get("value"), 120),
        "owner": _clean_manual_text(payload.get("owner") or payload.get("workflowOwner"), 120),
        "workflowOwner": _clean_manual_text(payload.get("workflowOwner") or payload.get("owner"), 120),
        "score": score,
        "recommendation": recommendation,
        "reasons": reasons or ["manual external intake"],
        "fitReason": context or f"Manually added from {source_name}.",
        "nextAction": _clean_manual_text(
            payload.get("nextAction") or "Review source details, confirm fit, and decide pursue/monitor/no-bid.",
            500,
        ),
    }


def _attach_workflow_context(store: Store, matches: list[dict]) -> None:
    notice_ids = [str(match.get("noticeId")) for match in matches if match.get("noticeId")]
    status_map = store.status_map(notice_ids)
    proposal_map = store.proposal_map(notice_ids)
    proposal_documents_map = store.proposal_document_map(notice_ids)
    proposal_artifact_map = store.proposal_artifact_map(notice_ids)
    for match in matches:
        notice_id = str(match.get("noticeId") or "")
        workflow = status_map.get(notice_id) or store.get_status(notice_id)
        match["workflowStatus"] = workflow["status"]
        match["workflowNotes"] = workflow["notes"]
        match["workflowPriority"] = workflow.get("priority", "normal")
        match["workflowOwner"] = workflow.get("owner", "")
        match["workflowNextAction"] = workflow.get("nextAction", "")
        match["workflowFollowUpAt"] = workflow.get("followUpAt", "")
        match["workflowDecisionReason"] = workflow.get("decisionReason", "")
        match["workflowNoBidReason"] = workflow.get("noBidReason", "")
        match["workflowNoBidDetail"] = workflow.get("noBidDetail", "")
        match["workflowDocuments"] = workflow.get("documents", [])
        match["workflowEvents"] = workflow.get("events", [])
        match["workflowUpdatedAt"] = workflow["updatedAt"]
        match["proposal"] = proposal_map.get(notice_id) or {}
        match["proposalDocuments"] = proposal_documents_map.get(notice_id, [])
        match["evidenceSnippets"] = store.evidence_snippets(notice_id) if proposal_documents_map.get(notice_id) else []
        match["proposalArtifacts"] = proposal_artifact_map.get(notice_id, [])


def rebuild_report_from_cache(settings: Settings) -> dict:
    profile = load_business_profile(settings.profile_path)
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    latest = settings.reports_dir / "latest.json"
    payload: dict = {"matches": [], "postedFrom": "", "postedTo": "", "errors": []}
    if latest.exists():
        try:
            cached = json.loads(latest.read_text() or "{}")
            summary = cached.get("summary") or {}
            payload = {
                "matches": [
                    dict(match)
                    for match in (cached.get("matches") or [])
                    if not (match.get("manualTracked") or match.get("manualExternal"))
                ],
                "postedFrom": summary.get("postedFrom") or "",
                "postedTo": summary.get("postedTo") or "",
                "errors": cached.get("errors") or [],
            }
        except Exception:  # noqa: BLE001
            payload = {"matches": [], "postedFrom": "", "postedTo": "", "errors": ["cached report could not be read"]}
    matches = payload.get("matches") or []
    match_ids = {str(match.get("noticeId") or "") for match in matches}
    manual_matches = [opp for opp in store.manual_tracked_opportunities() if str(opp.get("noticeId") or "") not in match_ids]
    if manual_matches:
        matches.extend(manual_matches)
    payload["matches"] = matches
    payload["totalMatches"] = len(matches)
    _attach_workflow_context(store, matches)
    unseen = store.unseen(matches)
    report = build_report_payload(payload, profile, settings, unseen=unseen)
    paths = write_reports(report, settings)
    return {"ok": True, "summary": report["summary"], "report": paths, "newMatches": len(unseen), "cached": True}



def search_intelligence(settings: Settings, query: str = "") -> dict:
    profile = load_business_profile(settings.profile_path)
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    status_text = list(getattr(profile, "set_asides", []) or [])
    saved_profiles = store.saved_search_profiles()
    return {
        "ok": True,
        "query": query,
        "naics": search_reference("naics", query),
        "psc": search_reference("psc", query),
        "setAsides": set_asides_for_status(status_text),
        "allSetAsides": SET_ASIDES,
        "savedCodes": store.saved_reference_codes(),
        "profiles": saved_profiles,
        "profileQuality": store.profile_quality(),
        "feedback": store.search_feedback_summary(),
    }


def save_search_reference_code(settings: Settings, payload: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    return {"ok": True, "code": store.save_reference_code(payload)}


def delete_search_reference_code(settings: Settings, payload: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    return {"ok": True, "code": store.delete_reference_code(payload)}


def save_search_profile(settings: Settings, payload: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    return {"ok": True, "profile": store.save_search_profile(payload)}


def add_search_feedback(settings: Settings, payload: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    return {"ok": True, "feedback": store.add_search_feedback(payload), "summary": store.search_feedback_summary()}


def search_coach(settings: Settings, payload: dict) -> dict:
    profile = load_business_profile(settings.profile_path)
    text = str(payload.get("text") or "")
    if not text:
        parts = [profile.display_name]
        parts.extend(getattr(profile, "capabilities", []) or [])
        parts.extend(getattr(profile, "keywords", []) or [])
        text = " ".join(str(part) for part in parts)
    status_text: list[str] = list(getattr(profile, "set_asides", []) or [])
    return {"ok": True, "mode": "deterministic", **suggest_profiles(text, status_text)}


def ai_settings(settings: Settings) -> dict:
    return {"ok": True, "ai": settings_summary(settings)}


def ai_audit_log(settings: Settings) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    return {"ok": True, "events": store.ai_audit_events()}


def ai_opportunity_requirements(settings: Settings, payload: dict) -> dict:
    return opportunity_requirements(settings, payload)


def ai_opportunity_gaps(settings: Settings, payload: dict) -> dict:
    return opportunity_gaps(settings, payload)


def ai_prime_templates(settings: Settings, payload: dict) -> dict:
    return generate_prime_proposal_templates(settings, payload)


def ai_subcontractor_templates(settings: Settings, payload: dict) -> dict:
    return generate_subcontractor_proposal_templates(settings, payload)


def ai_connection_test(settings: Settings) -> dict:
    return LLMClient(settings).test_connection()


def ai_opportunity_summary(settings: Settings, payload: dict) -> dict:
    return opportunity_summary(settings, payload)


def manual_search(settings: Settings, criteria: dict) -> dict:
    if not settings.sam_gov_api_key:
        raise RuntimeError("SAM_GOV_API_KEY is required")
    profile = load_business_profile(settings.profile_path)
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    today = dt.datetime.now(dt.UTC).date()
    days = max(1, min(int(criteria.get("days") or settings.search_days or 7), 60))
    posted_from = _manual_date(str(criteria.get("postedFrom") or ""), today - dt.timedelta(days=days))
    posted_to = _manual_date(str(criteria.get("postedTo") or ""), today)
    profile_name = str(criteria.get("profileName") or "").strip()
    if profile_name:
        saved_profile = store.saved_search_profile(profile_name)
        if saved_profile:
            criteria = {**saved_profile, **{k: v for k, v in criteria.items() if v not in (None, "", [])}}
    params = {"api_key": settings.sam_gov_api_key, "postedFrom": posted_from, "postedTo": posted_to, "limit": str(max(1, min(int(criteria.get("limit") or 25), 100)))}
    field_map = {"keyword": "title", "naics": "ncode", "psc": "ccode", "ptype": "ptype", "setAside": "typeOfSetAside"}
    for source, target in field_map.items():
        raw_value = criteria.get(source)
        if raw_value in (None, ""):
            alias = {"keyword": "keywords", "naics": "naics", "psc": "psc", "ptype": "noticeTypes", "setAside": "setAsides"}.get(source)
            raw_value = criteria.get(alias) if alias else raw_value
        if isinstance(raw_value, list):
            value = ",".join(str(item).strip() for item in raw_value if str(item).strip())
        else:
            value = str(raw_value or "").strip()
        if value:
            params[target] = value
    if not any(params.get(key) for key in ["title", "ncode", "ccode", "ptype", "typeOfSetAside"]):
        raise ValueError("Manual search requires keyword, NAICS, PSC, notice type, or set-aside.")
    payload = fetch_json(params, timeout=12)
    now = dt.datetime.now(dt.UTC)
    current_report_ids = _current_report_ids(settings)
    tracked_ids = store.tracked_notice_ids() | current_report_ids
    results = []
    seen_ids: set[str] = set()
    for raw in (payload or {}).get("opportunitiesData") or []:
        notice_id = str(raw.get("noticeId") or raw.get("noticeid") or "")
        if not notice_id or notice_id in seen_ids:
            continue
        seen_ids.add(notice_id)
        if str(raw.get("type") or "") in DEFAULT_EXCLUDED_TYPES:
            continue
        if is_expired(raw, now):
            continue
        score, reasons = score_opp(profile, raw)
        opp = normalize_opp(raw, score, reasons)
        opp["url"] = opp.get("uiLink") or opp.get("descriptionUrl") or ""
        opp["manualSearch"] = True
        opp["alreadyTracked"] = notice_id in tracked_ids
        opp["trackedReason"] = "Already tracked" if opp["alreadyTracked"] else ""
        results.append(opp)
    results.sort(key=lambda item: item.get("score") or 0, reverse=True)
    description_errors = enrich_descriptions(
        results,
        api_key=settings.sam_gov_api_key,
        data_dir=settings.data_dir,
        enabled=settings.enable_descriptions,
        limit=min(settings.description_fetch_limit, len(results)),
    )
    return {"ok": True, "criteria": {k: v for k, v in params.items() if k != "api_key"}, "matches": results, "count": len(results), "reportsUnchanged": True, "descriptionErrors": description_errors}



def _profile_from_saved(base: BusinessProfile, saved: dict) -> BusinessProfile:
    return BusinessProfile(
        name=base.name,
        dba=str(saved.get("name") or base.display_name),
        location=base.location,
        summary=base.summary,
        capabilities=base.capabilities,
        naics_primary=[str(item) for item in (saved.get("naics") or base.naics_primary)],
        naics_secondary=base.naics_secondary,
        psc=[str(item) for item in (saved.get("psc") or base.psc)],
        set_asides=[str(item) for item in (saved.get("setAsides") or base.set_asides)],
        strong_keywords=[str(item).lower() for item in (saved.get("keywords") or base.strong_keywords)],
        exclude_keywords=[str(item).lower() for item in (saved.get("exclusions") or base.exclude_keywords)],
    )


def _search_with_active_profiles(settings: Settings, profile: BusinessProfile, store: Store) -> dict:
    payload = search_opportunities(
        settings.sam_gov_api_key,
        profile,
        days=settings.search_days,
        max_results=settings.report_limit,
    )
    combined: dict[str, dict] = {}

    def add_matches(matches: list[dict], profile_name: str) -> None:
        for match in matches:
            key = str(match.get("noticeId") or match.get("title") or "")
            if not key:
                continue
            if key not in combined:
                item = dict(match)
                item["searchProfiles"] = [profile_name]
                combined[key] = item
                continue
            existing = combined[key]
            profiles = existing.setdefault("searchProfiles", [])
            if profile_name not in profiles:
                profiles.append(profile_name)
            if int(match.get("score") or 0) > int(existing.get("score") or 0):
                existing.update({k: v for k, v in match.items() if k != "searchProfiles"})
                existing["searchProfiles"] = profiles

    add_matches(payload.get("matches") or [], "Base profile")
    errors = list(payload.get("errors") or [])
    for saved in store.saved_search_profiles():
        if not saved.get("active"):
            continue
        saved_profile = _profile_from_saved(profile, saved)
        try:
            saved_payload = search_opportunities(
                settings.sam_gov_api_key,
                saved_profile,
                days=max(1, min(int(saved.get("days") or settings.search_days), 60)),
                limit=max(1, min(int(saved.get("limit") or 25), 100)),
                max_results=settings.report_limit,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Saved profile {saved_profile.display_name}: {type(exc).__name__}: {exc}")
            continue
        errors.extend(f"Saved profile {saved_profile.display_name}: {error}" for error in (saved_payload.get("errors") or []))
        add_matches(saved_payload.get("matches") or [], saved_profile.display_name)
    matches = list(combined.values())
    matches.sort(key=lambda item: item.get("score") or 0, reverse=True)
    payload["matches"] = matches[: settings.report_limit]
    payload["totalMatches"] = len(payload["matches"])
    payload["errors"] = errors
    return payload


def add_manual_opportunity(settings: Settings, opp: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    manual_external = not str(opp.get("noticeId") or opp.get("notice_id") or "").strip() or bool(opp.get("manualExternal"))
    if manual_external:
        opp = _manual_opportunity_payload(opp)
    notice_id = str(opp.get("noticeId") or "")
    if notice_id in _current_report_ids(settings) or store.is_tracked(notice_id):
        return {"ok": False, "duplicate": True, "error": "Already tracked"}
    workflow = store.add_manual_tracked(opp)
    owner = _clean_manual_text(opp.get("workflowOwner") or opp.get("owner"), 120)
    if owner:
        workflow = store.set_workflow(notice_id, {"owner": owner})
    report = rebuild_report_from_cache(settings)
    return {"ok": True, "workflow": workflow, "opportunity": opp, "report": report.get("report"), "cached": True}


def create_proposal(settings: Settings, payload: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    notice_id = str(payload.get("noticeId") or "")
    proposal = store.create_proposal(notice_id, payload)
    return {"ok": True, "proposal": proposal}


def update_proposal(settings: Settings, payload: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    notice_id = str(payload.get("noticeId") or "")
    proposal = store.update_proposal_stage(notice_id, payload)
    return {"ok": True, "proposal": proposal}


def proposal_list(settings: Settings) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    return {"ok": True, "proposals": store.proposals()}


def add_proposal_document(settings: Settings, payload: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    prepared = dict(payload)
    if str(prepared.get("sourceType") or "").lower() == "upload":
        notice_id = str(prepared.get("noticeId") or "").strip()
        filename = safe_filename(str(prepared.get("filename") or "upload.txt"))
        encoded = str(prepared.get("contentBase64") or "")
        if not notice_id:
            raise ValueError("noticeId is required")
        if not encoded:
            raise ValueError("Uploaded file content is required")
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise ValueError("Uploaded document exceeds 10 MB limit.")
        upload_dir = settings.data_dir / "documents" / safe_filename(notice_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        local_path = upload_dir / f"upload-{dt.datetime.now(dt.UTC).strftime('%Y%m%d%H%M%S')}-{filename}"
        local_path.write_bytes(data)
        prepared.update(
            {
                "source": f"upload:{local_path.name}",
                "filename": filename,
                "localPath": str(local_path),
                "contentType": str(prepared.get("contentType") or "application/octet-stream"),
                "sizeBytes": len(data),
            }
        )
    document = store.add_proposal_document(prepared)
    return {"ok": True, "document": document, "documents": store.proposal_documents(document["noticeId"])}


def parse_proposal_document(settings: Settings, payload: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    document_id = int(payload.get("documentId") or payload.get("id") or 0)
    if not document_id:
        raise ValueError("documentId is required")
    return parse_registered_document(settings, store, document_id)


def remove_proposal_document(settings: Settings, payload: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    document_id = int(payload.get("documentId") or payload.get("id") or 0)
    if not document_id:
        raise ValueError("documentId is required")
    document = store.proposal_document(document_id)
    if not document:
        raise ValueError("document does not exist")
    removed = store.remove_proposal_document(document_id)
    data_dir = settings.data_dir.resolve()
    for key in ("localPath", "extractedTextPath"):
        raw_path = document.get(key) or ""
        if not raw_path:
            continue
        path = Path(raw_path)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if data_dir in resolved.parents and resolved.is_file():
            resolved.unlink(missing_ok=True)
    return {
        "ok": True,
        "document": removed,
        "documents": store.proposal_documents(removed["noticeId"]),
        "evidence": store.evidence_snippets(removed["noticeId"]),
    }


def proposal_documents(settings: Settings, notice_id: str) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    return {"ok": True, "documents": store.proposal_documents(notice_id), "evidence": store.evidence_snippets(notice_id)}


def _markdown_filename(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", value or "proposal-artifact").strip(".-")
    return (name[:120] or "proposal-artifact") + ".md"


def export_proposal_artifact_markdown(settings: Settings, artifact_id: int) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    artifact = store.proposal_artifact(artifact_id)
    if not artifact:
        raise ValueError("artifact does not exist")
    title = artifact.get("title") or "Proposal Artifact"
    body = str(artifact.get("content") or "").strip()
    metadata = [
        f"# {title}",
        "",
        f"- Notice ID: {artifact.get('noticeId') or ''}",
        f"- Type: {artifact.get('artifactType') or ''}",
        f"- Status: {artifact.get('status') or ''}",
        f"- Version: {artifact.get('version') or 1}",
        f"- Updated: {artifact.get('updatedAt') or ''}",
    ]
    notes = str(artifact.get("notes") or "").strip()
    if notes:
        metadata.extend([f"- Notes: {notes}"])
    metadata.extend(["", "---", "", body or "_No artifact content yet._", ""])
    return {"ok": True, "filename": _markdown_filename(str(title)), "content": "\n".join(metadata), "artifact": artifact}


def proposal_artifacts(settings: Settings, notice_id: str) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    return {"ok": True, "artifacts": store.proposal_artifacts(notice_id)}


def proposal_artifact_history(settings: Settings, artifact_id: int) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    return {"ok": True, "history": store.proposal_artifact_history(artifact_id)}


def add_proposal_artifact(settings: Settings, payload: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    artifact = store.add_proposal_artifact(payload)
    return {"ok": True, "artifact": artifact, "artifacts": store.proposal_artifacts(artifact["noticeId"])}


def update_proposal_artifact(settings: Settings, payload: dict) -> dict:
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    artifact_id = int(payload.get("artifactId") or payload.get("id") or 0)
    artifact = store.update_proposal_artifact(artifact_id, payload)
    return {"ok": True, "artifact": artifact, "artifacts": store.proposal_artifacts(artifact["noticeId"])}

def refresh_report(
    settings: Settings,
    *,
    mark_seen: bool = False,
    notify: bool = False,
    notify_no_matches: bool = False,
) -> dict:
    if not settings.sam_gov_api_key:
        raise RuntimeError("SAM_GOV_API_KEY is required")
    profile = load_business_profile(settings.profile_path)
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    payload = _search_with_active_profiles(settings, profile, store)
    matches = payload.get("matches") or []
    match_ids = {str(match.get("noticeId") or "") for match in matches}
    manual_matches = [
        opp
        for opp in store.manual_tracked_opportunities()
        if str(opp.get("noticeId") or "") not in match_ids
    ]
    if manual_matches:
        matches.extend(manual_matches)
        payload["matches"] = matches
        payload["totalMatches"] = len(matches)
    description_errors = enrich_descriptions(
        matches,
        api_key=settings.sam_gov_api_key,
        data_dir=settings.data_dir,
        enabled=settings.enable_descriptions,
        limit=settings.description_fetch_limit,
    )
    if description_errors:
        payload.setdefault("errors", []).extend(f"description: {error}" for error in description_errors)
    notice_ids = [str(match.get("noticeId")) for match in matches if match.get("noticeId")]
    status_map = store.status_map(notice_ids)
    proposal_map = store.proposal_map(notice_ids)
    proposal_documents_map = store.proposal_document_map(notice_ids)
    proposal_artifact_map = store.proposal_artifact_map(notice_ids)
    for match in matches:
        notice_id = str(match.get("noticeId") or "")
        workflow = status_map.get(notice_id) or store.get_status(notice_id)
        match["workflowStatus"] = workflow["status"]
        match["workflowNotes"] = workflow["notes"]
        match["workflowPriority"] = workflow.get("priority", "normal")
        match["workflowOwner"] = workflow.get("owner", "")
        match["workflowNextAction"] = workflow.get("nextAction", "")
        match["workflowFollowUpAt"] = workflow.get("followUpAt", "")
        match["workflowDecisionReason"] = workflow.get("decisionReason", "")
        match["workflowNoBidReason"] = workflow.get("noBidReason", "")
        match["workflowNoBidDetail"] = workflow.get("noBidDetail", "")
        match["workflowDocuments"] = workflow.get("documents", [])
        match["workflowEvents"] = workflow.get("events", [])
        match["workflowUpdatedAt"] = workflow["updatedAt"]
        match["proposal"] = proposal_map.get(notice_id) or {}
        match["proposalDocuments"] = proposal_documents_map.get(notice_id, [])
        match["evidenceSnippets"] = store.evidence_snippets(notice_id) if proposal_documents_map.get(notice_id) else []
        match["proposalArtifacts"] = proposal_artifact_map.get(notice_id, [])
    unseen = store.unseen(matches)
    report = build_report_payload(payload, profile, settings, unseen=unseen)
    paths = write_reports(report, settings)
    sent_channels: list[str] = []
    if notify and unseen:
        message = build_digest(unseen, profile, settings, payload.get("postedFrom", ""), payload.get("postedTo", ""), paths["latestUrl"])
        sent_channels = send_notifications(settings, message)
    elif notify and notify_no_matches:
        message = build_no_new_digest(profile, payload.get("postedFrom", ""), payload.get("postedTo", ""), paths["latestUrl"])
        sent_channels = send_notifications(settings, message)
    workflow_channels = send_workflow_notifications(settings, store, report, paths["latestUrl"])
    sent_channels.extend(workflow_channels)
    if mark_seen:
        store.mark_seen(unseen, notified=bool(sent_channels))
    return {
        "ok": True,
        "summary": report["summary"],
        "report": paths,
        "newMatches": len(unseen),
        "notifications": sent_channels,
    }
