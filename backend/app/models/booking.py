"""Bookings + payments + the idempotency ledger (gateway_events).

`gateway_events.event_id` is the duplicate-callback key (REQ-14). It is the
PK precisely so a second INSERT is rejected by the DB, not by application
logic that races.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Booking(Base):
    __tablename__ = "bookings"

    booking_ref: Mapped[str] = mapped_column(String(30), primary_key=True)
    hold_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("holds.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    show_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shows.id", ondelete="RESTRICT"), nullable=False
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING_OTP")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BDT")

    otp_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    otp_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    otp_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    otp_last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    ticket_code: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_OTP','OTP_VERIFIED','PAYMENT_PENDING',"
            "'CONFIRMED','FAILED','EXPIRED','REFUNDED')",
            name="booking_status_valid",
        ),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booking_ref: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("bookings.booking_ref", ondelete="CASCADE"),
        nullable=False,
    )
    gateway_payment_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BDT")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "status IN ('PENDING','SUCCEEDED','FAILED','REFUNDED')",
            name="payment_status_valid",
        ),
    )


class GatewayEvent(Base):
    """The idempotency ledger. PK = gateway's event_id (REQ-14)."""

    __tablename__ = "gateway_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    booking_ref: Mapped[str] = mapped_column(String(30), nullable=False)  # no FK (REQ-13)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    process_error: Mapped[str | None] = mapped_column(String(300), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('SUCCEEDED','FAILED','REFUNDED')",
            name="gateway_event_status_valid",
        ),
    )
