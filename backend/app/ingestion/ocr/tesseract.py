"""Tesseract OCR, via pytesseract over the pypdfium2 rasteriser.

**The decision, and the alternative it beat.** Tesseract is the default real
engine because of one property the others do not match: it reports a confidence
per recognised word. DR-1 is not a logging requirement — `LL_OCR_CONFIDENCE_FLOOR`
decides whether a document is indexed or quarantined, and that gate is only
honest if the number feeding it means something. Tesseract's TSV output gives a
0-100 confidence per word, so a page's confidence is a real length-weighted mean
over its own words.

RapidOCR (`ocr/rapid.py`, the configured alternative) scores per detected text
*box*, which is coarser and is calibrated by its own detector rather than by
character recognition — a box of clean text that the detector found awkwardly can
score low, and a box of confident nonsense can score high. It remains the better
choice when a system binary is not acceptable, which is why both exist.

**The cost of this choice, stated plainly:** Tesseract is a system binary
(`brew install tesseract`, `apt install tesseract-ocr`). `uv sync` does not
provide it. `available()` therefore probes for the executable and the pipeline
reports its absence as a named condition, never as a traceback and never by
quietly indexing an empty document.

**Why `--psm 1` (automatic page segmentation with orientation detection).**
Filed exhibits arrive rotated and two-column opinions arrive as two columns.
PSM 3, the pytesseract default, does page segmentation but no orientation
detection, so a sideways fax comes back as noise with high confidence — the worst
possible combination. PSM 1 costs an extra orientation pass per page and is worth
it. PSM 6 ("assume a single uniform block") is faster and actively wrong on
anything with a column, a header or a signature block.

**Why OEM 1 (LSTM only).** The legacy engine is kept in Tesseract 4/5 for
backwards compatibility and is worse on modern scans; OEM 3 ("both") runs it
anyway on some installs. Pinning LSTM makes the output reproducible across
machines that happen to ship different legacy data files, which NFR-12 needs.
"""

from __future__ import annotations

import csv
import io
import logging
import shutil
from importlib.util import find_spec

from app.ingestion.ocr.raster import DEFAULT_DPI, PageImage, render_pages
from app.ingestion.ports import ParsedPage, ParseResult

log = logging.getLogger("legallens.ingest.ocr")

#: Automatic page segmentation *with* orientation and script detection. See the
#: module docstring — a rotated exhibit is the common case this guards against.
PSM = 1
#: LSTM only, for reproducibility across installs.
OEM = 1

#: Tesseract reports -1 for a box it recognised no text in. Treating that as a
#: confidence of zero would drag a good page below the floor; it carries no
#: information and is dropped.
NO_CONFIDENCE = -1

#: Below this, a word is noise rather than a reading. Kept out of the page text
#: as well as out of the mean: a 3%-confidence token is not evidence, and
#: letting it into an indexed chunk is how a citation ends up pointing at
#: garbage that looks like a clause.
WORD_CONFIDENCE_FLOOR = 30.0


