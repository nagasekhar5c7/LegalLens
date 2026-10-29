"""The fixture corpus, ported from `frontend/src/mocks/documents.ts`.

Deliberately not all healthy: one scanned document with low-confidence pages,
one mid-ingestion, one hard failure. Those three are what exercise FR-3, FR-7
and DR-1, and the frontend was validated against exactly this set — so reusing
it makes integration a transport diff rather than an argument about which side
is right.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai.fixtures.pages import LOW_CONFIDENCE


def _ready_steps(parse_ms: int, index_ms: int, chunks: int) -> list[dict[str, Any]]:
    return [
        {"label": "Uploaded", "status": "done", "ms": 240},
        {
            "label": "Text extracted",
            "status": "done",
            "detail": "layout-aware parse",
            "ms": parse_ms,
        },
        {"label": "Classified", "status": "done", "ms": 420},
        {"label": "Chunked", "status": "done", "detail": f"{chunks} chunks", "ms": 1100},
        {"label": "Embedded & indexed", "status": "done", "ms": index_ms},
    ]


@dataclass
class FixtureDocument:
    id: str
    name: str
    type: str
    type_source: str
    classifier_confidence: float
    pages: int
    size_bytes: int
    status: str
    uploaded_hours_ago: float
    progress: int | None = None
    ocr: dict[str, Any] | None = None
    failure: dict[str, str] | None = None
    ingestion: list[dict[str, Any]] = field(default_factory=list)


CORPUS: list[FixtureDocument] = [
    FixtureDocument(
        id="doc_msa",
        name="Nexora–Arclight Master Services Agreement.pdf",
        type="contract",
        type_source="detected",
        classifier_confidence=0.97,
        pages=312,
        size_bytes=18_412_000,
        status="ready",
        uploaded_hours_ago=9 * 24,
        ingestion=_ready_steps(27_400, 41_200, 1847),
    ),
    FixtureDocument(
        id="doc_vance",
        name="Vance v. Ridgeline Logistics, 118 F.4th 221 (9th Cir. 2025).pdf",
        type="opinion",
        type_source="detected",
        classifier_confidence=0.99,
        pages=34,
        size_bytes=1_240_000,
        status="ready",
        uploaded_hours_ago=6 * 24,
        ingestion=_ready_steps(3_100, 5_400, 212),
    ),
    FixtureDocument(
        id="doc_delmar",
        name="Delmar Holdings v. Castellan, 2024 WL 881204 (Del. Ch.).pdf",
        type="opinion",
        type_source="detected",
        classifier_confidence=0.96,
        pages=22,
        size_bytes=870_000,
        status="ready",
        uploaded_hours_ago=6 * 24,
        ingestion=_ready_steps(2_200, 3_900, 141),
    ),
    FixtureDocument(
        id="doc_tessara",
        name="Complaint — Tessara Fabrication v. Juneau Mills.pdf",
        type="pleading",
        type_source="detected",
        classifier_confidence=0.94,
        pages=28,
        size_bytes=1_420_000,
        status="ready",
        uploaded_hours_ago=3 * 24,
        ingestion=_ready_steps(2_700, 4_300, 168),
    ),
    FixtureDocument(
        id="doc_nda",
        name="Mutual NDA — Harbour Point Capital.pdf",
        type="contract",
        # FR-5: provenance kept. The classifier said something else; the
        # attorney overrode it, and the badge has to say so.
        type_source="overridden",
        classifier_confidence=0.71,
        pages=11,
        size_bytes=402_000,
        status="ready",
        uploaded_hours_ago=2 * 24,
        ingestion=_ready_steps(1_400, 2_100, 64),
    ),
    FixtureDocument(
        id="doc_amendment",
        name="Executed Amendment No. 3 (scanned).pdf",
        type="contract",
        type_source="detected",
        classifier_confidence=0.82,
        pages=7,
        size_bytes=9_840_000,
        status="ready",
        uploaded_hours_ago=20,
        ocr={
            "scanned": True,
            "mean_confidence": 0.79,
            "low_confidence_pages": LOW_CONFIDENCE["doc_amendment"],
        },
        ingestion=[
            {"label": "Uploaded", "status": "done", "ms": 2_100},
            {
                "label": "No text layer found",
                "status": "done",
                "detail": "fell back to OCR",
                "ms": 180,
            },
            {
                "label": "OCR complete",
                "status": "done",
                "detail": "mean confidence 0.79 · 2 pages flagged",
                "ms": 31_800,
            },
            {"label": "Classified", "status": "done", "ms": 510},
            {"label": "Chunked", "status": "done", "detail": "38 chunks", "ms": 700},
            {"label": "Embedded & indexed", "status": "done", "ms": 1_900},
        ],
    ),
    FixtureDocument(
        id="doc_depo",
        name="Deposition Transcript — R. Okafor (Vol. II).pdf",
        type="other",
        type_source="detected",
        classifier_confidence=0.63,
        pages=142,
        size_bytes=4_120_000,
        # FR-7: excluded from retrieval, visibly and with a reason.
        status="indexing",
        progress=62,
        uploaded_hours_ago=1,
        ingestion=[
            {"label": "Uploaded", "status": "done", "ms": 900},
            {
                "label": "Text extracted",
                "status": "done",
                "detail": "layout-aware parse",
                "ms": 12_400,
            },
            {
                "label": "Classified",
                "status": "done",
                "detail": "low confidence — review the type",
                "ms": 460,
            },
            {"label": "Chunked", "status": "done", "detail": "604 chunks", "ms": 2_300},
            {
                "label": "Embedded & indexed",
                "status": "active",
                "detail": "374 of 604 chunks",
            },
        ],
    ),
    FixtureDocument(
        id="doc_exhibits",
        name="Exhibit Bundle C (fax).pdf",
        type="other",
        type_source="detected",
        classifier_confidence=0.41,
        pages=9,
        size_bytes=6_700_000,
        status="failed",
        uploaded_hours_ago=4,
        failure={
            "reason": "Text extraction failed on 7 of 9 pages",
            "detail": (
                "The pages appear to be a fax of a photocopy. OCR confidence averaged 0.31, "
                "below the 0.50 floor, so the document was not indexed rather than indexed "
                "with unreliable text."
            ),
        },
        ingestion=[
            {"label": "Uploaded", "status": "done", "ms": 1_600},
            {
                "label": "No text layer found",
                "status": "done",
                "detail": "fell back to OCR",
                "ms": 210,
            },
            {
                "label": "OCR below confidence floor",
                "status": "failed",
                "detail": "0.31 mean · floor 0.50",
                "ms": 44_100,
            },
            {"label": "Chunked", "status": "pending"},
            {"label": "Embedded & indexed", "status": "pending"},
        ],
    ),
]

BY_ID: dict[str, FixtureDocument] = {d.id: d for d in CORPUS}


def document_name(document_id: str) -> str | None:
    doc = BY_ID.get(document_id)
    return doc.name if doc else None


def short_doc_name(name: str, limit: int = 34) -> str:
    """Long legal filenames do not fit anywhere; abbreviate predictably.
    Mirrors `shortDocName` in frontend/src/lib/format.ts."""
    stem = name
    for ext in (".pdf", ".docx", ".txt", ".PDF", ".DOCX", ".TXT"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    if len(stem) <= limit:
        return stem
    return stem[: limit - 1].rstrip() + "…"


def citation_label(document_name: str, page: int, section: str | None) -> str:
    """Mirrors `citationLabel` in frontend/src/lib/format.ts, so a label
    rendered by the backend reads identically to one the mock produced."""
    doc = short_doc_name(document_name)
    return f"{doc} {section} · p. {page}" if section else f"{doc} · p. {page}"
