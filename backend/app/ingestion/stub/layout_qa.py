"""STUB. Real reading-order, column and table checks are M4.

BRD §15 names this as the build's highest risk and probably its largest hidden
cost. The gate exists now so that the *shape* is right — a document can be
quarantined before indexing, and the UI has somewhere to show flagged pages —
but the checks below are proxies, not the real thing, and are labelled as such
in the warning they emit.
"""

from __future__ import annotations

import re

from app.config import get_settings
from app.ingestion.ports import LayoutQaResult, ParseResult

#: A page of mostly very short lines is the signature of a mis-ordered
#: two-column parse: the columns interleave into fragments.
SHORT_LINE_CHARS = 28


class StubLayoutQa:
    name = "layout-qa/stub-m3"

    def check(self, result: ParseResult) -> LayoutQaResult:
        settings = get_settings()
        flagged: list[int] = []
        scores: list[float] = []

        for page in result.pages:
            if page.ocr_confidence is not None and (
                page.ocr_confidence < settings.ocr_confidence_floor
            ):
                flagged.append(page.page_no)
            lines = [ln for ln in page.text.split("\n") if ln.strip()]
            if not lines:
                scores.append(0.0 if page.ocr_confidence is None else 0.5)
                continue
            short = sum(1 for ln in lines if len(ln) < SHORT_LINE_CHARS)
            fragmentation = short / len(lines)
            scores.append(max(0.0, 1.0 - fragmentation))

        reading_order = sum(scores) / max(1, len(scores))

        ocr_pages = [p for p in result.pages if p.ocr_confidence is not None]
        if ocr_pages:
            mean_conf = sum(p.ocr_confidence or 0 for p in ocr_pages) / len(ocr_pages)
            if mean_conf < settings.ocr_confidence_floor:
                return LayoutQaResult(
                    reading_order_score=reading_order,
                    column_sanity=False,
                    table_integrity=True,
                    quarantine=True,
                    reason=(
                        f"OCR confidence averaged {mean_conf:.2f}, below the "
                        f"{settings.ocr_confidence_floor:.2f} floor, so the document was not "
                        "indexed rather than indexed with unreliable text."
                    ),
                    flagged_pages=flagged,
                )

        # A document with effectively no extractable text is not indexable, and
        # saying so is more useful than a `ready` document that answers nothing.
        if result.total_chars < 40 * max(1, result.page_count):
            return LayoutQaResult(
                reading_order_score=reading_order,
                column_sanity=False,
                table_integrity=True,
                quarantine=True,
                reason=(
                    f"Only {result.total_chars} characters were extracted across "
                    f"{result.page_count} page(s). There is no usable text layer and no OCR "
                    "engine is configured, so nothing could be indexed."
                ),
                flagged_pages=flagged,
            )

        return LayoutQaResult(
            reading_order_score=reading_order,
            column_sanity=reading_order > 0.45,
            table_integrity=_tables_look_intact(result),
            quarantine=False,
            flagged_pages=flagged,
        )


_ROW_SEPARATORS = re.compile(r"\|")


def _tables_look_intact(result: ParseResult) -> bool:
    """Proxy: if any page carries pipe-delimited rows, assume the table survived.
    A real integrity check compares cell counts per row and is M4's."""
    return not any(_ROW_SEPARATORS.search(p.text) and p.text.count("|") < 2 for p in result.pages)
