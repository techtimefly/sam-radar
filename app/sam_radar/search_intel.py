from __future__ import annotations

import re
from typing import Any

NAICS_REFERENCE = [
    {"code": "541512", "title": "Computer Systems Design Services", "description": "Planning and designing computer systems that integrate hardware, software, and communications technologies.", "terms": ["systems design", "cloud", "infrastructure", "architecture", "integration", "devsecops", "cicd"]},
    {"code": "541511", "title": "Custom Computer Programming Services", "description": "Writing, modifying, testing, and supporting software to meet customer requirements.", "terms": ["software", "application", "development", "programming", "automation", "api"]},
    {"code": "541519", "title": "Other Computer Related Services", "description": "Computer related services such as disaster recovery, installation, and technology support not elsewhere classified.", "terms": ["it support", "operations", "migration", "help desk", "systems"]},
    {"code": "541690", "title": "Other Scientific and Technical Consulting Services", "description": "Scientific and technical consulting services not classified elsewhere, including technical advisory and compliance support.", "terms": ["technical consulting", "compliance", "advisory", "security", "risk", "cmmc"]},
    {"code": "541330", "title": "Engineering Services", "description": "Engineering design, development, and technical services for systems and facilities.", "terms": ["engineering", "systems engineering", "technical support"]},
    {"code": "541715", "title": "Research and Development in Nanotechnology", "description": "R&D services in nanotechnology and advanced technical research.", "terms": ["research", "development", "prototype"]},
    {"code": "611420", "title": "Computer Training", "description": "Training in computer programming, software, systems, cybersecurity, and related technologies.", "terms": ["training", "enablement", "course", "curriculum", "upskill", "technical training"]},
    {"code": "513210", "title": "Software Publishers", "description": "Publishing and licensing software products and related updates.", "terms": ["software product", "license", "subscription", "saas"]},
    {"code": "541611", "title": "Administrative Management and General Management Consulting Services", "description": "Management consulting and process improvement services.", "terms": ["process", "management consulting", "program support"]},
    {"code": "541990", "title": "All Other Professional, Scientific, and Technical Services", "description": "Professional, scientific, and technical services not elsewhere classified.", "terms": ["professional services", "technical services", "specialized"]},
]

PSC_REFERENCE = [
    {"code": "DJ01", "title": "IT and Telecom - Security and Compliance Support", "description": "Security, compliance, cyber, and information assurance support.", "terms": ["security", "compliance", "cmmc", "cybersecurity", "ato", "risk"]},
    {"code": "DA01", "title": "IT and Telecom - Software Application Development Support", "description": "Software application development, modernization, and support services.", "terms": ["software", "application", "development", "modernization", "api"]},
    {"code": "DB10", "title": "IT and Telecom - Compute as a Service", "description": "Cloud, compute, hosting, and infrastructure services.", "terms": ["cloud", "compute", "infrastructure", "hosting"]},
    {"code": "DC01", "title": "IT and Telecom - Data Center Support", "description": "Data center, infrastructure, operations, and platform support.", "terms": ["data center", "operations", "platform", "server"]},
    {"code": "R425", "title": "Support - Professional: Engineering/Technical", "description": "Engineering and technical professional support services.", "terms": ["technical", "engineering", "professional support"]},
    {"code": "U012", "title": "Education/Training - Information Technology", "description": "Information technology training and education services.", "terms": ["training", "education", "computer training", "technical training"]},
    {"code": "D307", "title": "IT Strategy and Architecture", "description": "IT strategy, architecture, and planning support.", "terms": ["architecture", "strategy", "planning", "enterprise"]},
]

SET_ASIDES = [
    {"code": "SBA", "label": "Total Small Business", "description": "Set aside for small business concerns."},
    {"code": "SDVOSBC", "label": "Service-Disabled Veteran-Owned Small Business", "description": "Set aside for eligible SDVOSB firms."},
    {"code": "VSA", "label": "Veteran-Owned Small Business", "description": "Veteran-owned small business set-aside."},
    {"code": "8A", "label": "8(a)", "description": "8(a) business development program set-aside."},
    {"code": "HZC", "label": "HUBZone", "description": "HUBZone small business set-aside."},
    {"code": "WOSB", "label": "Women-Owned Small Business", "description": "Women-owned small business program set-aside."},
    {"code": "EDWOSB", "label": "Economically Disadvantaged WOSB", "description": "Economically disadvantaged women-owned small business set-aside."},
]

