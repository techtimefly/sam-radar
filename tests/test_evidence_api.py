import json
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from sam_radar.cli import RadarHandler
from sam_radar.config import Settings
from sam_radar.storage import Store


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


def test_amendment_api_task_auth_and_cross_notice_integrity(tmp_path: Path):
    settings = Settings(sam_gov_api_key="test", data_dir=tmp_path / "data", reports_dir=tmp_path / "reports", app_write_token="secret")
    settings.reports_dir.mkdir()
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    rev = store.capture_opportunity_revision({"noticeId": "opp-api-amd", "title": "A", "responseDeadline": "2026-08-20T12:00:00+00:00"})["revision"]
    server, base = _serve(settings)
    try:
        status, data = _json(f"{base}/api/amendments/opp-api-amd")
        assert status == 200
        assert data["amendments"]["summary"]["revisionCount"] == 1

        try:
            _json(f"{base}/api/amendments/task/create", {"noticeId": "opp-api-amd", "revisionId": rev["revisionId"]})
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("Expected write without APP_WRITE_TOKEN to fail")

        status, created = _json(
            f"{base}/api/amendments/task/create",
            {"noticeId": "opp-api-amd", "revisionId": rev["revisionId"], "assignee": "Lead", "status": "open"},
            token="secret",
        )
        assert status == 200
        assert created["task"]["assignee"] == "Lead"

        status, deleted = _json(
            f"{base}/api/amendments/task/delete",
            {"noticeId": "opp-api-amd", "taskId": created["task"]["id"]},
            token="secret",
        )
        assert status == 200
        assert deleted["tasks"] == []

        try:
            _json(
                f"{base}/api/amendments/task/update",
                {"taskId": created["task"]["id"], "noticeId": "other", "status": "done"},
                token="secret",
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("Expected cross-notice update to fail")
    finally:
        server.shutdown()
        server.server_close()


def test_amendment_api_mark_reviewed_requires_token_and_updates_unread(tmp_path: Path):
    settings = Settings(sam_gov_api_key="test", data_dir=tmp_path / "data", reports_dir=tmp_path / "reports", app_write_token="secret")
    settings.reports_dir.mkdir()
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    store.capture_opportunity_revision({"noticeId": "opp-api-review", "title": "A", "responseDeadline": "2026-08-20T12:00:00+00:00"})
    store.capture_opportunity_revision({"noticeId": "opp-api-review", "title": "A", "responseDeadline": "2026-08-12T12:00:00+00:00"})
    change = store.amendment_changes("opp-api-review")[0]
    server, base = _serve(settings)
    try:
        try:
            _json(f"{base}/api/amendments/mark-reviewed", {"noticeId": "opp-api-review", "changeIds": [change["id"]]})
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("Expected mark-reviewed without APP_WRITE_TOKEN to fail")

        status, reviewed = _json(
            f"{base}/api/amendments/mark-reviewed",
            {"noticeId": "opp-api-review", "changeIds": [change["id"]]},
            token="secret",
        )
        assert status == 200
        assert reviewed["amendments"]["summary"]["unreadCount"] == 0

        try:
            _json(f"{base}/api/amendments/mark-reviewed", {"noticeId": "other", "changeIds": [change["id"]]}, token="secret")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("Expected cross-notice mark-reviewed to fail")
    finally:
        server.shutdown()
        server.server_close()


def test_compliance_api_crud_generate_verify_exports_and_cross_notice(tmp_path: Path):
    settings = Settings(sam_gov_api_key="test", data_dir=tmp_path / "data", reports_dir=tmp_path / "reports", app_write_token="secret")
    settings.reports_dir.mkdir()
    store = Store(settings.data_dir / "sam-radar.sqlite3")
    doc = store.add_proposal_document({"noticeId": "opp-api-comp", "sourceType": "url", "source": "https://example.test/pws.txt"})
    citation = store.create_evidence_citation({"noticeId": "opp-api-comp", "documentId": doc["id"], "sourceExcerpt": "Offeror shall submit a management plan.", "extractedClaim": "Management plan."})
    other_doc = store.add_proposal_document({"noticeId": "opp-api-other", "sourceType": "url", "source": "https://example.test/other.txt"})
    other_citation = store.create_evidence_citation({"noticeId": "opp-api-other", "documentId": other_doc["id"], "sourceExcerpt": "Other"})
    server, base = _serve(settings)
    try:
        status, empty = _json(f"{base}/api/compliance/opp-api-comp")
        assert status == 200
        assert empty == {"ok": True, "requirements": []}

        try:
            _json(f"{base}/api/compliance/add", {"noticeId": "opp-api-comp", "citationId": citation["id"], "requirementText": "Submit a plan."})
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("Expected write without APP_WRITE_TOKEN to fail")

        status, created = _json(
            f"{base}/api/compliance/add",
            {"noticeId": "opp-api-comp", "citationId": citation["id"], "category": "Submission", "requirementText": "Submit a plan."},
            token="secret",
        )
        assert status == 200
        assert created["requirement"]["category"] == "Submission"

        try:
            _json(
                f"{base}/api/compliance/update",
                {"requirementId": created["requirement"]["id"], "requirementText": "Bad"},
                token="secret",
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert "noticeId is required" in exc.read().decode("utf-8")
        else:
            raise AssertionError("Expected update without noticeId to fail")

        try:
            _json(
                f"{base}/api/compliance/update",
                {"noticeId": "opp-api-other", "requirementId": created["requirement"]["id"], "requirementText": "Bad"},
                token="secret",
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert "noticeId does not match" in exc.read().decode("utf-8")
        else:
            raise AssertionError("Expected cross-notice update to fail")

        try:
            _json(
                f"{base}/api/compliance/add",
                {"noticeId": "opp-api-comp", "citationId": other_citation["id"], "requirementText": "Bad"},
                token="secret",
            )
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert "citationId does not belong to noticeId" in exc.read().decode("utf-8")
        else:
            raise AssertionError("Expected cross-notice citation to fail")

        status, verified = _json(
            f"{base}/api/compliance/verify",
            {"noticeId": "opp-api-comp", "requirementId": created["requirement"]["id"], "state": "verified", "verifier": "Lead"},
            token="secret",
        )
        assert status == 200
        assert verified["requirement"]["verificationState"] == "verified"

        status, generated = _json(f"{base}/api/compliance/generate", {"noticeId": "opp-api-comp"}, token="secret")
        assert status == 200
        assert generated["createdCount"] == 0

        for path, ctype, disposition in [
            ("/api/compliance-export/opp-api-comp.csv", "text/csv; charset=utf-8", "attachment; filename=\"opp-api-comp-compliance-matrix.csv\""),
            ("/api/compliance-export/opp-api-comp.md", "text/markdown; charset=utf-8", "attachment; filename=\"opp-api-comp-compliance-matrix.md\""),
        ]:
            with urllib.request.urlopen(f"{base}{path}", timeout=5) as res:
                text = res.read().decode("utf-8")
                assert res.status == 200
                assert res.headers["Content-Type"] == ctype
                assert res.headers["Content-Disposition"] == disposition
                assert "opp-api-comp" in text
                assert "secret" not in text
    finally:
        server.shutdown()
        server.server_close()
