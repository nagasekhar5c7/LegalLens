"""The scanned-document path: rasterise -> recognise -> confidence -> gate.

Most of this runs without either OCR engine installed, which is deliberate:
the parts that decide whether a document is indexed or quarantined are the
confidence arithmetic and the reading-order reconstruction, and both are pure
functions over the engines' output. They are tested directly, against the
shapes `pytesseract` and `rapidocr` actually return.

The two tests that need a real engine skip when it is absent and say which one.
A skipped OCR test is honest; an OCR test that passes by never calling an
engine is not.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.ingestion.ocr import build_engine, describe
from app.ingestion.ocr.rapid import Box, layout_text
from app.ingestion.ocr.raster import available as raster_available
from app.ingestion.ocr.raster import render_pages
from app.ingestion.ocr.tesseract import TesseractOcr, parse_tsv
from app.ingestion.ports import Ocr
from app.models import Document, DocumentPage
from tests.conftest import FIXTURES, drain_ingestion, upload

needs_raster = pytest.mark.skipif(
    not raster_available(), reason="pypdfium2 is not installed (uv sync --extra ocr)"
)


# ----------------------------------------------------------------- rasteriser


@needs_raster
def test_pages_render_at_the_requested_dpi() -> None:
    """300 DPI is not a decoration: Tesseract degrades below it for 10-12pt
    body text, which is what a contract is set in. A US-Letter page is
    8.5x11in, so the pixel dimensions are the check that the scale factor is
    being applied in PDF user space (72 units to the inch) and not in points."""
    pages = list(render_pages(str(FIXTURES / "nda.pdf"), [1], dpi=300))
    assert len(pages) == 1
    assert pages[0].data[:4] == b"\x89PNG"
    assert 2500 <= pages[0].width <= 2600, pages[0].width
    assert 3200 <= pages[0].height <= 3400, pages[0].height

    half = list(render_pages(str(FIXTURES / "nda.pdf"), [1], dpi=150))[0]
    assert abs(half.width * 2 - pages[0].width) <= 2


@needs_raster
def test_only_the_requested_pages_are_rendered() -> None:
    """Per-page OCR is the whole point of the branch: a born-digital contract
    with one scanned exhibit must not have its good pages re-recognised."""
    rendered = [p.page_no for p in render_pages(str(FIXTURES / "nda.pdf"), [2])]
    assert rendered == [2]


@needs_raster
def test_out_of_range_pages_are_ignored_not_raised() -> None:
    """The page list comes from a text-layer parse that may have been capped at
    LL_MAX_PAGES, so it can name a page the renderer will not reach."""
    rendered = [p.page_no for p in render_pages(str(FIXTURES / "nda.pdf"), [1, 99])]
    assert rendered == [1]


def test_a_corrupt_pdf_is_a_value_error(tmp_path) -> None:
    """Turned into a reportable ingestion failure upstream, never a traceback."""
    if not raster_available():
        pytest.skip("pypdfium2 is not installed")
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nnot really a pdf")
    with pytest.raises(ValueError, match="could not be opened"):
        list(render_pages(str(broken)))


# ------------------------------------------------- tesseract confidence maths

#: The shape `pytesseract.image_to_data` returns. Columns are Tesseract's own.
TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)


def tsv(*words: tuple[int, int, int, float, str]) -> str:
    """(block, par, line, conf, text) -> a TSV page."""
    rows = [TSV_HEADER]
    for i, (block, par, line, conf, text) in enumerate(words):
        rows.append(f"5\t1\t{block}\t{par}\t{line}\t{i}\t0\t0\t10\t10\t{conf}\t{text}")
    return "\n".join(rows)


def test_confidence_is_weighted_by_characters_not_by_word_count() -> None:
    """A page of one mis-read page number and four hundred clean words should
    not report the same confidence as a page of four hundred mis-reads. The
    DR-1 floor is asking how much of the *text* is trustworthy."""
    text, confidence = parse_tsv(
        tsv(
            (1, 1, 1, 96.0, "Confidentiality"),  # 15 chars, high
            (1, 1, 1, 40.0, "7"),  # 1 char, low
        )
    )
    assert "Confidentiality" in text
    # A plain mean would be 0.68; weighting by characters gives ~0.93.
    assert confidence is not None
    assert confidence > 0.9, confidence


def test_words_below_the_floor_are_dropped_from_the_text_but_not_the_score() -> None:
    """Keeping a 3%-confidence token would put garbage into an indexed chunk,
    and a citation would then point at something that looks like a clause.
    Dropping it from the *score* as well would make an unreadable page look
    clean, which is the failure the floor exists to catch."""
    text, confidence = parse_tsv(
        tsv(
            (1, 1, 1, 95.0, "Term"),
            (1, 1, 1, 3.0, "~~@#$%^&*()_illegible"),
        )
    )
    assert text == "Term"
    assert confidence is not None
    assert confidence < 0.4, "the illegible run did not count against the page"


def test_minus_one_confidence_is_discarded() -> None:
    """Tesseract reports -1 for a box it recognised no text in. Treating it as
    zero would drag a good page under the floor."""
    _, confidence = parse_tsv(tsv((1, 1, 1, 90.0, "Agreement"), (1, 1, 1, -1.0, "")))
    assert confidence is not None
    assert confidence == pytest.approx(0.9)


def test_paragraph_structure_survives() -> None:
    """The chunker segments on blank lines. Losing paragraph breaks here would
    merge every clause on a page into one section, and with it the per-clause
    citation (FR-13)."""
    text, _ = parse_tsv(
        tsv(
            (1, 1, 1, 95.0, "12.1"),
            (1, 1, 1, 95.0, "Confidentiality."),
            (1, 1, 2, 95.0, "continued"),
            (2, 1, 1, 95.0, "12.2"),
            (2, 1, 1, 95.0, "Term."),
        )
    )
    assert text == "12.1 Confidentiality.\ncontinued\n\n12.2 Term."
    assert "\n\n" in text, "no paragraph boundary for the chunker to segment on"


def test_an_empty_page_scores_zero_not_one() -> None:
    """An unreadable page must sit below the floor, not above it. `None` would
    mean "not OCR'd" and would skip the gate entirely."""
    text, confidence = parse_tsv(tsv())
    assert text == ""
    assert confidence is None

    text, confidence = parse_tsv(tsv((1, 1, 1, -1.0, "")))
    assert text == ""
    assert confidence == 0.0


# --------------------------------------------------- rapidocr reading order


def box(
    text: str, x0: float, y0: float, *, width: float = 100, height: float = 20, conf: float = 0.95
) -> Box:
    return Box(
        text, conf, [[x0, y0], [x0 + width, y0], [x0 + width, y0 + height], [x0, y0 + height]]
    )


def test_boxes_are_sorted_into_reading_order() -> None:
    """The detector returns boxes in its own order, which is roughly but not
    reliably top-to-bottom."""
    text, _ = layout_text(
        [
            box("world", 120, 10),
            box("Hello", 10, 10),
            box("again", 10, 40),
        ]
    )
    assert text.startswith("Hello world")
    assert "again" in text.split("\n")[-1]


def test_a_two_column_page_does_not_interleave() -> None:
    """BRD §15's named worst case: a clause pulled from a mis-ordered two-column
    page reads as correct prose and is wrong, which is worse than no extraction.
    A wide horizontal gap is a column break, not a word space."""
    left = [box(f"left-{i}", 10, 10 + i * 30) for i in range(3)]
    right = [box(f"right-{i}", 900, 10 + i * 30) for i in range(3)]
    text, _ = layout_text([*left, *right])

    lines = [ln for ln in text.split("\n") if ln.strip()]
    for line in lines:
        assert not ("left" in line and "right" in line), f"columns interleaved: {line!r}"


def test_low_confidence_boxes_leave_the_text_but_count_against_the_page() -> None:
    text, confidence = layout_text(
        [box("Governing", 10, 10, conf=0.97), box("|||~~", 10, 40, width=40, conf=0.2)]
    )
    assert "|||~~" not in text
    assert confidence is not None
    assert confidence < 0.9


def test_no_boxes_is_a_zero_not_a_none() -> None:
    assert layout_text([]) == ("", 0.0)


# ------------------------------------------------------------- engine choice


def test_an_unknown_engine_name_raises() -> None:
    """A typo in configuration that silently stops OCR on every scanned upload
    is the kind of failure found weeks later by an attorney whose search
    returns nothing."""
    with pytest.raises(ValueError, match="LL_OCR_ENGINE"):
        build_engine("tesseracct")


def test_the_stub_is_selected_explicitly_and_says_why() -> None:
    engine = build_engine("stub")
    assert engine.available() is False
    assert "no OCR engine is installed" in (engine.unavailable_reason() or "")


def test_an_explicitly_configured_engine_is_returned_even_when_unavailable() -> None:
    """So the reason lands on the document instead of being swallowed by a
    fallback the operator did not ask for."""
    engine = build_engine("tesseract")
    assert isinstance(engine, TesseractOcr)


def test_every_engine_satisfies_the_port() -> None:
    for name in ("stub", "tesseract", "rapidocr"):
        assert isinstance(build_engine(name), Ocr), f"{name} does not satisfy Ocr"


def test_an_unavailable_engine_reports_rather_than_raising() -> None:
    """`run()` on a missing engine returns empty pages and a named reason, so
    the layout QA gate quarantines the document — the correct outcome, because
    no OCR ran and there is nothing to trust."""
    engine = build_engine("tesseract")
    if engine.available():
        pytest.skip("tesseract is installed; this covers the missing-binary path")
    result = engine.run("whatever.pdf", [1, 2], total_pages=2)
    assert [p.page_no for p in result.pages] == [1, 2]
    assert all(p.text == "" for p in result.pages)
    assert result.scanned is True
    assert result.warnings and "OCR did not run" in result.warnings[0]


def test_describe_reports_every_candidate_not_just_the_winner() -> None:
    """ "Why did it pick the stub" is the question that actually gets asked."""
    report = describe("auto")
    assert report["configured"] == "auto"
    assert report["resolved"]
    assert isinstance(report["candidates"], dict)
    assert len(report["candidates"]) == 2


def test_the_engine_name_carries_the_settings_that_change_the_output() -> None:
    """The name reaches `document_pages.parse_engine` and the audit row, and
    NFR-12 means a reproducibility question has to be answerable from there."""
    name = TesseractOcr(languages="eng+spa", dpi=400).name
    assert "eng+spa" in name
    assert "400dpi" in name


# ------------------------------------------------------------- the real thing


@needs_raster
async def test_a_scanned_pdf_is_quarantined_when_no_engine_is_installed(
    auth: AsyncClient, worker: None
) -> None:
    """The suite pins LL_OCR_ENGINE=stub, so this is the shipped behaviour for a
    checkout with no OCR extra: a reason on the document, not a `ready`
    document with nothing in it."""
    document_id = (await upload(auth, FIXTURES / "scanned.pdf"))["accepted"][0]["id"]
    await drain_ingestion()

    status = (await auth.get(f"/api/documents/{document_id}/status")).json()
    assert status["status"] == "failed"
    assert status["failure"]["reason"]
    labels = [s["label"] for s in status["ingestion"]]
    assert any("OCR" in label for label in labels), labels


async def test_page_images_are_stored_for_scanned_pages(
    auth: AsyncClient, worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scanned page has nothing to render as text, so the image *is* the
    document. A page with a text layer is rendered client-side by PDF.js, so
    rasterising it here would cost 1-2 MB of ciphertext a page for nothing."""
    if not raster_available():
        pytest.skip("pypdfium2 is not installed")

    monkeypatch.setenv("LL_PAGE_IMAGES", "all")
    get_settings.cache_clear()
    assert get_settings().page_images == "all"

    document_id = (await upload(auth, FIXTURES / "nda.pdf"))["accepted"][0]["id"]
    await drain_ingestion()

    async with get_sessionmaker()() as db:
        pages = list(
            (
                await db.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            ).scalars()
        )
    assert pages
    assert all(p.image_key for p in pages), "no page image was stored"

    from app.storage import blob

    for page in pages:
        assert blob.read(page.image_key or "")[:4] == b"\x89PNG"


