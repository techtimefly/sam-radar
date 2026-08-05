import zipfile
from pathlib import Path

from sam_radar.document_intake import evidence_from_text, extract_text_from_bytes, safe_filename


def test_extracts_text_and_html_documents():
    txt = extract_text_from_bytes(b"Security requirement and submission deadline.", "notice.txt", "text/plain")
    html = extract_text_from_bytes(b"<html><body><h1>Requirements</h1><p>Submit by Friday.</p></body></html>", "notice.html", "text/html")

    assert txt.status == "parsed"
    assert "Security requirement" in txt.text
    assert html.status == "parsed"
    assert "Submit by Friday" in html.text


def test_extracts_docx_text_with_standard_library(tmp_path: Path):
    docx = tmp_path / "notice.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Evaluation requirement text.</w:t></w:r></w:p></w:body></w:document>')

    parsed = extract_text_from_bytes(docx.read_bytes(), "notice.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    assert parsed.status == "parsed"
    assert "Evaluation requirement text" in parsed.text


def test_evidence_snippets_find_capture_terms_and_safe_filename():
    snippets = evidence_from_text("The offeror must meet the security requirement and submit evidence of certification before the deadline.")

    assert snippets
    assert snippets[0]["section"] in {"Security", "Requirement", "Certification", "Deadline"}
    assert safe_filename("https://example.test/A bad file name!.txt") == "A-bad-file-name-.txt"


def test_uploaded_document_payload_is_stored_and_parseable(tmp_path: Path):
    import base64

    from sam_radar.config import Settings
    from sam_radar.core import add_proposal_document, parse_proposal_document

    settings = Settings(sam_gov_api_key="test", data_dir=tmp_path)
    created = add_proposal_document(
        settings,
        {
            "noticeId": "upload-1",
            "sourceType": "upload",
            "filename": "requirements.txt",
            "contentType": "text/plain",
            "contentBase64": base64.b64encode(b"The deadline and security requirement are important.").decode("ascii"),
            "label": "Requirements",
        },
    )

    assert created["document"]["sourceType"] == "upload"
    assert created["document"]["filename"] == "requirements.txt"
    parsed = parse_proposal_document(settings, {"documentId": created["document"]["id"]})
    assert parsed["ok"] is True
    assert parsed["document"]["parseStatus"] == "parsed"
    assert parsed["evidence"]
