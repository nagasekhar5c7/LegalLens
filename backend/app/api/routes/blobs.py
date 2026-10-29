"""Signed blob reads.

The only way bytes leave the encrypted store. A token is bound to one object and
one user and expires in 60 seconds, so a URL that leaks out of a browser history
or a screenshot is not a workspace key (architecture band 6: "signed,
short-lived read URLs only").
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response

from app.api import errors
from app.api.deps import CurrentUser
from app.storage import blob, signed_url

log = logging.getLogger("legallens.blobs")

router = APIRouter(prefix="/api/blobs", tags=["documents"])

#: Served inline for the viewer. Everything else is a download, so a crafted
#: object can never be rendered as HTML in the app's origin.
INLINE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


@router.get("/{token}")
async def read_blob(token: str, user: CurrentUser) -> Response:
    verified = signed_url.verify(token)
    if verified is None:
        raise errors.AppError(
            "network",
            "This link has expired.",
            "Reload the page to get a fresh link.",
            status_code=410,
        )

    key, token_user = verified
    # The session and the token must agree. A valid token presented by another
    # session is a replay, not an authorisation.
    if token_user != user.id:
        raise errors.not_found("That file")

    try:
        data = blob.read(key)
    except FileNotFoundError as exc:
        raise errors.not_found("That file") from exc
    except blob.BlobDecryptError as exc:
        log.error("blob %s failed authentication", key)
        raise errors.AppError(
            "network",
            "That file could not be read.",
            "Re-upload the document.",
            status_code=500,
        ) from exc

    suffix = key.rsplit(".", 2)[-2] if key.count(".") > 1 else ""
    media_type = INLINE_TYPES.get(suffix.lower(), "application/octet-stream")
    disposition = "inline" if suffix.lower() in INLINE_TYPES else "attachment"
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f"{disposition}",
            "Cache-Control": "private, max-age=60",
            # Nothing served from here should ever be sniffed into a script.
            "X-Content-Type-Options": "nosniff",
        },
    )