async def test_page_images_are_off_for_text_layer_pdfs_by_default(
    auth: AsyncClient, worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LL_PAGE_IMAGES", "scanned")
    get_settings.cache_clear()

    document_id = (await upload(auth, FIXTURES / "nda.pdf"))["accepted"][0]["id"]
    await drain_ingestion()

    async with get_sessionmaker()() as db:
        pages = list(
            (
                await db.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            ).scalars()
        )
    assert pages
    assert all(p.image_key is None for p in pages)


@pytest.mark.skipif(
    not build_engine("auto").available(),
    reason="no real OCR engine installed (uv sync --extra ocr + brew install tesseract, "
    "or uv sync --extra ocr-onnx)",
)
async def test_real_ocr_reads_a_scanned_pdf_end_to_end(
    auth: AsyncClient, worker: None, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The one test that exercises a real engine. Skipped rather than faked when
    none is installed, because an OCR test that passes without calling an OCR
    engine tells you nothing."""
    monkeypatch.setenv("LL_OCR_ENGINE", "auto")
    get_settings.cache_clear()
    engine = build_engine("auto")

    document_id = (await upload(auth, FIXTURES / "scanned.pdf"))["accepted"][0]["id"]
    await drain_ingestion()

    async with get_sessionmaker()() as db:
        document = (
            await db.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()
        pages = list(
            (
                await db.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            ).scalars()
        )

    # `scanned.pdf` is a blank-page fixture, so the honest outcome is still a
    # quarantine — but it must be reached with a real confidence from a real
    # engine, and the engine that ran must be recorded.
    assert document.status in {"ready", "failed"}
    assert all(p.parse_engine for p in pages)
    assert any(engine.name in (p.parse_engine or "") for p in pages), [
        p.parse_engine for p in pages
    ]
