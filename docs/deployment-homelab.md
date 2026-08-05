# Homelab Deployment

This guide keeps the public SAM Radar repo generic while supporting a private `.lan` deployment.

## Target Shape

- App container runs on the Docker host and exposes `0.0.0.0:8066`.
- `APP_BASE_URL=https://sam-radar.example.test` controls links shown in Slack/Telegram/report digests.
- Private files stay local and are mounted as Docker volumes: `.env`, `config/business.yaml`, `data/`, and `reports/`.
- nginx terminates TLS and proxies `https://sam-radar.example.test` to the Docker host on port `8066`.
- DNS resolves `sam-radar.example.test` to the active nginx reverse proxy.

## Local App Host

Create local private config from the examples:

```bash
cp .env.example .env
cp config/business.example.yaml config/business.yaml
```

Minimum `.env` keys. Fill `SAM_GOV_API_KEY` and `APP_WRITE_TOKEN` locally before deploying:

```env
SAM_GOV_API_KEY=
APP_BASE_URL=https://sam-radar.example.test
SAM_RADAR_HOST=0.0.0.0
SAM_RADAR_PORT=8066
APP_WRITE_TOKEN=
ENABLE_SCHEDULER=true
REFRESH_CRON=0 6 * * *
BUSINESS_PROFILE=config/business.yaml
DATA_DIR=data
REPORTS_DIR=reports
```

Start the app:

```bash
docker-compose up -d --build
```

Smoke test from the app host:

```bash
curl -fsS http://127.0.0.1:8066/healthz
```

## nginx Reverse Proxy

Use `deploy/nginx/sam-radar.example.test.conf` as the vhost example. Install it on the active nginx host, enable it, then test and reload nginx.

The example proxies to `http://DOCKER_HOST_IP:8066`. Change that upstream if your Docker host uses a different IP or port. Keep `client_max_body_size 25m;` or higher for proposal document uploads.

## DNS And TLS

Create a DNS record for `sam-radar.example.test` pointing at the nginx reverse proxy. In a Pi-hole-backed homelab, add a local DNS record:

```text
REVERSE_PROXY_IP sam-radar.example.test
```

Add `sam-radar.example.test` to the local TLS certificate SAN list if your `.lan` certificate is not wildcard-compatible or does not already include it.

## Verification

Run these checks after deployment:

```bash
curl -fsS http://127.0.0.1:8066/healthz
dig +short @PIHOLE_IP sam-radar.example.test
curl -kfsS https://sam-radar.example.test/healthz
curl -kfsS https://sam-radar.example.test/api/status/smoke
```

If the Docker host itself does not use Pi-hole for DNS, verify the nginx route with:

```bash
curl -kfsS --resolve sam-radar.example.test:443:REVERSE_PROXY_IP https://sam-radar.example.test/healthz
```

Status writes should fail without `APP_WRITE_TOKEN`:

```bash
curl -ks -o /tmp/sam-radar-noauth.json -w '%{http_code}\n' \
  -X POST https://sam-radar.example.test/api/status/smoke \
  -H 'Content-Type: application/json' \
  -d '{"status":"pursue","notes":"smoke"}'
```

Expected result: `403`.

## Rollback

1. Stop the app container: `docker-compose down`.
2. Disable/remove the nginx vhost and reload nginx.
3. Remove the DNS override if this hostname should disappear.
4. Preserve `data/` and `reports/` unless you intentionally want to discard local history.
