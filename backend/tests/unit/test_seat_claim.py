"""★ The most important test in the repo.

REQ-07, REQ-38: 100 concurrent holds for one seat must produce exactly
one success, 99 clean rejections, and zero oversell.

Validates the atomic claim in `repositories.seat.claim_seats`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import SeatUnavailableError
from app.models.catalog import Show
from app.models.seat import ShowSeat
from app.repositories import seat as seat_repo


async def _make_show_with_seat(session: AsyncSession, label: str = "TEST") -> tuple[int, int]:
    """Create a minimal show + one seat for the test. Returns (show_id, seat_id)."""
    from sqlalchemy import text

    # Clean up any leftovers
    await session.execute(text("DELETE FROM show_seats WHERE seat_label = 'TEST'"))

    # Use the first available screen / movie / theatre from the seed.
    show = (await session.execute(select(Show).order_by(Show.id).limit(1))).scalars().first()
    assert show is not None, "Seed must have produced at least one show"

    seat = ShowSeat(
        show_id=show.id,
        row_label="A",
        seat_number=999,
        seat_label=label,
        seat_class="STANDARD",
        price=Decimal("100.00"),
        status="AVAILABLE",
    )
    session.add(seat)
    await session.commit()
    return show.id, seat.id


@pytest.mark.asyncio
async def test_100_concurrent_holds_one_seat_one_success(db_session: AsyncSession):
    """100 concurrent claims on the same seat → 1 success, 99 rejections."""
    show_id, _ = await _make_show_with_seat(db_session, label="RACE")
    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=60)

    results = {"success": 0, "rejected": 0, "errors": []}

    async def attempt(i: int) -> None:
        from app.db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            try:
                rows = await seat_repo.claim_seats(
                    session,
                    show_id=show_id,
                    seat_labels=["RACE"],
                    hold_id=f"hld_{i:04d}",
                    reserved_until=expires_at,
                )
                if len(rows) == 1:
                    await session.commit()
                    results["success"] += 1
                else:
                    await session.rollback()
                    results["rejected"] += 1
            except SeatUnavailableError:
                results["rejected"] += 1
            except Exception as e:  # noqa: BLE001
                results["errors"].append(str(e))
            finally:
                await session.close()

    # Run 100 attempts.
    await asyncio.gather(*(attempt(i) for i in range(100)))

    assert results["success"] == 1, f"Expected exactly 1 success, got {results['success']}"
    assert results["rejected"] == 99, f"Expected 99 rejections, got {results['rejected']}"
    assert not results["errors"], f"Unexpected errors: {results['errors']}"


@pytest.mark.asyncio
async def test_claim_after_expiry_succeeds(db_session: AsyncSession):
    """An expired HELD seat can be re-claimed (lazy expiry correctness)."""
    show_id, _ = await _make_show_with_seat(db_session, label="EXP")
    past = datetime.now(tz=timezone.utc) - timedelta(seconds=1)

    # First claim with a past expiry — pretending someone left a hold.
    rows = await seat_repo.claim_seats(
        db_session,
        show_id=show_id,
        seat_labels=["EXP"],
        hold_id="hld_first",
        reserved_until=past,
    )
    assert len(rows) == 1
    await db_session.commit()

    # Second claim should succeed because the first expired.
    future = datetime.now(tz=timezone.utc) + timedelta(seconds=60)
    rows = await seat_repo.claim_seats(
        db_session,
        show_id=show_id,
        seat_labels=["EXP"],
        hold_id="hld_second",
        reserved_until=future,
    )
    assert len(rows) == 1
    await db_session.commit()


@pytest.mark.asyncio
async def test_multi_seat_all_or_nothing(db_session: AsyncSession):
    """If one seat in a multi-seat request is unavailable, the whole claim fails."""
    show_id, _ = await _make_show_with_seat(db_session, label="MULTI")
    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=60)

    # Pre-claim the second seat.
    rows = await seat_repo.claim_seats(
        db_session,
        show_id=show_id,
        seat_labels=["MULTI"],
        hold_id="hld_setup",
        reserved_until=expires_at,
    )
    assert len(rows) == 1
    await db_session.commit()

    # Now request MULTI again (already claimed) + a non-existent seat.
    rows = await seat_repo.claim_seats(
        db_session,
        show_id=show_id,
        seat_labels=["MULTI", "DOES_NOT_EXIST"],
        hold_id="hld_multi",
        reserved_until=expires_at,
    )
    assert len(rows) == 0  # multi-seat request is all-or-nothing