DEFAULT_KEYWORDS = {
    "devsecops": ["DevSecOps", "CI/CD", "secure software", "security automation", "CMMC", "ATO"],
    "security": ["cybersecurity", "CMMC", "compliance", "risk", "ATO", "security controls"],
    "software": ["software modernization", "application development", "API", "automation", "cloud native"],
    "training": ["technical training", "computer training", "enablement", "curriculum", "workshop"],
    "infrastructure": ["cloud", "infrastructure", "architecture", "migration", "systems integration"],
}

EXCLUSION_SUGGESTIONS = ["construction", "janitorial", "medical staffing", "food service", "vehicle maintenance", "groundskeeping"]


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def search_reference(kind: str, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
    source = NAICS_REFERENCE if kind == "naics" else PSC_REFERENCE if kind == "psc" else []
    q = normalize(query)
    scored = []
    for item in source:
        haystack = normalize(" ".join([item["code"], item["title"], item["description"], " ".join(item.get("terms", []))]))
        score = 0
        if not q:
            score = 1
        elif q == normalize(item["code"]):
            score = 100
        else:
            for term in q.split():
                if term in haystack:
                    score += 5
            if q in haystack:
                score += 25
        if score:
            scored.append((score, item))
    scored.sort(key=lambda row: (-row[0], row[1]["code"]))
    return [{**item, "kind": kind, "score": score} for score, item in scored[:limit]]


def set_asides_for_status(statuses: list[str] | None = None) -> list[dict[str, Any]]:
    text = normalize(" ".join(statuses or []))
    results = []
    for item in SET_ASIDES:
        eligible = False
        if item["code"] == "SBA":
            eligible = "sba" in text or "small" in text or not statuses
        elif item["code"] == "SDVOSBC":
            eligible = "sdvosb" in text or "service-disabled" in text
        elif item["code"] == "VSA":
            eligible = "vosb" in text or "veteran" in text
        elif item["code"] in {"8A", "HZC", "WOSB", "EDWOSB"}:
            eligible = item["label"].lower() in text or item["code"].lower() in text
        results.append({**item, "eligibleHint": eligible})
    return results


def suggest_profiles(text: str, statuses: list[str] | None = None) -> dict[str, Any]:
    lower = normalize(text)
    naics = []
    psc = []
    keywords: list[str] = []
    for item in NAICS_REFERENCE:
        if any(term in lower for term in item.get("terms", [])) or item["code"] in lower:
            naics.append(item)
    for item in PSC_REFERENCE:
        if any(term in lower for term in item.get("terms", [])) or item["code"].lower() in lower:
            psc.append(item)
    for key, values in DEFAULT_KEYWORDS.items():
        if key in lower or any(normalize(v) in lower for v in values):
            keywords.extend(values)
    if not naics:
        naics = [NAICS_REFERENCE[0], NAICS_REFERENCE[1], NAICS_REFERENCE[3]]
    if not psc:
        psc = [PSC_REFERENCE[0], PSC_REFERENCE[1]]
    if not keywords:
        keywords = DEFAULT_KEYWORDS["security"] + DEFAULT_KEYWORDS["software"][:2]
    seen = set()
    unique_keywords = []
    for keyword in keywords:
        k = normalize(keyword)
        if k not in seen:
            seen.add(k)
            unique_keywords.append(keyword)
    eligible_set_asides = [item for item in set_asides_for_status(statuses) if item["eligibleHint"]]
    profile = {
        "name": "Suggested Capability Search",
        "description": "Draft search profile generated from capability text. Review before activating.",
        "keywords": unique_keywords[:10],
        "naics": [item["code"] for item in naics[:6]],
        "psc": [item["code"] for item in psc[:6]],
        "setAsides": [item["code"] for item in eligible_set_asides[:4]],
        "exclusions": EXCLUSION_SUGGESTIONS,
        "noticeTypes": ["o", "p", "r"],
        "days": 7,
        "limit": 25,
        "active": False,
        "rationale": [
            "Suggested from deterministic reference matching; no LLM required.",
            "NAICS/PSC codes are based on capability terms and should be reviewed before use.",
            "Set-asides are hinted from configured socio-economic status when available.",
        ],
    }
    return {"profile": profile, "naicsMatches": naics[:6], "pscMatches": psc[:6], "setAsideMatches": eligible_set_asides}
