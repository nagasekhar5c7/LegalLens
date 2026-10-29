"""PDF text layer via pypdf.

What this does: pulls the text layer page by page, preserving per-page character
offsets, which is what makes a span resolvable (FR-4, FR-11, FR-13).

What this deliberately does not do: layout-aware parsing. Reading order on a
two-column opinion, exhibit stamps over body text, and substantive footnotes are
BRD §15's highest risk and are M4's problem behind the `Parser` port. The
text-layer probe below is how a document that needs that work gets routed to OCR
instead of being silently indexed with mis-ordered text.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.ingestion.ports import ParsedPage, ParseResult

log = logging.getLogger("legallens.ingest.pdf")


class PdfParser:
    name = "pdf/pypdf"

    def supports(self, ext: str) -> bool:
        return ext == ".pdf"

    def page_count(self, path: str) -> int:
        from pypdf import PdfReader

        return len(PdfReader(path, strict=False).pages)

    def parse(self, path: str, *, max_pages: int) -> ParseResult:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            # An empty owner password is common on filed documents and is worth
            # one attempt; a real password is a parse failure with a next action.
            try:
                reader.decrypt("")
            except (NotImplementedError, PdfReadError) as exc:
                raise ValueError("This PDF is password-protected and cannot be read.") from exc

        pages: list[ParsedPage] = []
        warnings: list[str] = []
        total = len(reader.pages)

        for i, page in enumerate(reader.pages[:max_pages], start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001 — one bad page must not fail the file
                log.warning("page %s of %s failed to extract: %s", i, path, type(exc).__name__)
                warnings.append(f"page {i}: extraction failed ({type(exc).__name__})")
                text = ""
            pages.append(ParsedPage(page_no=i, text=_normalise(text), engine=self.name))

        if total > max_pages:
            warnings.append(f"only the first {max_pages} of {total} pages were parsed")

        return ParseResult(
            pages=pages,
            scanned=has_no_text_layer(pages),
            engine=self.name,
            warnings=warnings,
        )


def _normalise(text: str) -> str:
    """pypdf emits ragged line breaks mid-sentence. Joining them matters because
    a span highlight that straddles a spurious newline looks broken."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        if (
            out
            and out[-1]
            and not out[-1].endswith((".", ":", ";", "?", "!", '"', "”", ")"))
            and stripped[:1].islower()
        ):
            out[-1] = f"{out[-1]} {stripped}"
        else:
            out.append(stripped)
    collapsed: list[str] = []
    for line in out:
        if line or (collapsed and collapsed[-1]):
            collapsed.append(line)
    return "\n".join(collapsed).strip()


def has_no_text_layer(pages: list[ParsedPage]) -> bool:
    """The text-layer probe: chars-per-page against a floor.

    A scanned fax yields a handful of stray characters per page, not zero, so a
    `== 0` check would misclassify it as machine-readable and index garbage.
    """
    if not pages:
        return True
    floor = get_settings().text_layer_chars_per_page_floor
    mean_chars = sum(len(p.text) for p in pages) / len(pages)
    return mean_chars < floor
