"""POST /payments/callback — gateway webhook.

★ REQ-13, REQ-14: this handler ALWAYS returns 200, even for duplicates,
malformed bodies, invalid signatures, and our own internal exceptions.
A non-200 makes the gateway retry up to 8 times with exponential backoff
(payment_gateway.md, "Three rules"). Returning 200 is the only way to
break the retry loop and tell the gateway "got it, I own this now".

The try/except lives INSIDE the handler. A non-200 means the global
catch-all ran — which would mean REQ-13 is broken.

Bonus (payment_gateway.md): we verify the X-Signature HMAC over the RAW
body before we accept anything. If verification fails we still return
200 (REQ-13 wins), but we log loudly so the misbehavior is visible.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.logging import logger
from app.schemas.gateway import GatewayCallback
from app.services.payment import apply_callback
from app.services.signature import SIG_HEADER, verify as verify_signature

router = APIRouter(tags=["payments"])


@router.post("/payments/callback", status_code=status.HTTP_200_OK)
async def payments_callback(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    # 1) Capture the raw body BEFORE any parsing — the HMAC is over the
    #    bytes the gateway actually sent. Pydantic re-serialisation would
    #    change whitespace and key order and the signature would never match.
    raw_body = await request.body()
    signature = request.headers.get(SIG_HEADER)
    settings = get_settings()

    # 2) Verify the HMAC. On failure: still return 200 (REQ-13), but log.
    if not verify_signature(settings, raw_body, signature):
        logger.warning(
            "callback: invalid X-Signature (still 200, gateway's retry-loop rule)",
            extra={
                "remote": request.client.host if request.client else None,
                "path": request.url.path,
            },
        )
        return JSONResponse(
            status_code=200,
            content={"received": True, "accepted": False, "reason": "invalid_signature"},
        )

    # 3) Parse JSON. Malformed body → still 200.
    try:
        raw_json = json.loads(raw_body) if raw_body else {}
        payload = GatewayCallback.model_validate(raw_json)
    except Exception as e:
        logger.warning(
            "callback: malformed body",
            extra={"error": f"{type(e).__name__}: {e}"},
        )
        return JSONResponse(
            status_code=200,
            content={"received": True, "accepted": False, "reason": "malformed_body"},
        )

    # 4) Apply the callback. The apply step is idempotent on event_id.
    try:
        result = await apply_callback(session, payload=payload)
        return JSONResponse(status_code=200, content=result)
    except Exception:
        logger.exception(
            "callback: unhandled exception",
            extra={"event_id": payload.event_id},
        )
        return JSONResponse(
            status_code=200,
            content={"received": True, "accepted": False, "reason": "internal_error"},
        )
