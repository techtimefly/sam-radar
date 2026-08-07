# SAM Radar

SAM Radar is a self-hosted SAM.gov opportunity intelligence dashboard for small businesses, consultants, capture teams, and GovCon operators.

Define your business profile once. SAM Radar searches SAM.gov, scores opportunities against your capabilities, filters noise, generates polished reports, and can send notification digests.

## What It Does

- Searches the SAM.gov opportunities API
- Scores matches against your local business context
- Filters expired, duplicate, and low-actionability noise
- Generates a polished HTML report and JSON export
- Includes a built-in design-system showcase under Resources
- Normalizes deadlines to your configured timezone
- Provides a browser refresh button
- Adds a primary Pursuit Command Center with deterministic Do Today, Portfolio Health, and Recent Intelligence queues
- Adds a manual SAM search workspace that does not overwrite generated reports
- Prevents manually tracking opportunities already in the report or local tracking store
- Stores proposal evidence and citations in SQLite with source excerpts, claims, confidence, and human verification state
- Captures immutable SAM.gov revision snapshots on refresh and flags material amendment changes
- Keeps local seen/history data out of Git
- Supports Docker and Docker Compose
- Separates listen address from public `APP_BASE_URL` for reverse proxies
- Includes GitHub publication and homelab deployment guides

## Quick Start

```bash
git clone https://github.com/YOUR-ORG/sam-radar.git
cd sam-radar
cp .env.example .env
cp config/business.example.yaml config/business.yaml
```

Edit `.env` and set:

```env
SAM_GOV_API_KEY=
APP_BASE_URL=http://localhost:8066
```

Edit `config/business.yaml` for your company. This file is ignored by Git.

Run:

```bash
docker compose up -d --build
```

Open:

```text
http://localhost:8066
```

## Private Context Boundary

Do not commit your real business context or secrets. The repo intentionally ignores:

- `.env`
- `config/business.yaml`
- `config/*.local.yaml`
- `data/*`
- `reports/*`

Only generic examples are intended for GitHub.

## Configuration

`.env` controls runtime settings and secrets.

```env
SAM_GOV_API_KEY=
APP_BASE_URL=http://localhost:8066
SAM_RADAR_HOST=0.0.0.0
SAM_RADAR_PORT=8066
TIMEZONE=America/Denver
# Generate with: sam-radar generate-token
APP_WRITE_TOKEN=
BUSINESS_PROFILE=config/business.yaml
DATA_DIR=data
REPORTS_DIR=reports
SEARCH_DAYS=7
REPORT_LIMIT=20
```

`config/business.yaml` controls fit context. Start from `config/business.example.yaml`.

## Reverse Proxy / Homelab

For a homelab site such as:

```text
https://sam-radar.lan
```

Use:

```env
APP_BASE_URL=https://sam-radar.lan
SAM_RADAR_HOST=0.0.0.0
SAM_RADAR_PORT=8066
```

Example nginx config:

