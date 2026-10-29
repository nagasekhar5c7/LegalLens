"""Spans, feedback, traces, usage, workspace health and workspace deletion.

The read surfaces behind the evidence panel, the trace drawer and the settings
page — plus the one destructive endpoint that is not about a single document.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api import errors
from app.api.deps import CurrentUser, DbSession, client_ip
from app.models import (
    Conversation,
    Document,
    DocumentSpan,
    Feedback,
    Message,
    Trace,
    UsageTransaction,
)
from app.models.base import utcnow
from app.schemas.base import iso
from app.schemas.common import Span, Stage, Versions
from app.schemas.documents import WorkspaceDeleteRequest, WorkspaceHealth
from app.schemas.misc import (
    FeedbackRequest,
    RetrievalCandidate,
    UsageLedger,
    UsageLedgerRow,
)
from app.schemas.misc import (
    Trace as TraceSchema,
)
from app.services import audit
from app.services import documents as doc_service
from app.storage import blob

log = logging.getLogger("legallens.platform")

router = APIRouter(prefix="/api", tags=["platform"])


# ------------------------------------------------------------------- spans


@router.get("/spans/{span_id}", response_model=Span)
async def get_span(span_id: str, user: CurrentUser, db: DbSession) -> Span:
    """FR-11: a citation resolves to document, page and character span.
    Offsets are page-local — see BACKEND-TODO §8.2."""
    row = (
        await db.execute(
            select(DocumentSpan)
            .join(Document, DocumentSpan.document_id == Document.id)
            .where(
                DocumentSpan.id == span_id,
                Document.user_id == user.id,
                Document.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # A span whose document was deleted is genuinely gone (FR-26), and
        # saying "not found" is the honest answer.
        raise errors.not_found("That span")
    return Span(
        id=row.id,
        document_id=row.document_id,
        page=row.page_no,
        char_start=row.char_start,
        char_end=row.char_end,
        section=row.section,
    )


# ---------------------------------------------------------------- feedback


@router.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def submit_feedback(
    body: FeedbackRequest, request: Request, user: CurrentUser, db: DbSession
) -> Response:
    """FR-25: linked to the response *and* its retrieval trace. "The answer is
    in my documents" on a refusal is the single most valuable signal here, so
    the trace has to come along or the report is unactionable."""
    message = (
        await db.execute(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Message.id == body.turn_id, Conversation.user_id == user.id)
            .options(selectinload(Message.feedback))
        )
    ).scalar_one_or_none()
    if message is None:
        raise errors.not_found("That response")
    if message.role != "assistant":
        raise errors.bad_request(
            "Feedback applies to a response, not to your own message.",
            "Send the assistant turn's id.",
        )

    if message.feedback is not None:
        message.feedback.verdict = body.verdict
        message.feedback.reasons_json = list(body.reasons)
        message.feedback.note = body.note
        message.feedback.trace_id = message.trace_id
    else:
        db.add(
            Feedback(
                message_id=message.id,
                trace_id=message.trace_id,
                verdict=body.verdict,
                reasons_json=list(body.reasons),
                note=body.note,
            )
        )

    audit.record(
        db,
        action=audit.ACTION_FEEDBACK,
        user_id=user.id,
        conversation_id=message.conversation_id,
        message_id=message.id,
        trace_id=message.trace_id,
        detail={"verdict": body.verdict, "reasons": list(body.reasons)},
        ip=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ traces


@router.get("/traces/{trace_id}", response_model=TraceSchema)
async def get_trace(trace_id: str, user: CurrentUser, db: DbSession) -> TraceSchema:
    """NFR-11 / NFR-12: stage timings, the queries actually run, the candidate
    set with scores, and the versions that produced the answer."""
    row = (
        await db.execute(select(Trace).where(Trace.id == trace_id, Trace.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        raise errors.not_found("That trace")

    return TraceSchema(
        trace_id=row.id,
        message_id=row.message_id,
        conversation_id=row.conversation_id,
        stages=[Stage.model_validate(s) for s in row.stages_json],
        queries=list(row.queries_json),
        candidates=[RetrievalCandidate.model_validate(c) for c in row.candidates_json],
        timings=dict(row.timings_json),
        versions=Versions.model_validate(row.versions_json) if row.versions_json else None,
        created_at=iso(row.created_at),
    )


# ------------------------------------------------------------------- usage


@router.get("/usage", response_model=UsageLedger)
async def get_usage(user: CurrentUser, db: DbSession) -> UsageLedger:
    """NFR-8: cost attributed per request and visible. The frontend's settings
    page shows spend against the $0.05-per-ask budget, so the totals ride along
    rather than being summed client-side over a partial page."""
    rows = list(
        (
            await db.execute(
                select(
                    UsageTransaction.conversation_id,
                    func.count(UsageTransaction.id),
                    func.coalesce(func.sum(UsageTransaction.usd), 0.0),
                    func.coalesce(
                        func.sum(UsageTransaction.input_tokens + UsageTransaction.output_tokens),
                        0,
                    ),
                )
                .where(UsageTransaction.user_id == user.id, UsageTransaction.kind == "turn")
                .group_by(UsageTransaction.conversation_id)
            )
        ).all()
    )

    titles = dict(
        (
            await db.execute(
                select(Conversation.id, Conversation.title).where(Conversation.user_id == user.id)
            )
        ).all()
    )

    ledger = [
        UsageLedgerRow(
            conversation_id=conversation_id or "—",
            title=titles.get(conversation_id or "", "Deleted conversation"),
            asks=int(asks),
            usd=round(float(usd), 6),
            tokens=int(tokens),
        )
        for conversation_id, asks, usd, tokens in rows
    ]
    ledger.sort(key=lambda r: r.usd, reverse=True)

    total_usd = round(sum(r.usd for r in ledger), 6)
    total_asks = sum(r.asks for r in ledger)
    return UsageLedger(
        rows=ledger,
        total_usd=total_usd,
        total_tokens=sum(r.tokens for r in ledger),
        total_asks=total_asks,
        mean_usd_per_ask=round(total_usd / total_asks, 6) if total_asks else 0.0,
    )


# --------------------------------------------------------------- workspace


@router.get("/workspace/health", response_model=WorkspaceHealth)
async def health(user: CurrentUser, db: DbSession) -> WorkspaceHealth:
    """⊕ The sidebar's `6 documents · 4 ready · 1 indexing · 1 failed` card."""
    return await doc_service.workspace_health(db, user.id)


