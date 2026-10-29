"""PDF page -> bitmap, via pypdfium2.

OCR needs pixels, and so does the viewer when a document has no text layer to
render as text. Both go through this one module.

**Why pypdfium2 and not the obvious alternatives.** This is a licensing decision
before it is a technical one, and the same decision the embedder made when it
ruled out a CC-BY-NC model:

  * `pypdfium2` — Apache-2.0 / BSD-3-Clause, Google's PDFium behind a
    self-contained wheel. No system package, so `uv sync` is the whole install.
  * `PyMuPDF` — faster and nicer to use, and **AGPL-3.0** unless a commercial
    licence is bought. A product billing attorneys cannot ship AGPL inference
    code on a hunch, so it is out regardless of how well it performs.
  * `pdf2image` — a thin wrapper over the poppler `pdftoppm` binary, which means
    a system dependency (`brew install poppler`) that CI and every developer
    machine has to acquire before ingestion works at all.

**Why 300 DPI by default.** Tesseract's own guidance is that recognition degrades
below roughly 300 DPI for 10-12pt body text, which is what a contract is set in.
A US-Letter page at 300 DPI is 2550x3300, about 8 MP — renderable in well under a
second, and roughly 1-2 MB as PNG. 150 DPI halves the memory and measurably
loses small print; 600 DPI quadruples the cost for text this size and buys
nothing. The number is configurable because a scan of a scan sometimes needs
more.

Rendering is sequential and page-at-a-time on purpose: a 500-page document at
8 MP a page is ~12 GB if the bitmaps are held at once, so each page is converted
to PNG bytes and the bitmap released before the next is rendered.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.util import find_spec

log = logging.getLogger("legallens.ingest.raster")

#: See the module docstring. Tesseract's floor for body text, not a guess.
DEFAULT_DPI = 300

#: PDF user space is 72 units to the inch, which is what the scale factor is
#: relative to. `scale=300/72` is how pypdfium2 spells "300 DPI".
PDF_UNITS_PER_INCH = 72


class RasterUnavailable(RuntimeError):
    """pypdfium2 is not installed. Reported, never worked around: silently
    skipping OCR would mark a scanned document `ready` with no text in it."""


@dataclass
class PageImage:
    """One rendered page. `data` is PNG bytes, ready to encrypt and store."""

    page_no: int
    data: bytes
    width: int
    height: int
    dpi: int

    @property
    def megapixels(self) -> float:
        return (self.width * self.height) / 1_000_000


def available() -> bool:
    """Whether rasterisation is possible in this install.

    Checked by import spec rather than by importing: this is called on the
    startup/readiness path, and importing the PDFium binding costs a few MB of
    RSS that a backend running the stub OCR has no reason to pay.
    """
    return find_spec("pypdfium2") is not None


def render_pages(
    path: str,
    pages: list[int] | None = None,
    *,
    dpi: int = DEFAULT_DPI,
    max_pages: int | None = None,
) -> Iterator[PageImage]:
    """Yield `PageImage` for the requested 1-based page numbers, in order.

    A generator, not a list: the caller OCRs or encrypts each page and lets it
    go, which is what keeps a 500-page render inside a sane memory ceiling.

    Raises `RasterUnavailable` if pypdfium2 is not installed, and `ValueError`
    if the file cannot be opened — both of which the pipeline turns into a
    reportable ingestion failure rather than a traceback.
    """
    if not available():
        raise RasterUnavailable(
            "pypdfium2 is not installed, so PDF pages cannot be rendered. "
            "Install the `ocr` extra: uv sync --extra ocr"
        )

    import pypdfium2 as pdfium

    scale = dpi / PDF_UNITS_PER_INCH
    try:
        document = pdfium.PdfDocument(path)
    except Exception as exc:  # noqa: BLE001 — pdfium raises its own error types
        raise ValueError(
            f"the PDF could not be opened for rendering ({type(exc).__name__})"
        ) from exc

    try:
        total = len(document)
        wanted = sorted({p for p in (pages or range(1, total + 1)) if 1 <= p <= total})
        if max_pages is not None:
            wanted = wanted[:max_pages]

        for page_no in wanted:
            page = document[page_no - 1]
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                try:
                    buffer = io.BytesIO()
                    # `optimize` is deliberately off: it costs real CPU per page
                    # and these are encrypted immediately, so a few percent of
                    # file size is not worth it on the ingestion path.
                    image.save(buffer, format="PNG")
                    yield PageImage(
                        page_no=page_no,
                        data=buffer.getvalue(),
                        width=image.width,
                        height=image.height,
                        dpi=dpi,
                    )
                finally:
                    image.close()
            finally:
                page.close()
    finally:
        document.close()
