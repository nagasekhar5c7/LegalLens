"""Document and page wire shapes. Mirrors `LegalDocument`, `OcrInfo`,
`IngestionStep`, `Page` in types.ts."""

from __future__ import annotations

from pydantic import Field

from app.schemas.base import CamelModel, StrictCamelModel
from app.schemas.common import DocStatus, DocType, IngestionStepStatus, TypeSource


class OcrInfo(CamelModel):
    """DR-1. `low_confidence_pages` is 1-indexed and surfaced in the UI."""

    scanned: bool
    mean_confidence: float
    low_confidence_pages: list[int] = Field(default_factory=list)


class IngestionStep(CamelModel):
    label: str
    status: IngestionStepStatus
    detail: str | None = None
    ms: int | None = None


class DocumentFailure(CamelModel):
    reason: str
    detail: str


class LegalDocument(CamelModel):
    id: str
    name: str
    type: DocType
    type_source: TypeSource
    classifier_confidence: float
    pages: int
    size_bytes: int
    status: DocStatus
    progress: int | None = None
    uploaded_at: str
    ocr: OcrInfo | None = None
    failure: DocumentFailure | None = None
    ingestion: list[IngestionStep] | None = None
    # ⊕ not in types.ts: set when an upload was deduped by SHA-256 onto an
    # existing document. Additive, so the frontend can ignore it.
    duplicate_of: str | None = None


class RejectedFile(CamelModel):
    """Mirrors `RejectedFile` in client.ts — FR-2 wording lives server-side now."""

    name: str
    reason: str


class UploadResult(CamelModel):
    """FR-3: per-file outcomes, so one rejection never fails the batch."""

    accepted: list[LegalDocument] = Field(default_factory=list)
    rejected: list[RejectedFile] = Field(default_factory=list)


class DocumentStatus(CamelModel):
    """The poll target for the upload queue."""

    id: str
    status: DocStatus
    progress: int | None = None
    pages: int
    ingestion: list[IngestionStep] = Field(default_factory=list)
    failure: DocumentFailure | None = None
    ocr: OcrInfo | None = None


class DocumentTypePatch(StrictCamelModel):
    type: DocType


class Page(CamelModel):
    document_id: str
    page: int
    text: str
    ocr_confidence: float | None = None


class WorkspaceHealth(CamelModel):
    documents: int
    ready: int
    indexing: int
    failed: int
    pages: int


class WorkspaceDeleteRequest(StrictCamelModel):
    confirm: str


class ServerConfig(CamelModel):
    """So FR-2's client-side validation reads its limits from one place."""

    max_bytes: int
    max_pages: int
    max_documents: int
    accepted_extensions: list[str]
    scenarios_enabled: bool
    generation_budget_s: int
