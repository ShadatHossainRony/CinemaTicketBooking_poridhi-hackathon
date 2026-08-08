"""Booking + payment + gateway-event repository."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking, GatewayEvent, Payment


# --------------------------------------------------------------------------- #
# Bookings
# --------------------------------------------------------------------------- #
async def create_booking(
    session: AsyncSession,
    *,
    booking_ref: str,
    hold_id: str,
    show_id: int,
    phone: str,
    total_amount: Decimal,
    currency: str,
) -> Booking:
    booking = Booking(
        booking_ref=booking_ref,
        hold_id=hold_id,
        show_id=show_id,
        phone=phone,
        total_amount=total_amount,
        currency=currency,
        status="PENDING_OTP",
    )
    session.add(booking)
    await session.flush()
    return booking


async def get_booking(session: AsyncSession, booking_ref: str) -> Booking | None:
    return await session.get(Booking, booking_ref)


async def get_booking_seat_labels(
    session: AsyncSession, booking_ref: str
) -> list[str]:
    result = await session.execute(
        text("SELECT seat_label FROM show_seats WHERE booking_ref = :ref ORDER BY seat_label"),
        {"ref": booking_ref},
    )
    return [r[0] for r in result.all()]


async def set_booking_status(
    session: AsyncSession,
    *,
    booking_ref: str,
    status: str,
    ticket_code: str | None = None,
    confirmed_at: datetime | None = None,
) -> int:
    """Unconditional write. Only safe when the caller already holds an
    exclusive reason to believe no concurrent writer can be racing this
    booking — e.g. `apply_callback`, which is itself serialised per
    `event_id` by the gateway_events ledger insert before this ever runs.

    For any caller that reads the booking, does other work, and only
    later decides to write (e.g. /pay) — use `transition_booking_status`
    instead. This function performs no state check and will happily
    overwrite whatever is there.
    """
    booking = await session.get(Booking, booking_ref)
    if booking is None:
        return 0
    booking.status = status
    if ticket_code is not None:
        booking.ticket_code = ticket_code
    if confirmed_at is not None:
        booking.confirmed_at = confirmed_at
    return 1


async def transition_booking_status(
    session: AsyncSession,
    *,
    booking_ref: str,
    to_status: str,
    from_statuses: list[str],
) -> bool:
    """Conditional transition. Returns True if the row moved.

    Mirrors `seat_repo.claim_seats`: the precondition and the write are
    one statement, so a caller that read the booking earlier (and may now
    be acting on stale information — a duplicate /pay call racing its own
    confirmation callback is the concrete case this exists for) cannot
    clobber a state it never actually observed. The row only moves if it
    is still in one of `from_statuses` at the instant this statement runs.

    NOTE: no `bindparam(..., expanding=True)` here — that substitutes
    `:from_statuses` with `(?, ?, ?)`, producing `ANY((?, ?, ?))`, a
    parenthesized tuple rather than a Postgres array, which Postgres
    rejects. `ANY(...)` takes exactly one array-typed parameter; passing
    the plain Python list lets psycopg adapt it correctly. (Same bug,
    same fix, as `seat_repo.claim_seats` — see the note there.)
    """
    sql = text(
        """
        UPDATE bookings
           SET status = :to_status,
               updated_at = now()
         WHERE booking_ref = :booking_ref
           AND status = ANY(:from_statuses)
        RETURNING booking_ref
        """
    )
    result = await session.execute(
        sql,
        {
            "booking_ref": booking_ref,
            "to_status": to_status,
            "from_statuses": from_statuses,
        },
    )
    return result.first() is not None


async def set_booking_otp_verified(
    session: AsyncSession, *, booking_ref: str, verified_at: datetime
) -> None:
    booking = await session.get(Booking, booking_ref)
    if booking is None:
        return
    booking.status = "OTP_VERIFIED"
    booking.otp_verified_at = verified_at


async def set_booking_otp_ref(
    session: AsyncSession,
    *,
    booking_ref: str,
    otp_ref: str | None,
    last_sent_at: datetime | None,
    bump_attempts: bool = False,
) -> None:
    booking = await session.get(Booking, booking_ref)
    if booking is None:
        return
    if otp_ref is not None:
        booking.otp_ref = otp_ref
    if last_sent_at is not None:
        booking.otp_last_sent_at = last_sent_at
    if bump_attempts:
        booking.otp_attempts = (booking.otp_attempts or 0) + 1


# --------------------------------------------------------------------------- #
# Payments
# --------------------------------------------------------------------------- #
async def create_pending_payment(
    session: AsyncSession,
    *,
    booking_ref: str,
    amount: Decimal,
    currency: str,
) -> int:
    """Insert a PENDING row before calling the gateway (`03-data-model.md` §4.4).

    The partial unique index `uq_payments_live_per_booking` blocks a second
    PENDING/SUCCEEDED row for the same booking, so a duplicate /pay that
    slipped past every other defence would still fail here.
    """
    sql = text(
        """
        INSERT INTO payments (booking_ref, amount, currency, status)
        VALUES (:booking_ref, :amount, :currency, 'PENDING')
        ON CONFLICT DO NOTHING
        RETURNING id
        """
    )
    result = await session.execute(
        sql,
        {"booking_ref": booking_ref, "amount": amount, "currency": currency},
    )
    row = result.first()
    return int(row[0]) if row else 0


async def get_latest_payment_for_booking(
    session: AsyncSession, booking_ref: str
) -> Payment | None:
    stmt = (
        select(Payment)
        .where(Payment.booking_ref == booking_ref)
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def update_payment_gateway_id(
    session: AsyncSession,
    *,
    payment_id_local: int,
    gateway_payment_id: str,
) -> None:
    sql = text(
        """
        UPDATE payments
           SET gateway_payment_id = COALESCE(gateway_payment_id, :gid),
               updated_at = now()
         WHERE id = :pid
        """
    )
    await session.execute(
        sql, {"gid": gateway_payment_id, "pid": payment_id_local}
    )


async def settle_payment(
    session: AsyncSession,
    *,
    booking_ref: str,
    status: str,
    settled_at: datetime,
    failure_reason: str | None = None,
) -> None:
    payment = await get_latest_payment_for_booking(session, booking_ref)
    if payment is None:
        return
    payment.status = status
    payment.settled_at = settled_at
    payment.failure_reason = failure_reason


# --------------------------------------------------------------------------- #
# Gateway events (the idempotency ledger — REQ-14)
# --------------------------------------------------------------------------- #
async def insert_gateway_event(
    session: AsyncSession,
    *,
    event_id: str,
    booking_ref: str,
    gateway_payment_id: str | None,
    status: str,
    amount: Decimal,
    raw_payload: dict[str, Any],
) -> bool:
    """Insert one row; return True if inserted (new), False if duplicate.

    The PK is the gateway's `event_id`, so duplicate deliveries hit
    `ON CONFLICT DO NOTHING` and 0 rows come back. The DB decides,
    not application logic.
    """
    sql = text(
        """
        INSERT INTO gateway_events
            (event_id, booking_ref, gateway_payment_id, status, amount, raw_payload)
        VALUES
            (:event_id, :booking_ref, :gateway_payment_id, :status, :amount,
             CAST(:raw_payload AS JSONB))
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
        """
    )
    import json as _json

    result = await session.execute(
        sql,
        {
            "event_id": event_id,
            "booking_ref": booking_ref,
            "gateway_payment_id": gateway_payment_id,
            "status": status,
            "amount": amount,
            "raw_payload": _json.dumps(raw_payload, default=str),
        },
    )
    return result.first() is not None


async def mark_gateway_event_processed(
    session: AsyncSession, *, event_id: str, error: str | None = None
) -> None:
    sql = text(
        """
        UPDATE gateway_events
           SET processed_at = COALESCE(processed_at, now()),
               process_error = COALESCE(:err, process_error)
         WHERE event_id = :eid
        """
    )
    await session.execute(sql, {"eid": event_id, "err": error})


async def find_unprocessed_events(
    session: AsyncSession, *, older_than_seconds: int = 60
) -> list[GatewayEvent]:
    sql = text(
        """
        SELECT * FROM gateway_events
         WHERE processed_at IS NULL
           AND received_at < now() - (:age || ' seconds')::interval
         ORDER BY received_at
         LIMIT 50
        """
    )
    result = await session.execute(sql, {"age": str(older_than_seconds)})
    cols = result.keys()
    return [GatewayEvent(**dict(zip(cols, row))) for row in result.all()]
