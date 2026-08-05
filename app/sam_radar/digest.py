from __future__ import annotations

from .config import BusinessProfile, Settings
from .reports import fit_reason, format_local_datetime, format_posted_date, next_action, sam_url


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def build_digest(
    matches: list[dict],
    profile: BusinessProfile,
    settings: Settings,
    posted_from: str,
    posted_to: str,
    report_url: str,
    *,
    max_items: int = 7,
    max_total: int = 7000,
    max_item: int = 800,
) -> str:
    selected = matches[:max_items]
    lines = [
        f"SAM Radar - {profile.display_name}",
        f"Window: {posted_from} to {posted_to}",
        f"New unseen matches: {len(matches)}",
        f"Full report: {report_url}",
        "",
    ]
    for idx, opp in enumerate(selected, 1):
        block = "\n".join(
            [
                f"{idx}. {opp.get('recommendation', 'Review').upper()} - {opp.get('title', '(untitled)')}",
                f"Agency: {opp.get('organization') or 'n/a'}",
                f"Posted: {format_posted_date(opp.get('postedDate') or '')} | Due: {format_local_datetime(opp.get('responseDeadline') or '', settings.timezone)} | Score: {opp.get('score', 'n/a')}",
                f"NAICS: {opp.get('naicsCode') or 'n/a'} | PSC: {opp.get('classificationCode') or 'n/a'} | Set-aside: {opp.get('setAsideCode') or opp.get('setAside') or 'n/a'}",
                f"Why: {fit_reason(profile, opp)}",
                f"Next: {next_action(opp)}",
                f"URL: {sam_url(opp) or 'n/a'}",
            ]
        )
        lines.append(truncate(block, max_item))
        lines.append("")
    if len(matches) > len(selected):
        lines.append(f"Additional matches available in the full report: {report_url}")
    return truncate("\n".join(lines).strip(), max_total)


def build_no_new_digest(profile: BusinessProfile, posted_from: str, posted_to: str, report_url: str) -> str:
    return "\n".join(
        [
            f"SAM Radar - {profile.display_name}",
            f"Window: {posted_from} to {posted_to}",
            "No new unseen matches.",
            f"Full report: {report_url}",
        ]
    )
