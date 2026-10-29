"""Structured logging with a content filter.

DR-5 is the reason this module exists: no document content may reach telemetry
or an error tracker. The filter below is a backstop, not a licence — call sites
are expected not to log page text in the first place.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from app.config import get_settings

_SUSPICIOUS = re.compile(
    r"(?:page_text|extracted_text|excerpt_text|document_text)\s*[=:]\s*\S+", re.IGNORECASE
)
#: A log line longer than this is almost certainly carrying content.
MAX_MESSAGE_CHARS = 2000


class ContentFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — a broken format string must not kill logging
            return True
        if len(msg) > MAX_MESSAGE_CHARS or _SUSPICIOUS.search(msg):
            record.msg = (
                f"[redacted: {len(msg)} chars suppressed by the DR-5 content filter "
                f"from {record.name}]"
            )
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "trace_id", "user_id", "document_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(ContentFilter())
    handler.setFormatter(
        JsonFormatter()
        if settings.env == "production"
        else logging.Formatter("%(levelname)-5.5s %(name)s · %(message)s")
    )

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.INFO if settings.env != "test" else logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
