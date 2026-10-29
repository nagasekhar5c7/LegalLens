"""RapidOCR — the no-system-binary alternative to Tesseract.

**Why this engine exists alongside `tesseract.py`.** Tesseract gives the better
confidence signal (per word, from character recognition) and is therefore the
default. It is also a system package, which means `uv sync` does not get you a
working OCR path: a developer, a CI image and a container all have to install it
separately. RapidOCR ships PP-OCRv4 as ONNX models inside the wheel — about
15 MB, Apache-2.0, CPU-only, no `brew` step — so `uv sync --extra ocr-onnx` is
the whole install.

**What the choice costs, stated rather than glossed.** RapidOCR scores per
detected text box, not per word, and the score comes from the recogniser's own
confidence over a box the detector chose. It is a coarser number than
Tesseract's, and the DR-1 floor is calibrated against a scale, so a deployment
that switches engines should re-check `LL_OCR_CONFIDENCE_FLOOR` rather than
assume it transfers. `document_pages.parse_engine` records which engine produced
each page precisely so that question is answerable after the fact.

**Reading order is reconstructed here, not trusted.** The detector returns boxes
in its own order, which is roughly but not reliably top-to-bottom. Boxes are
grouped into lines by vertical overlap and sorted left-to-right within a line,
because a mis-ordered page produces text that reads as plausible prose while
belonging to two different columns — BRD §15's named worst case, and the reason
the layout QA gate exists at all.
"""

from __future__ import annotations

import logging
from importlib.util import find_spec
from typing import Any

from app.ingestion.ocr.raster import DEFAULT_DPI, PageImage, render_pages
from app.ingestion.ports import ParsedPage, ParseResult

log = logging.getLogger("legallens.ingest.ocr")

#: Two boxes belong to the same line when they overlap vertically by at least
#: this fraction of the shorter box's height. 0.5 groups a superscript or a
#: slightly skewed scan with its line without merging two adjacent columns.
LINE_OVERLAP = 0.5

#: Below this a box is noise. PP-OCRv4 returns a 0-1 score; 0.5 is its own
#: demo default and is the point below which output is usually not words.
BOX_CONFIDENCE_FLOOR = 0.5

#: Horizontal gap, as a multiple of line height, that reads as a column break
#: rather than a word space. Two boxes further apart than this on the same line
#: are kept as separate lines so a two-column page does not interleave.
COLUMN_GAP = 2.0


class RapidOcr:
    """PP-OCRv4 via onnxruntime. No system dependency."""

    def __init__(self, *, dpi: int = DEFAULT_DPI) -> None:
        self.dpi = dpi
        self._engine: Any | None = None

    @property
    def name(self) -> str:
        return f"ocr/rapidocr-ppocrv4@{self.dpi}dpi"

    def available(self) -> bool:
        return self._module_name() is not None and find_spec("pypdfium2") is not None

    def unavailable_reason(self) -> str | None:
        if find_spec("pypdfium2") is None:
            return "pypdfium2 is not installed (uv sync --extra ocr)"
        if self._module_name() is None:
            return "rapidocr-onnxruntime is not installed (uv sync --extra ocr-onnx)"
        return None

    @staticmethod
    def _module_name() -> str | None:
        """The package was renamed between major versions; both are supported
        so an install that pulled the newer one still works."""
        for candidate in ("rapidocr_onnxruntime", "rapidocr"):
            if find_spec(candidate) is not None:
                return candidate
        return None

    def _load(self) -> Any:
        if self._engine is not None:
            return self._engine
        import importlib

        module = importlib.import_module(self._module_name() or "rapidocr_onnxruntime")
        self._engine = module.RapidOCR()
        return self._engine

    def run(self, path: str, pages: list[int], *, total_pages: int) -> ParseResult:
        reason = self.unavailable_reason()
        if reason is not None:
            return ParseResult(
                pages=[
                    ParsedPage(page_no=p, text="", ocr_confidence=None, engine=self.name)
                    for p in pages
                ],
                scanned=True,
                engine=self.name,
                warnings=[f"OCR did not run: {reason}"],
            )

        engine = self._load()
        out: list[ParsedPage] = []
        warnings: list[str] = []

        for image in render_pages(path, pages, dpi=self.dpi):
            try:
                boxes = _normalise_output(engine(_to_array(image)))
            except Exception as exc:  # noqa: BLE001 — one bad page must not fail the file
                log.warning("rapidocr failed on page %s: %s", image.page_no, exc)
                warnings.append(f"page {image.page_no}: OCR failed ({type(exc).__name__})")
                out.append(
                    ParsedPage(
                        page_no=image.page_no, text="", ocr_confidence=None, engine=self.name
                    )
                )
                continue

            text, confidence = layout_text(boxes)
            out.append(
                ParsedPage(
                    page_no=image.page_no,
                    text=text,
                    ocr_confidence=confidence,
                    engine=self.name,
                )
            )

        missing = sorted(set(pages) - {p.page_no for p in out})
        if missing:
            warnings.append(f"page(s) {missing} could not be rendered and were not recognised")
            out.extend(
                ParsedPage(page_no=p, text="", ocr_confidence=None, engine=self.name)
                for p in missing
            )

        out.sort(key=lambda p: p.page_no)
        return ParseResult(pages=out, scanned=True, engine=self.name, warnings=warnings)


