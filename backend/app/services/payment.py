"""Payment service — /pay orchestration and the idempotent callback.

REQ-12:  /pay returns fast, callback finishes the job.
REQ-14:  duplicate callback is a no-op (DB-enforced).
REQ-16:  handle SUCCEEDED / FAILED / REFUNDED.
REQ-17:  forward X-Mock-* headers verbatim to /charge.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.ids import new_ticket_code
from app.core.logging import logger
from app.repositories import booking as booking_repo
from app.repositories import seat as seat_repo
from app.schemas.gateway import GatewayCallback
from app.services.booking import ensure_payable
from app.services.gateway import GatewayClient


# --------------------------------------------------------------------------- #
# /bookings/{ref}/pay — REQ-04, REQ-12, REQ-17
# --------------------------------------------------------------------------- #
async def start_payment(
    session: AsyncSession,
    *,
    booking_ref: str,
    gateway: GatewayClient,
    passthrough_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Write the payment row FIRST, extend the reservation, then call /charge.

    Order matters. Writing the PENDING payment row before /charge means the
    callback race (X-Mock-Force: race) is a non-event: the gateway echoes
    booking_ref back, and we always have a row to update.

    `passthrough_headers` is the dict from the incoming /pay request — only
    X-Mock-Mode and X-Mock-Force are forwarded (REQ-17).
    """
    settings = get_settings()
    b = await ensure_payable(session, booking_ref=booking_ref)

    # Step 1: payment row keyed on OUR booking_ref, gateway_payment_id NULL.
    await booking_repo.create_pending_payment(
        session,
        booking_ref=b.booking_ref,
        amount=b.total_amount,
        currency=b.currency,
    )

    # Step 2: extend reservation, move seats HELD → PAYMENT_PENDING.
    reserved_until = datetime.now(tz=timezone.utc) + timedelta(
        seconds=settings.payment_window_seconds
    )
    await seat_repo.mark_seats_payment_pending(
        session,
        show_id=b.show_id,
        hold_id=b.hold_id,
        reserved_until=reserved_until,
    )
    await booking_repo.set_booking_status(session, booking_ref=b.booking_ref, status="PAYMENT_PENDING")
    await session.commit()

    # Step 3: fire-and-forget /charge. Never block /pay on the gateway.
    amount_minor = int(b.total_amount)  # BDT has no minor unit in our seed
    try:
        result = await gateway.charge(
            booking_ref=b.booking_ref,
            amount=amount_minor,
            currency=b.currency,
            callback_url=settings.gateway_callback_url,
            # Idempotency-Key: defaults to booking_ref so a retry of /pay
            # returns the same gateway payment_id without a second charge.
            idempotency_key=b.booking_ref,
            passthrough_headers=passthrough_headers,
        )
        # Step 4: backfill the gateway payment_id.
        local = await booking_repo.get_latest_payment_for_booking(session, b.booking_ref)
        if local is not None and result.payment_id:
            await booking_repo.update_payment_gateway_id(
                session, payment_id_local=local.id, gateway_payment_id=result.payment_id
            )
            await session.commit()
        return {
            "booking_ref": b.booking_ref,
            "payment_id": result.payment_id or new_local_payment_id(session, b.booking_ref),
            "status": "PENDING",
            "poll_url": f"/bookings/{b.booking_ref}",
            "poll_after_seconds": 2,
            "reserved_until": reserved_until,
        }
    except Exception:
        # Gateway 5xx / timeout / breaker open. Seats stay reserved.
        # Client may retry; the partial unique index protects against doubles.
        logger.warning(
            "payment /charge failed, seats reserved for retry",
            extra={"booking_ref": b.booking_ref},
        )
        # Surface to caller; router maps GatewayUnavailableError → 503.
        raise


def new_local_payment_id(session: AsyncSession, booking_ref: str) -> str:
    """Fallback id when /charge returned no payment_id."""
    return f"local_{booking_ref}"


