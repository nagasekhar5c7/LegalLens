"""The wire is camelCase, because `frontend/src/api/types.ts` already says so.

Every model in this package inherits CamelModel, so `answer_md` serialises as
`answerMd` and the frontend needs no mapping layer. snake_case is still accepted
on input (`populate_by_name`), which keeps curl and the tests readable.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        ser_json_timedelta="float",
    )


class StrictCamelModel(CamelModel):
    """Request bodies. `extra='forbid'` turns a client typo into a 422 rather
    than a silently ignored field."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        extra="forbid",
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    """ISO-8601 with a Z suffix — what `new Date(...)` in the frontend expects."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
