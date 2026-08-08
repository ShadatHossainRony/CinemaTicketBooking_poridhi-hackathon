"""Hold aggregate. PK is the opaque public reference (REQ-06, INF-07)."""
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Hold(Base):
    __tablename__ = "holds"

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    show_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shows.id", ondelete="CASCADE"), nullable=False
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    seat_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BDT")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','EXPIRED','CONVERTED','RELEASED')",
            name="hold_status_valid",
        ),
        CheckConstraint("seat_count BETWEEN 1 AND 10", name="hold_seat_count_range"),
    )
