from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

TRUSTED_DESCRIPTION_HOSTS = {"api.sam.gov", "api-alpha.sam.gov"}
TAG_BREAK_RE = re.compile(r"</?(?:p|div|br|li|tr|h[1-6])\b[^>]*>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def is_trusted_description_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "https" and parsed.netloc.lower() in TRUSTED_DESCRIPTION_HOSTS


def description_url_with_key(url: str, api_key: str) -> str:
    if not is_trusted_description_url(url):
        raise ValueError("description URL is not a trusted SAM.gov API URL")
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("api_key", api_key)
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def clean_description_html(value: str) -> str:
    text = SCRIPT_STYLE_RE.sub(" ", value or "")
    text = TAG_BREAK_RE.sub("\n", text)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def format_description(value: str, *, max_chars: int = 6000, max_paragraphs: int = 10) -> dict[str, Any]:
    text = clean_description_html(value)[:max_chars].strip()
    paragraphs = [part.strip() for part in re.split(r"\n{1,}", text) if part.strip()]
    if len(paragraphs) == 1 and len(paragraphs[0]) > 900:
        paragraphs = [part.strip() for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", paragraphs[0]) if part.strip()]
    paragraphs = paragraphs[:max_paragraphs]
    return {"available": bool(paragraphs), "text": "\n".join(paragraphs), "paragraphs": paragraphs}


def extract_description_body(value: str) -> str:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return value
    if isinstance(payload, dict):
        description = payload.get("description")
        if isinstance(description, str):
            return description
        for key in ("descriptionText", "text", "content"):
            body = payload.get(key)
            if isinstance(body, str):
                return body
    return value


@contextmanager
def prefer_ipv4() -> Any:
    original_getaddrinfo = socket.getaddrinfo

    def getaddrinfo_ipv4(host: str, port: int, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0):
        return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = getaddrinfo_ipv4
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo


def fetch_description(url: str, api_key: str, *, timeout: int = 8) -> str:
    request = urllib.request.Request(
        description_url_with_key(url, api_key),
        headers={"Accept": "application/json", "User-Agent": "curl/8.0 sam-radar/0.1"},
    )
    try:
        with prefer_ipv4(), urllib.request.urlopen(request, timeout=timeout) as response:
            return extract_description_body(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:180]
        raise RuntimeError(f"description HTTP {exc.code}: {body}") from exc


def cache_path(data_dir: Path, notice_id: str) -> Path:
    digest = hashlib.sha1(str(notice_id).encode("utf-8")).hexdigest()[:16]
    return data_dir / "descriptions" / f"{digest}.json"


def read_cached_description(data_dir: Path, notice_id: str) -> dict[str, Any] | None:
    path = cache_path(data_dir, notice_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None


def write_cached_description(data_dir: Path, notice_id: str, payload: dict[str, Any]) -> None:
    path = cache_path(data_dir, notice_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def attach_description(opp: dict[str, Any], payload: dict[str, Any]) -> None:
    opp["descriptionStatus"] = payload.get("status", "available" if payload.get("available") else "unavailable")
    opp["descriptionFetchedAt"] = payload.get("fetchedAt", "")
    opp["descriptionText"] = payload.get("text", "")
    opp["descriptionParagraphs"] = payload.get("paragraphs", [])


def enrich_descriptions(
    matches: list[dict[str, Any]],
    *,
    api_key: str,
    data_dir: Path,
    enabled: bool = True,
    limit: int = 10,
    timeout: int = 8,
) -> list[str]:
    errors: list[str] = []
    if not enabled or not api_key or limit <= 0:
        return errors
    fetched = 0
    for opp in matches:
        if fetched >= limit:
            break
        notice_id = str(opp.get("noticeId") or "")
        url = str(opp.get("descriptionUrl") or "")
        if not notice_id or not url:
            opp.setdefault("descriptionStatus", "unavailable")
            continue
        if not is_trusted_description_url(url):
            attach_description(opp, {"status": "untrusted-url", "paragraphs": [], "text": ""})
            continue
        cached = read_cached_description(data_dir, notice_id)
        if cached:
            attach_description(opp, cached)
            fetched += 1
            continue
        try:
            formatted = format_description(fetch_description(url, api_key, timeout=timeout))
            payload = {
                **formatted,
                "noticeId": notice_id,
                "sourceUrl": url,
                "status": "available" if formatted["available"] else "empty",
                "fetchedAt": dt.datetime.now(dt.UTC).isoformat(),
            }
            write_cached_description(data_dir, notice_id, payload)
            attach_description(opp, payload)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{notice_id}: {exc}")
            attach_description(opp, {"status": "error", "paragraphs": [], "text": ""})
        fetched += 1
    return errors
