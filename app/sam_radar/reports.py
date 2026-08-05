
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
      <div class="opp-head"><div><div class="meta"><span class="rank">#{e(opp.get('rank'))}</span><span class="pill rec-{e(str(opp.get('recommendation') or '').lower())}">{e(opp.get('recommendation'))}</span>{new_badge}<span class="pill status-pill status-{status}">{status}</span><span class="pill urgency-{e(str(opp.get('urgency') or '').lower())}">{e(opp.get('urgency'))}</span><span class="pill priority-{e(opp.get('workflowPriority'))}">{e(opp.get('workflowPriority'))}</span></div><h2>{e(opp.get('title'))}</h2></div><div class="head-actions"><button class="open-detail" data-id="{notice_id}" type="button">Details</button><a class="sam" href="{e(opp.get('url'))}" target="_blank" rel="noopener">Open SAM.gov</a></div></div>
      <div class="facts"><span><b>Agency</b>{e(opp.get('organization') or 'n/a')}</span><span><b>Posted</b>{e(opp.get('postedDisplay') or 'n/a')}</span><span><b>Due</b>{e(opp.get('dueDisplay') or 'n/a')}</span><span><b>Score</b>{e(opp.get('score') or 'n/a')}</span><span><b>Owner</b>{e(opp.get('workflowOwner') or 'Unassigned')}</span><span><b>Follow-up</b>{e(opp.get('workflowFollowUpAt') or 'n/a')}</span><span><b>NAICS</b>{e(opp.get('naicsCode') or 'n/a')}</span><span><b>PSC</b>{e(opp.get('classificationCode') or 'n/a')}</span></div>
      <div class="follow-row">{follow}</div>
      <div class="analysis"><section><h3>Why It Fits</h3><p>{e(opp.get('fitReason'))}</p><ul>{reasons}</ul></section><section><h3>Recommended Action</h3><p>{e(opp.get('workflowNextAction') or opp.get('nextAction'))}</p><p class="muted">{e(opp.get('urgencyText'))}</p></section></div>
      <div class="workflow"><label>Status <select class="workflow-status"><option value="new">New</option><option value="reviewing">Reviewing</option><option value="pursue">Pursue</option><option value="teaming">Teaming</option><option value="monitor">Monitor</option><option value="no-bid">No-Bid</option><option value="submitted">Submitted</option><option value="archived">Archived</option></select></label><label>Priority <select class="workflow-priority"><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="urgent">Urgent</option></select></label><label>Notes <input class="workflow-notes" value="{e(opp.get('workflowNotes') or '')}" placeholder="Capture notes"></label><button class="save-status" type="button">Save</button><span class="workflow-message muted">Updated: {e(opp.get('workflowUpdatedDisplay') or 'Not saved')}</span></div>
      <div class="dims">{dims}</div>
    </article>"""


def build_html_report(report: dict) -> str:
    summary = report["summary"]
    metrics = report.get("metrics") or {}
    rows_html = "\n".join(row_html(opp) for opp in report["matches"]) or '<section class="empty">No high-fit matches found for this window.</section>'
    data_json = json.dumps(report, separators=(",", ":")).replace("<", "\\u003c")
    style = """
