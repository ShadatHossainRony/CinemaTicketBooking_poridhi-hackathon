"""Structured JSON logging with a request-id contextvar.

Every log line carries `request_id` (the same one in the response header and
the error envelope). Never logs phones, OTP codes, full card numbers, tokens,
or query strings. Compatible with `uvicorn` access logs.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Bound at request start by RequestIDMiddleware; "" outside a request.
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(rid: str) -> None:
    _request_id.set(rid)


def get_request_id() -> str:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """One JSON object per line. No pretty-printing — this is for machines."""

    # Fields we never want to log, even by accident.
    _SENSITIVE_KEYS = {
        "phone", "code", "otp", "token", "password", "secret",
        "authorization", "cookie", "set-cookie", "card",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": get_request_id(),
        }
        # Anything passed via `extra={"foo": ...}` lands in __dict__.
        for k, v in record.__dict__.items():
            if k in (
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "module", "msecs",
                "message", "msg", "name", "pathname", "process", "processName",
                "relativeCreated", "stack_info", "thread", "threadName",
                "taskName",
            ):
                continue
            if k.lower() in self._SENSITIVE_KEYS:
                v = "[REDACTED]"
            payload[k] = v

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Wire JSON output to stdout and silence the noisy default handlers."""
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Quiet down uvicorn's default access logger; our middleware emits per-request.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True

    # SQLAlchemy chatter off by default; flip to INFO when debugging queries.
    logging.getLogger("sqlalchemy.engine").setLevel("WARNING")
