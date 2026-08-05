# GitHub Publication

Use this checklist when turning the local SAM Radar folder into a public repository.

## Before Publishing

```bash
git status --short
python -m py_compile app/sam_radar/*.py app/sam_radar/notifications/*.py generate_report.py server.py report_builder.py scripts/check_private_context.py
PRIVATE_CONTEXT_MARKERS='your-company-name,private-profile-marker' python scripts/check_private_context.py
pytest -q
docker build -t sam-radar:test .
```

Confirm these files are not tracked:

```bash
git check-ignore .env config/business.yaml data/sam-radar.sqlite3 reports/latest.html
```

## Create The Repository

Create a public GitHub repository, then push this local repo:

```bash
git remote add origin git@github.com:OWNER/sam-radar.git
git push -u origin main
```

## Seed Issues

After pushing, install and authenticate GitHub CLI, then run:

```bash
python scripts/create_github_issues.py OWNER/sam-radar
```

The script creates labels, milestones, and issues from `docs/github-backlog.md`.

## Privacy Boundary

The public repo should contain only generic examples. Keep real business context, API keys, notification tokens, generated reports, and SQLite data local.
