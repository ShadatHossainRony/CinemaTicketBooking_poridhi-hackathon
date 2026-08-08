"""Booking schemas (REQ-05, REQ-08, REQ-12, REQ-15)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class BookingCreate(BaseModel):
    hold_id: str = Field(..., min_length=8)


class OtpSendResponse(BaseModel):
    booking_ref: str
    otp_sent: bool
    otp_ref: str | None = None
    expires_in_seconds: int = 300
    resend_available_in_seconds: int = 30


class OtpVerifyRequest(BaseModel):
    code: str = Field(...)

    @field_validator("code")
    @classmethod
    def _check(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or not (4 <= len(v) <= 8):
            raise ValueError("code must be 4–8 digits")
        return v


class OtpVerifyResponse(BaseModel):
    booking_ref: str
    status: str
    verified_at: datetime


class PayResponse(BaseModel):
    booking_ref: str
    payment_id: str
    status: str = "PENDING"
    poll_url: str
    poll_after_seconds: int = 2
    reserved_until: datetime | None = None


class PaymentRead(BaseModel):
    payment_id: str | None
    status: str
    amount: str
    currency: str
    settled_at: datetime | None = None


class ShowStub(BaseModel):
    id: int
    starts_at: datetime
    movie: str
    theatre: str
    screen: str


class BookingRead(BaseModel):
    booking_ref: str
    status: str
    payment: PaymentRead | None = None
    show: ShowStub
    seats: list[str]
    total_amount: str
    currency: str
    phone_masked: str
    ticket_code: str | None = None
    created_at: datetime
    confirmed_at: datetime | None = None