@router.delete("/workspace", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    body: WorkspaceDeleteRequest, request: Request, user: CurrentUser, db: DbSession
) -> Response:
    """FR-26 at workspace level. Documents are hard-deleted; conversations,
    traces and usage go with them. `audit_log` is retained — a tombstone is the
    only record that survives, which is what DR-4 requires and what makes the
    open question in BRD §16 about audit retention worth asking."""
    if body.confirm != "DELETE":
        raise errors.bad_request(
            'Workspace deletion requires {"confirm": "DELETE"}.',
            "Type DELETE to confirm, then retry.",
        )

    documents = await doc_service.list_documents(db, user.id)
    reports = [await doc_service.hard_delete(db, d) for d in documents]

    conversation_ids = list(
        (await db.execute(select(Conversation.id).where(Conversation.user_id == user.id))).scalars()
    )
    for conversation in list(
        (await db.execute(select(Conversation).where(Conversation.user_id == user.id))).scalars()
    ):
        # Real delete here, not the soft delete a single conversation gets: the
        # user asked for the workspace to be gone.
        await db.delete(conversation)

    for trace in list((await db.execute(select(Trace).where(Trace.user_id == user.id))).scalars()):
        await db.delete(trace)
    for transaction in list(
        (
            await db.execute(select(UsageTransaction).where(UsageTransaction.user_id == user.id))
        ).scalars()
    ):
        await db.delete(transaction)
    await db.flush()

    # Reconciliation: anything still on disk under a prefix the database no
    # longer knows about is an orphan from an interrupted delete.
    orphans = blob.orphan_sweep(await doc_service.known_blob_prefixes(db))

    audit.record(
        db,
        action=audit.ACTION_WORKSPACE_DELETE,
        user_id=user.id,
        document_ids=[r.document_id for r in reports],
        detail={
            "documents": len(reports),
            "conversations": len(conversation_ids),
            "blobs": sum(r.blobs for r in reports) + orphans,
            "orphans_swept": orphans,
            "at": iso(utcnow()),
        },
        ip=client_ip(request),
    )
    log.warning(
        "workspace hard-deleted for %s: %d documents, %d conversations",
        user.id,
        len(reports),
        len(conversation_ids),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
