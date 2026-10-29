"""Users, sessions, conversations, messages, citations — the "conversation DB"
of architecture band 6."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UtcDateTime, created_at_col, pk, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = pk("u")
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_name: Mapped[str] = mapped_column(String(200), nullable=False, default="Workspace")
    created_at: Mapped[datetime] = created_at_col()

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    """FR-27: server-side sessions. A revocable row is the only honest way to
    implement logout in a product holding privileged material."""

    __tablename__ = "sessions"

    id: Mapped[str] = pk("s")
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = created_at_col()

    user: Mapped[User] = relationship(back_populates="sessions")

    def is_live(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return self.revoked_at is None and self.expires_at > now


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = pk("c")
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="New chat")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dominant_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="answer")
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_message_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, index=True
    )
    created_at: Mapped[datetime] = created_at_col()
    # Soft delete: audit retention (DR-4) outlives the thread the user removed.
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True, index=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.seq",
    )

    __table_args__ = (
        Index("ix_conversations_list", "user_id", "deleted_at", "pinned", "last_message_at"),
    )


class Message(Base):
    """One row per turn, user or assistant.

    Citations, usage and audit are gated on and queried, so they are columns and
    separate tables. Stages, refusal, summary and draft payloads are only ever
    read back whole to re-render a turn, so they live in `envelope_json`.
    """

    __tablename__ = "messages"

    id: Mapped[str] = pk("m")
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)

    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    requested_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # streaming | complete | refused | error | incomplete  (NFR-7)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    scope_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    draft_params_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    envelope_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Survives an idempotent replay of the same POST.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = created_at_col()

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    citations: Mapped[list[MessageCitation]] = relationship(
        back_populates="message", cascade="all, delete-orphan", order_by="MessageCitation.n"
    )
    feedback: Mapped[Feedback | None] = relationship(
        back_populates="message", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (Index("ix_messages_thread", "conversation_id", "seq", unique=True),)


class MessageCitation(Base):
    """FR-11, normalised so citation precision (NFR-15) is a query, not a scan
    of JSON blobs."""

    __tablename__ = "message_citations"

    id: Mapped[str] = pk("mc")
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    span_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_name: Mapped[str] = mapped_column(String(500), nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    # Set by the citation verification gate at M4; NULL means "not yet gated".
    resolved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    relevance: Mapped[float | None] = mapped_column(Float, nullable=True)

    message: Mapped[Message] = relationship(back_populates="citations")


class Feedback(Base):
    """FR-25: joined to the message and, through its `trace_id`, to the
    retrieval trace that produced it."""

    __tablename__ = "feedback"

    id: Mapped[str] = pk("fb")
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    verdict: Mapped[str] = mapped_column(String(8), nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_col()

    message: Mapped[Message] = relationship(back_populates="feedback")
