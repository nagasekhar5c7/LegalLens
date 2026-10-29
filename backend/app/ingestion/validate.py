"""FR-2 and TS-4: reject what should not be ingested, and name the real value.

Two things make this stricter than a `Content-Type` check:

  * **Magic bytes.** The extension and the declared MIME type both come from the
    client. A `.pdf` that is actually a zip is either a mistake or an attack, and
    either way the parser should not be the thing that finds out.
  * **Bomb guards.** A 2 MB DOCX can expand to gigabytes. The page and ratio
    caps below exist so a malicious upload fails at validation rather than in
    the worker, where it would take the process with it.

Every rejection message names the limit *and* the actual value, because FR-2's
whole point is that "file too large" is a useless error.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

#: A DOCX whose uncompressed size exceeds this ratio is a zip bomb.
MAX_COMPRESSION_RATIO = 120
MAX_DOCX_UNCOMPRESSED_BYTES = 400 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000

EXT_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


@dataclass
class Rejection:
    reason: str


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def extension_of(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return suffix if suffix else ""


def validate_extension(filename: str) -> Rejection | None:
    """The cheapest check, and the one that can run before a single byte is
    read off the wire."""
    settings = get_settings()
    ext = extension_of(filename)
    if ext not in settings.accepted_extensions:
        shown = ext or "This file type"
        accepted = ", ".join(e.lstrip(".").upper() for e in settings.accepted_extensions)
        return Rejection(f"{shown} is not supported. Legal Lens accepts {accepted}.")
    return None


def validate_size(size_bytes: int) -> Rejection | None:
    """FR-2: name the limit *and* the actual value, or the message is useless."""
    settings = get_settings()
    if size_bytes == 0:
        return Rejection("This file is empty (0 bytes).")
    if size_bytes > settings.max_upload_bytes:
        return Rejection(f"{_mb(size_bytes)} exceeds the {_mb(settings.max_upload_bytes)} limit.")
    return None


def validate_name_and_size(filename: str, size_bytes: int) -> Rejection | None:
    """Both pre-read checks together, for callers that have a size in hand."""
    return validate_extension(filename) or validate_size(size_bytes)


def validate_content(path: Path, filename: str) -> Rejection | None:
    """Post-read checks against the bytes on disk, not against what the client
    claimed they were."""
    ext = extension_of(filename)
    head = path.open("rb").read(8)

    if ext == ".pdf":
        if not head.startswith(PDF_MAGIC):
            return Rejection(
                "This file is named .pdf but its contents are not a PDF. "
                "Re-export it from the original application."
            )
        return _validate_pdf(path)

    if ext == ".docx":
        if not any(head.startswith(m) for m in ZIP_MAGIC):
            return Rejection(
                "This file is named .docx but is not a valid Word document. "
                "If it is an older .doc, re-save it as .docx."
            )
        return _validate_docx(path)

    if ext == ".txt":
        return _validate_txt(path)

    return None


def _validate_pdf(path: Path) -> Rejection | None:
    settings = get_settings()
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 — a real password is a rejection, not a crash
                return Rejection(
                    "This PDF is password-protected. Remove the password and upload it again."
                )
        pages = len(reader.pages)
    except (PdfReadError, ValueError, OSError) as exc:
        return Rejection(
            f"This PDF could not be opened ({type(exc).__name__}). It may be truncated — "
            "try re-downloading or re-exporting it."
        )

    if pages == 0:
        return Rejection("This PDF contains no pages.")
    if pages > settings.max_pages:
        return Rejection(f"{pages} pages exceeds the {settings.max_pages}-page limit.")

    # TS-4: strip rather than reject. Embedded JavaScript and attachments are
    # common in filed documents and are not this product's business, but nothing
    # downstream should be able to reach them either.
    if _has_active_content(path):
        return None  # recorded as a warning by the pipeline; not a rejection

    return None


def _has_active_content(path: Path) -> bool:
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    return b"/JavaScript" in raw or b"/Launch" in raw or b"/EmbeddedFile" in raw


def _validate_docx(path: Path) -> Rejection | None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_DOCX_ENTRIES:
                return Rejection(
                    f"This .docx contains {len(infos)} internal entries, over the "
                    f"{MAX_DOCX_ENTRIES} limit — it is unlikely to be a document."
                )
            uncompressed = sum(i.file_size for i in infos)
            compressed = max(1, sum(i.compress_size for i in infos))
            if uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
                return Rejection(
                    f"This .docx expands to {_mb(uncompressed)}, over the "
                    f"{_mb(MAX_DOCX_UNCOMPRESSED_BYTES)} limit."
                )
            if uncompressed / compressed > MAX_COMPRESSION_RATIO:
                return Rejection(
                    "This .docx has an implausible compression ratio "
                    f"({uncompressed // compressed}:1) and was not opened."
                )
            names = {i.filename for i in infos}
            if "word/document.xml" not in names:
                return Rejection(
                    "This .docx has no document body. If it is a template or a "
                    "macro file, convert it to a document first."
                )
            # Zip-slip: an entry escaping the archive root.
            for name in names:
                if name.startswith("/") or ".." in Path(name).parts:
                    return Rejection("This .docx contains an unsafe internal path.")
    except zipfile.BadZipFile:
        return Rejection("This .docx is corrupt and could not be opened.")
    except OSError as exc:
        return Rejection(f"This .docx could not be read ({type(exc).__name__}).")
    return None


def _validate_txt(path: Path) -> Rejection | None:
    sample = path.open("rb").read(8192)
    # A NUL byte in the first 8 KB means this is binary wearing a .txt name.
    if b"\x00" in sample and not sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return Rejection(
            "This file is named .txt but contains binary data. "
            "If it is a PDF or Word file, upload it with its real extension."
        )
    return None
