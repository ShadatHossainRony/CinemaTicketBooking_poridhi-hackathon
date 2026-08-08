"""Request middleware: RequestID, AccessLog, RateLimit, CORS.

Outer-first ordering registered in `main.py`:
    RequestID → AccessLog → CORS → RateLimit → router

`POST /payments/callback` is EXEMPT from rate limiting
(`04-api-contract.md` §8) — limiting it would cause the gateway to retry
forever, which is exactly what REQ-13 forbids.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import MutableMapping
from typing import Deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.core.logging import get_request_id, logger, set_request_id

REQUEST_ID_HEADER = "X-Request-ID"
_CORS_SAFE_HEADERS = {"content-type", REQUEST_ID_HEADER.lower(), "x-mock-mode", "x-mock-force"}


# --------------------------------------------------------------------------- #
# Request ID
# --------------------------------------------------------------------------- #
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        set_request_id(rid)
        request.state.request_id = rid
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response


# --------------------------------------------------------------------------- #
# Access log
# --------------------------------------------------------------------------- #
class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        # Bypass CORS preflight noise.
        if request.method == "OPTIONS":
            return await call_next(request)

        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            client_ip = request.client.host if request.client else "-"
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": duration_ms,
                    "client_ip": client_ip,
                },
            )
        return response


# --------------------------------------------------------------------------- #
# In-process rate limiter
# --------------------------------------------------------------------------- #
class _Counter:
    """Per-process fixed-window counter, keyed on (client_ip, bucket)."""

    def __init__(self) -> None:
        # bucket -> deque[monotonic_seconds]
        self._hits: MutableMapping[str, Deque[float]] = defaultdict(deque)

    def hit(self, key: str, max_per_minute: int) -> bool:
        """Return True if the request is allowed (and recorded), False if rate-limited."""
        now = time.monotonic()
        window = 60.0
        dq = self._hits[key]
        # Drop expired entries.
        while dq and (now - dq[0]) > window:
            dq.popleft()
        if len(dq) >= max_per_minute:
            return False
        dq.append(now)
        return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-process fixed-window counter.

    Honest limitation: with two uvicorn workers × two API replicas we have
    four processes, so the *effective* limit is 4× the configured value.
    ADR-008. We accept this because Nginx is the real flood gate and the
    load test runs from a single IP — see Scenario A.
    """

    # EXEMPT path prefixes — never rate-limit these (4-api-contract §8).
    EXEMPT_PATH_PREFIXES = ("/payments/callback",)

    def __init__(self, app):
        super().__init__(app)
        self._counters: dict[str, _Counter] = {}

    def _bucket_for(self, request: Request) -> tuple[str, int] | None:
        path = request.url.path
        method = request.method
        client_ip = request.client.host if request.client else "anon"
        # POST /holds is the contended endpoint — generous, but capped.
        if method == "POST" and path == "/holds":
            return f"hold:{client_ip}", get_settings().rate_limit_hold_per_minute
        # Generic bucket for everything else we choose to limit at the app layer.
        if method == "POST" and path.startswith("/bookings/"):
            return f"default:{client_ip}", get_settings().rate_limit_default_per_minute
        return None

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in self.EXEMPT_PATH_PREFIXES):
            return await call_next(request)

        bucket = self._bucket_for(request)
        if bucket is not None:
            key, limit = bucket
            counter = self._counters.setdefault(bucket[0].split(":")[0], _Counter())
            if not counter.hit(key, limit):
                retry_after = 60
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                    content={
                        "error": {
                            "code": "RATE_LIMITED",
                            "message": "Too many requests.",
                            "details": None,
                            "request_id": get_request_id(),
                        }
                    },
                )
        return await call_next(request)


__all__ = [
    "RequestIDMiddleware",
    "AccessLogMiddleware",
    "RateLimitMiddleware",
    "REQUEST_ID_HEADER",
]
