# Operations Guide

This guide covers the stable self-hosted release path for public SAM Radar users while keeping private business context local.

## Versioned Releases

Use semantic version tags for stable releases:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Tags that match `v*.*.*` publish a container image to GitHub Container Registry through `.github/workflows/container.yml`.

Recommended release checklist:

1. Confirm `ruff check .`, `pytest -q`, `python scripts/check_private_context.py`, and `docker build -t sam-radar:test .` pass.
2. Confirm `.env`, `config/business.yaml`, `data/`, and `reports/` are ignored and not tracked.
3. Create a GitHub release from the tag with upgrade notes and any breaking changes.

## Container Images

Published images use this pattern:

```text
ghcr.io/OWNER/sam-radar:v1.0.0
ghcr.io/OWNER/sam-radar:latest
```

A compose deployment can either build locally or pull a tagged image:

```yaml
services:
  sam-radar:
    image: ghcr.io/OWNER/sam-radar:v1.0.0
    env_file:
      - .env
    ports:
      - "8066:8066"
    volumes:
      - ./config:/app/config
      - ./data:/app/data
      - ./reports:/app/reports
    restart: unless-stopped
```

## Backup And Restore

Back up only local runtime state. Do not commit these paths:

```bash
tar -czf sam-radar-backup-$(date +%Y%m%d).tgz .env config/business.yaml data reports
```

Restore onto a fresh checkout:

```bash
tar -xzf sam-radar-backup-YYYYMMDD.tgz -C /opt/sam-radar
docker-compose up -d --build
curl -fsS http://127.0.0.1:8066/healthz
```

Important files:

- `.env`: API keys, write token, app URL, notification settings, scheduler settings.
- `config/business.yaml`: private business profile and search context.
- `data/sam-radar.sqlite3`: workflow state, dedupe history, saved profiles, proposals, AI audit metadata.
- `reports/`: generated HTML, JSON, and CSV report artifacts.

## Reverse Proxy Hardening

For nginx or another reverse proxy:

- Terminate TLS at the proxy and forward to `http://APP_HOST:8066`.
- Set `APP_BASE_URL` to the public HTTPS URL so Slack, Telegram, exports, and report links are correct.
- Keep `client_max_body_size 25m;` or higher for proposal document uploads.
- Forward `Host`, `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Real-IP` headers.
- Restrict access at the network/proxy layer for private homelab deployments.
- Keep `APP_WRITE_TOKEN` private; it is not the SAM.gov API key.

Minimal nginx location block:

```nginx
location / {
    proxy_pass http://APP_HOST:8066;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 25m;
}
```

## Upgrade Guide

1. Back up `.env`, `config/business.yaml`, `data/`, and `reports/`.
2. Pull the target tag or update the image tag in `docker-compose.yml`.
3. Recreate the app with `docker-compose up -d --build` or `docker-compose up -d` for pulled images.
4. Verify `/healthz`, `/reports/latest.html`, `/reports/latest.csv`, and `/api/notifications/preview`.
5. If a rollback is needed, restore the backup and restart the previous image/tag.

The SQLite store is designed to migrate forward at app startup by adding missing columns/tables without deleting existing workflow state.
