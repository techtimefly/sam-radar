
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


def build_report_payload(payload: dict, profile: BusinessProfile, settings: Settings, unseen: list[dict] | None = None) -> dict:
    unseen_ids = {opp.get("noticeId") for opp in (unseen or [])}
    enriched = []
    for rank, opp in enumerate(payload.get("matches") or [], 1):
        urgency_label, urgency_text = urgency(opp)
        item = dict(opp)
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
        enriched.append(item)
    due_sorted = sorted(
        [m for m in enriched if parse_deadline(m.get("responseDeadline") or "")],
        key=lambda m: parse_deadline(m.get("responseDeadline") or "") or dt.datetime.max.replace(tzinfo=dt.UTC),
    )
    generated = dt.datetime.now(ZoneInfo(settings.timezone))
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
    return {"summary": summary, "matches": enriched, "errors": payload.get("errors") or []}


def e(value) -> str:
    return html.escape(str(value if value is not None else ""))


def row_html(opp: dict) -> str:
    dims = "".join(f"<span><b>{e(k)}</b>{e(v)}</span>" for k, v in opp.get("dimensions", {}).items())
    reasons = "".join(f"<li>{e(reason)}</li>" for reason in (opp.get("reasons") or [])) or "<li>Matched configured search profile.</li>"
    new_badge = '<span class="pill new">New</span>' if opp.get("isNew") else ""
    search_text = " ".join([opp.get("title") or "", opp.get("organization") or "", opp.get("capabilityArea") or ""])
    notice_id = e(opp.get('noticeId') or '')
    status = e(opp.get('workflowStatus') or 'new')
    notes = e(opp.get('workflowNotes') or '')
    updated = e(opp.get('workflowUpdatedDisplay') or 'Not saved')
    return f"""
    <article class="opp" data-id="{notice_id}" data-rec="{e(opp.get('recommendation'))}" data-new="{str(bool(opp.get('isNew'))).lower()}" data-type="{e(opp.get('type'))}" data-search="{e(search_text)}">
      <div class="opp-head"><div><div class="meta"><span class="rank">#{e(opp.get('rank'))}</span><span class="pill rec-{e(str(opp.get('recommendation') or '').lower())}">{e(opp.get('recommendation'))}</span>{new_badge}<span class="pill urgency-{e(str(opp.get('urgency') or '').lower())}">{e(opp.get('urgency'))}</span></div><h2>{e(opp.get('title'))}</h2></div><a class="sam" href="{e(opp.get('url'))}" target="_blank" rel="noopener">Open SAM.gov</a></div>
      <div class="facts"><span><b>Agency</b>{e(opp.get('organization') or 'n/a')}</span><span><b>Posted</b>{e(opp.get('postedDisplay') or 'n/a')}</span><span><b>Due</b>{e(opp.get('dueDisplay') or 'n/a')}</span><span><b>Score</b>{e(opp.get('score') or 'n/a')}</span><span><b>NAICS</b>{e(opp.get('naicsCode') or 'n/a')}</span><span><b>PSC</b>{e(opp.get('classificationCode') or 'n/a')}</span><span><b>Set-aside</b>{e(opp.get('setAsideCode') or opp.get('setAside') or 'n/a')}</span><span><b>Type</b>{e(opp.get('type') or 'n/a')}</span></div>
      <div class="analysis"><section><h3>Why It Fits</h3><p>{e(opp.get('fitReason'))}</p><ul>{reasons}</ul></section><section><h3>Recommended Action</h3><p>{e(opp.get('nextAction'))}</p><p class="muted">{e(opp.get('urgencyText'))}</p></section></div>
      <div class="workflow"><label>Status <select class="workflow-status"><option value="new">New</option><option value="reviewing">Reviewing</option><option value="pursue">Pursue</option><option value="teaming">Teaming</option><option value="monitor">Monitor</option><option value="no-bid">No-Bid</option><option value="submitted">Submitted</option><option value="archived">Archived</option></select></label><label>Notes <input class="workflow-notes" value="{notes}" placeholder="Capture notes"></label><button class="save-status" data-status="{status}">Save</button><span class="workflow-message muted">Updated: {updated}</span></div>
      <div class="dims">{dims}</div>
    </article>"""


