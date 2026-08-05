from __future__ import annotations

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from typing import Any

from .config import BusinessProfile
from .sam_api import build_queries, fetch_json, window_for_days

DEFAULT_EXCLUDED_TYPES = {"Award Notice", "Justification"}
DEFAULT_LOW_VALUE_NOTICE_KEYWORDS = [
    "intent to sole source",
    "notice of intent to sole source",
    "sole source",
    "notification of award",
    "bridge action",
]
VETERAN_SET_ASIDES = {"SDVOSBC", "SDVOSBS", "VSA", "VSS"}


def normalized_text(opp: dict[str, Any]) -> str:
    return " ".join(
        str(opp.get(k) or "")
        for k in [
            "title",
            "type",
            "baseType",
            "setAside",
            "typeOfSetAsideDescription",
            "naicsCode",
            "classificationCode",
            "fullParentPathName",
        ]
    ).lower()


def keyword_matches(text: str, keywords: list[str]) -> list[str]:
    text = text.lower()
    matches: list[str] = []
    for keyword in keywords:
        keyword = keyword.lower().strip()
        if not keyword:
            continue
        pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            matches.append(keyword)
    return matches


def title_key(opp: dict[str, Any]) -> str:
    title = re.sub(r"\s+", " ", str(opp.get("title") or "").lower()).strip()
    deadline = str(opp.get("responseDeadLine") or opp.get("responseDeadline") or opp.get("reponseDeadLine") or "")
    return f"{title}|{deadline}"


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


def is_expired(opp: dict[str, Any], now: dt.datetime) -> bool:
    deadline = parse_deadline(str(opp.get("responseDeadLine") or opp.get("responseDeadline") or opp.get("reponseDeadLine") or ""))
    return bool(deadline and deadline < now)


def is_relevant_anchor(profile: BusinessProfile, naics: str, psc: str, matches: list[str]) -> bool:
    has_keyword = bool(matches)
    if naics in profile.naics_all:
        return True
    if psc in profile.psc:
        return True
    return has_keyword


def score_opp(profile: BusinessProfile, opp: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    text = normalized_text(opp)
    naics = str(opp.get("naicsCode") or "").strip()
    psc = str(opp.get("classificationCode") or "").strip()
    set_aside = str(opp.get("typeOfSetAside") or opp.get("setAsideCode") or "").strip()
    opp_type = str(opp.get("type") or "").strip()
    matches = keyword_matches(text, profile.strong_keywords)

    if psc in profile.psc:
        score += 4
        reasons.append(f"PSC {psc}")
    if naics in profile.naics_all:
        score += 4
        reasons.append(f"NAICS {naics}")
    if set_aside in VETERAN_SET_ASIDES:
        score += 3
        reasons.append(f"veteran set-aside {set_aside}")
    elif set_aside in profile.set_asides:
        score += 1
        reasons.append(f"set-aside {set_aside}")
    if opp_type in {"Solicitation", "Combined Synopsis/Solicitation"}:
        score += 1
        reasons.append(opp_type)
    elif opp_type == "Sources Sought":
        score += 2
        reasons.append("early market research")
    elif opp_type == "Special Notice":
        score -= 2
        reasons.append(opp_type)
    if matches:
        score += min(5, len(matches) * 2)
        reasons.append("keywords: " + ", ".join(matches[:5]))
    return score, reasons


def normalize_opp(opp: dict[str, Any], score: int, reasons: list[str]) -> dict[str, Any]:
    return {
        "noticeId": opp.get("noticeId") or opp.get("noticeid"),
        "title": opp.get("title") or "(untitled)",
        "type": opp.get("type") or "",
        "postedDate": opp.get("postedDate") or "",
        "responseDeadline": opp.get("responseDeadLine") or opp.get("reponseDeadLine") or opp.get("responseDeadline") or "",
        "naicsCode": opp.get("naicsCode") or "",
        "classificationCode": opp.get("classificationCode") or "",
        "setAside": opp.get("typeOfSetAsideDescription") or opp.get("setAside") or "",
        "setAsideCode": opp.get("typeOfSetAside") or opp.get("setAsideCode") or "",
        "organization": opp.get("fullParentPathName")
        or " / ".join(filter(None, [opp.get("department"), opp.get("subTier"), opp.get("office")]))
        or "",
        "uiLink": opp.get("uiLink") or "",
        "descriptionUrl": opp.get("description") or "",
        "score": score,
        "reasons": reasons,
        "recommendation": "Pursue" if score >= 10 else "Monitor" if score >= 7 else "Review",
    }


def search_opportunities(
    api_key: str,
    profile: BusinessProfile,
    *,
    days: int = 7,
    limit: int = 10,
    max_results: int = 20,
    minimum_score: int = 6,
    timeout: int = 8,
    global_timeout: int = 90,
    max_workers: int = 4,
    include_expired: bool = False,
    include_awards: bool = False,
    include_notices: bool = False,
    loose: bool = False,
) -> dict[str, Any]:
    window = window_for_days(days)
    queries = build_queries(api_key, profile, window, limit)
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    matches: list[dict[str, Any]] = []
    errors: list[str] = []
    now = dt.datetime.now(dt.UTC)

    def run_query(params: dict[str, str]):
        redacted = {k: v for k, v in params.items() if k != "api_key"}
        try:
            return redacted, fetch_json(params, timeout), None
        except Exception as exc:  # noqa: BLE001
            return redacted, None, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        future_map = {executor.submit(run_query, params): params for params in queries}
        try:
            for future in as_completed(future_map, timeout=global_timeout):
                redacted, payload, error = future.result()
                if error:
                    errors.append(f"{redacted}: {error}")
                    continue
                for opp in (payload or {}).get("opportunitiesData") or []:
                    notice_id = opp.get("noticeId") or opp.get("noticeid")
                    if not notice_id or notice_id in seen_ids:
                        continue
                    dedupe_title = title_key(opp)
                    if dedupe_title in seen_titles:
                        continue
                    text = normalized_text(opp)
                    if keyword_matches(text, profile.exclude_keywords):
                        continue
                    if not include_notices and keyword_matches(text, DEFAULT_LOW_VALUE_NOTICE_KEYWORDS):
                        continue
                    opp_type = str(opp.get("type") or "").strip()
                    if not include_awards and opp_type in DEFAULT_EXCLUDED_TYPES:
                        continue
                    if not include_expired and is_expired(opp, now):
                        continue
                    score, reasons = score_opp(profile, opp)
                    naics = str(opp.get("naicsCode") or "").strip()
                    psc = str(opp.get("classificationCode") or "").strip()
                    matched_keywords = keyword_matches(text, profile.strong_keywords)
                    if not loose and not is_relevant_anchor(profile, naics, psc, matched_keywords):
                        continue
                    if score < minimum_score:
                        continue
                    seen_ids.add(notice_id)
                    seen_titles.add(dedupe_title)
                    matches.append(normalize_opp(opp, score, reasons))
        except TimeoutError:
            errors.append(f"Global timeout after {global_timeout}s; returned partial results")
            for future in future_map:
                future.cancel()

    matches.sort(key=lambda item: item["score"], reverse=True)
    return {"postedFrom": window.posted_from, "postedTo": window.posted_to, "matches": matches[:max_results], "errors": errors}
