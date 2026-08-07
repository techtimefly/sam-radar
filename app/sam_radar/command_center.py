from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RISK_WEIGHT = {"critical": 0, "high": 1, "medium": 2, "low": 3}
TERMINAL_STATUSES = {"submitted", "no-bid", "archived"}
ACTIVE_STATUSES = {"new", "reviewing", "pursue", "teaming", "monitor"}


def parse_command_datetime(value: object, timezone: str) -> dt.datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        local_tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        local_tz = dt.UTC
    try:
        if len(raw) == 10:
            parsed = dt.datetime.combine(dt.date.fromisoformat(raw), dt.time.min, tzinfo=local_tz)
        else:
            normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            parsed = dt.datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=local_tz)
    except ValueError:
        return None
    return parsed.astimezone(dt.UTC)


def _deadline_delta(value: object, now: dt.datetime, timezone: str) -> dt.timedelta | None:
    parsed = parse_command_datetime(value, timezone)
    if not parsed:
        return None
    return parsed - now.astimezone(dt.UTC)


def _hours_until(value: object, now: dt.datetime, timezone: str) -> int | None:
    delta = _deadline_delta(value, now, timezone)
    if delta is None:
        return None
    return int(delta.total_seconds() // 3600)


def _date_due(value: object, now: dt.datetime, timezone: str) -> bool:
    parsed = parse_command_datetime(value, timezone)
    if not parsed:
        return False
    try:
        local_tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        local_tz = dt.UTC
    return parsed.astimezone(local_tz).date() <= now.astimezone(local_tz).date()


def _relative_deadline_reason(prefix: str, hours: int) -> str:
    if hours < 0:
        amount = abs(hours)
        if amount < 24:
            return f"{prefix} is overdue by {amount} hour{'s' if amount != 1 else ''}."
        days = amount // 24
        return f"{prefix} is overdue by {days} day{'s' if days != 1 else ''}."
    if hours < 24:
        return f"{prefix} is due in {hours} hour{'s' if hours != 1 else ''}."
    days = hours // 24
    return f"{prefix} is due in {days} day{'s' if days != 1 else ''}."


def _target(view: str, notice_id: str, surface: str, anchor: str = "") -> dict[str, str]:
    return {"view": view, "noticeId": notice_id, "surface": surface, "anchor": anchor}


def _action(
    *,
    kind: str,
    notice: dict,
    risk: str,
    action: str,
    reason: str,
    target: dict[str, str],
    due_at: str = "",
    sort_rank: int,
    metadata: dict | None = None,
) -> dict:
    notice_id = str(notice.get("noticeId") or "")
    return {
        "kind": kind,
        "noticeId": notice_id,
        "title": notice.get("title") or notice_id,
        "agency": notice.get("organization") or "n/a",
        "risk": risk,
        "priority": risk,
        "action": action,
        "reason": reason,
        "target": target,
        "dueAt": due_at,
        "owner": notice.get("workflowOwner") or "",
        "_sort": (RISK_WEIGHT.get(risk, 9), sort_rank, due_at or "9999-12-31T23:59:59+00:00", notice_id, kind),
        "metadata": metadata or {},
    }


def _dedupe(matches: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique = []
    for item in matches:
        notice_id = str(item.get("noticeId") or "").strip()
        if not notice_id or notice_id in seen:
            continue
        seen.add(notice_id)
        unique.append(item)
    return unique


def build_command_center(matches: list[dict], *, now: dt.datetime | None = None, timezone: str = "UTC") -> dict:
    now = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    actions: list[dict] = []
    recent: list[dict] = []
    metrics = {
        "totalActions": 0,
        "criticalCount": 0,
        "highCount": 0,
        "mediumCount": 0,
        "lowCount": 0,
        "overdueFollowUps": 0,
        "unreadMaterialAmendments": 0,
        "staleEvidence": 0,
        "unverifiedEvidence": 0,
        "complianceGaps": 0,
        "proposalDeadlines": 0,
        "highFitUnreviewed": 0,
        "activeAssignments": 0,
    }
    unique = _dedupe(matches)
    for notice in unique:
        notice_id = str(notice.get("noticeId") or "")
        status = str(notice.get("workflowStatus") or "new")
        owner = str(notice.get("workflowOwner") or "")
        deadline = str(notice.get("responseDeadline") or "")
        deadline_delta = _deadline_delta(deadline, now, timezone)
        deadline_hours = int(deadline_delta.total_seconds() // 3600) if deadline_delta is not None else None
        summary = notice.get("amendmentSummary") or {}
        stale_warnings = notice.get("staleEvidenceWarnings") or {}
        citations = notice.get("evidenceCitations") or []
        requirements = notice.get("complianceRequirements") or []
        proposal = notice.get("proposal") or {}

        if status in TERMINAL_STATUSES:
            continue

        if status in ACTIVE_STATUSES and owner:
            metrics["activeAssignments"] += 1

        if deadline_delta is not None and deadline_delta <= dt.timedelta(hours=48):
            risk = "critical" if deadline_delta <= dt.timedelta(hours=4) else "high"
            actions.append(
                _action(
                    kind="proposal-deadline",
                    notice=notice,
                    risk=risk,
                    action="Advance proposal stage",
                    reason=_relative_deadline_reason("Proposal deadline", deadline_hours),
                    target=_target("proposals" if proposal else "command", notice_id, "proposal-workspace" if proposal else "opportunity-detail"),
                    due_at=parse_command_datetime(deadline, timezone).isoformat() if parse_command_datetime(deadline, timezone) else "",
                    sort_rank=10,
                    metadata={"proposalId": proposal.get("id"), "stage": proposal.get("stage") or ""},
                )
            )
            metrics["proposalDeadlines"] += 1

        follow_up = str(notice.get("workflowFollowUpAt") or "")
        if follow_up and _date_due(follow_up, now, timezone):
            actions.append(
                _action(
                    kind="follow-up-overdue",
                    notice=notice,
                    risk="critical",
                    action="Set follow-up",
                    reason="Workflow follow-up is due or overdue.",
                    target=_target("command", notice_id, "workflow"),
                    due_at=parse_command_datetime(follow_up, timezone).isoformat() if parse_command_datetime(follow_up, timezone) else follow_up,
                    sort_rank=20,
                )
            )
            metrics["overdueFollowUps"] += 1

        if int(summary.get("materialChangeCount") or 0) and int(summary.get("unreadCount") or 0):
            actions.append(
                _action(
                    kind="amendment-review",
                    notice=notice,
                    risk="critical",
                    action="Review amendment",
                    reason=f"{summary.get('unreadCount')} unread material amendment change(s).",
                    target=_target("command", notice_id, "amendments"),
                    sort_rank=30,
                    metadata={"unreadCount": summary.get("unreadCount"), "materialChangeCount": summary.get("materialChangeCount")},
                )
            )
            recent.append(
                {
                    "noticeId": notice_id,
                    "title": notice.get("title") or notice_id,
                    "risk": "critical",
                    "reason": f"{summary.get('materialChangeCount')} material amendment change(s) need review.",
                    "target": _target("command", notice_id, "amendments"),
                }
            )
            metrics["unreadMaterialAmendments"] += 1

        stale_count = int(stale_warnings.get("count") or summary.get("staleEvidenceCount") or 0)
        if stale_count:
            actions.append(
                _action(
                    kind="stale-evidence",
                    notice=notice,
                    risk="critical",
                    action="Verify evidence",
                    reason=f"{stale_count} evidence item(s) may be stale after amendments.",
                    target=_target("command", notice_id, "evidence"),
                    sort_rank=40,
                    metadata={"staleEvidenceCount": stale_count},
                )
            )
            metrics["staleEvidence"] += 1

        if any(str(item.get("verificationState") or "needs-review") != "verified" for item in citations):
            actions.append(
                _action(
                    kind="evidence-unverified",
                    notice=notice,
                    risk="high",
                    action="Verify evidence",
                    reason="One or more evidence citations still need human verification.",
                    target=_target("command", notice_id, "evidence"),
                    sort_rank=50,
                )
            )
            metrics["unverifiedEvidence"] += 1

        if any(
            item.get("invalidated")
            or str(item.get("status") or "open") in {"open", "gap", "blocked"}
            or str(item.get("verificationState") or "needs-review") != "verified"
            for item in requirements
        ):
            actions.append(
                _action(
                    kind="compliance-gap",
                    notice=notice,
                    risk="high",
                    action="Open compliance matrix",
                    reason="Compliance matrix has open, invalidated, or unverified rows.",
                    target=_target("command", notice_id, "compliance"),
                    sort_rank=60,
                )
            )
            metrics["complianceGaps"] += 1

        if int(notice.get("score") or 0) >= 12 and status in {"new", "reviewing"} and not owner:
            actions.append(
                _action(
                    kind="high-fit-unreviewed",
                    notice=notice,
                    risk="high",
                    action="Assign owner",
                    reason="High-fit opportunity has not been assigned for pursuit review.",
                    target=_target("command", notice_id, "opportunity-detail"),
                    sort_rank=70,
                )
            )
            metrics["highFitUnreviewed"] += 1

        if status in {"pursue", "teaming", "reviewing"} and owner and not str(notice.get("workflowNextAction") or ""):
            actions.append(
                _action(
                    kind="assignment-next-action",
                    notice=notice,
                    risk="medium",
                    action="Assign owner" if not owner else "Set follow-up",
                    reason="Active assignment is missing a recorded next action.",
                    target=_target("command", notice_id, "workflow"),
                    sort_rank=80,
                )
            )

    actions = sorted(actions, key=lambda item: item["_sort"])
    for item in actions:
        item.pop("_sort", None)
    for item in recent:
        item.pop("_sort", None)
    metrics["totalActions"] = len(actions)
    for item in actions:
        key = f"{item['risk']}Count"
        if key in metrics:
            metrics[key] += 1
    risk_level = "low"
    if metrics["criticalCount"]:
        risk_level = "critical"
    elif metrics["highCount"]:
        risk_level = "high"
    elif metrics["mediumCount"]:
        risk_level = "medium"
    if not unique:
        explanation = "No active portfolio items are currently in the report."
    elif risk_level == "low":
        explanation = "No urgent pursuit blockers are currently visible."
    else:
        parts = []
        for risk in ("critical", "high", "medium"):
            count = metrics[f"{risk}Count"]
            if count:
                parts.append(f"{count} {risk}")
        explanation = f"{', '.join(parts)} action(s) need attention across {len(unique)} opportunity record(s)."
    return {
        "metrics": metrics,
        "doToday": actions,
        "portfolioHealth": {"riskLevel": risk_level, "explanation": explanation},
        "recentIntelligence": sorted(recent, key=lambda item: (RISK_WEIGHT.get(item.get("risk"), 9), item.get("noticeId") or "")),
    }
