from __future__ import annotations

import html.parser
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from .config import Settings
from .storage import Store

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
SUPPORTED_SUFFIXES = {".txt", ".html", ".htm", ".docx", ".pdf"}


class _HTMLTextParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


@dataclass
class ParsedDocument:
    status: str
    text: str = ""
    error: str = ""
    content_type: str = ""
    size_bytes: int = 0
    local_path: str = ""


def safe_filename(value: str) -> str:
    name = Path(urllib.parse.urlparse(value or "").path).name or Path(value or "document.txt").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return name[:180] or "document.txt"


def guess_content_type(name: str) -> str:
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _html_to_text(data: bytes) -> str:
    parser = _HTMLTextParser()
    parser.feed(_decode_text(data))
    return "\n".join(parser.parts)


def _docx_to_text(data: bytes) -> str:
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    parts = [node.text for node in root.iter(f"{ns}t") if node.text]
    return "\n".join(parts)


def _pdf_to_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:
        raise RuntimeError("PDF parsing requires optional package pypdf.") from exc
    from io import BytesIO

    reader = PdfReader(BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages[:80]]
    return "\n\n".join(page for page in pages if page)


def extract_text_from_bytes(data: bytes, filename: str, content_type: str = "") -> ParsedDocument:
    if len(data) > MAX_DOCUMENT_BYTES:
        return ParsedDocument(status="failed", error="Document exceeds 10 MB limit.", content_type=content_type, size_bytes=len(data))
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".txt" or content_type.startswith("text/plain"):
            text = _decode_text(data)
        elif suffix in {".html", ".htm"} or content_type.startswith("text/html"):
            text = _html_to_text(data)
        elif suffix == ".docx":
            text = _docx_to_text(data)
        elif suffix == ".pdf" or content_type == "application/pdf":
            text = _pdf_to_text(data)
        else:
            return ParsedDocument(status="unsupported", error="Supported document types: TXT, HTML, DOCX, PDF.", content_type=content_type, size_bytes=len(data))
    except RuntimeError as exc:
        return ParsedDocument(status="unsupported", error=str(exc), content_type=content_type, size_bytes=len(data))
    except Exception as exc:  # noqa: BLE001
        return ParsedDocument(status="failed", error=f"Could not parse document: {exc}", content_type=content_type, size_bytes=len(data))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return ParsedDocument(status="parsed" if text else "failed", text=text, error="" if text else "No text extracted.", content_type=content_type, size_bytes=len(data))


def evidence_from_text(text: str) -> list[dict[str, object]]:
    headings = ("deadline", "submission", "evaluation", "requirement", "qualification", "security", "certification", "past performance", "deliverable", "pricing")
    snippets: list[dict[str, object]] = []
    for paragraph in re.split(r"\n\s*\n|(?<=[.!?])\s+(?=[A-Z])", text):
        clean = re.sub(r"\s+", " ", paragraph).strip()
        if len(clean) < 60:
            continue
        lower = clean.lower()
        matched = next((word for word in headings if word in lower), "")
        if matched:
            snippets.append({"section": matched.title(), "snippet": clean[:900], "confidence": 0.7})
        if len(snippets) >= 12:
            break
    if not snippets and text.strip():
        snippets.append({"section": "Overview", "snippet": re.sub(r"\s+", " ", text.strip())[:900], "confidence": 0.4})
    return snippets


def _read_document_bytes(document: dict, settings: Settings) -> tuple[bytes, str, str, str]:
    source = str(document.get("source") or "")
    source_type = str(document.get("sourceType") or "url")
    filename = str(document.get("filename") or safe_filename(source))
    if source_type in {"local-path", "upload"}:
        path = Path(str(document.get("localPath") or source)).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Local document not found: {path}")
        data = path.read_bytes()
        return data, filename or path.name, guess_content_type(path.name), str(path)
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL documents must use http or https.")
    request = urllib.request.Request(source, headers={"User-Agent": "sam-radar/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            content_length = int(response.headers.get("Content-Length") or 0)
            if content_length > MAX_DOCUMENT_BYTES:
                raise ValueError("Remote document exceeds 10 MB limit.")
            data = response.read(MAX_DOCUMENT_BYTES + 1)
            if len(data) > MAX_DOCUMENT_BYTES:
                raise ValueError("Remote document exceeds 10 MB limit.")
            content_type = response.headers.get_content_type() or guess_content_type(filename)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Document download failed: {exc}") from exc
    docs_dir = settings.data_dir / "documents" / safe_filename(str(document.get("noticeId") or "unknown"))
    docs_dir.mkdir(parents=True, exist_ok=True)
    local_path = docs_dir / filename
    local_path.write_bytes(data)
    return data, filename, content_type, str(local_path)


def parse_registered_document(settings: Settings, store: Store, document_id: int) -> dict:
    document = store.proposal_document(document_id)
    if not document:
        raise ValueError("document does not exist")
    try:
        data, filename, content_type, local_path = _read_document_bytes(document, settings)
        parsed = extract_text_from_bytes(data, filename, content_type)
        text_path = ""
        if parsed.text:
            extract_dir = settings.data_dir / "documents" / safe_filename(str(document.get("noticeId") or "unknown"))
            extract_dir.mkdir(parents=True, exist_ok=True)
            text_file = extract_dir / f"{document_id}-{safe_filename(filename)}.txt"
            text_file.write_text(parsed.text[:250000], encoding="utf-8")
            text_path = str(text_file)
        updated = store.update_proposal_document_parse(
            document_id,
            {
                "parseStatus": parsed.status,
                "parseError": parsed.error,
                "extractedTextPath": text_path,
                "contentType": parsed.content_type or content_type,
                "sizeBytes": parsed.size_bytes or len(data),
                "localPath": local_path,
            },
        )
        snippets = store.replace_evidence_snippets(document["noticeId"], document_id, evidence_from_text(parsed.text)) if parsed.text else store.evidence_snippets(document["noticeId"])
        return {"ok": True, "document": updated, "evidence": snippets}
    except Exception as exc:  # noqa: BLE001
        updated = store.update_proposal_document_parse(document_id, {"parseStatus": "failed", "parseError": str(exc)})
        return {"ok": False, "document": updated, "evidence": store.evidence_snippets(document["noticeId"]), "error": str(exc)}
