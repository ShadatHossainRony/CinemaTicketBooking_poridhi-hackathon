"""Hold repository — CRUD on the hold aggregate."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hold import Hold


async def create_hold(
    session: AsyncSession,
    *,
    hold_id: str,
    show_id: int,
    phone: str,
    seat_count: int,
    total_amount: Decimal,
    currency: str,
    expires_at: datetime,
) -> Hold:
    hold = Hold(
        id=hold_id,
        show_id=show_id,
        phone=phone,
        seat_count=seat_count,
        total_amount=total_amount,
        currency=currency,
        expires_at=expires_at,
        status="ACTIVE",
    )
    session.add(hold)
    await session.flush()
    return hold


async def get_hold(session: AsyncSession, hold_id: str) -> Hold | None:
    return await session.get(Hold, hold_id)


async def mark_hold_status(
    session: AsyncSession, *, hold_id: str, status: str
) -> int:
    hold = await session.get(Hold, hold_id)
    if hold is None:
        return 0
    hold.status = status
    return 1


async def expire_active_holds(session: AsyncSession) -> int:
    """Mark holds ACTIVE → EXPIRED when their window passed."""
    from sqlalchemy import update

    result = await session.execute(
        update(Hold)
        .where(Hold.status == "ACTIVE", Hold.expires_at <= datetime.now(tz=timezone.utc))
        .values(status="EXPIRED")
    )
    return result.rowcount or 0
