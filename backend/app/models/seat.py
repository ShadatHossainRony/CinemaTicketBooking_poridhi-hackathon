"""The contention table: one row per (show, seat).

The atomic claim predicate in `repositories/seat.py` is the *only* place
that ever writes `status`. The CHECK constraint here makes the
"HELD with no expiry" state unrepresentable.
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ShowSeat(Base):
    __tablename__ = "show_seats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    show_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shows.id", ondelete="CASCADE"), nullable=False
    )
    row_label: Mapped[str] = mapped_column(String(2), nullable=False)
    seat_number: Mapped[int] = mapped_column(Integer, nullable=False)
    seat_label: Mapped[str] = mapped_column(String(5), nullable=False)
    seat_class: Mapped[str] = mapped_column(String(20), nullable=False, default="STANDARD")
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="AVAILABLE")
    hold_id: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("holds.id", ondelete="SET NULL"), nullable=True
    )
    booking_ref: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("bookings.booking_ref", ondelete="SET NULL"),
        nullable=True,
    )
    reserved_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("show_id", "row_label", "seat_number", name="uq_show_seats_show_row_num"),
        UniqueConstraint("show_id", "seat_label", name="uq_show_seats_show_label"),
        CheckConstraint("seat_number > 0", name="seat_number_positive"),
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint(
            "seat_class IN ('STANDARD','PREMIUM')", name="seat_class_valid"
        ),
        CheckConstraint(
            "status IN ('AVAILABLE','HELD','PAYMENT_PENDING','BOOKED')",
            name="status_valid",
        ),
        # The big one: held without an expiry is unrepresentable.
        CheckConstraint(
            "(status = 'AVAILABLE' AND reserved_until IS NULL AND hold_id IS NULL) OR "
            "(status IN ('HELD','PAYMENT_PENDING') AND reserved_until IS NOT NULL) OR "
            "(status = 'BOOKED' AND booking_ref IS NOT NULL)",
            name="status_expiry_consistent",
        ),
    )
