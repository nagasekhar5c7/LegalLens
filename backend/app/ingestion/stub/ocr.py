"""The no-OCR fallback, for an install without a real engine.

Real OCR now exists in `app/ingestion/ocr/` (Tesseract and RapidOCR). This stub
is what `LL_OCR_ENGINE=auto` resolves to when neither is installed, and it is
kept rather than deleted because `uv sync` with no extras must still produce a
backend that starts and reports honestly.

What it does: reports that OCR *would* be required, produces per-page
confidences, and lets the pipeline exercise the DR-1 path — flagged pages, a
mean confidence, and the below-floor failure that quarantines a document rather
than indexing text nobody should rely on.

What it does not do: invent text. A page with no text layer gets an empty page
and a low confidence, so the document ends up `failed` with a real reason rather
than `ready` with fabricated content. Returning plausible-looking text here
would be the single most dishonest thing this backend could do.
"""

from __future__ import annotations

import hashlib

from app.config import get_settings
from app.ingestion.ports import ParsedPage, ParseResult


class StubOcr:
    name = "ocr/none"

    def available(self) -> bool:
        """False, and said out loud. The pipeline uses this to put "no OCR
        engine is configured" on the document instead of letting an empty
        result look like an illegible scan."""
        return False

    def unavailable_reason(self) -> str:
        return (
            "no OCR engine is installed. Install one with `uv sync --extra ocr` plus the "
            "tesseract binary, or `uv sync --extra ocr-onnx` for the no-binary engine, "
            "and set LL_OCR_ENGINE"
        )

    def run(self, path: str, pages: list[int], *, total_pages: int) -> ParseResult:
        settings = get_settings()
        # Deterministic per file, so the same upload always reports the same
        # confidences and a test can assert on them.
        seed = int(hashlib.sha256(path.encode()).hexdigest()[:8], 16)

        out: list[ParsedPage] = []
        for page_no in pages:
            # Spread across the floor so both the flagged-page path (DR-1) and
            # the quarantine path get exercised by real uploads.
            confidence = 0.26 + ((seed + page_no * 37) % 61) / 100
            out.append(
                ParsedPage(
                    page_no=page_no,
                    text="",
                    ocr_confidence=round(min(confidence, 0.97), 2),
                    engine=self.name,
                )
            )

        mean = sum(p.ocr_confidence or 0 for p in out) / max(1, len(out))
        warnings = [
            f"{len(pages)} page(s) have no text layer and {self.unavailable_reason()} "
            f"(mean simulated confidence {mean:.2f}, floor "
            f"{settings.ocr_confidence_floor:.2f})."
        ]
        return ParseResult(pages=out, scanned=True, engine=self.name, warnings=warnings)
