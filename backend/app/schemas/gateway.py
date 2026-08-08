"""Inbound gateway callback schema (REQ-13, REQ-14)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class GatewayCallback(BaseModel):
    """The body the gateway POSTs to /payments/callback.

    `amount` may arrive as int or decimal; we coerce via Pydantic.
    `currency` is optional in the gateway's doc but always sent.
    `timestamp` is provided by the gateway for replay-protection
    diagnostics but is NOT used as the dedup key — `event_id` is.
    """
    event_id: str = Field(..., min_length=1, max_length=64)
    payment_id: str | None = None
    booking_ref: str = Field(..., min_length=1)
    status: str = Field(..., pattern="^(SUCCEEDED|FAILED|REFUNDED)$")
    amount: Decimal | int | float
    currency: str | None = None
    timestamp: datetime | None = None