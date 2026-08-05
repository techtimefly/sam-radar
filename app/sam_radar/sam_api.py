from __future__ import annotations

import datetime as dt
import json
import subprocess
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .config import BusinessProfile

BASE_URL = "https://api.sam.gov/opportunities/v2/search"
PTYPES = ["o", "k", "r", "p", "s"]


@dataclass(slots=True)
class SearchWindow:
    posted_from: str
    posted_to: str


def mmddyyyy(day: dt.date) -> str:
    return day.strftime("%m/%d/%Y")


def window_for_days(days: int) -> SearchWindow:
    today = dt.datetime.now(dt.UTC).date()
    return SearchWindow(posted_from=mmddyyyy(today - dt.timedelta(days=days)), posted_to=mmddyyyy(today))


def build_queries(api_key: str, profile: BusinessProfile, window: SearchWindow, limit: int) -> list[dict[str, str]]:
    base = {"api_key": api_key, "postedFrom": window.posted_from, "postedTo": window.posted_to, "limit": str(limit)}
    queries: list[dict[str, str]] = []
    queries += [base | {"ncode": code} for code in profile.naics_all]
    queries += [base | {"ccode": code} for code in profile.psc]
    queries += [base | {"typeOfSetAside": code} for code in profile.set_asides]
    queries += [base | {"ptype": code} for code in PTYPES]
    return queries


def fetch_json(params: dict[str, str], timeout: int = 8) -> dict[str, Any]:
    url = BASE_URL + "?" + urllib.parse.urlencode(params)
    cmd = [
        "curl",
        "-fsS",
        "--connect-timeout",
        str(max(2, min(timeout, 10))),
        "--max-time",
        str(max(3, timeout)),
        "-H",
        "Accept: application/json",
        "-H",
        "User-Agent: sam-radar/0.1",
        url,
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout + 3)
    return json.loads(result.stdout)
