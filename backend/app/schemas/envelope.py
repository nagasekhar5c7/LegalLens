"""The one response shape all three workflows return, plus the summary and draft
payloads it carries. Mirrors `ResponseEnvelope` in types.ts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.base import CamelModel, StrictCamelModel
from app.schemas.common import (
    Citation,
    Coverage,
    DocType,
    DraftType,
    Excerpt,
    Intent,
    OutputKind,
    Refusal,
    ScopeUsed,
    Usage,
    Versions,
)


class SummaryField(CamelModel):
    key: str
    label: str
    # FR-16: None means genuinely absent — rendered "not addressed", never inferred.
    value_md: str | None = None
    citations: list[int] = Field(default_factory=list)
    flag: Literal["off_market", "unusual"] | None = None
    flag_note: str | None = None


class SummaryPayload(CamelModel):
    document_id: str
    document_name: str
    document_type: DocType
    template_name: str
    fields: list[SummaryField] = Field(default_factory=list)
    coverage: Coverage | None = None


class DraftParams(StrictCamelModel):
    """FR-19. `document_ids` empty is a validation failure, not a default —
    a draft with no sources cannot be grounded."""

    draft_type: DraftType
    question_presented: str = ""
    audience: str = "supervising partner"
    position: str = ""
    tone: str = "neutral predictive"
    document_ids: list[str] = Field(default_factory=list)


class DraftSection(CamelModel):
    key: str
    heading: str
    body_md: str
    citations: list[int] = Field(default_factory=list)


class DraftPayload(CamelModel):
    draft_type: DraftType
    title: str
    params: DraftParams
    sections: list[DraftSection] = Field(default_factory=list)


class ResponseEnvelope(CamelModel):
    answer_md: str = ""
    kind: OutputKind = "answer"
    intent: Intent = "ask"
    citations: list[Citation] = Field(default_factory=list)
    excerpts: list[Excerpt] = Field(default_factory=list)
    scope_used: ScopeUsed | None = None
    summary: SummaryPayload | None = None
    draft: DraftPayload | None = None
    refusal: Refusal | None = None
    coverage: Coverage | None = None
    trace_id: str | None = None
    versions: Versions | None = None
    usage: Usage | None = None
    # TS-5: present on every output, carried into every export.
    disclaimer: str = ""