def build_html_report(report: dict) -> str:
    summary = report["summary"]
    rows_html = "\n".join(row_html(opp) for opp in report["matches"]) or '<section class="empty">No high-fit matches found for this window.</section>'
    data_json = html.escape(json.dumps(report, separators=(",", ":")))
    style = """
:root{color-scheme:light;--ink:#18202a;--muted:#667085;--line:#d7dde5;--bg:#f6f8fb;--panel:#fff;--blue:#1768ac;--green:#167c57;--amber:#a86500;--red:#b42318}*{box-sizing:border-box}body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink);background:var(--bg)}header{background:#0d2235;color:white;padding:28px 32px 22px;border-bottom:5px solid #27a6a6}.wrap{max-width:1180px;margin:0 auto}h1{margin:0;font-size:30px;letter-spacing:0}.subtitle{margin:8px 0 0;color:#c8d7e5}.summary{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:12px;margin-top:22px}.stat{background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.18);padding:13px 14px;border-radius:8px;min-height:72px}.stat b{display:block;font-size:24px}.stat span{color:#c8d7e5;font-size:13px}main{padding:22px 32px 42px}.brief{display:grid;grid-template-columns:1.5fr 1fr;gap:18px;margin-bottom:18px}.panel,.opp,.empty{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:18px}.panel h2{margin:0 0 8px;font-size:18px}.panel p{margin:7px 0;color:var(--muted);line-height:1.45}.toolbar{position:sticky;top:0;z-index:5;background:rgba(246,248,251,.96);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:12px 0;margin-bottom:16px}.tools{display:flex;gap:10px;flex-wrap:wrap;align-items:center}button,input,select{border:1px solid var(--line);background:white;color:var(--ink);border-radius:6px;padding:9px 11px;font:inherit}button{cursor:pointer}button.active{border-color:var(--blue);background:#eaf3fb;color:#0b558f}input{min-width:280px;flex:1}.refresh{margin-left:auto;background:#0d2235;color:white;border-color:#0d2235;font-weight:700}.status{color:var(--muted);font-size:13px;min-width:180px}.opp{border-left:5px solid var(--blue);margin-bottom:14px}.opp-head{display:flex;justify-content:space-between;gap:16px;align-items:start}.meta{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:8px}.rank{color:var(--muted);font-weight:700}.pill{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:12px;font-weight:700;background:#f8fafc}.rec-pursue{color:var(--green);border-color:#9bd9c1;background:#ecfdf5}.rec-monitor{color:var(--amber);border-color:#f2c781;background:#fff7e8}.new{color:#075985;border-color:#8bd3f7;background:#e9f8ff}.urgency-high{color:var(--red);border-color:#fda29b;background:#fff1f0}.urgency-medium{color:var(--amber);border-color:#f2c781;background:#fff7e8}.urgency-low{color:var(--green);border-color:#9bd9c1;background:#ecfdf5}.opp h2{margin:0;font-size:20px;line-height:1.25;letter-spacing:0}.sam{white-space:nowrap;text-decoration:none;color:white;background:var(--blue);border-radius:6px;padding:9px 12px;font-weight:700}.facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:16px 0}.facts span,.dims span{border:1px solid var(--line);border-radius:6px;padding:9px 10px;color:var(--muted);min-width:0;overflow-wrap:anywhere;background:#fbfcfe}.facts b,.dims b{display:block;color:var(--ink);font-size:12px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px}.analysis{display:grid;grid-template-columns:1fr 1fr;gap:16px}h3{margin:0 0 7px;font-size:14px}p{line-height:1.45}ul{margin:8px 0 0 18px;padding:0;color:var(--muted)}.muted{color:var(--muted)}.workflow{display:grid;grid-template-columns:180px 1fr auto minmax(120px,auto);gap:10px;align-items:end;margin-top:14px;border-top:1px solid var(--line);padding-top:14px}.workflow label{display:grid;gap:4px;color:var(--muted);font-size:13px}.workflow select,.workflow input{width:100%;min-width:0}.save-status{background:#0d2235;color:white;border-color:#0d2235;font-weight:700}.dims{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-top:14px}@media(max-width:860px){header,main{padding-left:16px;padding-right:16px}.summary{grid-template-columns:repeat(2,1fr)}.brief,.analysis{grid-template-columns:1fr}.facts{grid-template-columns:1fr 1fr}.dims{grid-template-columns:1fr}.workflow{grid-template-columns:1fr}.opp-head{flex-direction:column}input{min-width:100%}}
"""
    script = """
const buttons=[...document.querySelectorAll('button[data-filter]')];
const input=document.getElementById('q');
const cards=[...document.querySelectorAll('.opp')];
const refresh=document.getElementById('refresh');
const refreshStatus=document.getElementById('refresh-status');
let filter='all';
cards.forEach(card=>{
  const btn=card.querySelector('.save-status');
  const select=card.querySelector('.workflow-status');
  if(select&&btn){select.value=btn.dataset.status||'new';}
});
function apply(){
  const q=(input.value||'').toLowerCase().trim();
  cards.forEach(card=>{
    const rec=card.dataset.rec||'';
    const type=card.dataset.type||'';
    const isNew=card.dataset.new==='true';
    const text=(card.dataset.search||'').toLowerCase();
    let ok=filter==='all'||rec===filter||type===filter||(filter==='new'&&isNew);
    if(q) ok=ok&&text.includes(q);
    card.style.display=ok?'block':'none';
  });
}
buttons.forEach(btn=>btn.addEventListener('click',()=>{
  buttons.forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  filter=btn.dataset.filter;
  apply();
}));
if(input){input.addEventListener('input',apply);}
if(refresh){
  refresh.addEventListener('click',async()=>{
    refresh.disabled=true;
    refreshStatus.textContent='Refreshing from SAM.gov...';
    try{
      const res=await fetch('/api/refresh',{method:'POST',headers:{'Accept':'application/json'}});
      const contentType=res.headers.get('content-type')||'';
      const data=contentType.includes('application/json')?await res.json():{ok:false,error:(await res.text()).slice(0,160)||'Refresh returned a non-JSON response'};
      if(!res.ok||!data.ok) throw new Error(data.error||'Refresh failed');
      refreshStatus.textContent='Refreshed. Reloading...';
      window.location.reload();
    }catch(err){
      refreshStatus.textContent=err.message||'Refresh failed';
      refresh.disabled=false;
    }
  });
}
document.querySelectorAll('.save-status').forEach(btn=>btn.addEventListener('click',async()=>{
  const card=btn.closest('.opp');
  const msg=card.querySelector('.workflow-message');
  let token=localStorage.getItem('samRadarWriteToken')||'';
  if(!token){
    token=prompt('APP_WRITE_TOKEN');
    if(token) localStorage.setItem('samRadarWriteToken',token);
  }
  if(!token){
    msg.textContent='Token required';
    return;
  }
  btn.disabled=true;
  msg.textContent='Saving...';
  try{
    const body={
      status:card.querySelector('.workflow-status').value,
      notes:card.querySelector('.workflow-notes').value
    };
    const res=await fetch('/api/status/'+encodeURIComponent(card.dataset.id),{
      method:'POST',
      headers:{'Content-Type':'application/json','X-SAM-RADAR-TOKEN':token},
      body:JSON.stringify(body)
    });
    const data=await res.json();
    if(!res.ok||!data.ok) throw new Error(data.error||'Save failed');
    msg.textContent='Saved';
    btn.dataset.status=data.workflow.status;
  }catch(err){
    msg.textContent=err.message||'Save failed';
    if((err.message||'').toLowerCase().includes('token')) localStorage.removeItem('samRadarWriteToken');
  }finally{
    btn.disabled=false;
  }
}));
"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>SAM Radar</title><style>{style}</style></head><body><header><div class="wrap"><h1>SAM Radar - {e(summary['businessName'])}</h1><p class="subtitle">Generated {e(summary['generatedAtDisplay'])} | Window {e(summary['postedFrom'])} to {e(summary['postedTo'])}</p><div class="summary"><div class="stat"><b>{e(summary['totalMatches'])}</b><span>Total matches</span></div><div class="stat"><b>{e(summary['newMatches'])}</b><span>New unseen</span></div><div class="stat"><b>{e(summary['pursueCount'])}</b><span>Pursue</span></div><div class="stat"><b>{e(summary['monitorCount'])}</b><span>Monitor</span></div><div class="stat"><b>{e(summary['sourcesSoughtCount'])}</b><span>Sources sought</span></div></div></div></header><main><div class="wrap"><section class="brief"><div class="panel"><h2>Executive Read</h2><p><b>Best match:</b> {e(summary['bestMatch'])}</p><p><b>Fastest deadline:</b> {e(summary['fastestDeadline'])} {e(summary['fastestDeadlineDisplay'])}</p><p>This report ranks opportunities by configured capability fit, set-aside signal, actionability, and deadline pressure.</p></div><div class="panel"><h2>BD Notes</h2><p>Sources sought are useful for early agency engagement. High-fit solicitations with short runway need same-day attachment review.</p><p>Use the status workflow to track pursue, teaming, monitor, and no-bid decisions.</p></div></section><div class="toolbar"><div class="tools"><button class="active" data-filter="all">All</button><button data-filter="Pursue">Pursue</button><button data-filter="Monitor">Monitor</button><button data-filter="new">New</button><button data-filter="Sources Sought">Sources Sought</button><input id="q" placeholder="Search title, agency, capability area"><button id="refresh" class="refresh">Refresh</button><span id="refresh-status" class="status"></span></div></div><section id="list">{rows_html}</section></div></main><script id="report-data" type="application/json">{data_json}</script><script>{script}</script></body></html>"""

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
