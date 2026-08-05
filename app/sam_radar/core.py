from __future__ import annotations

from .config import Settings, load_business_profile
from .digest import build_digest, build_no_new_digest
from .notifications.slack import send_slack_webhook
from .notifications.telegram import send_telegram
from .reports import build_report_payload, days_until, write_reports
from .scoring import search_opportunities
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
    payload = search_opportunities(
        settings.sam_gov_api_key,
        profile,
        days=settings.search_days,
        max_results=settings.report_limit,
    )
    matches = payload.get("matches") or []
    status_map = store.status_map([str(match.get("noticeId")) for match in matches if match.get("noticeId")])
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
