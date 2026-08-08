"""REQ-06, REQ-39: hold expiry.

The lazy predicate in the claim is the correctness backstop; the
sweeper task is for tidiness. These tests verify both.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.repositories import seat as seat_repo


@pytest.mark.asyncio
async def test_sweep_releases_expired_holds(db_session):
    """The sweeper moves expired seats to AVAILABLE."""
    from app.models.catalog import Show
    from sqlalchemy import select

    show = (await db_session.execute(select(Show).order_by(Show.id).limit(1))).scalars().first()
    assert show is not None

    # Create an already-expired seat.
    await db_session.execute(
        text(
            "DELETE FROM show_seats WHERE seat_label='SWEEP_TEST'"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO show_seats (show_id, row_label, seat_number, seat_label, "
            "seat_class, price, status, hold_id, reserved_until) "
            "VALUES (:sid, 'Z', 2, 'SWEEP_TEST', 'STANDARD', 100, 'HELD', 'hld_old', "
            "now() - interval '1 second')"
        ),
        {"sid": show.id},
    )
    await db_session.commit()

    released = await seat_repo.sweep_expired(db_session)
    await db_session.commit()
    assert released >= 1

    row = (
        await db_session.execute(
            text("SELECT status, hold_id FROM show_seats WHERE seat_label='SWEEP_TEST'")
        )
    ).first()
    assert row[0] == "AVAILABLE"
    assert row[1] is None


@pytest.mark.asyncio
async def test_summary_applies_lazy_expiry(db_session):
    """The seat-map summary counts an expired HELD seat as AVAILABLE."""
    from app.models.catalog import Show
    from sqlalchemy import select, text

    show = (await db_session.execute(select(Show).order_by(Show.id).limit(1))).scalars().first()
    assert show is not None

    await db_session.execute(text("DELETE FROM show_seats WHERE seat_label='LAZY_TEST'"))
    await db_session.execute(
        text(
            "INSERT INTO show_seats (show_id, row_label, seat_number, seat_label, "
            "seat_class, price, status, hold_id, reserved_until) "
            "VALUES (:sid, 'Z', 3, 'LAZY_TEST', 'STANDARD', 100, 'HELD', 'hld_lazy', "
            "now() - interval '1 second')"
        ),
        {"sid": show.id},
    )
    await db_session.commit()

    summary = await seat_repo.get_summary(db_session, show.id)
    # Available must include the LAZY_TEST seat.
    assert summary["available"] >= 1
    assert summary["held"] == 0  # LAZY_TEST is expired, so it counts as available