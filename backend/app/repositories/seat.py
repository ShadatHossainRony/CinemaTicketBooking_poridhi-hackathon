"""The seat repository.

★ This file holds THE atomic claim. The single `UPDATE ... RETURNING`
in `claim_seats` is the *only* mechanism by which a seat transitions from
AVAILABLE to HELD. It is the answer to REQ-07 and Scenario A.

`03-data-model.md` §4.1 — read it before editing this file.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Sequence

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import Screen, Show
from app.models.seat import ShowSeat


# --------------------------------------------------------------------------- #
# Read: seat map for one show (REQ-02, REQ-20)
# --------------------------------------------------------------------------- #
async def get_seat_map(
    session: AsyncSession, show_id: int
) -> tuple[Show, Screen, Sequence[dict]] | None:
    """Return (Show, Screen, [seat dicts]). Effective status is computed
    in SQL so the map never lies while the sweeper is mid-tick.
    """
    show = await session.get(Show, show_id)
    if show is None:
        return None
    screen = await session.get(Screen, show.screen_id)
    if screen is None:
        return None

    sql = text(
        """
        SELECT
            seat_label,
            row_label,
            seat_number,
            seat_class,
            price,
            CASE
                WHEN status IN ('HELD','PAYMENT_PENDING') AND reserved_until <= now()
                    THEN 'AVAILABLE'
                WHEN status = 'PAYMENT_PENDING' THEN 'HELD'
                ELSE status
            END                                                    AS effective_status,
            CASE WHEN status IN ('HELD','PAYMENT_PENDING') AND reserved_until > now()
                 THEN reserved_until END                          AS held_until
        FROM show_seats
        WHERE show_id = :show_id
        ORDER BY row_label, seat_number
        """
    )
    result = await session.execute(sql, {"show_id": show_id})
    rows = [dict(r._mapping) for r in result.all()]
    return show, screen, rows


async def get_summary(session: AsyncSession, show_id: int) -> dict[str, int]:
    """Cheap aggregate, applying lazy expiry in SQL."""
    sql = text(
        """
        SELECT
            count(*) AS total,
            count(*) FILTER (
                WHERE status = 'AVAILABLE'
                   OR (status IN ('HELD','PAYMENT_PENDING') AND reserved_until <= now())
            ) AS available,
            count(*) FILTER (
                WHERE status IN ('HELD','PAYMENT_PENDING') AND reserved_until > now()
            ) AS held,
            count(*) FILTER (WHERE status = 'BOOKED') AS booked
        FROM show_seats
        WHERE show_id = :show_id
        """
    )
    result = await session.execute(sql, {"show_id": show_id})
    row = result.mappings().first()
    return {
        "total": int(row["total"]),
        "available": int(row["available"]),
        "held": int(row["held"]),
        "booked": int(row["booked"]),
    }


async def seats_total(session: AsyncSession, show_id: int) -> int:
    result = await session.execute(
        select(func.count(ShowSeat.id)).where(ShowSeat.show_id == show_id)
    )
    return int(result.scalar_one())


# --------------------------------------------------------------------------- #
# ★ THE ATOMIC CLAIM — REQ-07, REQ-38
# --------------------------------------------------------------------------- #
async def claim_seats(
    session: AsyncSession,
    *,
    show_id: int,
    seat_labels: list[str],
    hold_id: str,
    reserved_until: datetime,
) -> list[dict]:
    """Atomically transition the requested seats to HELD.

    Returns the rows that were claimed. If fewer rows come back than the
    number of requested labels, the caller rolls back the transaction.

    This single statement is the *only* place a seat moves to HELD. Never
    read-then-write. Never a mutex. The predicate and the write are one
    statement so no window exists between checking and claiming.
    """
    sql = text(
        """
        UPDATE show_seats
           SET status         = 'HELD',
               hold_id        = :hold_id,
               reserved_until = :reserved_until,
               updated_at     = now()
         WHERE show_id    = :show_id
           AND seat_label = ANY(:seat_labels)
           AND (
                 status = 'AVAILABLE'
              OR (status IN ('HELD','PAYMENT_PENDING') AND reserved_until <= now())
           )
        RETURNING id, seat_label, seat_class, price
        """
    ).bindparams(bindparam("seat_labels", expanding=True))

    result = await session.execute(
        sql,
        {
            "show_id": show_id,
            "seat_labels": seat_labels,
            "hold_id": hold_id,
            "reserved_until": reserved_until,
        },
    )
    return [dict(r._mapping) for r in result.all()]


# --------------------------------------------------------------------------- #
# Status transitions for /pay and callback
# --------------------------------------------------------------------------- #
async def mark_seats_payment_pending(
    session: AsyncSession,
    *,
    show_id: int,
    hold_id: str,
    reserved_until: datetime,
) -> int:
    sql = text(
        """
        UPDATE show_seats
           SET status = 'PAYMENT_PENDING',
               reserved_until = :reserved_until,
               updated_at = now()
         WHERE show_id = :show_id
           AND hold_id = :hold_id
           AND status = 'HELD'
        """
    )
    result = await session.execute(
        sql,
        {"show_id": show_id, "hold_id": hold_id, "reserved_until": reserved_until},
    )
    return result.rowcount or 0


async def confirm_seats(
    session: AsyncSession, *, booking_ref: str
) -> int:
    sql = text(
        """
        UPDATE show_seats
           SET status = 'BOOKED',
               reserved_until = NULL,
               updated_at = now()
         WHERE booking_ref = :booking_ref
           AND status = 'PAYMENT_PENDING'
        """
    )
    result = await session.execute(sql, {"booking_ref": booking_ref})
    return result.rowcount or 0


async def release_hold_seats(session: AsyncSession, *, hold_id: str) -> int:
    sql = text(
        """
        UPDATE show_seats
           SET status = 'AVAILABLE',
               hold_id = NULL,
               booking_ref = NULL,
               reserved_until = NULL,
               updated_at = now()
         WHERE hold_id = :hold_id
           AND status IN ('HELD','PAYMENT_PENDING')
        """
    )
    result = await session.execute(sql, {"hold_id": hold_id})
    return result.rowcount or 0


async def release_booking_seats(session: AsyncSession, *, booking_ref: str) -> int:
    sql = text(
        """
        UPDATE show_seats
           SET status = 'AVAILABLE',
               hold_id = NULL,
               booking_ref = NULL,
               reserved_until = NULL,
               updated_at = now()
         WHERE booking_ref = :booking_ref
           AND status = 'PAYMENT_PENDING'
        """
    )
    result = await session.execute(sql, {"booking_ref": booking_ref})
    return result.rowcount or 0


async def link_seats_to_booking(
    session: AsyncSession, *, hold_id: str, booking_ref: str
) -> int:
    """Used when a booking is created from a hold."""
    sql = text(
        """
        UPDATE show_seats
           SET booking_ref = :booking_ref,
               updated_at = now()
         WHERE hold_id = :hold_id
           AND status = 'HELD'
        """
    )
    result = await session.execute(
        sql, {"booking_ref": booking_ref, "hold_id": hold_id}
    )
    return result.rowcount or 0


# --------------------------------------------------------------------------- #
# Sweeper (REQ-06, REQ-44)
# --------------------------------------------------------------------------- #
async def sweep_expired(session: AsyncSession) -> int:
    sql = text(
        """
        UPDATE show_seats
           SET status = 'AVAILABLE',
               hold_id = NULL,
               booking_ref = NULL,
               reserved_until = NULL,
               updated_at = now()
         WHERE status IN ('HELD','PAYMENT_PENDING')
           AND reserved_until <= now()
        """
    )
    result = await session.execute(sql)
    return result.rowcount or 0


async def get_seat_prices_for_labels(
    session: AsyncSession, show_id: int, seat_labels: list[str]
) -> dict[str, Decimal]:
    result = await session.execute(
        select(ShowSeat.seat_label, ShowSeat.price).where(
            ShowSeat.show_id == show_id, ShowSeat.seat_label.in_(seat_labels)
        )
    )
    return {row[0]: row[1] for row in result.all()}
