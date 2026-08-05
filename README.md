# SAM Radar

SAM Radar is a self-hosted SAM.gov opportunity intelligence dashboard for small businesses, consultants, capture teams, and GovCon operators.

Define your business profile once. SAM Radar searches SAM.gov, scores opportunities against your capabilities, filters noise, generates polished reports, and can send notification digests.

## What It Does

- Searches the SAM.gov opportunities API
- Scores matches against your local business context
- Filters expired, duplicate, and low-actionability noise
- Generates a polished HTML report and JSON export
- Normalizes deadlines to your configured timezone
- Provides a browser refresh button
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
https://sam-radar.example.test
```

Use:

```env
APP_BASE_URL=https://sam-radar.example.test
SAM_RADAR_HOST=0.0.0.0
SAM_RADAR_PORT=8066
```

Example nginx config:

```nginx
server {
    listen 443 ssl;
    server_name sam-radar.example.test;

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

The dashboard can save lightweight pursuit workflow state per opportunity: `new`, `reviewing`, `pursue`, `teaming`, `monitor`, `no-bid`, `submitted`, and `archived`. The v0.4 pipeline adds owner, priority, next action, follow-up date, decision reason, structured no-bid reason, document review links, event history, follow-up queues, pipeline metrics, and an interactive board.

Status and notes writes require `APP_WRITE_TOKEN`. Generate one with `sam-radar generate-token` or `scripts/generate-token.sh`, save it in `.env`, then use the dashboard Unlock control to store it in your browser. This is separate from your SAM.gov API key. Leave `APP_WRITE_TOKEN` blank to disable browser writes.

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
- `docs/deployment-homelab.md` covers Docker plus `https://sam-radar.example.test` behind nginx.
- `docs/ux-density.md` documents the pipeline cockpit design direction and responsive QA targets.
- `docs/navigation-controls.md` documents sticky navigation, back-to-top, and board lane visibility controls.
- `deploy/nginx/sam-radar.example.test.conf` is an example nginx vhost.

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
- v1.0: stable self-hosted release

## Security Notes

SAM Radar should never log API keys or notification tokens. Keep secrets in `.env` or your container orchestrator. Keep real business context local unless you intentionally publish it.
