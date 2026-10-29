"""Conversations — including the NCC endpoint, `POST /api/conversations`.

Note the 404-not-403 choice throughout: another user's conversation ID returns
"not found", because a 403 confirms the ID exists. v1 has one user, so this is
belt-and-braces — but it is the kind of thing that is free now and awkward to
retrofit once multi-tenancy arrives.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status

from app.api import errors
from app.api.deps import CurrentUser, DbSession, client_ip
from app.models.base import utcnow
from app.schemas.conversations import (
    Conversation,
    ConversationCreate,
    ConversationPage,
    ConversationPatch,
    ConversationSummary,
    TurnPage,
)
from app.services import audit
from app.services import conversations as service

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.post("", response_model=Conversation, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate, user: CurrentUser, db: DbSession
) -> Conversation:
    """The NCC endpoint. Returns the full `Conversation` with an empty `turns`,
    so the client can route to it without a second fetch."""
    conversation = await service.create(db, user.id, body.title)
    return Conversation(**service.to_summary(conversation).model_dump(), turns=[])


@router.get("", response_model=ConversationPage)
async def list_conversations(
    user: CurrentUser,
    db: DbSession,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=service.DEFAULT_LIMIT, ge=1, le=service.MAX_LIMIT),
    q: str | None = Query(default=None, max_length=200),
) -> ConversationPage:
    return await service.list_conversations(db, user.id, cursor=cursor, limit=limit, query=q)


@router.get("/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str, user: CurrentUser, db: DbSession) -> Conversation:
    conversation = await service.get_conversation(db, user.id, conversation_id)
    if conversation is None:
        raise errors.not_found("That conversation")
    return await service.hydrate(db, conversation)


@router.get("/{conversation_id}/messages", response_model=TurnPage)
async def list_messages(
    conversation_id: str,
    user: CurrentUser,
    db: DbSession,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=service.DEFAULT_LIMIT, ge=1, le=service.MAX_LIMIT),
) -> TurnPage:
    conversation = await service.get_conversation(db, user.id, conversation_id)
    if conversation is None:
        raise errors.not_found("That conversation")
    return await service.list_turns(db, conversation_id, cursor=cursor, limit=limit)


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def patch_conversation(
    conversation_id: str, body: ConversationPatch, user: CurrentUser, db: DbSession
) -> ConversationSummary:
    conversation = await service.get_conversation(db, user.id, conversation_id)
    if conversation is None:
        raise errors.not_found("That conversation")

    if body.title is None and body.pinned is None:
        raise errors.bad_request(
            "Nothing to change — send a title, a pinned flag, or both.",
            "Include at least one field in the request body.",
        )
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise errors.bad_request(
                "A conversation title cannot be empty.",
                "Enter a title, or leave the existing one unchanged.",
            )
        conversation.title = title[:300]
    if body.pinned is not None:
        conversation.pinned = body.pinned

    return service.to_summary(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str, request: Request, user: CurrentUser, db: DbSession
) -> Response:
    conversation = await service.get_conversation(db, user.id, conversation_id)
    if conversation is None:
        raise errors.not_found("That conversation")

    # Soft delete: DR-4 requires the audit trail for the generations in this
    # thread to outlive the thread itself. Documents are hard-deleted (FR-26);
    # conversations are not, and the distinction is deliberate.
    conversation.deleted_at = utcnow()
    audit.record(
        db,
        action=audit.ACTION_CONVERSATION_DELETE,
        user_id=user.id,
        conversation_id=conversation.id,
        ip=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