class TesseractOcr:
    """Real OCR. Rasterise at `dpi`, recognise, keep per-word confidence."""

    def __init__(
        self,
        *,
        languages: str = "eng",
        dpi: int = DEFAULT_DPI,
        psm: int = PSM,
        oem: int = OEM,
    ) -> None:
        self.languages = languages
        self.dpi = dpi
        self.psm = psm
        self.oem = oem

    @property
    def name(self) -> str:
        # The engine name reaches `document_pages.parse_engine` and the audit
        # row, so it carries the settings that change the output (NFR-12).
        return f"ocr/tesseract-{self.languages}-psm{self.psm}@{self.dpi}dpi"

    def available(self) -> bool:
        """Both halves have to be present: the Python wrapper and the binary it
        shells out to. Reporting them together is the difference between
        "install the extra" and "install the extra *and* brew install tesseract"."""
        return (
            find_spec("pytesseract") is not None
            and find_spec("pypdfium2") is not None
            and shutil.which("tesseract") is not None
        )

    def unavailable_reason(self) -> str | None:
        if find_spec("pypdfium2") is None:
            return "pypdfium2 is not installed (uv sync --extra ocr)"
        if find_spec("pytesseract") is None:
            return "pytesseract is not installed (uv sync --extra ocr)"
        if shutil.which("tesseract") is None:
            return (
                "the tesseract binary is not on PATH — it is a system package, "
                "not a Python one (macOS: brew install tesseract)"
            )
        return None

    def run(self, path: str, pages: list[int], *, total_pages: int) -> ParseResult:
        reason = self.unavailable_reason()
        if reason is not None:
            # A ParseResult with empty pages and no confidence, plus the reason.
            # The layout QA gate then quarantines the document, which is the
            # correct outcome: no OCR ran, so there is nothing to trust.
            return ParseResult(
                pages=[
                    ParsedPage(page_no=p, text="", ocr_confidence=None, engine=self.name)
                    for p in pages
                ],
                scanned=True,
                engine=self.name,
                warnings=[f"OCR did not run: {reason}"],
            )

        import pytesseract

        config = f"--oem {self.oem} --psm {self.psm}"
        out: list[ParsedPage] = []
        warnings: list[str] = []

        for image in render_pages(path, pages, dpi=self.dpi):
            try:
                tsv = pytesseract.image_to_data(_to_pil(image), lang=self.languages, config=config)
            except Exception as exc:  # noqa: BLE001 — one bad page must not fail the file
                log.warning("tesseract failed on page %s: %s", image.page_no, exc)
                warnings.append(f"page {image.page_no}: OCR failed ({type(exc).__name__})")
                out.append(
                    ParsedPage(
                        page_no=image.page_no, text="", ocr_confidence=None, engine=self.name
                    )
                )
                continue

            text, confidence = parse_tsv(tsv)
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


def parse_tsv(tsv: str) -> tuple[str, float | None]:
    """Turn Tesseract's TSV into page text plus a length-weighted confidence.

    Length-weighted and not a plain mean: a page of one mis-read page number and
    four hundred clean words should not report the same confidence as a page of
    four hundred mis-reads. Weighting by the characters a word contributes makes
    the number track how much of the *text* is trustworthy, which is what the
    DR-1 floor is actually asking.

    Line structure is rebuilt from `block_num`/`par_num`/`line_num` rather than
    from word coordinates. The chunker segments on blank lines, so losing
    paragraph breaks here would merge every clause on a page into one section.
    """
    rows = list(csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE))
    if not rows:
        return "", None

    lines: dict[tuple[int, int, int], list[str]] = {}
    weighted = 0.0
    chars = 0

    for row in rows:
        word = (row.get("text") or "").strip()
        if not word:
            continue
        try:
            confidence = float(row.get("conf") or NO_CONFIDENCE)
            key = (
                int(row.get("block_num") or 0),
                int(row.get("par_num") or 0),
                int(row.get("line_num") or 0),
            )
        except ValueError:
            continue

        if confidence <= NO_CONFIDENCE:
            continue
        if confidence < WORD_CONFIDENCE_FLOOR:
            # Dropped from the text, but still counted against the page's
            # confidence — otherwise discarding the bad words would make an
            # unreadable page look clean.
            weighted += confidence * len(word)
            chars += len(word)
            continue

        lines.setdefault(key, []).append(word)
        weighted += confidence * len(word)
        chars += len(word)

    if not chars:
        return "", 0.0

    # Blank line between blocks and paragraphs, single newline within one, so
    # the chunker's `\n\n` paragraph split lands on real paragraph boundaries.
    text_parts: list[str] = []
    previous: tuple[int, int, int] | None = None
    for key in sorted(lines):
        if previous is not None:
            text_parts.append("\n\n" if key[:2] != previous[:2] else "\n")
        text_parts.append(" ".join(lines[key]))
        previous = key

    # Tesseract's 0-100 scale, normalised to the 0-1 the floor is expressed in.
    return "".join(text_parts).strip(), round(weighted / chars / 100, 4)


def _to_pil(image: PageImage):  # type: ignore[no-untyped-def]
    from PIL import Image

    return Image.open(io.BytesIO(image.data))
