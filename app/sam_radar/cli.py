from __future__ import annotations

import argparse
import json
import secrets
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from .config import load_settings
from .core import (
    add_manual_opportunity,
    add_proposal_artifact,
    add_proposal_document,
    add_search_feedback,
    ai_audit_log,
    ai_connection_test,
    ai_opportunity_gaps,
    ai_opportunity_requirements,
    ai_opportunity_summary,
    ai_prime_templates,
    ai_settings,
    ai_subcontractor_templates,
    create_proposal,
    delete_search_reference_code,
    manual_search,
    parse_proposal_document,
    proposal_artifacts,
    proposal_documents,
    proposal_list,
    refresh_report,
    remove_proposal_document,
    save_search_profile,
    save_search_reference_code,
    search_coach,
    search_intelligence,
    update_proposal,
    update_proposal_artifact,
)
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
        if parsed.path == "/api/search-intelligence":
            from urllib.parse import parse_qs
            query = (parse_qs(parsed.query).get("q") or [""])[0]
            self._send_json(200, search_intelligence(self.settings, query))
            return
        if parsed.path == "/api/proposals":
            self._send_json(200, proposal_list(self.settings))
            return
        if parsed.path == "/api/ai/settings":
            self._send_json(200, ai_settings(self.settings))
            return
        if parsed.path == "/api/ai/audit":
            if not self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "APP_WRITE_TOKEN is not configured; AI audit is disabled."})
                return
            if self.headers.get("X-SAM-RADAR-TOKEN") != self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "Invalid or missing APP_WRITE_TOKEN."})
                return
            self._send_json(200, ai_audit_log(self.settings))
            return
        if parsed.path.startswith("/api/proposal-documents/"):
            notice_id = unquote(parsed.path.rsplit("/", 1)[-1])
            self._send_json(200, proposal_documents(self.settings, notice_id))
            return
        if parsed.path.startswith("/api/proposal-artifacts/"):
            notice_id = unquote(parsed.path.rsplit("/", 1)[-1])
            self._send_json(200, proposal_artifacts(self.settings, notice_id))
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
        if parsed.path == "/api/manual-search":
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                payload = json.loads(body.decode("utf-8") or "{}")
                self._send_json(200, manual_search(self.settings, payload))
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path in {"/api/search-reference/save", "/api/search-reference/delete", "/api/search-profiles/save", "/api/search-feedback", "/api/search-coach/save-profile"}:
            if not self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "APP_WRITE_TOKEN is not configured; writes are disabled."})
                return
            if self.headers.get("X-SAM-RADAR-TOKEN") != self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "Invalid or missing APP_WRITE_TOKEN."})
                return
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                payload = json.loads(body.decode("utf-8") or "{}")
                if parsed.path == "/api/search-reference/save":
                    result = save_search_reference_code(self.settings, payload)
                elif parsed.path == "/api/search-reference/delete":
                    result = delete_search_reference_code(self.settings, payload)
                elif parsed.path == "/api/search-feedback":
                    result = add_search_feedback(self.settings, payload)
                else:
                    result = save_search_profile(self.settings, payload)
                self._send_json(200, result)
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/search-coach":
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                payload = json.loads(body.decode("utf-8") or "{}")
                self._send_json(200, search_coach(self.settings, payload))
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path in {"/api/ai/test", "/api/ai/summary", "/api/ai/requirements", "/api/ai/gaps", "/api/ai/prime-templates", "/api/ai/subcontractor-templates"}:
            if not self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "APP_WRITE_TOKEN is not configured; AI actions are disabled."})
                return
            if self.headers.get("X-SAM-RADAR-TOKEN") != self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "Invalid or missing APP_WRITE_TOKEN."})
                return
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                payload = json.loads(body.decode("utf-8") or "{}")
                if parsed.path == "/api/ai/test":
                    result = ai_connection_test(self.settings)
                elif parsed.path == "/api/ai/requirements":
                    result = ai_opportunity_requirements(self.settings, payload)
                elif parsed.path == "/api/ai/gaps":
                    result = ai_opportunity_gaps(self.settings, payload)
                elif parsed.path == "/api/ai/prime-templates":
                    result = ai_prime_templates(self.settings, payload)
                elif parsed.path == "/api/ai/subcontractor-templates":
                    result = ai_subcontractor_templates(self.settings, payload)
                else:
                    result = ai_opportunity_summary(self.settings, payload)
                self._send_json(200, result)
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path in {
            "/api/proposals/create",
            "/api/proposals/stage",
            "/api/proposal-documents/add",
            "/api/proposal-documents/parse",
            "/api/proposal-documents/remove",
            "/api/proposal-artifacts/add",
            "/api/proposal-artifacts/update",
        }:
            if not self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "APP_WRITE_TOKEN is not configured; proposal writes are disabled."})
                return
            if self.headers.get("X-SAM-RADAR-TOKEN") != self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "Invalid or missing APP_WRITE_TOKEN."})
                return
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                payload = json.loads(body.decode("utf-8") or "{}")
                if parsed.path == "/api/proposals/create":
                    result = create_proposal(self.settings, payload)
                elif parsed.path == "/api/proposals/stage":
                    result = update_proposal(self.settings, payload)
                elif parsed.path == "/api/proposal-documents/add":
                    result = add_proposal_document(self.settings, payload)
                elif parsed.path == "/api/proposal-documents/remove":
                    result = remove_proposal_document(self.settings, payload)
                elif parsed.path == "/api/proposal-artifacts/add":
                    result = add_proposal_artifact(self.settings, payload)
                elif parsed.path == "/api/proposal-artifacts/update":
                    result = update_proposal_artifact(self.settings, payload)
                else:
                    result = parse_proposal_document(self.settings, payload)
                self._send_json(200, result)
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/manual-add":
            if not self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "APP_WRITE_TOKEN is not configured; status writes are disabled."})
                return
            if self.headers.get("X-SAM-RADAR-TOKEN") != self.settings.app_write_token:
                self._send_json(403, {"ok": False, "error": "Invalid or missing APP_WRITE_TOKEN."})
                return
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                payload = json.loads(body.decode("utf-8") or "{}")
                result = add_manual_opportunity(self.settings, payload)
                self._send_json(409 if result.get("duplicate") else 200, result)
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(exc)})
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