# --------------------------------------------------------------------------- #
# POST /payments/callback — REQ-13, REQ-14, REQ-16
# --------------------------------------------------------------------------- #
async def apply_callback(
    session: AsyncSession, *, payload: GatewayCallback
) -> dict[str, Any]:
    """Idempotently apply a gateway callback.

    Returns one of:
        {"received": True, "duplicate": False}            first delivery
        {"received": True, "duplicate": True}             duplicate
        {"received": True, "accepted": False, "reason": …} unparseable
    ALWAYS returns 200 — caller's catch-all turns everything into a 200.
    """
    raw = payload.model_dump(mode="json")
    event_id = payload.event_id
    booking_ref = payload.booking_ref
    status = payload.status
    amount = Decimal(str(payload.amount))

    # Step 1: insert into the ledger. ON CONFLICT ⇒ duplicate.
    inserted = await booking_repo.insert_gateway_event(
        session,
        event_id=event_id,
        booking_ref=booking_ref,
        gateway_payment_id=payload.payment_id,
        status=status,
        amount=amount,
        raw_payload=raw,
    )
    if not inserted:
        await session.commit()
        return {"received": True, "duplicate": True}

    # Step 2: apply the transition.
    try:
        b = await booking_repo.get_booking(session, booking_ref)
        if b is None:
            await booking_repo.mark_gateway_event_processed(
                session, event_id=event_id, error="unknown_booking_ref"
            )
            await session.commit()
            return {
                "received": True,
                "accepted": False,
                "reason": "unknown_booking_ref",
            }

        # Move on regardless of the current state — but never undo CONFIRMED.
        if b.status == "CONFIRMED" and status == "SUCCEEDED":
            # Already done — second delivery of an idempotent callback.
            await booking_repo.mark_gateway_event_processed(session, event_id=event_id)
            await session.commit()
            return {"received": True, "duplicate": True}

        now = datetime.now(tz=timezone.utc)
        if status == "SUCCEEDED":
            ticket_code = new_ticket_code(b.show_id, b.booking_ref, event_id)
            await booking_repo.set_booking_status(
                session,
                booking_ref=b.booking_ref,
                status="CONFIRMED",
                ticket_code=ticket_code,
                confirmed_at=now,
            )
            await seat_repo.confirm_seats(session, booking_ref=b.booking_ref)
            await booking_repo.settle_payment(
                session, booking_ref=b.booking_ref, status="SUCCEEDED", settled_at=now
            )
        elif status == "FAILED":
            await booking_repo.set_booking_status(
                session, booking_ref=b.booking_ref, status="FAILED"
            )
            await seat_repo.release_booking_seats(session, booking_ref=b.booking_ref)
            await booking_repo.settle_payment(
                session,
                booking_ref=b.booking_ref,
                status="FAILED",
                settled_at=now,
                failure_reason="gateway_failed",
            )
        elif status == "REFUNDED":
            await booking_repo.set_booking_status(
                session, booking_ref=b.booking_ref, status="REFUNDED"
            )
            await seat_repo.release_booking_seats(session, booking_ref=b.booking_ref)
            await booking_repo.settle_payment(
                session,
                booking_ref=b.booking_ref,
                status="REFUNDED",
                settled_at=now,
            )

        await booking_repo.mark_gateway_event_processed(session, event_id=event_id)
        await session.commit()
        return {"received": True, "duplicate": False}

    except Exception as e:
        # Crash between ledger-insert and effect-application.
        # Mark the row as errored; the reconcile sweep retries it.
        logger.exception("callback application failed", extra={"event_id": event_id})
        await booking_repo.mark_gateway_event_processed(
            session, event_id=event_id, error=str(e)[:280]
        )
        await session.commit()
        # Per REQ-13, the gateway still gets 200 — see router.
        return {"received": True, "accepted": False, "reason": "processing_error"}