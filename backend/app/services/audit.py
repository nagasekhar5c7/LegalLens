"""DR-4: an audit row for every retrieval and generation.

Written in the caller's transaction, so it commits with the message it
describes. An audit write that can fail independently of the thing it records is
not an audit trail.

DR-5: the row stores a hash of the output, never the output, and never document
text — the audit log is itself a privileged artefact and the open question in
BRD §16 about its retention is why it holds as little as possible.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

# Actions are a closed vocabulary so the log is queryable.
ACTION_LOGIN = "auth.login"
ACTION_LOGIN_FAILED = "auth.login_failed"
ACTION_LOGOUT = "auth.logout"
ACTION_GENERATE = "ai.generate"
ACTION_REFUSE = "ai.refuse"
ACTION_RETRIEVE = "ai.retrieve"
ACTION_UPLOAD = "doc.upload"
ACTION_UPLOAD_REJECTED = "doc.upload_rejected"
ACTION_INGEST_COMPLETE = "doc.ingest_complete"
ACTION_INGEST_FAILED = "doc.ingest_failed"
ACTION_TYPE_OVERRIDE = "doc.type_override"
ACTION_DOC_DELETE = "doc.hard_delete"
ACTION_WORKSPACE_DELETE = "workspace.hard_delete"
ACTION_CONVERSATION_DELETE = "conversation.delete"
ACTION_FEEDBACK = "feedback.submit"
ACTION_POLICY_REFUSE = "policy.refuse"


def record(
    db: AsyncSession,
    *,
    action: str,
    user_id: str | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
    document_ids: list[str] | None = None,
    span_ids: list[str] | None = None,
    prompt_version: str | None = None,
    model_version: str | None = None,
    index_version: str | None = None,
    trace_id: str | None = None,
    output: str | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditLog:
    """Stage an audit row. The caller's commit is what makes it durable."""
    row = AuditLog(
        action=action,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        document_ids_json=document_ids or [],
        span_ids_json=span_ids or [],
        prompt_version=prompt_version,
        model_version=model_version,
        index_version=index_version,
        trace_id=trace_id,
        output_sha256=hashlib.sha256(output.encode()).hexdigest() if output else None,
        detail_json=detail,
        ip=ip,
    )
    db.add(row)
    return row
