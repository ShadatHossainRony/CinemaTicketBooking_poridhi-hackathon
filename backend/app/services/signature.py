"""HMAC-SHA256 verification for the gateway webhook.

Per payment_gateway.md ("Verifying the signature"):
  expected = hmac.new(secret, rawBody, sha256).hexdigest()
  if X-Signature != expected: 401

★ The signature is computed over the EXACT bytes the gateway sent.
FastAPI / Pydantic parse and re-serialise JSON, which changes whitespace
and key order. We therefore capture the raw body with `await request.body()`
BEFORE any JSON parsing happens.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Final

from app.core.config import Settings


SIG_HEADER: Final[str] = "X-Signature"


def compute(secret: str, raw_body: bytes) -> str:
    """Return the lowercase hex HMAC-SHA256 of `raw_body`."""
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify(settings: Settings, raw_body: bytes, signature_header: str | None) -> bool:
    """Constant-time compare. Returns False if the header is missing/empty.

    Skips verification entirely when `gateway_signature_required` is off.
    """
    if not settings.gateway_signature_required:
        return True
    if not signature_header:
        return False
    expected = compute(settings.gateway_secret, raw_body)
    # `hmac.compare_digest` is constant-time; tolerate either lowercase or
    # upper-case header (some clients send upper-case hex).
    return hmac.compare_digest(expected.lower(), signature_header.strip().lower())
