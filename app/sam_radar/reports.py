
from __future__ import annotations

import datetime as dt
import html
import json
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .config import BusinessProfile, Settings


def parse_deadline(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC)
    except ValueError:
        return None


def format_local_datetime(value: str, tz_name: str) -> str:
    parsed = parse_deadline(value)
    if not parsed:
        return "n/a"
    return parsed.astimezone(ZoneInfo(tz_name)).strftime("%b %-d, %Y %-I:%M %p %Z")


def format_posted_date(value: str) -> str:
    if not value:
        return "n/a"
    try:
        return dt.date.fromisoformat(str(value)[:10]).strftime("%b %-d, %Y")
    except ValueError:
        return str(value)


def days_until(deadline: str) -> int | None:
    parsed = parse_deadline(deadline)
    if not parsed:
        return None
    return int(((parsed - dt.datetime.now(dt.UTC)).total_seconds()) // 86400)


def urgency(opp: dict) -> tuple[str, str]:
    days = days_until(opp.get("responseDeadline") or "")
    if days is None:
        return "Unknown", "No response deadline listed."
    if days < 0:
        return "Expired", "Deadline has passed."
    if days <= 2:
        return "High", f"Due in {days} day(s). Decide immediately."
    if days <= 7:
        return "Medium", f"Due in {days} day(s). Review this week."
    return "Low", f"Due in {days} day(s). Enough runway for triage."


def capability_area(profile: BusinessProfile, opp: dict) -> str:
    text = " ".join([str(opp.get("title") or ""), " ".join(opp.get("reasons") or []), " ".join(profile.capabilities)]).lower()
    if any(term in text for term in ["security", "cyber", "cmmc", "fedramp", "devsecops"]):
        return "Security"
    if any(term in text for term in ["software", "application", "data", "quality"]):
        return "Software / Data"
    if any(term in text for term in ["infrastructure", "network", "identity", "service desk"]):
        return "Infrastructure"
    if any(term in text for term in ["training", "course", "cissp"]):
        return "Training"
    return "Configured Fit"


def dimension_score(label: str, opp: dict) -> str:
    score = int(opp.get("score") or 0)
    set_aside = str(opp.get("setAsideCode") or opp.get("setAside") or "")
    opp_type = str(opp.get("type") or "")
    urgency_label, _ = urgency(opp)
    if label == "Capability":
        return "High" if score >= 10 else "Medium" if score >= 7 else "Low"
    if label == "Set-aside":
        if any(token in set_aside for token in ["SDVOSB", "VOSB", "VSA", "VSS"]):
            return "High"
        if "SBA" in set_aside or "Small Business" in set_aside:
            return "Medium"
        return "Open"
    if label == "Timing":
        return {"High": "Tight", "Medium": "Soon", "Low": "Good", "Unknown": "Unknown", "Expired": "Expired"}[urgency_label]
    if label == "Competition":
        return "Lower now" if "Sources Sought" in opp_type else "Higher" if "Special Notice" in opp_type else "Open"
    if label == "Effort":
        return "High" if urgency_label == "High" else "Low-Medium" if "Sources Sought" in opp_type else "Medium"
    return "n/a"


def fit_reason(profile: BusinessProfile, opp: dict) -> str:
    reasons = "; ".join(opp.get("reasons") or [])
    set_aside = opp.get("setAsideCode") or opp.get("setAside") or "no set-aside signal"
    return f"{capability_area(profile, opp)} fit based on {reasons or 'SAM.gov metadata'}. Set-aside signal: {set_aside}."


def next_action(opp: dict) -> str:
    rec = opp.get("recommendation") or "Review"
    opp_type = str(opp.get("type") or "")
    urgency_label, _ = urgency(opp)
    if urgency_label == "High" and rec == "Pursue":
        return "Pull attachments today and make a same-day bid/no-bid call."
    if "Sources Sought" in opp_type:
        return "Prepare a concise capability response and use it to start agency awareness."
    if rec == "Pursue":
        return "Review PWS/attachments, confirm past performance fit, then decide direct versus teaming."
    if rec == "Monitor":
        return "Save for review, watch amendments, and consider teaming if scope is broader than current capacity."
    return "Quickly screen attachments; no-bid unless requirements reveal a sharper angle."


def sam_url(opp: dict) -> str:
    url = opp.get("uiLink") or opp.get("url") or ""
    if not url and opp.get("noticeId"):
        return f"https://sam.gov/opp/{opp['noticeId']}/view"
    return url


def event_display(value: str, tz_name: str) -> str:
    return format_local_datetime(value, tz_name) if value else "n/a"


def top_counts(matches: list[dict], field: str, limit: int = 5) -> list[dict]:
    counts: dict[str, int] = {}
    for item in matches:
        value = str(item.get(field) or "n/a").strip() or "n/a"
        counts[value] = counts.get(value, 0) + 1
    return [{"label": label, "count": count} for label, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def needs_followup(opp: dict) -> list[str]:
    reasons: list[str] = []
    status = opp.get("workflowStatus") or "new"
    days = days_until(opp.get("responseDeadline") or "")
    if days is not None and 0 <= days <= 2:
        reasons.append("Due within 48 hours")
    if days is not None and 0 <= days <= 7:
        reasons.append("Due this week")
    if status == "pursue" and not (opp.get("workflowNextAction") or ""):
        reasons.append("Pursue without next action")
    if status == "reviewing" and not (opp.get("workflowUpdatedAt") or ""):
        reasons.append("Reviewing needs owner/date")
    follow_up = str(opp.get("workflowFollowUpAt") or "")[:10]
    today = dt.datetime.now(dt.UTC).date().isoformat()
    if follow_up and follow_up <= today:
        reasons.append("Follow-up due")
    docs = opp.get("workflowDocuments") or []
    if docs and not all(doc.get("reviewed") for doc in docs):
        reasons.append("Document review pending")
    if not docs and (opp.get("descriptionUrl") or opp.get("url")):
        reasons.append("Attachment review not tracked")
    return reasons


def build_report_payload(payload: dict, profile: BusinessProfile, settings: Settings, unseen: list[dict] | None = None) -> dict:
    unseen_ids = {opp.get("noticeId") for opp in (unseen or [])}
    enriched = []
    for rank, opp in enumerate(payload.get("matches") or [], 1):
        urgency_label, urgency_text = urgency(opp)
        item = dict(opp)
        events = [
            {**event, "createdDisplay": event_display(event.get("createdAt") or "", settings.timezone)}
            for event in (opp.get("workflowEvents") or [])
        ]
        item.update({
            "rank": rank,
            "url": sam_url(opp),
            "isNew": opp.get("noticeId") in unseen_ids,
            "postedDisplay": format_posted_date(opp.get("postedDate") or ""),
            "dueDisplay": format_local_datetime(opp.get("responseDeadline") or "", settings.timezone),
            "capabilityArea": capability_area(profile, opp),
            "fitReason": fit_reason(profile, opp),
            "nextAction": next_action(opp),
            "urgency": urgency_label,
            "urgencyText": urgency_text,
            "workflowStatus": opp.get("workflowStatus") or "new",
            "workflowNotes": opp.get("workflowNotes") or "",
            "workflowPriority": opp.get("workflowPriority") or "normal",
            "workflowOwner": opp.get("workflowOwner") or "",
            "workflowNextAction": opp.get("workflowNextAction") or "",
            "workflowFollowUpAt": opp.get("workflowFollowUpAt") or "",
            "workflowDecisionReason": opp.get("workflowDecisionReason") or "",
            "workflowNoBidReason": opp.get("workflowNoBidReason") or "",
            "workflowNoBidDetail": opp.get("workflowNoBidDetail") or "",
            "workflowDocuments": opp.get("workflowDocuments") or [],
            "workflowEvents": events,
            "workflowUpdatedAt": opp.get("workflowUpdatedAt") or "",
            "workflowUpdatedDisplay": format_local_datetime(opp.get("workflowUpdatedAt") or "", settings.timezone)
            if opp.get("workflowUpdatedAt")
            else "Not saved",
            "dimensions": {
                "Capability": dimension_score("Capability", opp),
                "Set-aside": dimension_score("Set-aside", opp),
                "Timing": dimension_score("Timing", opp),
                "Competition": dimension_score("Competition", opp),
                "Effort": dimension_score("Effort", opp),
            },
        })
        item["followUpReasons"] = needs_followup(item)
        enriched.append(item)
    due_sorted = sorted(
        [m for m in enriched if parse_deadline(m.get("responseDeadline") or "")],
        key=lambda m: parse_deadline(m.get("responseDeadline") or "") or dt.datetime.max.replace(tzinfo=dt.UTC),
    )
    generated = dt.datetime.now(ZoneInfo(settings.timezone))
    active = [m for m in enriched if m.get("workflowStatus") not in {"archived", "no-bid"}]
    scores = [int(m.get("score") or 0) for m in enriched]
    no_bid = [m for m in enriched if m.get("workflowStatus") == "no-bid"]
    status_counts = {status: sum(1 for m in enriched if m.get("workflowStatus") == status) for status in ["new", "reviewing", "pursue", "teaming", "submitted", "monitor", "no-bid", "archived"]}
    metrics = {
        "activeCount": len(active),
        "newThisWeek": len(unseen or []),
        "dueSoonCount": sum(1 for m in enriched if (days_until(m.get("responseDeadline") or "") is not None and 0 <= (days_until(m.get("responseDeadline") or "") or 0) <= 7)),
        "followUpCount": sum(1 for m in enriched if m.get("followUpReasons")),
        "pursueCount": status_counts.get("pursue", 0),
        "monitorCount": status_counts.get("monitor", 0),
        "submittedCount": status_counts.get("submitted", 0),
        "noBidRate": round((len(no_bid) / len(enriched)) * 100, 1) if enriched else 0,
        "averageScore": round(sum(scores) / len(scores), 1) if scores else 0,
        "statusCounts": status_counts,
        "topAgencies": top_counts(enriched, "organization"),
        "topNaics": top_counts(enriched, "naicsCode"),
        "topPsc": top_counts(enriched, "classificationCode"),
        "noBidReasons": top_counts(no_bid, "workflowNoBidReason") if no_bid else [],
    }
    summary = {
        "generatedAt": generated.isoformat(timespec="seconds"),
        "generatedAtDisplay": generated.strftime("%b %-d, %Y %-I:%M %p %Z"),
        "businessName": profile.display_name,
        "postedFrom": payload.get("postedFrom", ""),
        "postedTo": payload.get("postedTo", ""),
        "totalMatches": len(enriched),
        "newMatches": len(unseen or []),
        "pursueCount": sum(1 for m in enriched if m.get("recommendation") == "Pursue"),
        "monitorCount": sum(1 for m in enriched if m.get("recommendation") == "Monitor"),
        "reviewCount": sum(1 for m in enriched if m.get("recommendation") == "Review"),
        "bestMatch": enriched[0].get("title") if enriched else "No matches",
        "fastestDeadline": due_sorted[0].get("title") if due_sorted else "No dated deadlines",
        "fastestDeadlineDisplay": due_sorted[0].get("dueDisplay") if due_sorted else "",
        "sourcesSoughtCount": sum(1 for m in enriched if m.get("type") == "Sources Sought"),
    }
    return {"summary": summary, "metrics": metrics, "matches": enriched, "errors": payload.get("errors") or []}


def e(value) -> str:
    return html.escape(str(value if value is not None else ""))


def icon(name: str) -> str:
    return f'<svg class="icon" aria-hidden="true"><use href="#icon-{e(name)}"></use></svg>'


def icon_label(name: str, label: str) -> str:
    return f'{icon(name)}<span>{e(label)}</span>'


def icon_sprite() -> str:
    paths = {
        "archive": '<path d="M3 7h18"/><path d="M5 7l1 13h12l1-13"/><path d="M9 11h6"/><path d="M8 3h8l1 4H7l1-4z"/>',
        "archive-restore": '<path d="M3 7h18"/><path d="M5 7l1 13h12l1-13"/><path d="M9 14h6"/><path d="M12 11v6"/><path d="M8 3h8l1 4H7l1-4z"/>',
        "bell": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/>',
        "book": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z"/>',
        "check": '<path d="M20 6L9 17l-5-5"/>',
        "close": '<path d="M18 6L6 18"/><path d="M6 6l12 12"/>',
        "columns": '<path d="M3 4h18v16H3z"/><path d="M9 4v16"/><path d="M15 4v16"/>',
        "external": '<path d="M15 3h6v6"/><path d="M10 14L21 3"/><path d="M21 14v6H3V3h6"/>',
        "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h6"/>',
        "kanban": '<path d="M4 4h16v16H4z"/><path d="M9 4v16"/><path d="M15 4v16"/><path d="M6 8h1"/><path d="M11 12h1"/><path d="M17 9h1"/>',
        "key": '<path d="M21 2l-2 2"/><path d="M15 8l4-4"/><circle cx="8" cy="16" r="5"/><path d="M10.5 13.5L21 3"/>',
        "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
        "plus": '<circle cx="12" cy="12" r="9"/><path d="M12 8v8"/><path d="M8 12h8"/>',
        "refresh": '<path d="M21 12a9 9 0 0 1-15.5 6.2"/><path d="M3 12A9 9 0 0 1 18.5 5.8"/><path d="M18 2v4h-4"/><path d="M6 22v-4h4"/>',
        "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
        "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
        "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M4.93 4.93l1.41 1.41"/><path d="M17.66 17.66l1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="M4.93 19.07l1.41-1.41"/><path d="M17.66 6.34l1.41-1.41"/>',
    }
    symbols = "".join(
        f'<symbol id="icon-{name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{path}</symbol>'
        for name, path in paths.items()
    )
    return f'<svg class="icon-sprite" aria-hidden="true">{symbols}</svg>'


def row_html(opp: dict) -> str:
    dims = "".join(f"<span><b>{e(k)}</b>{e(v)}</span>" for k, v in opp.get("dimensions", {}).items())
    reasons = "".join(f"<li>{e(reason)}</li>" for reason in (opp.get("reasons") or [])) or "<li>Matched configured search profile.</li>"
    follow = "".join(f'<span class="pill follow">{e(reason)}</span>' for reason in (opp.get("followUpReasons") or []))
    new_badge = '<span class="pill new">New</span>' if opp.get("isNew") else ""
    notice_id = e(opp.get("noticeId") or "")
    status = e(opp.get("workflowStatus") or "new")
    search_text = " ".join([opp.get("title") or "", opp.get("organization") or "", opp.get("capabilityArea") or "", opp.get("workflowOwner") or ""])
    return f"""
    <article class="opp" id="opp-{notice_id}" data-id="{notice_id}" data-rec="{e(opp.get('recommendation'))}" data-status="{status}" data-priority="{e(opp.get('workflowPriority'))}" data-new="{str(bool(opp.get('isNew'))).lower()}" data-type="{e(opp.get('type'))}" data-followup="{str(bool(opp.get('followUpReasons'))).lower()}" data-search="{e(search_text)}">
      <div class="opp-head"><div><div class="meta"><span class="rank">#{e(opp.get('rank'))}</span><span class="pill rec-{e(str(opp.get('recommendation') or '').lower())}">{e(opp.get('recommendation'))}</span>{new_badge}<span class="pill status-pill status-{status}">{status}</span><span class="pill urgency-{e(str(opp.get('urgency') or '').lower())}">{e(opp.get('urgency'))}</span><span class="pill priority-{e(opp.get('workflowPriority'))}">{e(opp.get('workflowPriority'))}</span></div><h2>{e(opp.get('title'))}</h2></div><div class="head-actions"><button class="card-toggle" data-id="{notice_id}" type="button" aria-expanded="true">{icon_label("columns", "Collapse")}</button><button class="archive-toggle" data-id="{notice_id}" type="button">{icon_label("archive", "Archive")}</button><button class="open-detail" data-id="{notice_id}" type="button">{icon_label("file", "Details")}</button><a class="sam" href="{e(opp.get('url'))}" target="_blank" rel="noopener">{icon_label("external", "Open SAM.gov")}</a></div></div>
      <div class="facts"><span><b>Agency</b>{e(opp.get('organization') or 'n/a')}</span><span><b>Posted</b>{e(opp.get('postedDisplay') or 'n/a')}</span><span><b>Due</b>{e(opp.get('dueDisplay') or 'n/a')}</span><span><b>Score</b>{e(opp.get('score') or 'n/a')}</span><span><b>Owner</b>{e(opp.get('workflowOwner') or 'Unassigned')}</span><span><b>Follow-up</b>{e(opp.get('workflowFollowUpAt') or 'n/a')}</span><span><b>NAICS</b>{e(opp.get('naicsCode') or 'n/a')}</span><span><b>PSC</b>{e(opp.get('classificationCode') or 'n/a')}</span></div>
      <div class="follow-row">{follow}</div>
      <div class="analysis"><section><h3>Why It Fits</h3><p>{e(opp.get('fitReason'))}</p><ul>{reasons}</ul></section><section><h3>Recommended Action</h3><p>{e(opp.get('workflowNextAction') or opp.get('nextAction'))}</p><p class="muted">{e(opp.get('urgencyText'))}</p></section></div>
      <div class="workflow"><label>Status <select class="workflow-status"><option value="new">New</option><option value="reviewing">Reviewing</option><option value="pursue">Pursue</option><option value="teaming">Teaming</option><option value="monitor">Monitor</option><option value="no-bid">No-Bid</option><option value="submitted">Submitted</option><option value="archived">Archived</option></select></label><label>Priority <select class="workflow-priority"><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="urgent">Urgent</option></select></label><label>Notes <input class="workflow-notes" value="{e(opp.get('workflowNotes') or '')}" placeholder="Capture notes"></label><button class="save-status" type="button">{icon_label("save", "Save")}</button><span class="workflow-message muted">Updated: {e(opp.get('workflowUpdatedDisplay') or 'Not saved')}</span></div>
      <div class="dims">{dims}</div>
    </article>"""


def build_html_report(report: dict) -> str:
    summary = report["summary"]
    metrics = report.get("metrics") or {}
    rows_html = "\n".join(row_html(opp) for opp in report["matches"]) or '<section class="empty">No high-fit matches found for this window.</section>'
    data_json = json.dumps(report, separators=(",", ":")).replace("<", "\\u003c")
    style = """
:root{color-scheme:light;--ink:#17212b;--muted:#5c6b7a;--line:#d5dde7;--bg:#f4f7fa;--panel:#fff;--panel-2:#f9fbfd;--header:#0b1f31;--header-text:#fff;--header-muted:#c7d6e6;--blue:#1768ac;--green:#167c57;--amber:#a86500;--red:#b42318;--cyan:#1a9da1;--shadow:0 12px 30px rgba(12,32,54,.08);--text-xs:12px;--text-sm:13px;--text-md:15px;--text-lg:16px;--text-xl:18px;--text-title:28px}[data-theme=dark]{color-scheme:dark;--ink:#edf5fb;--muted:#a8b7c6;--line:#33485c;--bg:#07131f;--panel:#102235;--panel-2:#0b1a2a;--header:#06101b;--header-text:#f8fbff;--header-muted:#9fb4c9;--blue:#65b7ff;--green:#67d8a4;--amber:#ffc46b;--red:#ff8f85;--cyan:#58d5d8;--shadow:0 18px 42px rgba(0,0,0,.32)}*{box-sizing:border-box}body{margin:0;font-family:"Aptos","Segoe UI Variable","Segoe UI",system-ui,sans-serif;font-size:var(--text-lg);font-variant-numeric:tabular-nums;color:var(--ink);background:var(--bg)}header{background:var(--header);color:var(--header-text);padding:24px 32px 20px;border-bottom:5px solid var(--cyan)}.wrap{max-width:min(1760px,calc(100vw - 48px));margin:0 auto}h1{margin:0;font-size:var(--text-title);font-weight:820;letter-spacing:0}.subtitle{margin:8px 0 0;color:var(--header-muted)}.summary{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:12px;margin-top:20px}.stat{background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.18);padding:13px 14px;border-radius:8px;min-height:72px}.stat b{display:block;font-size:24px}.stat span{color:var(--header-muted);font-size:var(--text-sm)}main{padding:18px 32px 36px}.panel,.opp,.empty,.lane,.modal-card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;box-shadow:var(--shadow)}.brief{display:grid;grid-template-columns:1.45fr 1fr;gap:14px;margin-bottom:14px}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px;margin-bottom:14px}.metric{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px}.metric b{display:block;font-size:22px}.metric span{color:var(--muted);font-size:var(--text-sm)}.panel h2{margin:0 0 8px;font-size:var(--text-xl)}.panel p{margin:7px 0;color:var(--muted);line-height:1.45}.toolbar{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--bg) 96%,transparent);backdrop-filter:blur(10px);border-bottom:1px solid var(--line);padding:10px 32px;margin-bottom:14px;box-shadow:0 10px 24px rgba(12,32,54,.08)}.commandbar{margin-bottom:0}.tools{display:grid;gap:10px}.tab-row{display:flex;gap:6px;align-items:center;overflow-x:auto;padding-bottom:2px}.command-row{display:grid;grid-template-columns:minmax(320px,1fr) minmax(280px,.9fr) auto;gap:10px;align-items:center}.tool-group{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.view-group{white-space:nowrap}.view-group button{border-color:transparent;background:transparent;border-radius:0;border-bottom:3px solid transparent;padding:10px 12px;color:var(--muted);font-weight:760}.view-group button.active{border-color:var(--cyan);background:color-mix(in srgb,var(--cyan) 10%,transparent);color:var(--ink)}.action-group{white-space:nowrap;justify-content:flex-end}.filter-group{min-width:0}.filter-group button{border-radius:999px;padding:7px 10px;font-size:var(--text-sm)}button,input,select,textarea{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:8px 10px;font:inherit}button{cursor:pointer}button,.sam{display:inline-flex;align-items:center;justify-content:center;gap:7px}.icon{width:16px;height:16px;flex:0 0 16px;stroke:currentColor}.icon-sprite{position:absolute;width:0;height:0;overflow:hidden}.icon-btn .icon{margin:0}button.active{border-color:var(--blue);background:color-mix(in srgb,var(--blue) 14%,var(--panel));color:var(--blue)}.view-group button.active{border-color:var(--cyan);background:color-mix(in srgb,var(--cyan) 10%,transparent);color:var(--ink)}input{min-width:0;flex:1}#q{width:100%;min-width:260px}textarea{width:100%;min-height:96px;line-height:1.4}.icon-btn{width:48px;min-width:48px;text-align:center;padding:9px 0}.theme-toggle{min-width:92px;white-space:nowrap}.refresh,.token-save,.save-detail{background:#0d2235;color:white;border-color:#0d2235;font-weight:700}.status{color:var(--muted);font-size:var(--text-sm);min-width:180px}.view{display:none}.view.active{display:block}.opp{border-left:5px solid var(--blue);margin-bottom:10px;padding:14px}.opp-head{display:flex;justify-content:space-between;gap:16px;align-items:start}.head-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.meta{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:8px}.rank{color:var(--muted);font-weight:700}.pill{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:var(--text-xs);font-weight:720;background:var(--panel-2)}.rec-pursue,.status-pursue,.priority-high,.priority-urgent{color:var(--green);border-color:#60bd91;background:color-mix(in srgb,var(--green) 14%,var(--panel))}.priority-urgent{color:var(--red);border-color:var(--red)}.rec-monitor,.status-monitor{color:var(--amber);border-color:#d89f4d;background:color-mix(in srgb,var(--amber) 16%,var(--panel))}.status-no-bid,.status-archived{color:var(--muted)}.status-teaming,.status-reviewing,.status-submitted{color:var(--blue);border-color:var(--blue);background:color-mix(in srgb,var(--blue) 13%,var(--panel))}.new{color:#0284c7;border-color:#8bd3f7;background:color-mix(in srgb,#38bdf8 16%,var(--panel))}.follow{color:var(--red);border-color:#fda29b;background:color-mix(in srgb,var(--red) 12%,var(--panel))}.urgency-high{color:var(--red);border-color:#fda29b;background:color-mix(in srgb,var(--red) 14%,var(--panel))}.urgency-medium{color:var(--amber);border-color:#d89f4d;background:color-mix(in srgb,var(--amber) 16%,var(--panel))}.urgency-low{color:var(--green);border-color:#60bd91;background:color-mix(in srgb,var(--green) 14%,var(--panel))}.opp h2{margin:0;font-size:var(--text-xl);line-height:1.22;font-weight:780;letter-spacing:0}.sam{white-space:nowrap;text-decoration:none;color:white;background:#1768ac;border-radius:6px;padding:9px 12px;font-weight:700}.facts{display:grid;grid-template-columns:2fr 120px 155px 80px 130px 130px 90px 90px;gap:8px;margin:12px 0}.facts span,.dims span,.mini-card{border:1px solid var(--line);border-radius:6px;padding:8px 9px;color:var(--muted);min-width:0;overflow-wrap:anywhere;background:var(--panel-2)}.facts b,.dims b,.mini-card b{display:block;color:var(--ink);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px;font-family:"Aptos Narrow","Segoe UI",system-ui,sans-serif}.analysis{display:grid;grid-template-columns:1.1fr .9fr;gap:14px}h3{margin:0 0 7px;font-size:var(--text-sm);text-transform:uppercase;letter-spacing:.04em}p{line-height:1.45}ul{margin:8px 0 0 18px;padding:0;color:var(--muted)}.muted{color:var(--muted)}.workflow{display:grid;grid-template-columns:150px 130px minmax(280px,1fr) auto minmax(120px,auto);gap:8px;align-items:end;margin-top:12px;border-top:1px solid var(--line);padding-top:12px}.workflow label,.modal-grid label{display:grid;gap:4px;color:var(--muted);font-size:var(--text-sm)}.workflow select,.workflow input{width:100%;min-width:0}.save-status{background:#0d2235;color:white;border-color:#0d2235;font-weight:700}.dims{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin-top:10px}.board{display:grid;grid-template-columns:repeat(var(--lane-count,8),minmax(210px,1fr));gap:10px;min-width:max(100%,calc(var(--lane-count,8)*220px))}.board-view,.view#board-view{overflow-x:auto;padding-bottom:8px}.lane{min-height:360px;padding:10px;display:grid;grid-template-rows:auto 1fr;align-content:start;gap:10px}.lane-head{position:relative;z-index:1;background:var(--panel);padding:2px 0 8px;border-bottom:1px solid var(--line);font-size:var(--text-sm);text-transform:uppercase;letter-spacing:.04em;margin:0;display:flex;justify-content:space-between;color:var(--muted)}.lane-cards{display:grid;align-content:start;gap:8px;min-width:0}.lane.drag-over{outline:2px solid var(--cyan);outline-offset:2px}.lane-count{font-weight:800;color:var(--ink)}.board-card{display:block;text-decoration:none;color:var(--ink);background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:9px;border-left:4px solid var(--blue)}.board-card:focus,.board-card:hover{border-color:var(--blue)}.board-card b{display:block;font-size:14px;line-height:1.25;margin-bottom:7px;font-weight:780}.board-card span{display:inline-flex;margin-right:6px;margin-bottom:4px;color:var(--muted);font-size:var(--text-xs)}.queue{display:grid;gap:12px}.queue-item{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center}.token-popover,.lane-popover{position:fixed;right:24px;top:76px;z-index:20;display:none;width:min(420px,calc(100vw - 32px));background:var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:0 24px 60px rgba(0,0,0,.28);padding:16px}.token-popover.open,.lane-popover.open{display:block}.lane-popover h2,.token-popover h2{margin:0 0 8px;font-size:16px}.lane-grid{display:grid;grid-template-columns:repeat(2,minmax(180px,1fr));gap:10px 24px;margin:14px 0}.lane-grid label,.lane-empty{display:grid;grid-template-columns:18px 1fr;gap:10px;align-items:center;justify-content:start;color:var(--muted);min-height:28px}.lane-grid input,.lane-empty input{width:18px;height:18px;margin:0;justify-self:start}.lane-actions{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}.back-to-top{position:fixed;right:24px;bottom:24px;z-index:12;opacity:0;pointer-events:none;transform:translateY(8px);transition:opacity .16s ease,transform .16s ease;background:#0d2235;color:white;border-color:#0d2235;font-weight:800;box-shadow:0 18px 36px rgba(0,0,0,.24)}.back-to-top.visible{opacity:1;pointer-events:auto;transform:translateY(0)}.token-row{display:flex;gap:8px}.token-row input{min-width:0}.token-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}.read-only{font-weight:700;color:var(--amber)}.modal{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;z-index:15;align-items:flex-start;justify-content:center;overflow:auto;padding:28px}.modal.open{display:flex}.modal-card{width:min(1320px,calc(100vw - 40px));padding:18px;overflow-wrap:anywhere;overflow-x:hidden}.modal-head{display:flex;justify-content:space-between;gap:12px;align-items:start}.modal-close{width:40px;height:40px;min-width:40px;padding:0;display:inline-grid;place-items:center}.modal-close span{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}.modal-close .icon{width:18px;height:18px}.modal-grid{display:grid;grid-template-columns:160px 150px minmax(190px,1fr) 170px 210px minmax(220px,1fr);gap:10px;margin:12px 0}.detail-sections{display:grid;grid-template-columns:minmax(460px,1.1fr) minmax(420px,.9fr);gap:14px}.detail-sections>section,.documents,.doc-row,.doc-row label{min-width:0}.detail-fit a{overflow-wrap:anywhere;word-break:break-word}.doc-row{display:grid;grid-template-columns:minmax(120px,.8fr) minmax(180px,1.5fr) minmax(92px,auto) auto;gap:8px;align-items:center;margin-bottom:8px}.doc-row input{width:100%;min-width:0}.timeline{display:grid;gap:8px;max-height:320px;overflow:auto}.event{border-left:3px solid var(--cyan);padding:6px 8px;background:var(--panel-2);border-radius:6px}.event b{display:block}.lists{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.follow-row{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}.archive-toggle,.manual-add{background:var(--panel-2);font-weight:700}.archive-toggle[data-archived=true]{color:var(--blue);border-color:var(--blue)}.search-form{display:grid;grid-template-columns:minmax(260px,2fr) repeat(5,minmax(110px,1fr)) auto;gap:10px;align-items:end;margin-bottom:14px}.search-form label{display:grid;gap:4px;color:var(--muted);font-size:var(--text-sm)}.manual-results,.resource-grid{display:grid;gap:10px}.manual-card{background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--cyan);border-radius:8px;padding:14px;box-shadow:var(--shadow)}.manual-card h2{margin:0 0 8px;font-size:18px}.manual-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}.manual-add:disabled{opacity:.55;cursor:not-allowed}.resource-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.resource-card{display:grid;gap:8px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;box-shadow:var(--shadow)}.resource-card a{color:var(--blue);font-weight:800;text-decoration:none}.resource-card p{margin:0;color:var(--muted)}@media(max-width:1500px){.board{grid-template-columns:repeat(var(--lane-count,8),minmax(220px,1fr));min-width:max(100%,calc(var(--lane-count,8)*220px))}.command-row{grid-template-columns:1fr}.tool-group{width:100%}.action-group{justify-content:flex-start}}@media(max-width:1040px){.metric-grid,.lists{grid-template-columns:repeat(2,1fr)}.facts{grid-template-columns:repeat(4,minmax(0,1fr))}}@media(max-width:860px){header,main,.toolbar{padding-left:16px;padding-right:16px}.wrap{max-width:100%}.summary{grid-template-columns:repeat(2,1fr)}.brief,.analysis,.detail-sections,.modal-grid,.metric-grid,.lists,.search-form,.resource-grid{grid-template-columns:1fr}.board{grid-template-columns:repeat(var(--lane-count,8),minmax(220px,1fr));min-width:max(100%,calc(var(--lane-count,8)*220px))}.facts{grid-template-columns:1fr 1fr}.dims{grid-template-columns:1fr}.workflow,.doc-row{grid-template-columns:1fr}.opp-head,.modal-head{flex-direction:column}input,#q{min-width:100%}.tab-row{margin-inline:-16px;padding-inline:16px}.view-group button{flex:0 0 auto}.status{min-width:0}.lane-grid{grid-template-columns:1fr}.back-to-top{right:16px;bottom:16px}.modal{padding:12px}.modal-card{width:100%;padding:14px}}
"""
    style += """
.list-controls{display:flex;gap:8px;align-items:center;justify-content:flex-end;margin-bottom:10px}.card-toggle{background:var(--panel-2);font-weight:700}.opp.collapsed .follow-row,.opp.collapsed .analysis,.opp.collapsed .workflow,.opp.collapsed .dims{display:none}.opp.collapsed .facts span:nth-child(n+4){display:none}.opp.collapsed .card-toggle{border-color:var(--blue);color:var(--blue)}.mobile-menu-row{display:none}.mobile-menu-row button{font-weight:800}.mobile-section-title{display:none}
@media(max-width:860px){header{padding-top:18px;padding-bottom:14px}h1{font-size:22px}.subtitle{font-size:var(--text-sm)}.summary{gap:8px;margin-top:14px}.stat{min-height:58px;padding:9px 10px}.stat b{font-size:20px}.toolbar{padding-top:8px;padding-bottom:8px}.tools{gap:8px}.tab-row{padding-bottom:0}.view-group{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;width:100%;white-space:normal}.view-group button:nth-child(n+4){display:none}.view-group.more-open button{display:inline-flex}.view-group button{border:1px solid var(--line);border-radius:6px;padding:8px 6px;font-size:var(--text-sm);min-width:0}.view-group button.active{border-color:var(--cyan)}.command-row{display:grid;grid-template-columns:1fr;gap:8px}.mobile-menu-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.filter-group,.action-group{display:none;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--panel-2)}.filter-group.open,.action-group.open{display:flex}.filter-group button,.action-group button{flex:1 1 130px}.filter-group::before,.action-group::before{content:attr(data-title);display:block;flex-basis:100%;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:800}.action-group{white-space:normal}.theme-toggle{min-width:0}.refresh{font-weight:800}.list-controls{justify-content:stretch;display:grid;grid-template-columns:1fr 1fr;margin-bottom:8px}.opp{padding:12px;margin-bottom:8px}.opp h2{font-size:17px}.opp-head{gap:10px}.head-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;width:100%}.head-actions .sam{grid-column:1/-1}.facts{gap:8px;margin:10px 0}.facts span{padding:7px 8px}.opp.collapsed .facts{grid-template-columns:1fr 1fr 1fr}.opp.collapsed .facts span:nth-child(n+4){display:none}.opp.collapsed .head-actions .archive-toggle{display:none}.card-toggle{order:3}.open-detail{order:4}.sam{order:5}.token-popover,.lane-popover{top:92px;right:12px;width:calc(100vw - 24px)}}
"""
    script = """
const report=JSON.parse(document.getElementById('report-data').textContent);
const byId=new Map(report.matches.map(o=>[String(o.noticeId),o]));
const buttons=[...document.querySelectorAll('button[data-filter]')];
const viewButtons=[...document.querySelectorAll('button[data-view]')];
const input=document.getElementById('q');
const cards=[...document.querySelectorAll('.opp')];
const refresh=document.getElementById('refresh');
const refreshStatus=document.getElementById('refresh-status');
const tokenButton=document.getElementById('token-button');
const tokenPanel=document.getElementById('token-panel');
const tokenInput=document.getElementById('token-input');
const tokenState=document.getElementById('token-state');
const themeButton=document.getElementById('theme-button');
const modal=document.getElementById('detail-modal');
const manualModal=document.getElementById('manual-detail-modal');
const laneButton=document.getElementById('lane-button');
const lanePanel=document.getElementById('lane-panel');
const laneOptions=document.getElementById('lane-options');
const laneHideEmpty=document.getElementById('lane-hide-empty');
const backTop=document.getElementById('back-to-top');
const manualForm=document.getElementById('manual-form');
const manualResults=document.getElementById('manual-results');
const manualStatus=document.getElementById('manual-status');
const resourceList=document.getElementById('resource-list');
const filterMenuButton=document.getElementById('filter-menu-button');
const toolsMenuButton=document.getElementById('tools-menu-button');
const moreViewButton=document.getElementById('more-view-button');
const expandAllButton=document.getElementById('expand-all');
const collapseAllButton=document.getElementById('collapse-all');
const mobileQuery=matchMedia('(max-width: 860px)');
const statusOrder=['new','reviewing','pursue','teaming','submitted','monitor','no-bid','archived'];
const activeLaneOrder=['reviewing','pursue','teaming','submitted'];
const resources=[
  ['SAM.gov Search','Saved searches, opportunity notices, attachments, and amendments.','https://sam.gov/content/opportunities'],
  ['SBA Certifications','Small business, SDVOSB, and contracting certification guidance.','https://certifications.sba.gov/'],
  ['APEX Accelerators','Free local GovCon counseling, bid review, and market research support.','https://www.apexaccelerators.us/'],
  ['ColoradoVSS','State of Colorado vendor registration and solicitations.','https://codpa-vss.hostams.com/webapp/PRDVSS2X1/AltSelfService'],
  ['CMMC Program','Cybersecurity maturity requirements for defense work.','https://dodcio.defense.gov/CMMC/'],
  ['GSA MAS Roadmap','Federal schedule path, readiness checks, and offer guidance.','https://www.gsa.gov/buy-through-us/purchasing-programs/multiple-award-schedule']
];
let filter='all';
function escapeHtml(value){return String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));}
function icon(name){return `<svg class="icon" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;}
function iconLabel(name,label){return `${icon(name)}<span>${escapeHtml(label)}</span>`;}
function setCardCollapsed(card,collapsed){card.classList.toggle('collapsed',collapsed);const btn=card.querySelector('.card-toggle');if(btn){btn.setAttribute('aria-expanded',String(!collapsed));btn.innerHTML=collapsed?iconLabel('plus','Expand'):iconLabel('columns','Collapse');}}
function setAllCardsCollapsed(collapsed){cards.forEach(card=>setCardCollapsed(card,collapsed));}
function initializeListDensity(){setAllCardsCollapsed(mobileQuery.matches);}
function syncMobileMenuButtons(){if(filterMenuButton){const open=document.querySelector('.filter-group')?.classList.contains('open');filterMenuButton.classList.toggle('active',!!open);filterMenuButton.setAttribute('aria-expanded',String(!!open));}if(toolsMenuButton){const open=document.querySelector('.action-group')?.classList.contains('open');toolsMenuButton.classList.toggle('active',!!open);toolsMenuButton.setAttribute('aria-expanded',String(!!open));}}
function closeMobileMenus(){document.querySelector('.filter-group')?.classList.remove('open');document.querySelector('.action-group')?.classList.remove('open');syncMobileMenuButtons();}
function setTheme(theme){document.documentElement.dataset.theme=theme;localStorage.setItem('samRadarTheme',theme);themeButton.innerHTML=theme==='dark'?iconLabel('sun','Light'):iconLabel('moon','Dark');}
setTheme(localStorage.getItem('samRadarTheme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'));
themeButton.addEventListener('click',()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark'));
function token(){return localStorage.getItem('samRadarWriteToken')||'';}
function updateTokenState(){tokenState.textContent=token()?'Write token saved':'Read-only mode';tokenState.classList.toggle('read-only',!token());tokenInput.value=token();}
updateTokenState();
tokenButton.addEventListener('click',event=>{event.stopPropagation();tokenPanel.classList.toggle('open');lanePanel?.classList.remove('open');laneButton?.setAttribute('aria-expanded','false');updateTokenState();});
laneButton?.addEventListener('click',event=>{event.stopPropagation();lanePanel.classList.toggle('open');tokenPanel.classList.remove('open');syncLaneControls();});
lanePanel?.addEventListener('change',event=>{if(event.target.matches('[data-lane]')){const status=event.target.dataset.lane;lanePrefs.visible=event.target.checked?[...new Set([...lanePrefs.visible,status])]:lanePrefs.visible.filter(item=>item!==status);saveLanePrefs();}if(event.target===laneHideEmpty){lanePrefs.hideEmpty=laneHideEmpty.checked;saveLanePrefs();}});
document.getElementById('lane-all')?.addEventListener('click',()=>{lanePrefs.visible=[...statusOrder];saveLanePrefs();});
document.getElementById('lane-active')?.addEventListener('click',()=>{lanePrefs.visible=[...activeLaneOrder];saveLanePrefs();});
document.getElementById('lane-reset')?.addEventListener('click',()=>{lanePrefs={visible:[...statusOrder],hideEmpty:false};saveLanePrefs();});
document.addEventListener('click',event=>{if(!lanePanel?.contains(event.target)&&!laneButton?.contains(event.target)){lanePanel?.classList.remove('open');laneButton?.setAttribute('aria-expanded','false');}if(!tokenPanel.contains(event.target)&&!tokenButton.contains(event.target)){tokenPanel.classList.remove('open');}});
document.getElementById('token-save').addEventListener('click',()=>{const value=tokenInput.value.trim();if(value){localStorage.setItem('samRadarWriteToken',value);}else{localStorage.removeItem('samRadarWriteToken');}tokenPanel.classList.remove('open');updateTokenState();});
document.getElementById('token-clear').addEventListener('click',()=>{localStorage.removeItem('samRadarWriteToken');tokenPanel.classList.remove('open');updateTokenState();});
function labelStatus(status){return {'new':'New','reviewing':'Reviewing','pursue':'Pursue','teaming':'Teaming','monitor':'Monitor','no-bid':'No-Bid','submitted':'Submitted','archived':'Archived'}[status]||status;}
function currentWorkflow(id){const opp=byId.get(String(id));return {status:opp.workflowStatus||'new',notes:opp.workflowNotes||'',priority:opp.workflowPriority||'normal',owner:opp.workflowOwner||'',nextAction:opp.workflowNextAction||'',followUpAt:opp.workflowFollowUpAt||'',decisionReason:opp.workflowDecisionReason||'',noBidReason:opp.workflowNoBidReason||'',noBidDetail:opp.workflowNoBidDetail||'',documents:opp.workflowDocuments||[]};}
function syncCard(id, workflow){const opp=byId.get(String(id));if(!opp)return;Object.assign(opp,{workflowStatus:workflow.status,workflowNotes:workflow.notes,workflowPriority:workflow.priority,workflowOwner:workflow.owner,workflowNextAction:workflow.nextAction,workflowFollowUpAt:workflow.followUpAt,workflowDecisionReason:workflow.decisionReason,workflowNoBidReason:workflow.noBidReason,workflowNoBidDetail:workflow.noBidDetail,workflowDocuments:workflow.documents||[],workflowEvents:workflow.events||[],workflowUpdatedAt:workflow.updatedAt});const card=document.querySelector(`.opp[data-id="${CSS.escape(String(id))}"]`);if(card){card.dataset.status=workflow.status;card.dataset.priority=workflow.priority;card.querySelector('.workflow-status').value=workflow.status;card.querySelector('.workflow-priority').value=workflow.priority;card.querySelector('.workflow-notes').value=workflow.notes||'';const pill=card.querySelector('.status-pill');if(pill){pill.textContent=labelStatus(workflow.status);pill.className='pill status-pill status-'+workflow.status;}const pp=card.querySelector('[class*=priority-]');if(pp){pp.textContent=workflow.priority;pp.className='pill priority-'+workflow.priority;}const archive=card.querySelector('.archive-toggle');if(archive){archive.dataset.archived=String(workflow.status==='archived');archive.innerHTML=workflow.status==='archived'?iconLabel('archive-restore','Unarchive'):iconLabel('archive','Archive');}}apply();}
async function saveWorkflow(id, body, msg){const writeToken=token();if(!writeToken){tokenPanel.classList.add('open');if(msg)msg.textContent='Unlock editing with APP_WRITE_TOKEN';throw new Error('Token required');}const res=await fetch('/api/status/'+encodeURIComponent(id),{method:'POST',headers:{'Content-Type':'application/json','X-SAM-RADAR-TOKEN':writeToken},body:JSON.stringify(body)});const data=await res.json();if(!res.ok||!data.ok)throw new Error(data.error||'Save failed');syncCard(id,data.workflow);return data.workflow;}
cards.forEach(card=>{card.querySelector('.workflow-status').value=card.dataset.status||'new';card.querySelector('.workflow-priority').value=card.dataset.priority||'normal';const archive=card.querySelector('.archive-toggle');if(archive){archive.dataset.archived=String(card.dataset.status==='archived');archive.innerHTML=card.dataset.status==='archived'?iconLabel('archive-restore','Unarchive'):iconLabel('archive','Archive');}});
initializeListDensity();
document.querySelectorAll('.card-toggle').forEach(btn=>btn.addEventListener('click',()=>{const card=btn.closest('.opp');setCardCollapsed(card,!card.classList.contains('collapsed'));}));
expandAllButton?.addEventListener('click',()=>setAllCardsCollapsed(false));
collapseAllButton?.addEventListener('click',()=>setAllCardsCollapsed(true));
filterMenuButton?.addEventListener('click',()=>{document.querySelector('.filter-group')?.classList.toggle('open');document.querySelector('.action-group')?.classList.remove('open');syncMobileMenuButtons();});
toolsMenuButton?.addEventListener('click',()=>{document.querySelector('.action-group')?.classList.toggle('open');document.querySelector('.filter-group')?.classList.remove('open');syncMobileMenuButtons();});
moreViewButton?.addEventListener('click',()=>document.querySelector('.view-group')?.classList.toggle('more-open'));
mobileQuery.addEventListener?.('change',event=>{if(event.matches)setAllCardsCollapsed(true);closeMobileMenus();});

function visibleCards(){return cards.filter(card=>card.style.display!=='none');}
function readJson(key,fallback){try{const value=localStorage.getItem(key);return value?JSON.parse(value):fallback;}catch(err){return fallback;}}
function writeJson(key,value){try{localStorage.setItem(key,JSON.stringify(value));}catch(err){}}
let lanePrefs=readJson('samRadarLanePrefs',{visible:statusOrder,hideEmpty:false});
function normalizeLanePrefs(){const visible=(lanePrefs.visible||statusOrder).filter(status=>statusOrder.includes(status));lanePrefs={visible:visible.length?visible:[...statusOrder],hideEmpty:!!lanePrefs.hideEmpty};}
function saveLanePrefs(){normalizeLanePrefs();writeJson('samRadarLanePrefs',lanePrefs);syncLaneControls();buildBoard();}
function syncLaneControls(){normalizeLanePrefs();if(laneOptions){laneOptions.innerHTML=statusOrder.map(status=>`<label><input type="checkbox" data-lane="${status}" ${lanePrefs.visible.includes(status)?'checked':''}> ${labelStatus(status)}</label>`).join('');}if(laneHideEmpty)laneHideEmpty.checked=lanePrefs.hideEmpty;if(laneButton){laneButton.innerHTML=iconLabel('columns',`Lanes ${lanePrefs.visible.length}/${statusOrder.length}`);laneButton.setAttribute('aria-expanded',lanePanel?.classList.contains('open')?'true':'false');}}
function apply(){const q=(input.value||'').toLowerCase().trim();cards.forEach(card=>{const rec=card.dataset.rec||'';const type=card.dataset.type||'';const status=card.dataset.status||'';const archived=status==='archived';const isNew=card.dataset.new==='true';const text=(card.dataset.search||'').toLowerCase();let ok=filter==='archived'?archived:(!archived&&(filter==='all'||rec===filter||type===filter||status===filter||card.dataset.priority===filter||(filter==='new'&&isNew)||(filter==='follow-up'&&card.dataset.followup==='true')));if(q)ok=ok&&text.includes(q);card.style.display=ok?'block':'none';});buildBoard();buildQueue();}
function boardCard(card){const id=card.dataset.id;const opp=byId.get(String(id))||{};return `<a class="board-card" draggable="true" href="#" data-id="${escapeHtml(id)}"><b>${escapeHtml(opp.title||'Untitled')}</b><span>Score ${escapeHtml(opp.score||'n/a')}</span><span>${escapeHtml(opp.dueDisplay||'n/a')}</span><span>${escapeHtml(opp.workflowOwner||'Unassigned')}</span></a>`;}
function buildBoard(){const board=document.getElementById('board');if(!board)return;const visible=visibleCards();let statuses=statusOrder.filter(status=>lanePrefs.visible.includes(status));if(lanePrefs.hideEmpty)statuses=statuses.filter(status=>visible.some(card=>card.dataset.status===status));board.style.setProperty('--lane-count',String(Math.max(statuses.length,1)));board.innerHTML=statuses.length?statuses.map(status=>{const items=visible.filter(card=>card.dataset.status===status);return `<section class="lane" data-status="${status}"><h2 class="lane-head">${labelStatus(status)} <span class="lane-count">${items.length}</span></h2><div class="lane-cards">${items.map(boardCard).join('')||'<p class="muted">No cards</p>'}</div></section>`;}).join(''):'<section class="empty">No visible lanes. Use Lanes to show a status.</section>';board.querySelectorAll('.board-card').forEach(el=>{el.addEventListener('click',event=>{event.preventDefault();openDetail(el.dataset.id);});el.addEventListener('dragstart',event=>{event.dataTransfer.setData('text/plain',el.dataset.id);});});board.querySelectorAll('.lane').forEach(lane=>{lane.addEventListener('dragover',event=>{event.preventDefault();lane.classList.add('drag-over');});lane.addEventListener('dragleave',()=>lane.classList.remove('drag-over'));lane.addEventListener('drop',async event=>{event.preventDefault();lane.classList.remove('drag-over');const id=event.dataTransfer.getData('text/plain');const wf=currentWorkflow(id);wf.status=lane.dataset.status;try{await saveWorkflow(id,wf,refreshStatus);refreshStatus.textContent='Status saved';}catch(err){refreshStatus.textContent=err.message||'Save failed';}});});}
function buildQueue(){const q=document.getElementById('queue');if(!q)return;const items=report.matches.filter(o=>(o.followUpReasons||[]).length&&o.workflowStatus!=='archived');q.innerHTML=items.map(o=>`<article class="panel queue-item"><div><h3>${escapeHtml(o.title)}</h3><p class="muted">${escapeHtml((o.followUpReasons||[]).join(', '))}</p><p>Due: ${escapeHtml(o.dueDisplay||'n/a')} | Owner: ${escapeHtml(o.workflowOwner||'Unassigned')}</p></div><button class="open-detail" data-id="${escapeHtml(o.noticeId)}" type="button">${iconLabel('file','Details')}</button></article>`).join('')||'<section class="empty">No follow-up items for this report.</section>';q.querySelectorAll('.open-detail').forEach(b=>b.addEventListener('click',()=>openDetail(b.dataset.id)));}
function docRows(docs){return (docs&&docs.length?docs:[{label:'SAM.gov listing',url:'',reviewed:false}]).map(doc=>`<div class="doc-row"><input class="doc-label" placeholder="Label" value="${escapeHtml(doc.label||'')}"><input class="doc-url" placeholder="URL" value="${escapeHtml(doc.url||'')}"><label><input class="doc-reviewed" type="checkbox" ${doc.reviewed?'checked':''}> Reviewed</label><button class="remove-doc" type="button">${iconLabel('close','Remove')}</button></div>`).join('');}
function collectDocs(){return [...modal.querySelectorAll('.doc-row')].map(row=>({label:row.querySelector('.doc-label').value,url:row.querySelector('.doc-url').value,reviewed:row.querySelector('.doc-reviewed').checked})).filter(d=>d.url.trim());}
function manualCard(opp){const id=String(opp.noticeId||'');const already=opp.alreadyTracked||byId.has(id);const label=already?'Already tracked':'Track';const disabled=already?'disabled':'';return `<article class="manual-card" data-manual-id="${escapeHtml(id)}"><div class="meta"><span class="pill">Score ${escapeHtml(opp.score||'n/a')}</span><span class="pill">${escapeHtml(opp.type||'n/a')}</span><span class="pill ${already?'status-archived':'status-reviewing'}">${already?'Already tracked':'Not in report'}</span></div><h2>${escapeHtml(opp.title||'Untitled')}</h2><div class="facts"><span><b>Agency</b>${escapeHtml(opp.organization||'n/a')}</span><span><b>Posted</b>${escapeHtml(opp.postedDate||'n/a')}</span><span><b>Due</b>${escapeHtml(opp.responseDeadline||'n/a')}</span><span><b>NAICS</b>${escapeHtml(opp.naicsCode||'n/a')}</span><span><b>PSC</b>${escapeHtml(opp.classificationCode||'n/a')}</span></div><p class="muted">${escapeHtml((opp.reasons||[]).join('; ')||'Matched manual search criteria.')}</p><div class="manual-actions"><button class="manual-detail" data-id="${escapeHtml(id)}" type="button">${iconLabel('file','Details')}</button><button class="manual-add" data-id="${escapeHtml(id)}" type="button" ${disabled}>${iconLabel(already?'check':'plus',label)}</button><a class="sam" href="${escapeHtml(opp.url||'#')}" target="_blank" rel="noopener">${iconLabel('external','Open SAM.gov')}</a><span class="manual-message muted"></span></div></article>`;}
async function trackManualOpportunity(opp,msg,btn){const id=String(opp.noticeId||'');if(opp.alreadyTracked||byId.has(id)){if(msg)msg.textContent='Already tracked';if(btn){btn.disabled=true;btn.innerHTML=iconLabel('check','Already tracked');}return false;}const writeToken=token();if(!writeToken){tokenPanel.classList.add('open');if(msg)msg.textContent='Unlock editing with APP_WRITE_TOKEN';return false;}if(btn)btn.disabled=true;if(msg)msg.textContent='Tracking...';try{const res=await fetch('/api/manual-add',{method:'POST',headers:{'Content-Type':'application/json','X-SAM-RADAR-TOKEN':writeToken},body:JSON.stringify(opp)});const data=await res.json();if(res.status===409||data.duplicate){opp.alreadyTracked=true;if(msg)msg.textContent='Already tracked';if(btn)btn.innerHTML=iconLabel('check','Already tracked');return false;}if(!res.ok||!data.ok)throw new Error(data.error||'Track failed');opp.alreadyTracked=true;if(msg)msg.textContent='Tracked in workflow';if(btn)btn.innerHTML=iconLabel('check','Tracked');return true;}catch(err){if(btn)btn.disabled=false;if(msg)msg.textContent=err.message||'Track failed';return false;}}
function openManualDetail(id){const opp=(manualMatches||[]).find(item=>String(item.noticeId)===String(id));if(!opp||!manualModal)return;const already=opp.alreadyTracked||byId.has(String(id));manualModal.dataset.id=id;manualModal.querySelector('.manual-detail-title').textContent=opp.title||'Untitled';manualModal.querySelector('.manual-detail-subtitle').textContent=`${opp.organization||'n/a'} | Score ${opp.score||'n/a'} | ${already?'Already tracked':'Not in report'}`;manualModal.querySelector('.manual-detail-facts').innerHTML=`<span><b>Type</b>${escapeHtml(opp.type||'n/a')}</span><span><b>Posted</b>${escapeHtml(opp.postedDate||'n/a')}</span><span><b>Due</b>${escapeHtml(opp.responseDeadline||'n/a')}</span><span><b>NAICS</b>${escapeHtml(opp.naicsCode||'n/a')}</span><span><b>PSC</b>${escapeHtml(opp.classificationCode||'n/a')}</span><span><b>Set-aside</b>${escapeHtml(opp.setAsideCode||opp.setAside||'n/a')}</span>`;manualModal.querySelector('.manual-detail-reasons').innerHTML=(opp.reasons||[]).map(reason=>`<li>${escapeHtml(reason)}</li>`).join('')||'<li>Matched manual search criteria.</li>';manualModal.querySelector('.manual-detail-link').href=opp.url||'#';const add=manualModal.querySelector('.manual-detail-track');add.disabled=already;add.innerHTML=already?iconLabel('check','Already tracked'):iconLabel('plus','Track Opportunity');manualModal.querySelector('.manual-detail-message').textContent='';manualModal.classList.add('open');}
function renderManual(matches){if(!manualResults)return;manualResults.innerHTML=(matches||[]).map(manualCard).join('')||'<section class="empty">No manual search matches found.</section>';manualResults.querySelectorAll('.manual-detail').forEach(btn=>btn.addEventListener('click',()=>openManualDetail(btn.dataset.id)));manualResults.querySelectorAll('.manual-add').forEach(btn=>btn.addEventListener('click',async()=>{const id=btn.dataset.id;const msg=btn.closest('.manual-card').querySelector('.manual-message');const opp=(manualMatches||[]).find(item=>String(item.noticeId)===String(id));if(opp)await trackManualOpportunity(opp,msg,btn);}));}
let manualMatches=[];
manualForm?.addEventListener('submit',async event=>{event.preventDefault();const body=Object.fromEntries(new FormData(manualForm).entries());manualStatus.textContent='Searching SAM.gov without changing this report...';manualResults.innerHTML='';try{const res=await fetch('/api/manual-search',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify(body)});const data=await res.json();if(!res.ok||!data.ok)throw new Error(data.error||'Manual search failed');manualMatches=data.matches||[];manualStatus.textContent=`${data.count||0} results. Report files unchanged.`;renderManual(manualMatches);}catch(err){manualStatus.textContent=err.message||'Manual search failed';}});
function renderResources(){if(!resourceList)return;resourceList.innerHTML=resources.map(item=>`<article class="resource-card"><a href="${escapeHtml(item[2])}" target="_blank" rel="noopener">${escapeHtml(item[0])}</a><p>${escapeHtml(item[1])}</p></article>`).join('');}
function syncBackTop(){if(backTop)backTop.classList.toggle('visible',window.scrollY>500&&!modal.classList.contains('open'));}
backTop?.addEventListener('click',()=>window.scrollTo({top:0,behavior:'smooth'}));
window.addEventListener('scroll',syncBackTop,{passive:true});
function openDetail(id){const opp=byId.get(String(id));if(!opp)return;modal.dataset.id=id;modal.querySelector('.detail-title').textContent=opp.title||'Untitled';modal.querySelector('.detail-subtitle').textContent=`${opp.organization||'n/a'} | Score ${opp.score||'n/a'} | Due ${opp.dueDisplay||'n/a'}`;modal.querySelector('[name=status]').value=opp.workflowStatus||'new';modal.querySelector('[name=priority]').value=opp.workflowPriority||'normal';modal.querySelector('[name=owner]').value=opp.workflowOwner||'';modal.querySelector('[name=followUpAt]').value=(opp.workflowFollowUpAt||'').slice(0,10);modal.querySelector('[name=nextAction]').value=opp.workflowNextAction||opp.nextAction||'';modal.querySelector('[name=notes]').value=opp.workflowNotes||'';modal.querySelector('[name=decisionReason]').value=opp.workflowDecisionReason||'';modal.querySelector('[name=noBidReason]').value=opp.workflowNoBidReason||'';modal.querySelector('[name=noBidDetail]').value=opp.workflowNoBidDetail||'';modal.querySelector('.detail-fit').innerHTML=`<p>${escapeHtml(opp.fitReason||'')}</p><p class="muted">SAM.gov: <a href="${escapeHtml(opp.url||'#')}" target="_blank" rel="noopener">${escapeHtml(opp.url||'n/a')}</a></p>`;modal.querySelector('.documents').innerHTML=docRows(opp.workflowDocuments||[]);modal.querySelector('.timeline').innerHTML=(opp.workflowEvents||[]).map(ev=>`<div class="event"><b>${escapeHtml(ev.type)}</b><span>${escapeHtml(ev.createdDisplay||ev.createdAt||'')}</span><p>${escapeHtml(ev.message||'')}</p></div>`).join('')||'<p class="muted">No workflow events yet.</p>';modal.classList.add('open');syncBackTop();}
modal.querySelector('.close-modal').addEventListener('click',()=>{modal.classList.remove('open');syncBackTop();});
manualModal?.querySelector('.close-manual-modal')?.addEventListener('click',()=>manualModal.classList.remove('open'));
manualModal?.querySelector('.manual-detail-track')?.addEventListener('click',async()=>{const id=manualModal.dataset.id;const opp=(manualMatches||[]).find(item=>String(item.noticeId)===String(id));const msg=manualModal.querySelector('.manual-detail-message');const btn=manualModal.querySelector('.manual-detail-track');if(opp)await trackManualOpportunity(opp,msg,btn);renderManual(manualMatches);});
manualModal?.addEventListener('click',event=>{if(event.target===manualModal)manualModal.classList.remove('open');});
modal.querySelector('.add-doc').addEventListener('click',()=>{modal.querySelector('.documents').insertAdjacentHTML('beforeend',docRows([{label:'',url:'',reviewed:false}]));});
modal.addEventListener('click',event=>{if(event.target===modal){modal.classList.remove('open');syncBackTop();}if(event.target.classList.contains('remove-doc'))event.target.closest('.doc-row').remove();});
modal.querySelector('.save-detail').addEventListener('click',async()=>{const id=modal.dataset.id;const msg=modal.querySelector('.detail-message');const body={status:modal.querySelector('[name=status]').value,priority:modal.querySelector('[name=priority]').value,owner:modal.querySelector('[name=owner]').value,followUpAt:modal.querySelector('[name=followUpAt]').value,nextAction:modal.querySelector('[name=nextAction]').value,notes:modal.querySelector('[name=notes]').value,decisionReason:modal.querySelector('[name=decisionReason]').value,noBidReason:modal.querySelector('[name=noBidReason]').value,noBidDetail:modal.querySelector('[name=noBidDetail]').value,documents:collectDocs()};msg.textContent='Saving...';try{const wf=await saveWorkflow(id,body,msg);msg.textContent='Saved';openDetail(id);}catch(err){msg.textContent=err.message||'Save failed';}});
buttons.forEach(btn=>btn.addEventListener('click',()=>{buttons.forEach(b=>b.classList.remove('active'));btn.classList.add('active');filter=btn.dataset.filter;apply();if(mobileQuery.matches)closeMobileMenus();}));
viewButtons.forEach(btn=>btn.addEventListener('click',()=>{viewButtons.forEach(b=>b.classList.remove('active'));btn.classList.add('active');document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));document.getElementById(btn.dataset.view+'-view').classList.add('active');document.querySelector('.view-group')?.classList.remove('more-open');buildBoard();buildQueue();}));
document.querySelectorAll('.archive-toggle').forEach(btn=>btn.addEventListener('click',async()=>{const id=btn.dataset.id;const card=btn.closest('.opp');const msg=card.querySelector('.workflow-message');const wf=currentWorkflow(id);const restoring=wf.status==='archived';wf.status=restoring?'reviewing':'archived';wf.decisionReason=restoring?'Restored from archive':'Dismissed from active queue';msg.textContent=restoring?'Restoring...':'Archiving...';try{await saveWorkflow(id,wf,msg);msg.textContent=restoring?'Restored':'Archived';}catch(err){msg.textContent=err.message||'Save failed';}}));
document.querySelectorAll('.open-detail').forEach(b=>b.addEventListener('click',()=>openDetail(b.dataset.id)));
document.querySelectorAll('.save-status').forEach(btn=>btn.addEventListener('click',async()=>{const card=btn.closest('.opp');const msg=card.querySelector('.workflow-message');const wf=currentWorkflow(card.dataset.id);wf.status=card.querySelector('.workflow-status').value;wf.priority=card.querySelector('.workflow-priority').value;wf.notes=card.querySelector('.workflow-notes').value;msg.textContent='Saving...';try{await saveWorkflow(card.dataset.id,wf,msg);msg.textContent='Saved';}catch(err){msg.textContent=err.message||'Save failed';}}));
if(input)input.addEventListener('input',apply);
if(refresh){refresh.addEventListener('click',async()=>{refresh.disabled=true;refreshStatus.textContent='Refreshing from SAM.gov...';try{const res=await fetch('/api/refresh',{method:'POST',headers:{'Accept':'application/json'}});const contentType=res.headers.get('content-type')||'';const data=contentType.includes('application/json')?await res.json():{ok:false,error:(await res.text()).slice(0,160)||'Refresh returned a non-JSON response'};if(!res.ok||!data.ok)throw new Error(data.error||'Refresh failed');refreshStatus.textContent='Refreshed. Reloading...';window.location.reload();}catch(err){refreshStatus.textContent=err.message||'Refresh failed';refresh.disabled=false;}});}
syncLaneControls();
renderResources();
syncBackTop();
apply();
"""
    top_agencies = "".join(f"<div class='mini-card'><b>{e(item['label'])}</b>{e(item['count'])}</div>" for item in metrics.get("topAgencies", [])) or "<p class='muted'>n/a</p>"
    top_naics = "".join(f"<div class='mini-card'><b>{e(item['label'])}</b>{e(item['count'])}</div>" for item in metrics.get("topNaics", [])) or "<p class='muted'>n/a</p>"
    top_psc = "".join(f"<div class='mini-card'><b>{e(item['label'])}</b>{e(item['count'])}</div>" for item in metrics.get("topPsc", [])) or "<p class='muted'>n/a</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>SAM Radar</title><style>{style}</style></head><body>{icon_sprite()}<header><div class="wrap"><h1>SAM Radar - {e(summary['businessName'])}</h1><p class="subtitle">Generated {e(summary['generatedAtDisplay'])} | Window {e(summary['postedFrom'])} to {e(summary['postedTo'])}</p><div class="summary"><div class="stat"><b>{e(summary['totalMatches'])}</b><span>Total matches</span></div><div class="stat"><b>{e(summary['newMatches'])}</b><span>New unseen</span></div><div class="stat"><b>{e(metrics.get('activeCount',0))}</b><span>Active pipeline</span></div><div class="stat"><b>{e(metrics.get('followUpCount',0))}</b><span>Need follow-up</span></div><div class="stat"><b>{e(metrics.get('averageScore',0))}</b><span>Avg score</span></div></div></div></header><nav class="toolbar commandbar" aria-label="Report controls"><div class="wrap"><div class="tools"><div class="tab-row"><div class="tool-group view-group" role="tablist" aria-label="Report views"><button class="active" data-view="executive" role="tab">{icon_label("file", "Executive")}</button><button data-view="list" role="tab">{icon_label("file", "List")}</button><button data-view="board" role="tab">{icon_label("kanban", "Board")}</button><button data-view="queue" role="tab">{icon_label("bell", "Follow-Up")}</button><button data-view="manual" role="tab">{icon_label("search", "Manual Search")}</button><button data-view="resources" role="tab">{icon_label("book", "Resources")}</button></div></div><div class="mobile-menu-row"><button id="filter-menu-button" type="button" aria-expanded="false">{icon_label("search", "Filters")}</button><button id="tools-menu-button" type="button" aria-expanded="false">{icon_label("columns", "Tools")}</button><button id="more-view-button" type="button">{icon_label("book", "More")}</button></div><div class="command-row"><div class="tool-group filter-group" data-title="Filters"><button class="active" data-filter="all">{icon_label("check", "All")}</button><button data-filter="pursue">{icon_label("plus", "Pursue")}</button><button data-filter="monitor">{icon_label("search", "Monitor")}</button><button data-filter="urgent">{icon_label("bell", "Urgent")}</button><button data-filter="follow-up">{icon_label("bell", "Needs Follow-Up")}</button><button data-filter="new">{icon_label("plus", "New")}</button><button data-filter="Sources Sought">{icon_label("search", "Sources Sought")}</button><button data-filter="archived">{icon_label("archive", "Archived")}</button></div><input id="q" placeholder="Search title, agency, owner, capability"><div class="tool-group action-group" data-title="Tools"><button id="lane-button" class="lane-button" type="button" aria-expanded="false">{icon_label("columns", "Lanes")}</button><button id="theme-button" class="theme-toggle" type="button">{icon_label("moon", "Dark")}</button><button id="token-button" type="button">{icon_label("key", "Unlock")}</button><button id="refresh" class="refresh">{icon_label("refresh", "Refresh")}</button></div><span id="refresh-status" class="status"></span></div></div></div></nav><section id="lane-panel" class="lane-popover"><h2>Board Lanes</h2><div class="lane-actions"><button id="lane-all" type="button">{icon_label("check", "All")}</button><button id="lane-active" type="button">{icon_label("columns", "Active Only")}</button><button id="lane-reset" type="button">{icon_label("refresh", "Reset")}</button></div><label class="lane-empty"><input id="lane-hide-empty" type="checkbox"> Hide empty lanes</label><div id="lane-options" class="lane-grid"></div></section><section id="token-panel" class="token-popover"><h2>Editing Token</h2><p id="token-state"></p><p>Use your local APP_WRITE_TOKEN here. This is separate from your SAM.gov API key.</p><div class="token-row"><input id="token-input" type="password" autocomplete="off" placeholder="APP_WRITE_TOKEN"><button id="token-save" class="token-save" type="button">{icon_label("save", "Save")}</button></div><div class="token-actions"><button id="token-clear" type="button">{icon_label("close", "Clear Browser Token")}</button></div></section><button id="back-to-top" class="back-to-top" type="button" aria-label="Back to top">{icon_label("columns", "Top")}</button><main><div class="wrap"><section id="executive-view" class="view active"><section class="brief"><div class="panel"><h2>Executive Read</h2><p><b>Best match:</b> {e(summary['bestMatch'])}</p><p><b>Fastest deadline:</b> {e(summary['fastestDeadline'])} {e(summary['fastestDeadlineDisplay'])}</p><p>This report ranks opportunities by configured capability fit, set-aside signal, actionability, and deadline pressure.</p></div><div class="panel"><h2>Pipeline Notes</h2><p><b>No-bid rate:</b> {e(metrics.get('noBidRate',0))}%</p><p><b>Due soon:</b> {e(metrics.get('dueSoonCount',0))} | <b>Submitted:</b> {e(metrics.get('submittedCount',0))}</p><p>Use Board and Follow-Up to move work, track document review, and preserve decision history.</p></div></section><section class="metric-grid"><div class="metric"><b>{e(metrics.get('newThisWeek',0))}</b><span>New this week</span></div><div class="metric"><b>{e(metrics.get('pursueCount',0))}</b><span>Pipeline pursue</span></div><div class="metric"><b>{e(metrics.get('monitorCount',0))}</b><span>Pipeline monitor</span></div><div class="metric"><b>{e(metrics.get('submittedCount',0))}</b><span>Submitted</span></div></section><section class="brief"><div class="panel"><h2>Top Agencies</h2><div class="lists">{top_agencies}</div></div><div class="panel"><h2>Top Codes</h2><div class="lists">{top_naics}{top_psc}</div></div></section></section><section id="list-view" class="view"><div class="list-controls"><button id="expand-all" type="button">{icon_label("plus", "Expand All")}</button><button id="collapse-all" type="button">{icon_label("columns", "Collapse All")}</button></div><section id="list">{rows_html}</section></section><section id="board-view" class="view"><div id="board" class="board"></div></section><section id="queue-view" class="view"><div id="queue" class="queue"></div></section><section id="manual-view" class="view"><div class="panel"><h2>Manual SAM Search</h2><form id="manual-form" class="search-form"><label>Keyword<input name="keyword" placeholder="security, DevSecOps, training"></label><label>NAICS<input name="naics" placeholder="541512"></label><label>PSC<input name="psc" placeholder="DJ01"></label><label>Type<select name="ptype"><option value="">Any</option><option value="o">Solicitation</option><option value="p">Pre-solicitation</option><option value="r">Sources Sought</option><option value="s">Special Notice</option></select></label><label>Days<select name="days"><option value="1">1 day</option><option value="3">3 days</option><option value="7" selected>7 days</option><option value="14">14 days</option><option value="30">30 days</option><option value="60">60 days</option></select></label><label>Limit<select name="limit"><option value="10">10</option><option value="25" selected>25</option><option value="50">50</option><option value="100">100</option></select></label><button class="refresh" type="submit">{icon_label("search", "Search SAM")}</button></form><p id="manual-status" class="status">Manual search results are separate from the weekly report.</p></div><div id="manual-results" class="manual-results"></div></section><section id="resources-view" class="view"><div class="panel"><h2>GovCon Resources</h2><p>Quick links for opportunity review, certifications, market research, and contracting readiness.</p></div><div id="resource-list" class="resource-grid"></div></section></div></main><section id="detail-modal" class="modal"><div class="modal-card"><div class="modal-head"><div><h2 class="detail-title"></h2><p class="detail-subtitle muted"></p></div><button class="close-modal modal-close" type="button" aria-label="Close">{icon("close")}<span>Close</span></button></div><div class="modal-grid"><label>Status<select name="status"><option value="new">New</option><option value="reviewing">Reviewing</option><option value="pursue">Pursue</option><option value="teaming">Teaming</option><option value="monitor">Monitor</option><option value="no-bid">No-Bid</option><option value="submitted">Submitted</option><option value="archived">Archived</option></select></label><label>Priority<select name="priority"><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="urgent">Urgent</option></select></label><label>Owner<input name="owner" placeholder="Owner"></label><label>Follow-up<input name="followUpAt" type="date"></label><label>No-bid reason<select name="noBidReason"><option value="">n/a</option><option value="poor-fit">Poor fit</option><option value="deadline-too-short">Deadline too short</option><option value="incumbent-likely">Incumbent likely</option><option value="too-large">Too large</option><option value="certification-gap">Certification gap</option><option value="clearance-gap">Clearance gap</option><option value="geography">Geography</option><option value="staffing-gap">Staffing gap</option><option value="past-performance-gap">Past performance gap</option><option value="not-it-security">Not IT/security</option><option value="duplicate-noise">Duplicate/noise</option><option value="other">Other</option></select></label><label>Decision reason<input name="decisionReason" placeholder="Decision rationale"></label></div><div class="detail-sections"><section><h3>Capture Fields</h3><label>Next action<textarea name="nextAction"></textarea></label><label>Notes<textarea name="notes"></textarea></label><label>No-bid detail<textarea name="noBidDetail"></textarea></label><button class="save-detail" type="button">{icon_label("save", "Save Opportunity")}</button><span class="detail-message status"></span></section><section><h3>Fit</h3><div class="detail-fit"></div><h3>Documents</h3><div class="documents"></div><button class="add-doc" type="button">{icon_label("plus", "Add Document")}</button></section></div><section><h3>Timeline</h3><div class="timeline"></div></section></div></section><section id="manual-detail-modal" class="modal"><div class="modal-card"><div class="modal-head"><div><h2 class="manual-detail-title"></h2><p class="manual-detail-subtitle muted"></p></div><button class="close-manual-modal modal-close" type="button" aria-label="Close">{icon("close")}<span>Close</span></button></div><div class="facts manual-detail-facts"></div><section class="analysis"><section><h3>Manual Search Fit</h3><ul class="manual-detail-reasons"></ul></section><section><h3>Actions</h3><p class="muted">Tracking saves this opportunity to the local workflow store without overwriting the generated report.</p><div class="manual-actions"><button class="manual-detail-track refresh" type="button">{icon_label("plus", "Track Opportunity")}</button><a class="manual-detail-link sam" href="#" target="_blank" rel="noopener">{icon_label("external", "Open SAM.gov")}</a><span class="manual-detail-message status"></span></div></section></section></div></section><script id="report-data" type="application/json">{data_json}</script><script>{script}</script></body></html>"""

def write_reports(report: dict, settings: Settings) -> dict[str, str]:
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    slug = dt.datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d-%H%M%S")
    html_text = build_html_report(report)
    json_text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    html_path = settings.reports_dir / f"{slug}.html"
    json_path = settings.reports_dir / f"{slug}.json"
    latest_html = settings.reports_dir / "latest.html"
    latest_json = settings.reports_dir / "latest.json"
    html_path.write_text(html_text)
    json_path.write_text(json_text)
    latest_html.write_text(html_text)
    latest_json.write_text(json_text)
    return {
        "htmlPath": str(html_path),
        "jsonPath": str(json_path),
        "latestHtmlPath": str(latest_html),
        "latestJsonPath": str(latest_json),
        "htmlUrl": f"{settings.report_url_base}/{quote(html_path.name)}",
        "latestUrl": settings.latest_report_url,
    }
