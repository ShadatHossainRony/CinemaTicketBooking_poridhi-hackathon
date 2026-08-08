"""Hold service — the rules around claiming seats.

★ The claim is one statement in `repositories/seat.py`. This service does
the surrounding rules: TTL, phone format, multi-seat all-or-nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import (
    BadRequestError,
    NotFoundError,
    SeatUnavailableError,
    TooManySeatsError,
)
from app.core.ids import new_hold_id
from app.models.catalog import Show
from app.repositories import hold as hold_repo
from app.repositories import seat as seat_repo


async def create_hold_for_seats(
    session: AsyncSession,
    *,
    show_id: int,
    seat_labels: list[str],
    phone: str,
) -> dict[str, Any]:
    """Create a hold atomically. Raises SeatUnavailableError on contention.

    Returns the serialized hold payload (seats → prices, totals, expiry).
    """
    settings = get_settings()

    if len(seat_labels) > settings.max_seats_per_hold:
        raise TooManySeatsError(
            f"A hold may cover at most {settings.max_seats_per_hold} seats.",
            details=[
                {"field": "seats", "issue": f"{len(seat_labels)} requested, maximum is {settings.max_seats_per_hold}"}
            ],
        )

    show = await session.get(Show, show_id)
    if show is None:
        raise NotFoundError(f"Show {show_id} not found.")
    if show.status == "CANCELLED":
        raise BadRequestError("Show is cancelled.")
    if show.starts_at <= datetime.now(tz=timezone.utc):
        raise BadRequestError("Show has already started.")

    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=settings.hold_ttl_seconds)
    hold_id = new_hold_id()

    # The atomic claim. If the row count is short, the whole tx rolls back.
    claimed = await seat_repo.claim_seats(
        session,
        show_id=show_id,
        seat_labels=seat_labels,
        hold_id=hold_id,
        reserved_until=expires_at,
    )
    if len(claimed) != len(seat_labels):
        # Find which seats were not claimed so the message is useful.
        claimed_labels = {row["seat_label"] for row in claimed}
        unavailable = [s for s in seat_labels if s not in claimed_labels]
        raise SeatUnavailableError(
            "One or more seats are no longer available.",
            details=[{"field": "seats", "issue": f"seat {s} is not available"} for s in unavailable],
        )

    total_amount = sum((Decimal(str(row["price"])) for row in claimed), Decimal("0"))
    await hold_repo.create_hold(
        session,
        hold_id=hold_id,
        show_id=show_id,
        phone=phone,
        seat_count=len(seat_labels),
        total_amount=total_amount,
        currency=show.currency,
        expires_at=expires_at,
    )
    await session.commit()

    return {
        "hold_id": hold_id,
        "show_id": show_id,
        "status": "ACTIVE",
        "seats": [
            {"seat": row["seat_label"], "seat_class": row["seat_class"], "price": str(row["price"])}
            for row in claimed
        ],
        "total_amount": str(total_amount),
        "currency": show.currency,
        "expires_at": expires_at,
        "expires_in_seconds": settings.hold_ttl_seconds,
    }


async def get_hold_payload(session: AsyncSession, hold_id: str) -> dict[str, Any]:
    """For GET /holds/{id}. Used by Scenario B (REQ-39)."""
    hold = await hold_repo.get_hold(session, hold_id)
    if hold is None:
        raise NotFoundError(f"Hold {hold_id} not found.")

    # SELECT the seats directly for this hold.
    from sqlalchemy import select

    from app.models.seat import ShowSeat

    result = await session.execute(
        select(ShowSeat.seat_label, ShowSeat.price, ShowSeat.seat_class).where(
            ShowSeat.hold_id == hold_id
        )
    )
    seats = [
        {"seat": r[0], "seat_class": r[2], "price": str(r[1])} for r in result.all()
    ]

    now = datetime.now(tz=timezone.utc)
    if hold.expires_at.tzinfo is None:
        # compare in UTC
        from datetime import timezone as _tz

        expires_at = hold.expires_at.replace(tzinfo=_tz.utc)
    else:
        expires_at = hold.expires_at
    remaining = int((expires_at - now).total_seconds())
    if remaining < 0:
        remaining = 0

    return {
        "hold_id": hold.id,
        "show_id": hold.show_id,
        "status": hold.status,
        "seats": seats,
        "total_amount": str(hold.total_amount),
        "currency": hold.currency,
        "expires_at": expires_at,
        "expires_in_seconds": remaining,
        "booking_ref": None,
    }
