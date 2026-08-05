#!/usr/bin/env python3
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "data", "reports"}
IGNORED_NAMES = {".env", "business.yaml", "check_private_context.py"}
SECRET_ASSIGNMENT = re.compile(r"(?m)^(?:export\s+)?[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*=[ \t]*[^\s#][^\n]*")
ALLOWLIST = {".env.example", "README.md"}


def private_markers() -> list[str]:
    raw = os.getenv("PRIVATE_CONTEXT_MARKERS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    return (
        path.name in IGNORED_NAMES
        or any(part in IGNORED_PARTS for part in rel.parts)
        or any(part.endswith(".egg-info") for part in rel.parts)
    )


def main() -> int:
    findings: list[str] = []
    markers = private_markers()
    for path in ROOT.rglob("*"):
        if not path.is_file() or should_skip(path):
            continue
        text = path.read_text(errors="ignore")
        rel = str(path.relative_to(ROOT))
        for marker in markers:
            if marker in text:
                findings.append(f"{rel}: private marker {marker!r}")
        if path.name not in ALLOWLIST:
            for match in SECRET_ASSIGNMENT.finditer(text):
                value = match.group(0).split("=", 1)[1].strip()
                if value:
                    findings.append(f"{rel}: possible secret assignment")
    if findings:
        print("Private context / secret scan failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Private context / secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
