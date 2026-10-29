"""Short-lived signed read URLs. Originals and page images are never served
from a guessable path (architecture band 6: "signed, short-lived read URLs only").
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

_SALT = "legallens-blob-read"
DEFAULT_TTL_S = 60


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt=_SALT)


def sign(key: str, user_id: str) -> str:
    """The token is bound to the user, so a leaked URL is not a workspace key."""
    return _serializer().dumps({"k": key, "u": user_id})


def verify(token: str, *, ttl_s: int = DEFAULT_TTL_S) -> tuple[str, str] | None:
    """Returns (object_key, user_id), or None when expired or tampered."""
    try:
        data = _serializer().loads(token, max_age=ttl_s)
    except (SignatureExpired, BadSignature):
        return None
    if not isinstance(data, dict) or "k" not in data or "u" not in data:
        return None
    return str(data["k"]), str(data["u"])
