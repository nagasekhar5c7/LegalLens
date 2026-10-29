"""Auth, feedback, usage, traces. The small surfaces the frontend's settings,
trace drawer and feedback bar need."""

from __future__ import annotations

from typing import Any

from pydantic import EmailStr, Field

from app.schemas.base import CamelModel, StrictCamelModel
from app.schemas.common import FeedbackReason, FeedbackVerdict, Stage, Versions


class LoginRequest(StrictCamelModel):
    # A rejected-at-the-boundary bad address beats a generic auth failure, and
    # it matches the frontend's own inline validation.
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(CamelModel):
    ok: bool = True
    email: EmailStr
    workspace_name: str


class Me(CamelModel):
    email: EmailStr
    workspace_name: str


class FeedbackRequest(StrictCamelModel):
    """FR-25: joined to the message *and* its retrieval trace."""

    turn_id: str
    verdict: FeedbackVerdict
    reasons: list[FeedbackReason] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=2000)


class UsageLedgerRow(CamelModel):
    conversation_id: str
    title: str
    asks: int
    usd: float
    tokens: int


class UsageLedger(CamelModel):
    """⊕ types.ts has the row type; the frontend's settings page also wants
    totals, so they ride along rather than being recomputed client-side."""

    rows: list[UsageLedgerRow] = Field(default_factory=list)
    total_usd: float = 0.0
    total_tokens: int = 0
    total_asks: int = 0
    budget_per_ask_usd: float = 0.05
    mean_usd_per_ask: float = 0.0


class RetrievalCandidate(CamelModel):
    span_id: str
    document_id: str
    page: int
    score: float
    kept: bool


class Trace(CamelModel):
    """NFR-11 / NFR-12: retrieval → assembly → generation, with the versions
    that produced it, so any answer is reproducible."""

    trace_id: str
    message_id: str | None = None
    conversation_id: str | None = None
    stages: list[Stage] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    candidates: list[RetrievalCandidate] = Field(default_factory=list)
    timings: dict[str, Any] = Field(default_factory=dict)
    versions: Versions | None = None
    created_at: str | None = None
