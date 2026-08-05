from __future__ import annotations

import argparse
import json
import secrets
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from .config import load_settings
from .core import refresh_report
from .scheduler import Scheduler
from .storage import Store


class RadarHandler(SimpleHTTPRequestHandler):
    settings = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        if parsed.path.startswith("/api/status/"):
            notice_id = unquote(parsed.path.rsplit("/", 1)[-1])
            store = Store(self.settings.data_dir / "sam-radar.sqlite3")
            self._send_json(200, {"ok": True, "workflow": store.get_status(notice_id), "writesEnabled": bool(self.settings.app_write_token)})
            return
        if self.path in {"/", ""}:
            self.path = "/reports/latest.html"
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/api/refresh", "/refresh"}:
            try:
                payload = refresh_report(self.settings, mark_seen=False, notify=False)
                self._send_json(200, payload)
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": str(exc)})
            return
        if parsed.path.startswith("/api/status/"):
            if not self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "APP_WRITE_TOKEN is not configured; status writes are disabled."})
                return
            if self.headers.get("X-SAM-RADAR-TOKEN") != self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "Invalid or missing APP_WRITE_TOKEN."})
                return
            notice_id = unquote(parsed.path.rsplit("/", 1)[-1])
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                payload = json.loads(body.decode("utf-8") or "{}")
                store = Store(self.settings.data_dir / "sam-radar.sqlite3")
                workflow = store.set_workflow(notice_id, payload)
                self._send_json(200, {"ok": True, "workflow": workflow})
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(exc)})
            return
        self.send_error(404)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve() -> int:
    settings = load_settings()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    latest = settings.reports_dir / "latest.html"
    if not latest.exists() and settings.sam_gov_api_key and settings.profile_path.exists():
        refresh_report(settings, mark_seen=False, notify=False)
    scheduler = None
    if settings.enable_scheduler:
        scheduler = Scheduler(settings)
        scheduler.start()
        print(f"Scheduler enabled: {settings.refresh_cron} ({settings.timezone})")
    RadarHandler.settings = settings
    handler = partial(RadarHandler, directory=".")
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    print(f"Serving SAM Radar on http://{settings.host}:{settings.port}")
    try:
        server.serve_forever()
    finally:
        if scheduler:
            scheduler.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="sam-radar")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("generate-token")
    refresh_parser = sub.add_parser("refresh")
    refresh_parser.add_argument("--mark-seen", action="store_true")
    refresh_parser.add_argument("--notify", action="store_true")
    refresh_parser.add_argument("--notify-no-matches", action="store_true")
    args = parser.parse_args()
    if args.command == "serve":
        return serve()
    if args.command == "refresh":
        payload = refresh_report(load_settings(), mark_seen=args.mark_seen, notify=args.notify, notify_no_matches=args.notify_no_matches)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "generate-token":
        print(secrets.token_urlsafe(32))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
