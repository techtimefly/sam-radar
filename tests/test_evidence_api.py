import json
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from sam_radar.cli import RadarHandler
from sam_radar.config import Settings


def _serve(settings: Settings):
    handler = type("TestRadarHandler", (RadarHandler,), {"settings": settings})
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(settings.reports_dir)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _json(url: str, payload: dict | None = None, token: str = ""):
    data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json"})
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-SAM-RADAR-TOKEN", token)
    with urllib.request.urlopen(req, timeout=5) as res:
        return res.status, json.loads(res.read().decode("utf-8"))


def test_evidence_api_read_and_write_auth_boundary(tmp_path: Path):
    settings = Settings(sam_gov_api_key="test", data_dir=tmp_path / "data", reports_dir=tmp_path / "reports", app_write_token="secret")
    settings.reports_dir.mkdir()
    server, base = _serve(settings)
    try:
        status, data = _json(f"{base}/api/evidence/opp-api-1")
        assert status == 200
        assert data == {"ok": True, "evidence": []}

        try:
            _json(
                f"{base}/api/evidence/add",
                {"noticeId": "opp-api-1", "sourceExcerpt": "Contractor shall submit a plan.", "confidence": 0.8},
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("Expected write without APP_WRITE_TOKEN to fail")

        status, created = _json(
            f"{base}/api/evidence/add",
            {"noticeId": "opp-api-1", "sourceExcerpt": "Contractor shall submit a plan.", "confidence": 0.8},
            token="secret",
        )
        assert status == 200
        assert created["evidence"]["verificationState"] == "needs-review"

        status, verified = _json(
            f"{base}/api/evidence/verify",
            {"evidenceId": created["evidence"]["id"], "state": "verified", "verifier": "Lead"},
            token="secret",
        )
        assert status == 200
        assert verified["evidence"]["verificationState"] == "verified"
        assert verified["evidence"]["verifier"] == "Lead"
    finally:
        server.shutdown()
        server.server_close()
