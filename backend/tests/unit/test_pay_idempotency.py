"""services/payment.py::start_payment must not corrupt a CONFIRMED booking
when a stale duplicate /pay call reaches the database after the gateway
has already confirmed the booking via callback.

`ensure_payable` only reads; the actual guard is the conditional
transition in `repositories.booking.transition_booking_status`. A naive
"call start_payment twice, sequentially, after confirmation" test would
pass even without that guard, because `ensure_payable`'s own upfront
check already rejects an already-CONFIRMED booking when the two calls
are sequential. The real bug only appears when a caller's WRITE lands
after a state it never actually observed — i.e. the write is decoupled
in time from the read that authorized it. The first test below exercises
the guard at that level directly, simulating the interleaving precisely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from app.core.errors import BookingNotPayableError
from app.models.booking import Booking, Payment
from app.repositories import booking as booking_repo
from app.schemas.gateway import GatewayCallback
from app.services.payment import apply_callback, start_payment
from sqlalchemy import func, select


@dataclass
class _FakeChargeResult:
    payment_id: str
    raw: dict = field(default_factory=dict)


class _FakeGateway:
    """Stub satisfying GatewayClient's `charge` interface. No network I/O."""

    def __init__(self) -> None:
        self.charge_calls = 0

    async def charge(self, **kwargs):
        self.charge_calls += 1
        return _FakeChargeResult(payment_id=f"pay_fake_{self.charge_calls}")


async def _seed_otp_verified_booking(db_session, booking_ref: str) -> None:
    booking = Booking(
        booking_ref=booking_ref,
        hold_id=f"hld_{booking_ref}",
        show_id=1,
        phone="+8801700000098",
        status="OTP_VERIFIED",
        total_amount=Decimal("450.00"),
        currency="BDT",
    )
    db_session.add(booking)
    await db_session.commit()


@pytest.mark.asyncio
async def test_transition_rejects_after_confirmation(db_session):
    """The repository-level guard: a transition attempt that targets
    PAYMENT_PENDING must fail once the booking is CONFIRMED, regardless
    of when the caller last read the row."""
    booking_ref = "bk_pay_race_0001"
    await _seed_otp_verified_booking(db_session, booking_ref)

    # Simulates T1: the first, legitimate /pay call moving the booking on.
    ok = await booking_repo.transition_booking_status(
        db_session,
        booking_ref=booking_ref,
        to_status="PAYMENT_PENDING",
        from_statuses=["OTP_VERIFIED", "PAYMENT_PENDING"],
    )
    assert ok is True
    await db_session.commit()

    # Simulates the gateway's callback confirming the booking.
    await booking_repo.set_booking_status(
        db_session, booking_ref=booking_ref, status="CONFIRMED"
    )
    await db_session.commit()

    # Simulates T2: a STALE /pay call whose write reaches the database
    # only now — after confirmation — even though its own `ensure_payable`
    # read (not modelled here) happened earlier, before confirmation.
    ok = await booking_repo.transition_booking_status(
        db_session,
        booking_ref=booking_ref,
        to_status="PAYMENT_PENDING",
        from_statuses=["OTP_VERIFIED", "PAYMENT_PENDING"],
    )
    assert ok is False, "a confirmed booking must not be movable back to PAYMENT_PENDING"

    booking = await db_session.get(Booking, booking_ref)
    assert booking.status == "CONFIRMED"


@pytest.mark.asyncio
async def test_duplicate_pay_after_confirmation_raises_and_does_not_revert(db_session):
    """End-to-end through start_payment: once a callback has confirmed the
    booking, a later start_payment call must raise BookingNotPayableError
    and must not touch the booking's status or call the gateway."""
    booking_ref = "bk_pay_race_0002"
    await _seed_otp_verified_booking(db_session, booking_ref)
    gateway = _FakeGateway()

    result = await start_payment(db_session, booking_ref=booking_ref, gateway=gateway)
    assert result["status"] == "PENDING"
    assert gateway.charge_calls == 1

    payload = GatewayCallback(
        event_id="evt_pay_race_0002",
        payment_id="pay_fake_1",
        booking_ref=booking_ref,
        status="SUCCEEDED",
        amount=450,
    )
    cb_result = await apply_callback(db_session, payload=payload)
    assert cb_result["duplicate"] is False

    booking = await db_session.get(Booking, booking_ref)
    assert booking.status == "CONFIRMED"

    # ensure_payable's own upfront check already rejects a sequential call
    # here — this asserts the whole endpoint stays safe end-to-end, on
    # top of the interleaved-race guard proven directly above.
    with pytest.raises(BookingNotPayableError):
        await start_payment(db_session, booking_ref=booking_ref, gateway=gateway)

    booking = await db_session.get(Booking, booking_ref)
    assert booking.status == "CONFIRMED"
    assert gateway.charge_calls == 1, "the gateway must not be charged a second time"


@pytest.mark.asyncio
async def test_duplicate_pay_while_still_pending_is_safe(db_session):
    """The legitimate retry case: a second /pay call while the first is
    still PAYMENT_PENDING (e.g. the client never saw the first response)
    must not raise and must not create a second payment row — the
    transition to PAYMENT_PENDING from PAYMENT_PENDING is a no-op success,
    and uq_payments_live_per_booking blocks a second live payment row."""
    booking_ref = "bk_pay_retry_0001"
    await _seed_otp_verified_booking(db_session, booking_ref)
    gateway = _FakeGateway()

    first = await start_payment(db_session, booking_ref=booking_ref, gateway=gateway)
    assert first["status"] == "PENDING"

    second = await start_payment(db_session, booking_ref=booking_ref, gateway=gateway)
    assert second["status"] == "PENDING"

    booking = await db_session.get(Booking, booking_ref)
    assert booking.status == "PAYMENT_PENDING"

    payments = (
        await db_session.execute(
            select(func.count(Payment.id)).where(Payment.booking_ref == booking_ref)
        )
    ).scalar_one()
    assert payments == 1, "uq_payments_live_per_booking must prevent a second live payment row"
