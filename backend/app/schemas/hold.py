"""Hold schemas (REQ-03, REQ-20)."""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


SEAT_LABEL_RE = re.compile(r"^[A-Z]{1,2}[0-9]{1,3}$")
PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")


class HoldCreate(BaseModel):
    show_id: int = Field(..., ge=1)
    seats: list[str] = Field(..., min_length=1)
    phone: str = Field(...)

    @field_validator("seats")
    @classmethod
    def _validate_seats(cls, v: list[str]) -> list[str]:
        if len(v) > 6:
            raise ValueError("at most 6 seats per hold")
        cleaned: list[str] = []
        for s in v:
            s = s.upper().strip()
            if not SEAT_LABEL_RE.match(s):
                raise ValueError(f"seat '{s}' does not match required pattern")
            cleaned.append(s)
        # No duplicates.
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("duplicate seat labels")
        return cleaned

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        v = v.strip()
        if not PHONE_RE.match(v):
            raise ValueError("phone must be E.164-ish, 10–15 digits")
        return v


class HoldSeat(BaseModel):
    seat: str
    seat_class: str
    price: str


class HoldRead(BaseModel):
    hold_id: str
    show_id: int
    status: str
    seats: list[HoldSeat]
    total_amount: str
    currency: str
    expires_at: datetime
    expires_in_seconds: int
    booking_ref: str | None = None
