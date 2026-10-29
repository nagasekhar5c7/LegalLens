"""FR-7 and FR-9: what was actually searched, and what fell out of it.

Scope resolution is a backend concern, not the engine's. The engine receives a
resolved `ScopeUsed` and the documents it may touch — which is why FR-7 cannot
be violated by a change in retrieval logic.

The `excluded` list is the part that earns its keep: silently dropping a
document the user selected produces a "wrong" answer they cannot explain
(frontend nuance #7), so every exclusion carries a reason.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.ports import ScopedDocument
from app.models import Document
from app.schemas.common import ExcludedDocument, Scope, ScopeUsed

#: Reasons mirror `statusExclusionReason` in frontend/src/lib/format.ts.
EXCLUSION_REASON: dict[str, str] = {
    "queued": "Queued — not yet searchable",
    "parsing": "Still parsing — not yet searchable",
    "ocr": "Running OCR — not yet searchable",
    "indexing": "Still indexing — not yet searchable",
    "failed": "Failed to process — cannot be searched",
}


async def resolve(
    db: AsyncSession, user_id: str, scope: Scope
) -> tuple[ScopeUsed, list[ScopedDocument]]:
    """Returns the scope actually used and the documents the engine may read."""
    stmt = select(Document).where(Document.user_id == user_id, Document.deleted_at.is_(None))
    if scope.kind != "workspace":
        if not scope.document_ids:
            return (
                ScopeUsed(
                    kind=scope.kind, document_ids=[], document_count=0, page_count=0, excluded=[]
                ),
                [],
            )
        stmt = stmt.where(Document.id.in_(scope.document_ids))

    requested = list((await db.execute(stmt.order_by(Document.uploaded_at.desc()))).scalars())

    included = [d for d in requested if d.status == "ready"]
    excluded = [
        ExcludedDocument(
            document_id=d.id,
            name=d.name,
            reason=EXCLUSION_REASON.get(d.status, "Not ready"),
        )
        for d in requested
        if d.status != "ready"
    ]

    scope_used = ScopeUsed(
        kind=scope.kind,
        document_ids=[d.id for d in included],
        document_count=len(included),
        page_count=sum(d.pages for d in included),
        excluded=excluded,
    )
    scoped = [
        ScopedDocument(id=d.id, name=d.name, type=d.type, pages=d.pages, status=d.status)
        for d in included
    ]
    return scope_used, scoped
