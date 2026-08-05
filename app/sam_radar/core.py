from __future__ import annotations

from .config import Settings, load_business_profile
from .digest import build_digest, build_no_new_digest
from .notifications.slack import send_slack_webhook
from .notifications.telegram import send_telegram
from .reports import build_report_payload, write_reports
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
        workflow = status_map.get(notice_id) or {"status": "new", "notes": "", "updatedAt": ""}
        match["workflowStatus"] = workflow["status"]
        match["workflowNotes"] = workflow["notes"]
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
    if mark_seen:
        store.mark_seen(unseen, notified=bool(sent_channels))
    return {
        "ok": True,
        "summary": report["summary"],
        "report": paths,
        "newMatches": len(unseen),
        "notifications": sent_channels,
    }
