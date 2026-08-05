#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BACKLOG = Path(__file__).resolve().parents[1] / "docs" / "github-backlog.md"

LABELS = [
    "type:feature",
    "type:bug",
    "type:docs",
    "type:infra",
    "type:test",
    "area:sam-api",
    "area:scoring",
    "area:web",
    "area:docker",
    "area:notifications",
    "area:storage",
    "priority:p0",
    "priority:p1",
    "priority:p2",
    "agent:ready",
    "agent:blocked",
]


@dataclass(frozen=True)
class Issue:
    milestone: str
    title: str
    body: str


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, text=True, capture_output=True)


def parse_backlog(text: str) -> tuple[list[str], list[Issue]]:
    milestones: list[str] = []
    issues: list[Issue] = []
    blocks = re.split(r"^### ", text, flags=re.MULTILINE)
    for block in blocks[1:]:
        lines = block.strip().splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        if not title.startswith("v"):
            continue
        milestones.append(title)
        issue_lines = []
        in_issues = False
        for line in lines[1:]:
            if line.strip() == "Issues:":
                in_issues = True
                continue
            if in_issues and line.startswith("### "):
                break
            if in_issues:
                match = re.match(r"\d+\.\s+(.+)", line.strip())
                if match:
                    issue_lines.append(match.group(1))
        for item in issue_lines:
            issues.append(
                Issue(
                    milestone=title,
                    title=item,
                    body=(
                        f"## Goal\n\nTrack `{item}` for the `{title}` milestone.\n\n"
                        "## Acceptance Criteria\n\n"
                        "- [ ] Behavior implemented\n"
                        "- [ ] Tests or verification added\n"
                        "- [ ] Docs updated when user-facing\n"
                        "- [ ] No secrets or private business context committed\n"
                    ),
                )
            )
    return milestones, issues


def ensure_gh() -> None:
    if not shutil.which("gh"):
        raise SystemExit("GitHub CLI `gh` is required. Install and authenticate it, then rerun this script.")


def create_label(repo: str, label: str) -> None:
    color = "ededed"
    if label.startswith("type:"):
        color = "0e8a16"
    elif label.startswith("area:"):
        color = "1d76db"
    elif label.startswith("priority:"):
        color = "d93f0b"
    elif label.startswith("agent:"):
        color = "5319e7"
    run(["gh", "label", "create", label, "--repo", repo, "--color", color], check=False)


def create_milestone(repo: str, milestone: str) -> None:
    run(["gh", "api", f"repos/{repo}/milestones", "-f", f"title={milestone}"], check=False)


def issue_exists(repo: str, title: str) -> bool:
    result = run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--search",
            f"{title} in:title",
            "--json",
            "title",
        ],
        check=False,
    )
    return result.returncode == 0 and f'"title":"{title}"' in result.stdout


def create_issue(repo: str, issue: Issue) -> None:
    if issue_exists(repo, issue.title):
        print(f"skip existing issue: {issue.title}")
        return
    run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            issue.title,
            "--body",
            issue.body,
            "--milestone",
            issue.milestone,
            "--label",
            "agent:ready",
        ]
    )
    print(f"created issue: {issue.title}")


def main() -> int:
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        print("Usage: scripts/create_github_issues.py OWNER/REPO", file=sys.stderr)
        return 2
    ensure_gh()
    repo = sys.argv[1]
    milestones, issues = parse_backlog(BACKLOG.read_text())
    for label in LABELS:
        create_label(repo, label)
    for milestone in milestones:
        create_milestone(repo, milestone)
    for issue in issues:
        create_issue(repo, issue)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