# ------------------------------------------------------------------ geometry


class Box:
    """One detected text box, reduced to what reading order needs."""

    __slots__ = ("confidence", "text", "x0", "x1", "y0", "y1")

    def __init__(self, text: str, confidence: float, points: list[list[float]]) -> None:
        xs = [float(p[0]) for p in points]
        ys = [float(p[1]) for p in points]
        self.text = text
        self.confidence = confidence
        self.x0, self.x1 = min(xs), max(xs)
        self.y0, self.y1 = min(ys), max(ys)

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)


def layout_text(boxes: list[Box]) -> tuple[str, float | None]:
    """Boxes -> page text in reading order, plus a length-weighted confidence.

    Weighted by characters for the same reason as the Tesseract path: the floor
    is asking "how much of this text can be trusted", not "how did the average
    box do".
    """
    if not boxes:
        return "", 0.0

    weighted = sum(b.confidence * len(b.text) for b in boxes)
    chars = sum(len(b.text) for b in boxes)
    confidence = round(weighted / chars, 4) if chars else 0.0

    kept = [b for b in boxes if b.confidence >= BOX_CONFIDENCE_FLOOR and b.text.strip()]
    if not kept:
        return "", confidence

    lines: list[list[Box]] = []
    for box in sorted(kept, key=lambda b: (b.y0, b.x0)):
        placed = False
        for line in lines:
            reference = line[-1]
            overlap = min(box.y1, reference.y1) - max(box.y0, reference.y0)
            if overlap >= LINE_OVERLAP * min(box.height, reference.height):
                # Same band vertically, but a wide horizontal gap on a page of
                # columns is a column break, not a space.
                if box.x0 - reference.x1 > COLUMN_GAP * reference.height:
                    continue
                line.append(box)
                placed = True
                break
        if not placed:
            lines.append([box])

    rendered: list[str] = []
    previous_bottom: float | None = None
    for line in lines:
        line.sort(key=lambda b: b.x0)
        top = min(b.y0 for b in line)
        height = max(b.height for b in line)
        # A vertical gap wider than a line is a paragraph break. The chunker
        # segments on `\n\n`, so getting this wrong merges every clause on the
        # page into one section.
        if previous_bottom is not None and top - previous_bottom > height * 0.8:
            rendered.append("")
        rendered.append(" ".join(b.text.strip() for b in line))
        previous_bottom = max(b.y1 for b in line)

    return "\n".join(rendered).strip(), confidence


def _normalise_output(raw: Any) -> list[Box]:
    """RapidOCR returns `(result, elapse)` where result is
    `[[points, text, score], ...]`, or `None` for a page it found no text on.
    The newer `rapidocr` package returns an object with `.boxes/.txts/.scores`.
    Both are flattened here so the engine class does not branch on versions."""
    result = raw[0] if isinstance(raw, tuple) else raw
    if result is None:
        return []

    if hasattr(result, "txts"):
        boxes = getattr(result, "boxes", None) or []
        texts = getattr(result, "txts", None) or []
        scores = getattr(result, "scores", None) or []
        return [
            Box(str(t), float(s), [list(p) for p in box])
            for box, t, s in zip(boxes, texts, scores, strict=False)
        ]

    out: list[Box] = []
    for item in result:
        if not item or len(item) < 3:
            continue
        points, text, score = item[0], item[1], item[2]
        out.append(Box(str(text), float(score), [list(p) for p in points]))
    return out


def _to_array(image: PageImage) -> Any:
    """RapidOCR takes a numpy array (or a path). Decoding through Pillow keeps
    the rasteriser's PNG bytes as the one interchange format between engines."""
    import io

    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(image.data)) as handle:
        return np.asarray(handle.convert("RGB"))
