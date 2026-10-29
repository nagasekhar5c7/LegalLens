"""OCR engine selection.

`LL_OCR_ENGINE` picks one:

    auto        tesseract if its binary is on PATH, else rapidocr if installed,
                else the stub. The default.
    tesseract   real OCR, best confidence signal, needs a system binary.
    rapidocr    real OCR, no system binary, coarser confidence.
    stub        no OCR. Reports that OCR *would* have run and produces no text,
                so the document is quarantined rather than marked ready empty.

**Why `auto` rather than a fixed default.** The backend's standing rule is that
it must stay installable and startable without the optional halves — the AI
extra, the model weights, a vector store. OCR is the same: `uv sync` with no
extras has to produce a working backend, and that backend cannot do OCR. The
alternative, defaulting to `tesseract`, means a fresh checkout fails every
scanned upload with a missing-binary error, which reads as a bug rather than as
a deliberate build boundary.

**What `auto` must never do is hide which engine ran.** A silent fallback is how
a demo "works" on one machine and produces empty documents on another. So the
resolved engine name lands in three places that outlive the process: each page's
`document_pages.parse_engine`, the document's `ocr_json.engine`, and the
`ingest.complete` audit row. `describe()` reports the same thing at startup and
on the readiness endpoint, so the answer is available before an upload rather
than after one.

**Why preference order is tesseract, then rapidocr.** DR-1's confidence floor
decides between indexing a document and quarantining it. Tesseract's per-word
confidence is the better input to that decision than RapidOCR's per-box score —
see the two modules for the full argument. Reproducibility across machines is
not sacrificed by the preference, because the engine that ran is recorded per
page rather than assumed from configuration (NFR-12).
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.ingestion.ocr.raster import DEFAULT_DPI, PageImage, RasterUnavailable, render_pages
from app.ingestion.ports import ParseResult

log = logging.getLogger("legallens.ingest.ocr")

ENGINES = ("auto", "tesseract", "rapidocr", "stub")


@runtime_checkable
class OcrEngine(Protocol):
    """The `Ocr` port plus the two availability methods the selector needs.

    `available()` is separate from `run()` returning empty because the pipeline
    has to be able to say *why* before it starts: "no OCR engine is configured"
    is a different failure, with a different next action, from "OCR ran and the
    page was illegible".
    """

    @property
    def name(self) -> str: ...

    def available(self) -> bool: ...

    def unavailable_reason(self) -> str | None: ...

    def run(self, path: str, pages: list[int], *, total_pages: int) -> ParseResult: ...


def build_engine(
    choice: str = "auto", *, dpi: int = DEFAULT_DPI, languages: str = "eng"
) -> OcrEngine:
    """Resolve `LL_OCR_ENGINE` to an engine instance.

    Never raises on an unavailable engine: an explicitly configured engine that
    is not installed is returned anyway, so that its `run()` reports the missing
    dependency on the document rather than crashing the worker. An unknown name
    *does* raise, because a typo in configuration should not silently degrade to
    the stub.
    """
    from app.ingestion.stub.ocr import StubOcr

    name = (choice or "auto").strip().lower()
    if name not in ENGINES:
        raise ValueError(f"LL_OCR_ENGINE must be one of {ENGINES}, not {name!r}")

    if name == "stub":
        return StubOcr()

    if name == "tesseract":
        from app.ingestion.ocr.tesseract import TesseractOcr

        return TesseractOcr(languages=languages, dpi=dpi)

    if name == "rapidocr":
        from app.ingestion.ocr.rapid import RapidOcr

        return RapidOcr(dpi=dpi)

    # auto: best available, in the order argued for in the module docstring.
    from app.ingestion.ocr.tesseract import TesseractOcr

    tesseract = TesseractOcr(languages=languages, dpi=dpi)
    if tesseract.available():
        return tesseract

    from app.ingestion.ocr.rapid import RapidOcr

    rapid = RapidOcr(dpi=dpi)
    if rapid.available():
        log.info(
            "OCR engine: %s (tesseract unavailable — %s)",
            rapid.name,
            tesseract.unavailable_reason(),
        )
        return rapid

    stub = StubOcr()
    log.warning(
        "no real OCR engine is available, falling back to %s. Scanned documents will be "
        "quarantined rather than indexed. tesseract: %s; rapidocr: %s",
        stub.name,
        tesseract.unavailable_reason(),
        rapid.unavailable_reason(),
    )
    return stub


def describe(
    choice: str = "auto", *, dpi: int = DEFAULT_DPI, languages: str = "eng"
) -> dict[str, object]:
    """What the readiness endpoint and the startup log report.

    Includes every candidate's status, not just the winner, because "why did it
    pick the stub" is the question that actually gets asked.
    """
    from app.ingestion.ocr.rapid import RapidOcr
    from app.ingestion.ocr.tesseract import TesseractOcr

    resolved = build_engine(choice, dpi=dpi, languages=languages)
    candidates = {}
    for engine in (TesseractOcr(languages=languages, dpi=dpi), RapidOcr(dpi=dpi)):
        candidates[engine.name] = engine.unavailable_reason() or "available"
    return {
        "configured": choice,
        "resolved": resolved.name,
        "real": resolved.available(),
        "candidates": candidates,
    }


__all__ = [
    "DEFAULT_DPI",
    "ENGINES",
    "OcrEngine",
    "PageImage",
    "RasterUnavailable",
    "build_engine",
    "describe",
    "render_pages",
]
