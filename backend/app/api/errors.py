"""FR-28: every error names a next action.

The HTTP body *is* an `ApiError` object, the same shape the `{type:'error'}`
stream event carries, so one frontend parser handles both. FastAPI's default
`{"detail": [...]}` never reaches the client — a validation array is not an
actionable error message.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import ApiError, ApiErrorKind

log = logging.getLogger("legallens.errors")

#: Starlette renamed this constant; pin the number so the import works on both.
HTTP_422 = 422


class AppError(Exception):
    """Raised anywhere in the app; rendered as an ApiError by the handler."""

    def __init__(
        self,
        kind: ApiErrorKind,
        message: str,
        action: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        retry_after_sec: int | None = None,
        document_id: str | None = None,
        excerpts_available: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.action = action
        self.status_code = status_code
        self.retry_after_sec = retry_after_sec
        self.document_id = document_id
        self.excerpts_available = excerpts_available

    def to_api_error(self, request_id: str | None = None) -> ApiError:
        return ApiError(
            kind=self.kind,
            message=self.message,
            action=self.action,
            retry_after_sec=self.retry_after_sec,
            document_id=self.document_id,
            excerpts_available=self.excerpts_available,
            request_id=request_id,
        )


# --------------------------------------------------------------- constructors
# Named constructors rather than raw AppError calls, so the wording for a given
# failure lives in exactly one place and can be reviewed as copy.


def unauthorized(action: str = "Sign in to continue.") -> AppError:
    return AppError(
        "network",
        "Your session has expired or you are not signed in.",
        action,
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def session_expired() -> AppError:
    """Distinct from `unauthorized`: a user whose session lapsed mid-session is
    told that, rather than being left to wonder whether they ever signed in."""
    return AppError(
        "network",
        "Your session is no longer valid — it expired or was signed out elsewhere.",
        "Sign in again to continue.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


def not_found(what: str) -> AppError:
    # 404 rather than 403 for another user's ID: a 403 confirms the ID exists.
    return AppError(
        "network",
        f"{what} was not found.",
        "Reload the page — it may have been deleted in another tab.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


def bad_request(message: str, action: str) -> AppError:
    return AppError("network", message, action, status_code=status.HTTP_400_BAD_REQUEST)


def rate_limited(retry_after_sec: int, what: str = "requests") -> AppError:
    return AppError(
        "rate_limited",
        f"Too many {what}. This request was not processed.",
        f"Retry in {retry_after_sec} seconds.",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        retry_after_sec=retry_after_sec,
        excerpts_available=True,
    )


def llm_unavailable() -> AppError:
    # NFR-6: retrieval survived, so the excerpts are still worth reading.
    return AppError(
        "llm_unavailable",
        "The language model provider is not responding. Retrieval ran and found "
        "supporting passages, but no answer could be generated.",
        "Retry, or read the retrieved passages directly — they are listed in the evidence panel.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        excerpts_available=True,
    )


def generation_timeout() -> AppError:
    return AppError(
        "timeout",
        "Generation exceeded the budget and was abandoned. No partial answer is being "
        "shown, because a truncated legal answer is worse than none.",
        "Retry with a narrower scope, or ask about one document at a time.",
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        excerpts_available=True,
    )


def parse_failed(document_id: str, detail: str) -> AppError:
    return AppError(
        "parse_failed",
        detail,
        "Open the document to see which pages failed, or re-upload a better scan.",
        status_code=HTTP_422,
        document_id=document_id,
    )


# ------------------------------------------------------------------ handlers

_STATUS_FALLBACK: dict[int, tuple[ApiErrorKind, str, str]] = {
    401: ("network", "Your session has expired or you are not signed in.", "Sign in to continue."),
    403: ("network", "That action is not permitted.", "Reload the page and try again."),
    404: ("network", "That resource was not found.", "Reload the page — it may have been deleted."),
    405: ("network", "That method is not allowed on this endpoint.", "Check the request method."),
    413: (
        "network",
        "The request body is larger than the server accepts.",
        "Upload fewer files at once.",
    ),
    429: ("rate_limited", "Too many requests.", "Wait a few seconds and retry."),
    503: ("llm_unavailable", "A dependency is unavailable.", "Retry in a moment."),
}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _json(
    error: ApiError, status_code: int, *, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error.model_dump(by_alias=True, exclude_none=True),
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        headers = (
            {"Retry-After": str(exc.retry_after_sec)} if exc.retry_after_sec is not None else None
        )
        return _json(exc.to_api_error(_request_id(request)), exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _json(
            ApiError(
                kind="network",
                message=_describe_validation(exc.errors()),
                action="Correct the request and try again.",
                request_id=_request_id(request),
            ),
            HTTP_422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        kind, message, action = _STATUS_FALLBACK.get(
            exc.status_code, ("network", "The request could not be completed.", "Try again.")
        )
        if isinstance(exc.detail, str) and exc.detail and exc.status_code not in (401, 404):
            message = exc.detail
        return _json(
            ApiError(kind=kind, message=message, action=action, request_id=_request_id(request)),
            exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # DR-5: the traceback goes to the server log, never to the client, and
        # the log filter keeps document text out of it.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return _json(
            ApiError(
                kind="network",
                message="Something went wrong on the server.",
                action=(
                    "Retry. If it persists, the request ID below identifies "
                    "this failure in the logs."
                ),
                request_id=_request_id(request),
            ),
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _describe_validation(errors: Sequence[Any]) -> str:
    """Turn Pydantic's error list into one sentence a person can act on."""
    if not errors:
        return "The request body was not valid."
    parts: list[str] = []
    for err in errors[:3]:
        loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
        field = ".".join(loc) or "request"
        parts.append(f"{field}: {err.get('msg', 'invalid')}")
    suffix = f" (and {len(errors) - 3} more)" if len(errors) > 3 else ""
    return "; ".join(parts) + suffix
