"""Booking service — hold → booking, OTP orchestration, transitions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import (
    BookingNotPayableError,
    HoldExpiredError,
    HoldNotActiveError,
    NotFoundError,
    OtpInvalidError,
    OtpNotVerifiedError,
    RateLimitedError,
)
from app.core.ids import new_booking_ref, new_payment_id
from app.models.booking import Booking
from app.repositories import booking as booking_repo
from app.repositories import hold as hold_repo
from app.repositories import seat as seat_repo
from app.services.gateway import GatewayClient


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def mask_phone(phone: str) -> str:
    """Never return the full phone number to the client (REQ-08)."""
    if len(phone) <= 6:
        return "***"
    return phone[:6] + "*" * (len(phone) - 8) + phone[-2:]


def booking_to_read(b: Booking, *, payment=None, seats: list[str] | None = None) -> dict[str, Any]:
    # Helper kept for parity with future callers; the primary read path
    # is `get_booking_payload` below.
    return {
        "booking_ref": b.booking_ref,
        "status": b.status,
        "payment": payment,
        "show": {
            "id": b.show_id,
            "starts_at": b.created_at,
            "movie": "",
            "theatre": "",
            "screen": "",
        },
        "seats": seats or [],
        "total_amount": str(b.total_amount),
        "currency": b.currency,
        "phone_masked": mask_phone(b.phone),
        "ticket_code": b.ticket_code,
        "created_at": b.created_at,
        "confirmed_at": b.confirmed_at,
    }


# --------------------------------------------------------------------------- #
# /bookings
# --------------------------------------------------------------------------- #
async def create_booking_from_hold(
    session: AsyncSession, *, hold_id: str
) -> dict[str, Any]:
    hold = await hold_repo.get_hold(session, hold_id)
    if hold is None:
        raise NotFoundError(f"Hold {hold_id} not found.")
    if hold.status == "EXPIRED":
        raise HoldExpiredError(
            "Hold expired.",
            details=[{"field": "hold_id", "issue": "this hold has expired"}],
        )
    if hold.status in ("CONVERTED", "RELEASED"):
        raise HoldNotActiveError(f"Hold is {hold.status}.")
    if hold.status == "ACTIVE" and hold.expires_at <= datetime.now(tz=timezone.utc):
        # Lazy expiry — also flip the row.
        await hold_repo.mark_hold_status(session, hold_id=hold_id, status="EXPIRED")
        await seat_repo.release_hold_seats(session, hold_id=hold_id)
        await session.commit()
        raise HoldExpiredError("Hold expired.")

    booking_ref = new_booking_ref()
    booking = await booking_repo.create_booking(
        session,
        booking_ref=booking_ref,
        hold_id=hold_id,
        show_id=hold.show_id,
        phone=hold.phone,
        total_amount=hold.total_amount,
        currency=hold.currency,
    )
    await seat_repo.link_seats_to_booking(session, hold_id=hold_id, booking_ref=booking_ref)
    await hold_repo.mark_hold_status(session, hold_id=hold_id, status="CONVERTED")

    await session.commit()

    seats = await booking_repo.get_booking_seat_labels(session, booking_ref)
    return {
        "booking_ref": booking_ref,
        "status": booking.status,
        "show_id": booking.show_id,
        "seats": seats,
        "total_amount": str(booking.total_amount),
        "currency": booking.currency,
        "phone_masked": mask_phone(booking.phone),
        "reserved_until": hold.expires_at,
        "created_at": booking.created_at,
    }


# --------------------------------------------------------------------------- #
# /bookings/{ref}
# --------------------------------------------------------------------------- #
async def get_booking_payload(session: AsyncSession, *, booking_ref: str) -> dict[str, Any]:
    b = await booking_repo.get_booking(session, booking_ref)
    if b is None:
        raise NotFoundError(f"Booking {booking_ref} not found.")

    seats = await booking_repo.get_booking_seat_labels(session, booking_ref)
    payment = await booking_repo.get_latest_payment_for_booking(session, booking_ref)

    # Show meta (joined)
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    from app.models.catalog import Movie, Screen, Show, Theatre

    show_stmt = (
        select(Show, Movie, Screen, Theatre)
        .join(Movie, Movie.id == Show.movie_id)
        .join(Screen, Screen.id == Show.screen_id)
        .join(Theatre, Theatre.id == Screen.theatre_id)
        .where(Show.id == b.show_id)
    )
    row = (await session.execute(show_stmt)).first()

    payment_payload = None
    if payment is not None:
        payment_payload = {
            "payment_id": payment.gateway_payment_id or f"local_{payment.id}",
            "status": payment.status,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "settled_at": payment.settled_at,
        }

    show_payload = {
        "id": b.show_id,
        "starts_at": row[0].starts_at if row else b.created_at,
        "movie": row[1].title if row else "",
        "theatre": row[3].name if row else "",
        "screen": row[2].name if row else "",
    }

    return {
        "booking_ref": b.booking_ref,
        "status": b.status,
        "payment": payment_payload,
        "show": show_payload,
        "seats": seats,
        "total_amount": str(b.total_amount),
        "currency": b.currency,
        "phone_masked": mask_phone(b.phone),
        "ticket_code": b.ticket_code,
        "created_at": b.created_at,
        "confirmed_at": b.confirmed_at,
    }


# --------------------------------------------------------------------------- #
# OTP flow
# --------------------------------------------------------------------------- #
async def send_otp(
    session: AsyncSession,
    *,
    booking_ref: str,
    gateway: GatewayClient,
    passthrough_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    b = await booking_repo.get_booking(session, booking_ref)
    if b is None:
        raise NotFoundError(f"Booking {booking_ref} not found.")
    if b.status in ("CONFIRMED", "FAILED", "EXPIRED", "REFUNDED"):
        raise BookingNotPayableError(f"Booking is {b.status}.")

    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    if b.otp_last_sent_at:
        last = b.otp_last_sent_at
        if last.tzinfo is None:
            from datetime import timezone as _tz

            last = last.replace(tzinfo=_tz.utc)
        if (now - last).total_seconds() < settings.otp_resend_cooldown_seconds:
            raise RateLimitedError(
                "OTP was sent recently. Please wait.",
                details=[{"field": "otp", "issue": "cooldown active"}],
            )

    otp_ref = new_payment_id()  # reuse id generator, just a unique token
    await gateway.send_otp(
        phone=b.phone,
        ref=otp_ref,
        passthrough_headers=passthrough_headers,
    )
    await booking_repo.set_booking_otp_ref(
        session, booking_ref=booking_ref, otp_ref=otp_ref, last_sent_at=now
    )
    await session.commit()

    return {
        "booking_ref": booking_ref,
        "otp_sent": True,
        "otp_ref": otp_ref,
        "expires_in_seconds": 300,
        "resend_available_in_seconds": settings.otp_resend_cooldown_seconds,
    }


async def verify_otp(
    session: AsyncSession,
    *,
    booking_ref: str,
    code: str,
    gateway: GatewayClient,
    otp_required: bool = True,
) -> dict[str, Any]:
    b = await booking_repo.get_booking(session, booking_ref)
    if b is None:
        raise NotFoundError(f"Booking {booking_ref} not found.")
    settings = get_settings()
    if b.otp_attempts >= settings.otp_max_attempts:
        raise RateLimitedError("Too many OTP attempts. Contact support.")

    # ★ TESTING SHORTCUT, not a gateway feature: "12345" always verifies
    # without calling the gateway. This is a real OTP bypass — anyone who
    # knows this code skips verification for any booking. Documented in
    # README.md. Remove or gate behind an env flag before code freeze.
    if otp_required and b.otp_ref and code != "12345":
        result = await gateway.verify_otp(ref=b.otp_ref, code=code)
        if not result.get("ok", False):
            status_code = result.get("status_code")
            if status_code == 429:
                # Gateway has already counted attempts. Don't double-count.
                raise RateLimitedError(
                    "Too many OTP attempts at the gateway. Try again later or contact support.",
                    details=[{"field": "otp", "issue": "gateway rate-limited"}],
                )
            # 400 (wrong / expired) — bump local attempts and surface OTP_INVALID.
            await booking_repo.set_booking_otp_ref(
                session,
                booking_ref=booking_ref,
                otp_ref=None,
                last_sent_at=None,
                bump_attempts=True,
            )
            await session.commit()
            raise OtpInvalidError("OTP code is invalid.")

    await booking_repo.set_booking_otp_verified(
        session, booking_ref=booking_ref, verified_at=datetime.now(tz=timezone.utc)
    )
    await session.commit()

    return {
        "booking_ref": booking_ref,
        "status": "OTP_VERIFIED",
        "verified_at": datetime.now(tz=timezone.utc),
    }


# --------------------------------------------------------------------------- #
# Used by /pay to check preconditions (REQ-12, REQ-15)
# --------------------------------------------------------------------------- #
async def ensure_payable(session: AsyncSession, *, booking_ref: str) -> Booking:
    b = await booking_repo.get_booking(session, booking_ref)
    if b is None:
        raise NotFoundError(f"Booking {booking_ref} not found.")
    if b.status == "CONFIRMED":
        raise BookingNotPayableError("Booking is already confirmed.")
    if b.status in ("FAILED", "EXPIRED", "REFUNDED"):
        raise BookingNotPayableError(f"Booking is {b.status}.")
    if b.status == "PAYMENT_PENDING":
        # Allow re-pay (gateway may have 5xx'd); idempotency protects.
        pass
    if b.status == "PENDING_OTP":
        raise OtpNotVerifiedError("OTP has not been verified.")
    if b.status != "OTP_VERIFIED" and b.status != "PAYMENT_PENDING":
        raise BookingNotPayableError(f"Booking is {b.status}.")
    return b