```nginx
server {
    listen 443 ssl;
    server_name sam-radar.lan;

    client_max_body_size 25m;

    location / {
        proxy_pass http://127.0.0.1:8066;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

`APP_BASE_URL` is what appears in Slack/Telegram/report links. `SAM_RADAR_HOST` and `SAM_RADAR_PORT` only control where the app listens.

## Pursuit Status Controls

The dashboard can save lightweight pursuit workflow state per opportunity: `new`, `reviewing`, `pursue`, `teaming`, `monitor`, `no-bid`, `submitted`, and `archived`. The pipeline includes owner, priority, next action, follow-up date, decision reason, structured no-bid reason, document review links, event history, follow-up queues, pipeline metrics, an interactive board, archive/unarchive controls, manual SAM search, and GovCon resource links.

The generated report now uses centralized design tokens for typography, spacing, surfaces, radii, shadows, status colors, focus states, and responsive density. Open Resources in the report to see the component showcase for buttons, forms, cards, tables, dialogs, badges, and feedback states.

Manual searches call SAM.gov on demand and render temporary results in the browser. They do not rewrite `reports/latest.html`, `reports/latest.json`, or historical report files until you click Track.

External opportunity intake lets you add a contract found outside SAM.gov, or keep working when the SAM.gov API limit has been reached. Add a title plus source URL, source name, agency/customer, or context note; SAM Radar generates a local `manual-*` ID, rebuilds the latest report from cached data, labels the item as manual, and rejects duplicate adds by source URL or generated fingerprint.

Clicking Track or Add Opportunity requires `APP_WRITE_TOKEN`; SAM Radar rejects manual adds when the notice already appears in the current report, seen history, saved workflow, or manual tracking table.

Status and notes writes require `APP_WRITE_TOKEN`. Generate one with `sam-radar generate-token` or `scripts/generate-token.sh`, save it in `.env`, then use the dashboard Unlock control to store it in your browser. This is separate from your SAM.gov API key. Leave `APP_WRITE_TOKEN` blank to disable browser writes.

## Pursuit Command Center

The generated report opens on the Pursuit Command Center. It aggregates overdue follow-ups, proposal deadlines, unread material amendments, stale or unverified evidence, compliance gaps, high-fit unassigned opportunities, and assigned pursuits missing next actions.

Priority is deterministic: `critical`, `high`, `medium`, then `low`, with stable notice/action ordering. `submitted`, `no-bid`, and `archived` are terminal in the Command Center and do not produce actions, recent intelligence, or active-assignment counts. Quick actions are notice-scoped writes protected by `APP_WRITE_TOKEN`: assign owner and set follow-up use `/api/status/{noticeId}`, amendment review uses `/api/amendments/mark-reviewed`, evidence verification uses `/api/evidence/verify` with `noticeId`, proposal stage advance uses `/api/proposals/stage`, and no-bid uses `/api/status/{noticeId}` after confirmation. Opening the compliance matrix is navigation only and never verifies or mutates rows.

Deep links open the proposal workspace, opportunity detail, amendment panel, evidence surface, compliance matrix, or workflow fields. Report rendering escapes source/user text and sanitizes CSS class tokens. See `docs/pursuit-command-center.md` for exact semantics, endpoint mapping, timezone behavior, privacy/XSS boundaries, limitations, and v0.12 behavior.

## Evidence And Citations

Proposal document parsing still produces backwards-compatible `evidenceSnippets`, and now also writes durable `evidenceCitations` records linked to the opportunity, optional proposal/document IDs, page or section, source excerpt, extracted claim, extraction method, confidence, and verification state.

Read endpoints are available under `/api/evidence/{noticeId}`. Mutations under `/api/evidence/add`, `/api/evidence/update`, `/api/evidence/verify`, and `/api/evidence/delete` require `APP_WRITE_TOKEN`; verification requires `{ noticeId, evidenceId, state, verifier }` and rejects missing or cross-notice IDs.

Summary, Requirements, and Gap assist outputs use citation records when present and separate source facts, business assumptions, and AI recommendations. The deterministic path remains available when AI is disabled or unavailable, and prompts/source text are not stored in the local AI audit log.

## Compliance Matrix

SAM Radar stores a local compliance matrix per opportunity in SQLite. Requirements can be created manually, generated deterministically from non-rejected evidence citations, edited, verified, rejected, merged, split, and exported. Generated requirements preserve human edits and verification review state on later regeneration. Requirements linked to stale citations are invalidated only when their own citation predates a material opportunity revision.

Read `/api/compliance/{noticeId}` to list requirements. Mutations under `/api/compliance/add`, `/api/compliance/update`, `/api/compliance/verify`, `/api/compliance/reject`, `/api/compliance/generate`, `/api/compliance/merge`, and `/api/compliance/split` require `APP_WRITE_TOKEN`. Export `/api/compliance-export/{noticeId}.csv` or `/api/compliance-export/{noticeId}.md` for a safe CSV or Markdown matrix.

The report UI shows filters for category, status, mandatory state, verification state, invalidation, and missing citation; inline row edits; verify/reject mark actions; merge/split controls; generation from evidence; and CSV/Markdown export links. Summary, Requirements, and Gap assist include compliance matrix context as source facts. See `docs/compliance-matrix.md` for schema, endpoints, safety rules, and limitations.

## Amendment Intelligence

SAM Radar captures normalized immutable opportunity and attachment metadata snapshots during SAM.gov refresh. It detects material changes for deadlines, cancellation/status, notice type, set-aside, NAICS, PSC, description, contacts, and attachments, then classifies impact as `critical`, `high`, `medium`, or `low`.

Read `/api/amendments/{noticeId}` for revision timeline, material/unread counts, stale-evidence warnings, and review tasks. Task mutations under `/api/amendments/task/*` require `APP_WRITE_TOKEN`. The report UI shows amendment timelines, before/after facts, stale citation warnings, and task controls in opportunity detail/workspace views. See `docs/amendment-intelligence.md` for schema, detection semantics, API, privacy, migration, and limitations.

## Notifications And Scheduler

Enable scheduled daily refreshes with:

```env
ENABLE_SCHEDULER=true
REFRESH_CRON=0 6 * * *
```

Scheduled refreshes call SAM.gov, generate reports, send enabled notification channels when unseen matches exist, and then mark those unseen matches as seen. Manual web refresh does not notify or mark seen.

Slack and Telegram are optional. Configure either or both in `.env`. Daily digest notifications remain the default. Optional workflow Slack notifications can be enabled with `ENABLE_SLACK_WORKFLOW=true` and narrowed with `SLACK_WORKFLOW_EVENTS=pursue,submitted,due-soon,follow-up-due`; duplicate workflow alerts are suppressed locally.

## Deployment Docs

- `docs/publication.md` covers public GitHub publishing, privacy checks, and issue seeding.
- `docs/deployment-homelab.md` covers Docker plus `https://sam-radar.lan` behind nginx.
- `docs/operations.md` covers releases, GHCR image publishing, backup/restore, reverse proxy hardening, and upgrades.
- `docs/ux-density.md` documents the pipeline cockpit design direction and responsive QA targets.
- `docs/navigation-controls.md` documents sticky navigation, back-to-top, and board lane visibility controls.
- `docs/design-system.md` documents report tokens, component primitives, accessibility, and responsive conventions.
- `docs/evidence-citations.md` documents the SQLite evidence model, APIs, verification states, UI behavior, and AI assist boundaries.
- `docs/compliance-matrix.md` documents the SQLite compliance model, endpoints, mark actions, merge/split, exports, invalidation, and AI assist boundaries.
- `docs/pursuit-command-center.md` documents v0.12 Command Center semantics, quick actions, auth, deep links, and limitations.
- `deploy/nginx/sam-radar.lan.conf` is an example nginx vhost.

## Local Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env
cp config/business.example.yaml config/business.yaml
sam-radar refresh
sam-radar serve
```

## Roadmap

- v0.1: public Docker MVP with configurable search and report dashboard
- v0.2: scheduler, Slack, Telegram, and richer persistence
- v0.3: board view, bid/no-bid statuses, notes, watchlist, and CSV export
- v0.4: opportunity pipeline with detail view, follow-up queue, event history, document review tracking, metrics, and optional workflow notifications
- v0.5: opportunity control center with archive/unarchive, isolated manual search, duplicate guards, and resource links
- v0.8: professional design system with tokens, reusable primitives, accessible feedback states, responsive density, and a Resources showcase
- v0.9: evidence and citation foundation with durable source excerpts, verification workflow, and source-aware assist output
- v0.10: amendment intelligence with immutable SAM.gov revision snapshots, material change detection, stale-evidence warnings, and review tasks
- v0.11: compliance matrix with evidence-backed generation, review marks, merge/split lineage, stale-source invalidation, exports, and source-aware assist context
- v0.12: Pursuit Command Center with deterministic risk/priority queues, portfolio health, recent intelligence, deep links, and authenticated notice-scoped quick actions
- v1.0: stable self-hosted release

## Security Notes

SAM Radar should never log API keys or notification tokens. Keep secrets in `.env` or your container orchestrator. Keep real business context local unless you intentionally publish it.
