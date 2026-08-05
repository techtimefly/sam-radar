# GitHub Backlog

Use these milestones and issues when publishing the public repository.

## Milestones

### v0.1 - Public MVP

Goal: Docker-deployable SAM Radar with configurable business context, SAM.gov search, scoring, HTML/JSON reports, refresh button, and private-context guardrails.

Issues:

1. Repo foundation and CI
2. Config loader for `.env` and `config/business.yaml`
3. SAM.gov API client
4. Deterministic scoring engine
5. SQLite seen registry
6. HTML/JSON report generation
7. Web server with `/healthz` and `POST /api/refresh`
8. Dockerfile and Docker Compose
9. README quickstart and homelab nginx docs
10. Private-context scanner

### v0.2 - Notifications And Scheduler

Goal: scheduled refreshes and configurable notification digests.

Status: core scheduler and Slack/Telegram adapters are implemented locally; future issues should harden previews, snapshots, and richer policies.

Issues:

1. Add APScheduler daily refresh
2. Add Slack webhook digest with `APP_BASE_URL` full-report link
3. Add Telegram build/deploy notification channel
4. Add notification preview/test endpoint
5. Add no-new-matches policy
6. Add snapshot tests for notification messages

### v0.3 - Pursuit Workflow

Goal: turn the dashboard into a lightweight capture workflow.

Status: status model, notes, authenticated status API, and audit timestamps are implemented locally. Remaining issues should focus on exports, richer filtering, and team workflow polish.

Issues:

1. Add status model: New, Reviewing, Pursue, Teaming, No-Bid, Submitted, Archived
2. Add notes per opportunity
3. Add status update API protected by `APP_WRITE_TOKEN`
4. Add watchlist/status filters
5. Add CSV export
6. Add audit timestamp display in report cards

### v0.7 - Automated Review and Release Gate

Goal: close the no-human-in-the-loop delivery loop with automated code quality, security, privacy, deployment, and issue/milestone closure verification. Human review is not required when all automated gates pass; Telegram remains the notification channel for progress, failures, and completion.

Issues:

1. Run final automated code and security review
2. Verify all implementation milestones and issues are closed or explicitly resolved
3. Verify local deployment health and generated report assets
4. Verify no private business context, API keys, tokens, or local data are tracked
5. Publish final automation completion summary

### v1.0 - Stable Self-Hosted Release

Goal: stable Docker release suitable for homelab and small-team use.

Issues:

1. Versioned releases
2. Container publish workflow
3. Backup/restore docs
4. Reverse proxy hardening docs
5. Upgrade guide

## Suggested Labels

- `type:feature`
- `type:bug`
- `type:docs`
- `type:infra`
- `type:test`
- `area:sam-api`
- `area:scoring`
- `area:web`
- `area:docker`
- `area:notifications`
- `area:storage`
- `priority:p0`
- `priority:p1`
- `priority:p2`
- `agent:ready`
- `agent:blocked`

## Publishing Checklist

- [ ] Create GitHub repo
- [ ] Push local `main`
- [ ] Create milestones above
- [ ] Create v0.1 issues from this document
- [ ] Confirm CI passes
- [ ] Confirm no private business context is tracked
