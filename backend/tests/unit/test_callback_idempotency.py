"""REQ-14: duplicate callback must be a no-op.

A second delivery of the same `event_id`:
- Must NOT create a second payment row.
- Must NOT double-confirm the booking.
- Must NOT double-count revenue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.booking import Booking, GatewayEvent, Payment
from app.repositories import booking as booking_repo
from app.schemas.gateway import GatewayCallback
from app.services.payment import apply_callback


async def _seed_booking_in_payment_pending(db_session) -> str:
    """Create a minimal booking + payment row in PENDING_PAYMENT for testing."""
    booking_ref = "bk_test_0001"
    booking = Booking(
        booking_ref=booking_ref,
        hold_id="hld_test_0001",
        show_id=1,
        phone="+8801700000099",
        status="PAYMENT_PENDING",
        total_amount=Decimal("450.00"),
        currency="BDT",
    )
    db_session.add(booking)
    await db_session.flush()

    # A pending payment row keyed on booking_ref.
    await booking_repo.create_pending_payment(
        db_session,
        booking_ref=booking_ref,
        amount=Decimal("450.00"),
        currency="BDT",
    )
    await db_session.commit()
    return booking_ref


@pytest.mark.asyncio
async def test_duplicate_callback_no_op(db_session):
    """Two deliveries of the same event_id produce exactly one effect."""
    booking_ref = await _seed_booking_in_payment_pending(db_session)

    payload = GatewayCallback(
        event_id="evt_dup_001",
        payment_id="pay_dup_001",
        booking_ref=booking_ref,
        status="SUCCEEDED",
        amount=450,
    )

    # First delivery.
    result = await apply_callback(db_session, payload=payload)
    assert result["duplicate"] is False

    # Second delivery.
    result = await apply_callback(db_session, payload=payload)
    assert result["duplicate"] is True

    # Exactly one event row, one SUCCEEDED payment, one CONFIRMED booking.
    events = (
        await db_session.execute(
            select(func.count(GatewayEvent.event_id)).where(
                GatewayEvent.event_id == "evt_dup_001"
            )
        )
    ).scalar_one()
    assert events == 1

    payments = (
        await db_session.execute(
            select(func.count(Payment.id)).where(Payment.booking_ref == booking_ref)
        )
    ).scalar_one()
    assert payments == 1

    booking = await db_session.get(Booking, booking_ref)
    assert booking is not None
    assert booking.status == "CONFIRMED"
    assert booking.confirmed_at is not None


@pytest.mark.asyncio
async def test_failed_callback_releases_seats(db_session):
    """A FAILED callback flips the booking to FAILED."""
    booking_ref = await _seed_booking_in_payment_pending(db_session)

    # Bind a show_seat to the booking so we can verify release.
    from sqlalchemy import text

    await db_session.execute(
        text(
            "INSERT INTO show_seats (show_id, row_label, seat_number, seat_label, "
            "seat_class, price, status, booking_ref) "
            "VALUES (1, 'Z', 1, 'Z1', 'STANDARD', 100, 'PAYMENT_PENDING', :ref)"
        ),
        {"ref": booking_ref},
    )
    await db_session.commit()

    payload = GatewayCallback(
        event_id="evt_fail_001",
        payment_id="pay_fail_001",
        booking_ref=booking_ref,
        status="FAILED",
        amount=450,
    )
    result = await apply_callback(db_session, payload=payload)
    assert result["duplicate"] is False

    booking = await db_session.get(Booking, booking_ref)
    assert booking is not None
    assert booking.status == "FAILED"

    seat_row = (
        await db_session.execute(text("SELECT status FROM show_seats WHERE booking_ref = :r"), {"r": booking_ref})
    ).first()
    assert seat_row is not None
    assert seat_row[0] == "AVAILABLE"


@pytest.mark.asyncio
async def test_unknown_booking_ref_still_recorded(db_session):
    """An event for an unknown booking must still be inserted and answered 200."""
    payload = GatewayCallback(
        event_id="evt_unknown_001",
        payment_id=None,
        booking_ref="bk_does_not_exist",
        status="SUCCEEDED",
        amount=100,
    )
    result = await apply_callback(db_session, payload=payload)
    assert result.get("accepted") is False
    assert result.get("reason") == "unknown_booking_ref"

    ev = await db_session.get(GatewayEvent, "evt_unknown_001")
    assert ev is not None