:root{color-scheme:light;--ink:#17212b;--muted:#5c6b7a;--line:#d5dde7;--bg:#f4f7fa;--panel:#fff;--panel-2:#f9fbfd;--header:#0b1f31;--header-text:#fff;--header-muted:#c7d6e6;--blue:#1768ac;--green:#167c57;--amber:#a86500;--red:#b42318;--cyan:#1a9da1;--shadow:0 12px 30px rgba(12,32,54,.08)}[data-theme=dark]{color-scheme:dark;--ink:#edf5fb;--muted:#a8b7c6;--line:#33485c;--bg:#07131f;--panel:#102235;--panel-2:#0b1a2a;--header:#06101b;--header-text:#f8fbff;--header-muted:#9fb4c9;--blue:#65b7ff;--green:#67d8a4;--amber:#ffc46b;--red:#ff8f85;--cyan:#58d5d8;--shadow:0 18px 42px rgba(0,0,0,.32)}*{box-sizing:border-box}body{margin:0;font-family:"Aptos","Segoe UI Variable","Segoe UI",system-ui,sans-serif;font-size:15px;color:var(--ink);background:var(--bg)}header{background:var(--header);color:var(--header-text);padding:24px 32px 20px;border-bottom:5px solid var(--cyan)}.wrap{max-width:min(1760px,calc(100vw - 48px));margin:0 auto}h1{margin:0;font-size:28px;font-weight:800;letter-spacing:0}.subtitle{margin:8px 0 0;color:var(--header-muted)}.summary{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:12px;margin-top:20px}.stat{background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.18);padding:13px 14px;border-radius:8px;min-height:72px}.stat b{display:block;font-size:24px}.stat span{color:var(--header-muted);font-size:13px}main{padding:18px 32px 36px}.panel,.opp,.empty,.lane,.modal-card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;box-shadow:var(--shadow)}.brief{display:grid;grid-template-columns:1.45fr 1fr;gap:14px;margin-bottom:14px}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px;margin-bottom:14px}.metric{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px}.metric b{display:block;font-size:22px}.metric span{color:var(--muted);font-size:13px}.panel h2{margin:0 0 8px;font-size:18px}.panel p{margin:7px 0;color:var(--muted);line-height:1.45}.toolbar{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 94%,transparent);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:10px 0;margin-bottom:14px}.tools{display:grid;grid-template-columns:auto auto minmax(280px,1fr) auto;gap:10px;align-items:center}.tool-group{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.view-group,.action-group{white-space:nowrap}.filter-group{min-width:0}button,input,select,textarea{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:8px 10px;font:inherit}button{cursor:pointer}button.active{border-color:var(--blue);background:color-mix(in srgb,var(--blue) 14%,var(--panel));color:var(--blue)}input{min-width:0;flex:1}#q{width:100%;min-width:260px}textarea{width:100%;min-height:96px;line-height:1.4}.icon-btn{width:48px;min-width:48px;text-align:center;padding:9px 0}.refresh,.token-save,.save-detail{background:#0d2235;color:white;border-color:#0d2235;font-weight:700}.status{color:var(--muted);font-size:13px;min-width:180px}.view{display:none}.view.active{display:block}.opp{border-left:5px solid var(--blue);margin-bottom:10px;padding:14px}.opp-head{display:flex;justify-content:space-between;gap:16px;align-items:start}.head-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.meta{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:8px}.rank{color:var(--muted);font-weight:700}.pill{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:12px;font-weight:700;background:var(--panel-2)}.rec-pursue,.status-pursue,.priority-high,.priority-urgent{color:var(--green);border-color:#60bd91;background:color-mix(in srgb,var(--green) 14%,var(--panel))}.priority-urgent{color:var(--red);border-color:var(--red)}.rec-monitor,.status-monitor{color:var(--amber);border-color:#d89f4d;background:color-mix(in srgb,var(--amber) 16%,var(--panel))}.status-no-bid,.status-archived{color:var(--muted)}.status-teaming,.status-reviewing,.status-submitted{color:var(--blue);border-color:var(--blue);background:color-mix(in srgb,var(--blue) 13%,var(--panel))}.new{color:#0284c7;border-color:#8bd3f7;background:color-mix(in srgb,#38bdf8 16%,var(--panel))}.follow{color:var(--red);border-color:#fda29b;background:color-mix(in srgb,var(--red) 12%,var(--panel))}.urgency-high{color:var(--red);border-color:#fda29b;background:color-mix(in srgb,var(--red) 14%,var(--panel))}.urgency-medium{color:var(--amber);border-color:#d89f4d;background:color-mix(in srgb,var(--amber) 16%,var(--panel))}.urgency-low{color:var(--green);border-color:#60bd91;background:color-mix(in srgb,var(--green) 14%,var(--panel))}.opp h2{margin:0;font-size:18px;line-height:1.25;font-weight:750;letter-spacing:0}.sam{white-space:nowrap;text-decoration:none;color:white;background:#1768ac;border-radius:6px;padding:9px 12px;font-weight:700}.facts{display:grid;grid-template-columns:2fr 120px 155px 80px 130px 130px 90px 90px;gap:8px;margin:12px 0}.facts span,.dims span,.mini-card{border:1px solid var(--line);border-radius:6px;padding:8px 9px;color:var(--muted);min-width:0;overflow-wrap:anywhere;background:var(--panel-2)}.facts b,.dims b,.mini-card b{display:block;color:var(--ink);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px;font-family:"Aptos Narrow","Segoe UI",system-ui,sans-serif}.analysis{display:grid;grid-template-columns:1.1fr .9fr;gap:14px}h3{margin:0 0 7px;font-size:13px;text-transform:uppercase;letter-spacing:.04em}p{line-height:1.45}ul{margin:8px 0 0 18px;padding:0;color:var(--muted)}.muted{color:var(--muted)}.workflow{display:grid;grid-template-columns:150px 130px minmax(280px,1fr) auto minmax(120px,auto);gap:8px;align-items:end;margin-top:12px;border-top:1px solid var(--line);padding-top:12px}.workflow label,.modal-grid label{display:grid;gap:4px;color:var(--muted);font-size:13px}.workflow select,.workflow input{width:100%;min-width:0}.save-status{background:#0d2235;color:white;border-color:#0d2235;font-weight:700}.dims{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin-top:10px}.board{display:grid;grid-template-columns:repeat(8,minmax(210px,1fr));gap:10px;min-width:1680px}.board-view,.view#board-view{overflow-x:auto;padding-bottom:8px}.lane{min-height:360px;padding:10px}.lane h2{position:sticky;top:64px;z-index:1;background:var(--panel);padding-bottom:8px}.lane.drag-over{outline:2px solid var(--cyan);outline-offset:2px}.lane h2{font-size:14px;text-transform:uppercase;letter-spacing:.04em;margin:0 0 10px;display:flex;justify-content:space-between;color:var(--muted)}.lane-count{font-weight:800;color:var(--ink)}.board-card{display:block;text-decoration:none;color:var(--ink);background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:9px;margin-bottom:8px;border-left:4px solid var(--blue)}.board-card:focus,.board-card:hover{border-color:var(--blue)}.board-card b{display:block;font-size:13px;line-height:1.25;margin-bottom:7px}.board-card span{display:inline-flex;margin-right:6px;margin-bottom:4px;color:var(--muted);font-size:12px}.queue{display:grid;gap:12px}.queue-item{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center}.token-popover{position:fixed;right:24px;top:96px;z-index:20;display:none;width:min(420px,calc(100vw - 32px));background:var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:0 24px 60px rgba(0,0,0,.28);padding:16px}.token-popover.open{display:block}.token-row{display:flex;gap:8px}.token-row input{min-width:0}.token-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}.read-only{font-weight:700;color:var(--amber)}.modal{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;z-index:15;align-items:flex-start;justify-content:center;overflow:auto;padding:28px}.modal.open{display:flex}.modal-card{width:min(1320px,calc(100vw - 40px));padding:18px;overflow-wrap:anywhere;overflow-x:hidden}.modal-head{display:flex;justify-content:space-between;gap:12px;align-items:start}.modal-grid{display:grid;grid-template-columns:160px 150px minmax(190px,1fr) 170px 210px minmax(220px,1fr);gap:10px;margin:12px 0}.detail-sections{display:grid;grid-template-columns:minmax(460px,1.1fr) minmax(420px,.9fr);gap:14px}.detail-sections>section,.documents,.doc-row,.doc-row label{min-width:0}.detail-fit a{overflow-wrap:anywhere;word-break:break-word}.doc-row{display:grid;grid-template-columns:minmax(120px,.8fr) minmax(180px,1.5fr) minmax(92px,auto) auto;gap:8px;align-items:center;margin-bottom:8px}.doc-row input{width:100%;min-width:0}.timeline{display:grid;gap:8px;max-height:320px;overflow:auto}.event{border-left:3px solid var(--cyan);padding:6px 8px;background:var(--panel-2);border-radius:6px}.event b{display:block}.lists{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.follow-row{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}@media(max-width:1500px){.board{grid-template-columns:repeat(8,minmax(220px,1fr));min-width:1760px}.tools{grid-template-columns:1fr}.tool-group{width:100%}.action-group{justify-content:flex-end}}@media(max-width:1040px){.metric-grid,.lists{grid-template-columns:repeat(2,1fr)}.facts{grid-template-columns:repeat(4,minmax(0,1fr))}}@media(max-width:860px){header,main{padding-left:16px;padding-right:16px}.wrap{max-width:100%}.summary{grid-template-columns:repeat(2,1fr)}.brief,.analysis,.detail-sections,.modal-grid,.metric-grid,.lists{grid-template-columns:1fr}.board{grid-template-columns:repeat(8,minmax(220px,1fr));min-width:1760px}.facts{grid-template-columns:1fr 1fr}.dims{grid-template-columns:1fr}.workflow,.doc-row{grid-template-columns:1fr}.opp-head,.modal-head{flex-direction:column}input,#q{min-width:100%}.status{min-width:0}.toolbar{position:static}.modal{padding:12px}.modal-card{width:100%;padding:14px}}
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
let filter='all';
function escapeHtml(value){return String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));}
function setTheme(theme){document.documentElement.dataset.theme=theme;localStorage.setItem('samRadarTheme',theme);themeButton.textContent=theme==='dark'?'Light':'Dark';}
setTheme(localStorage.getItem('samRadarTheme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'));
themeButton.addEventListener('click',()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark'));
function token(){return localStorage.getItem('samRadarWriteToken')||'';}
function updateTokenState(){tokenState.textContent=token()?'Write token saved':'Read-only mode';tokenState.classList.toggle('read-only',!token());tokenInput.value=token();}
updateTokenState();
tokenButton.addEventListener('click',()=>{tokenPanel.classList.toggle('open');updateTokenState();});
document.getElementById('token-save').addEventListener('click',()=>{const value=tokenInput.value.trim();if(value){localStorage.setItem('samRadarWriteToken',value);}else{localStorage.removeItem('samRadarWriteToken');}tokenPanel.classList.remove('open');updateTokenState();});
document.getElementById('token-clear').addEventListener('click',()=>{localStorage.removeItem('samRadarWriteToken');tokenPanel.classList.remove('open');updateTokenState();});
function labelStatus(status){return {'new':'New','reviewing':'Reviewing','pursue':'Pursue','teaming':'Teaming','monitor':'Monitor','no-bid':'No-Bid','submitted':'Submitted','archived':'Archived'}[status]||status;}
function currentWorkflow(id){const opp=byId.get(String(id));return {status:opp.workflowStatus||'new',notes:opp.workflowNotes||'',priority:opp.workflowPriority||'normal',owner:opp.workflowOwner||'',nextAction:opp.workflowNextAction||'',followUpAt:opp.workflowFollowUpAt||'',decisionReason:opp.workflowDecisionReason||'',noBidReason:opp.workflowNoBidReason||'',noBidDetail:opp.workflowNoBidDetail||'',documents:opp.workflowDocuments||[]};}
function syncCard(id, workflow){const opp=byId.get(String(id));if(!opp)return;Object.assign(opp,{workflowStatus:workflow.status,workflowNotes:workflow.notes,workflowPriority:workflow.priority,workflowOwner:workflow.owner,workflowNextAction:workflow.nextAction,workflowFollowUpAt:workflow.followUpAt,workflowDecisionReason:workflow.decisionReason,workflowNoBidReason:workflow.noBidReason,workflowNoBidDetail:workflow.noBidDetail,workflowDocuments:workflow.documents||[],workflowEvents:workflow.events||[],workflowUpdatedAt:workflow.updatedAt});const card=document.querySelector(`.opp[data-id="${CSS.escape(String(id))}"]`);if(card){card.dataset.status=workflow.status;card.dataset.priority=workflow.priority;card.querySelector('.workflow-status').value=workflow.status;card.querySelector('.workflow-priority').value=workflow.priority;card.querySelector('.workflow-notes').value=workflow.notes||'';const pill=card.querySelector('.status-pill');if(pill){pill.textContent=labelStatus(workflow.status);pill.className='pill status-pill status-'+workflow.status;}const pp=card.querySelector('[class*=priority-]');if(pp){pp.textContent=workflow.priority;pp.className='pill priority-'+workflow.priority;}}apply();}
async function saveWorkflow(id, body, msg){const writeToken=token();if(!writeToken){tokenPanel.classList.add('open');if(msg)msg.textContent='Unlock editing with APP_WRITE_TOKEN';throw new Error('Token required');}const res=await fetch('/api/status/'+encodeURIComponent(id),{method:'POST',headers:{'Content-Type':'application/json','X-SAM-RADAR-TOKEN':writeToken},body:JSON.stringify(body)});const data=await res.json();if(!res.ok||!data.ok)throw new Error(data.error||'Save failed');syncCard(id,data.workflow);return data.workflow;}
cards.forEach(card=>{card.querySelector('.workflow-status').value=card.dataset.status||'new';card.querySelector('.workflow-priority').value=card.dataset.priority||'normal';});
function visibleCards(){return cards.filter(card=>card.style.display!=='none');}
function apply(){const q=(input.value||'').toLowerCase().trim();cards.forEach(card=>{const rec=card.dataset.rec||'';const type=card.dataset.type||'';const isNew=card.dataset.new==='true';const text=(card.dataset.search||'').toLowerCase();let ok=filter==='all'||rec===filter||type===filter||card.dataset.status===filter||card.dataset.priority===filter||(filter==='new'&&isNew)||(filter==='follow-up'&&card.dataset.followup==='true');if(q)ok=ok&&text.includes(q);card.style.display=ok?'block':'none';});buildBoard();buildQueue();}
function boardCard(card){const id=card.dataset.id;const opp=byId.get(String(id))||{};return `<a class="board-card" draggable="true" href="#" data-id="${escapeHtml(id)}"><b>${escapeHtml(opp.title||'Untitled')}</b><span>Score ${escapeHtml(opp.score||'n/a')}</span><span>${escapeHtml(opp.dueDisplay||'n/a')}</span><span>${escapeHtml(opp.workflowOwner||'Unassigned')}</span></a>`;}
function buildBoard(){const board=document.getElementById('board');if(!board)return;const statuses=['new','reviewing','pursue','teaming','submitted','monitor','no-bid','archived'];board.innerHTML=statuses.map(status=>{const items=visibleCards().filter(card=>card.dataset.status===status);return `<section class="lane" data-status="${status}"><h2>${labelStatus(status)} <span class="lane-count">${items.length}</span></h2>${items.map(boardCard).join('')||'<p class="muted">No cards</p>'}</section>`;}).join('');board.querySelectorAll('.board-card').forEach(el=>{el.addEventListener('click',event=>{event.preventDefault();openDetail(el.dataset.id);});el.addEventListener('dragstart',event=>{event.dataTransfer.setData('text/plain',el.dataset.id);});});board.querySelectorAll('.lane').forEach(lane=>{lane.addEventListener('dragover',event=>{event.preventDefault();lane.classList.add('drag-over');});lane.addEventListener('dragleave',()=>lane.classList.remove('drag-over'));lane.addEventListener('drop',async event=>{event.preventDefault();lane.classList.remove('drag-over');const id=event.dataTransfer.getData('text/plain');const wf=currentWorkflow(id);wf.status=lane.dataset.status;try{await saveWorkflow(id,wf,refreshStatus);refreshStatus.textContent='Status saved';}catch(err){refreshStatus.textContent=err.message||'Save failed';}});});}
function buildQueue(){const q=document.getElementById('queue');if(!q)return;const items=report.matches.filter(o=>(o.followUpReasons||[]).length);q.innerHTML=items.map(o=>`<article class="panel queue-item"><div><h3>${escapeHtml(o.title)}</h3><p class="muted">${escapeHtml((o.followUpReasons||[]).join(', '))}</p><p>Due: ${escapeHtml(o.dueDisplay||'n/a')} | Owner: ${escapeHtml(o.workflowOwner||'Unassigned')}</p></div><button class="open-detail" data-id="${escapeHtml(o.noticeId)}" type="button">Details</button></article>`).join('')||'<section class="empty">No follow-up items for this report.</section>';q.querySelectorAll('.open-detail').forEach(b=>b.addEventListener('click',()=>openDetail(b.dataset.id)));}
function docRows(docs){return (docs&&docs.length?docs:[{label:'SAM.gov listing',url:'',reviewed:false}]).map(doc=>`<div class="doc-row"><input class="doc-label" placeholder="Label" value="${escapeHtml(doc.label||'')}"><input class="doc-url" placeholder="URL" value="${escapeHtml(doc.url||'')}"><label><input class="doc-reviewed" type="checkbox" ${doc.reviewed?'checked':''}> Reviewed</label><button class="remove-doc" type="button">Remove</button></div>`).join('');}
function collectDocs(){return [...modal.querySelectorAll('.doc-row')].map(row=>({label:row.querySelector('.doc-label').value,url:row.querySelector('.doc-url').value,reviewed:row.querySelector('.doc-reviewed').checked})).filter(d=>d.url.trim());}
function openDetail(id){const opp=byId.get(String(id));if(!opp)return;modal.dataset.id=id;modal.querySelector('.detail-title').textContent=opp.title||'Untitled';modal.querySelector('.detail-subtitle').textContent=`${opp.organization||'n/a'} | Score ${opp.score||'n/a'} | Due ${opp.dueDisplay||'n/a'}`;modal.querySelector('[name=status]').value=opp.workflowStatus||'new';modal.querySelector('[name=priority]').value=opp.workflowPriority||'normal';modal.querySelector('[name=owner]').value=opp.workflowOwner||'';modal.querySelector('[name=followUpAt]').value=(opp.workflowFollowUpAt||'').slice(0,10);modal.querySelector('[name=nextAction]').value=opp.workflowNextAction||opp.nextAction||'';modal.querySelector('[name=notes]').value=opp.workflowNotes||'';modal.querySelector('[name=decisionReason]').value=opp.workflowDecisionReason||'';modal.querySelector('[name=noBidReason]').value=opp.workflowNoBidReason||'';modal.querySelector('[name=noBidDetail]').value=opp.workflowNoBidDetail||'';modal.querySelector('.detail-fit').innerHTML=`<p>${escapeHtml(opp.fitReason||'')}</p><p class="muted">SAM.gov: <a href="${escapeHtml(opp.url||'#')}" target="_blank" rel="noopener">${escapeHtml(opp.url||'n/a')}</a></p>`;modal.querySelector('.documents').innerHTML=docRows(opp.workflowDocuments||[]);modal.querySelector('.timeline').innerHTML=(opp.workflowEvents||[]).map(ev=>`<div class="event"><b>${escapeHtml(ev.type)}</b><span>${escapeHtml(ev.createdDisplay||ev.createdAt||'')}</span><p>${escapeHtml(ev.message||'')}</p></div>`).join('')||'<p class="muted">No workflow events yet.</p>';modal.classList.add('open');}
modal.querySelector('.close-modal').addEventListener('click',()=>modal.classList.remove('open'));
modal.querySelector('.add-doc').addEventListener('click',()=>{modal.querySelector('.documents').insertAdjacentHTML('beforeend',docRows([{label:'',url:'',reviewed:false}]));});
modal.addEventListener('click',event=>{if(event.target===modal)modal.classList.remove('open');if(event.target.classList.contains('remove-doc'))event.target.closest('.doc-row').remove();});
modal.querySelector('.save-detail').addEventListener('click',async()=>{const id=modal.dataset.id;const msg=modal.querySelector('.detail-message');const body={status:modal.querySelector('[name=status]').value,priority:modal.querySelector('[name=priority]').value,owner:modal.querySelector('[name=owner]').value,followUpAt:modal.querySelector('[name=followUpAt]').value,nextAction:modal.querySelector('[name=nextAction]').value,notes:modal.querySelector('[name=notes]').value,decisionReason:modal.querySelector('[name=decisionReason]').value,noBidReason:modal.querySelector('[name=noBidReason]').value,noBidDetail:modal.querySelector('[name=noBidDetail]').value,documents:collectDocs()};msg.textContent='Saving...';try{const wf=await saveWorkflow(id,body,msg);msg.textContent='Saved';openDetail(id);}catch(err){msg.textContent=err.message||'Save failed';}});
buttons.forEach(btn=>btn.addEventListener('click',()=>{buttons.forEach(b=>b.classList.remove('active'));btn.classList.add('active');filter=btn.dataset.filter;apply();}));
viewButtons.forEach(btn=>btn.addEventListener('click',()=>{viewButtons.forEach(b=>b.classList.remove('active'));btn.classList.add('active');document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));document.getElementById(btn.dataset.view+'-view').classList.add('active');buildBoard();buildQueue();}));
document.querySelectorAll('.open-detail').forEach(b=>b.addEventListener('click',()=>openDetail(b.dataset.id)));
document.querySelectorAll('.save-status').forEach(btn=>btn.addEventListener('click',async()=>{const card=btn.closest('.opp');const msg=card.querySelector('.workflow-message');const wf=currentWorkflow(card.dataset.id);wf.status=card.querySelector('.workflow-status').value;wf.priority=card.querySelector('.workflow-priority').value;wf.notes=card.querySelector('.workflow-notes').value;msg.textContent='Saving...';try{await saveWorkflow(card.dataset.id,wf,msg);msg.textContent='Saved';}catch(err){msg.textContent=err.message||'Save failed';}}));
if(input)input.addEventListener('input',apply);
if(refresh){refresh.addEventListener('click',async()=>{refresh.disabled=true;refreshStatus.textContent='Refreshing from SAM.gov...';try{const res=await fetch('/api/refresh',{method:'POST',headers:{'Accept':'application/json'}});const contentType=res.headers.get('content-type')||'';const data=contentType.includes('application/json')?await res.json():{ok:false,error:(await res.text()).slice(0,160)||'Refresh returned a non-JSON response'};if(!res.ok||!data.ok)throw new Error(data.error||'Refresh failed');refreshStatus.textContent='Refreshed. Reloading...';window.location.reload();}catch(err){refreshStatus.textContent=err.message||'Refresh failed';refresh.disabled=false;}});}
apply();
"""
    top_agencies = "".join(f"<div class='mini-card'><b>{e(item['label'])}</b>{e(item['count'])}</div>" for item in metrics.get("topAgencies", [])) or "<p class='muted'>n/a</p>"
    top_naics = "".join(f"<div class='mini-card'><b>{e(item['label'])}</b>{e(item['count'])}</div>" for item in metrics.get("topNaics", [])) or "<p class='muted'>n/a</p>"
    top_psc = "".join(f"<div class='mini-card'><b>{e(item['label'])}</b>{e(item['count'])}</div>" for item in metrics.get("topPsc", [])) or "<p class='muted'>n/a</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>SAM Radar</title><style>{style}</style></head><body><header><div class="wrap"><h1>SAM Radar - {e(summary['businessName'])}</h1><p class="subtitle">Generated {e(summary['generatedAtDisplay'])} | Window {e(summary['postedFrom'])} to {e(summary['postedTo'])}</p><div class="summary"><div class="stat"><b>{e(summary['totalMatches'])}</b><span>Total matches</span></div><div class="stat"><b>{e(summary['newMatches'])}</b><span>New unseen</span></div><div class="stat"><b>{e(metrics.get('activeCount',0))}</b><span>Active pipeline</span></div><div class="stat"><b>{e(metrics.get('followUpCount',0))}</b><span>Need follow-up</span></div><div class="stat"><b>{e(metrics.get('averageScore',0))}</b><span>Avg score</span></div></div></div></header><main><div class="wrap"><section class="brief"><div class="panel"><h2>Executive Read</h2><p><b>Best match:</b> {e(summary['bestMatch'])}</p><p><b>Fastest deadline:</b> {e(summary['fastestDeadline'])} {e(summary['fastestDeadlineDisplay'])}</p><p>This report ranks opportunities by configured capability fit, set-aside signal, actionability, and deadline pressure.</p></div><div class="panel"><h2>Pipeline Notes</h2><p><b>No-bid rate:</b> {e(metrics.get('noBidRate',0))}%</p><p><b>Due soon:</b> {e(metrics.get('dueSoonCount',0))} | <b>Submitted:</b> {e(metrics.get('submittedCount',0))}</p><p>Use Board and Follow-Up to move work, track document review, and preserve decision history.</p></div></section><section class="metric-grid"><div class="metric"><b>{e(metrics.get('newThisWeek',0))}</b><span>New this week</span></div><div class="metric"><b>{e(metrics.get('pursueCount',0))}</b><span>Pipeline pursue</span></div><div class="metric"><b>{e(metrics.get('monitorCount',0))}</b><span>Pipeline monitor</span></div><div class="metric"><b>{e(metrics.get('submittedCount',0))}</b><span>Submitted</span></div></section><section class="brief"><div class="panel"><h2>Top Agencies</h2><div class="lists">{top_agencies}</div></div><div class="panel"><h2>Top Codes</h2><div class="lists">{top_naics}{top_psc}</div></div></section><div class="toolbar"><div class="tools"><div class="tool-group view-group"><button class="active" data-view="list">List</button><button data-view="board">Board</button><button data-view="queue">Follow-Up</button></div><div class="tool-group filter-group"><button class="active" data-filter="all">All</button><button data-filter="pursue">Pursue</button><button data-filter="monitor">Monitor</button><button data-filter="urgent">Urgent</button><button data-filter="follow-up">Needs Follow-Up</button><button data-filter="new">New</button><button data-filter="Sources Sought">Sources Sought</button></div><input id="q" placeholder="Search title, agency, owner, capability"><div class="tool-group action-group"><button id="theme-button" class="icon-btn" type="button">Dark</button><button id="token-button" type="button">Unlock</button><button id="refresh" class="refresh">Refresh</button></div><span id="refresh-status" class="status"></span></div></div><section id="token-panel" class="token-popover"><h2>Editing Token</h2><p id="token-state"></p><p>Use your local APP_WRITE_TOKEN here. This is separate from your SAM.gov API key.</p><div class="token-row"><input id="token-input" type="password" autocomplete="off" placeholder="APP_WRITE_TOKEN"><button id="token-save" class="token-save" type="button">Save</button></div><div class="token-actions"><button id="token-clear" type="button">Clear Browser Token</button></div></section><section id="list-view" class="view active"><section id="list">{rows_html}</section></section><section id="board-view" class="view"><div id="board" class="board"></div></section><section id="queue-view" class="view"><div id="queue" class="queue"></div></section></div></main><section id="detail-modal" class="modal"><div class="modal-card"><div class="modal-head"><div><h2 class="detail-title"></h2><p class="detail-subtitle muted"></p></div><button class="close-modal" type="button">Close</button></div><div class="modal-grid"><label>Status<select name="status"><option value="new">New</option><option value="reviewing">Reviewing</option><option value="pursue">Pursue</option><option value="teaming">Teaming</option><option value="monitor">Monitor</option><option value="no-bid">No-Bid</option><option value="submitted">Submitted</option><option value="archived">Archived</option></select></label><label>Priority<select name="priority"><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="urgent">Urgent</option></select></label><label>Owner<input name="owner" placeholder="Owner"></label><label>Follow-up<input name="followUpAt" type="date"></label><label>No-bid reason<select name="noBidReason"><option value="">n/a</option><option value="poor-fit">Poor fit</option><option value="deadline-too-short">Deadline too short</option><option value="incumbent-likely">Incumbent likely</option><option value="too-large">Too large</option><option value="certification-gap">Certification gap</option><option value="clearance-gap">Clearance gap</option><option value="geography">Geography</option><option value="staffing-gap">Staffing gap</option><option value="past-performance-gap">Past performance gap</option><option value="not-it-security">Not IT/security</option><option value="duplicate-noise">Duplicate/noise</option><option value="other">Other</option></select></label><label>Decision reason<input name="decisionReason" placeholder="Decision rationale"></label></div><div class="detail-sections"><section><h3>Capture Fields</h3><label>Next action<textarea name="nextAction"></textarea></label><label>Notes<textarea name="notes"></textarea></label><label>No-bid detail<textarea name="noBidDetail"></textarea></label><button class="save-detail" type="button">Save Opportunity</button><span class="detail-message status"></span></section><section><h3>Fit</h3><div class="detail-fit"></div><h3>Documents</h3><div class="documents"></div><button class="add-doc" type="button">Add Document</button></section></div><section><h3>Timeline</h3><div class="timeline"></div></section></div></section><script id="report-data" type="application/json">{data_json}</script><script>{script}</script></body></html>"""

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
