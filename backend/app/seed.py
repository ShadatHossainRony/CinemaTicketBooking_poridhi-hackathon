"""Idempotent catalogue seed (REQ-10).

Runs as part of the `migrate` container. Every insert is get-or-create on
a natural key. Running ten times must not duplicate or crash.

4 movies · 2 theatres · 3 screens · 12 shows · 1,440 show_seats.
Every screen is standardized to 8 rows (A-H) x 15 seats (120 seats/screen)
so the frontend seat map never has to render a different shape per show.
~20% of seats on the premiere show are pre-BOOKED. Seat F12 on show 1
is left AVAILABLE — that is the Scenario A target.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.ids import new_booking_ref
from app.core.logging import configure_logging, logger
from app.db.session import get_session_factory, dispose_engine
from app.models.booking import Booking
from app.models.catalog import Movie, Screen, Show, Theatre
from app.models.hold import Hold
from app.models.seat import ShowSeat


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #
MOVIES = [
    {
        "title": "Spider-Man: Brand New Day",
        "synopsis": "A new chapter for the wall-crawler, opening night worldwide.",
        "duration_minutes": 128,
        "rating": "PG-13",
    },
    {
        "title": "The Last Cartographer",
        "synopsis": "An aging mapmaker races to finish the unfinishable atlas.",
        "duration_minutes": 112,
        "rating": "PG",
    },
    {
        "title": "Midnight Cassette",
        "synopsis": "A late-night radio host hears something she shouldn't.",
        "duration_minutes": 99,
        "rating": "PG-13",
    },
    {
        "title": "Salt of the Earth",
        "synopsis": "A documentary about three generations of Bengal's salt workers.",
        "duration_minutes": 87,
        "rating": "PG",
    },
]

THEATRES = [
    {"name": "Star Cineplex Chattogram", "city": "Chattogram"},
    {"name": "Blockbuster Cinema Dhaka", "city": "Dhaka"},
]

SCREENS = [
    # (theatre_name, screen_name, row_count, seats_per_row, premium_from_row)
    # Standardized: every screen is 8 rows (A-H) x 15 seats. Rows E-H are
    # PREMIUM, A-D are STANDARD.
    ("Star Cineplex Chattogram", "Screen 1", 8, 15, "E"),
    ("Star Cineplex Chattogram", "Screen 2", 8, 15, "E"),
    ("Blockbuster Cinema Dhaka", "Hall A",    8, 15, "E"),
]

# (theatre_name, screen_name, movie_title, days_from_now, hour_utc)
SHOWS = [
    # Premiere — show 1 — Spider-Man on Star Screen 1
    ("Star Cineplex Chattogram", "Screen 1", "Spider-Man: Brand New Day", 1,  0),
    ("Star Cineplex Chattogram", "Screen 1", "Spider-Man: Brand New Day", 1,  4),
    ("Star Cineplex Chattogram", "Screen 1", "Spider-Man: Brand New Day", 1,  8),
    ("Star Cineplex Chattogram", "Screen 1", "Spider-Man: Brand New Day", 2,  0),
    # Other titles on the same screen later that day
    ("Star Cineplex Chattogram", "Screen 1", "Midnight Cassette",          1, 12),
    ("Star Cineplex Chattogram", "Screen 1", "The Last Cartographer",      1, 16),
    # Screen 2
    ("Star Cineplex Chattogram", "Screen 2", "Spider-Man: Brand New Day",  1,  1),
    ("Star Cineplex Chattogram", "Screen 2", "Midnight Cassette",           1, 14),
    ("Star Cineplex Chattogram", "Screen 2", "Salt of the Earth",           1, 18),
    # Hall A — Dhaka
    ("Blockbuster Cinema Dhaka", "Hall A",    "Spider-Man: Brand New Day",  1,  2),
    ("Blockbuster Cinema Dhaka", "Hall A",    "The Last Cartographer",      1, 10),
    ("Blockbuster Cinema Dhaka", "Hall A",    "Salt of the Earth",          1, 14),
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def get_or_create_movie(session: AsyncSession, m: dict) -> Movie:
    stmt = select(Movie).where(Movie.title == m["title"])
    existing = (await session.execute(stmt)).scalars().first()
    if existing:
        return existing
    obj = Movie(**m)
    session.add(obj)
    await session.flush()
    return obj


async def get_or_create_theatre(session: AsyncSession, t: dict) -> Theatre:
    stmt = select(Theatre).where(Theatre.name == t["name"])
    existing = (await session.execute(stmt)).scalars().first()
    if existing:
        return existing
    obj = Theatre(**t)
    session.add(obj)
    await session.flush()
    return obj


async def get_or_create_screen(
    session: AsyncSession, theatre_id: int, name: str, rows: int, per_row: int, premium_from: str
) -> Screen:
    stmt = select(Screen).where(Screen.theatre_id == theatre_id, Screen.name == name)
    existing = (await session.execute(stmt)).scalars().first()
    if existing:
        return existing
    obj = Screen(
        theatre_id=theatre_id,
        name=name,
        row_count=rows,
        seats_per_row=per_row,
        premium_from_row=premium_from,
    )
    session.add(obj)
    await session.flush()
    return obj


async def get_or_create_show(
    session: AsyncSession,
    *,
    screen_id: int,
    movie_id: int,
    starts_at: datetime,
    base_price: Decimal,
    currency: str,
) -> Show:
    stmt = select(Show).where(Show.screen_id == screen_id, Show.starts_at == starts_at)
    existing = (await session.execute(stmt)).scalars().first()
    if existing:
        return existing
    obj = Show(
        screen_id=screen_id,
        movie_id=movie_id,
        starts_at=starts_at,
        base_price=base_price,
        currency=currency,
        status="SCHEDULED",
    )
    session.add(obj)
    await session.flush()
    return obj


def _row_label(idx: int) -> str:
    """0 → 'A', 1 → 'B', ... 25 → 'Z'."""
    return chr(ord("A") + idx)


def _price_for_row(row_label: str, screen: Screen, base: Decimal) -> Decimal:
    premium = base * Decimal("1.3")
    if row_label >= screen.premium_from_row:
        return premium.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def seed_show_seats(session: AsyncSession, show: Show, screen: Screen) -> int:
    """Materialise one row per (show, seat). Returns inserted count."""
    existing = await session.execute(
        select(ShowSeat.id).where(ShowSeat.show_id == show.id).limit(1)
    )
    if existing.first() is not None:
        return 0

    rows: list[dict] = []
    for r in range(screen.row_count):
        rl = _row_label(r)
        for n in range(1, screen.seats_per_row + 1):
            price = _price_for_row(rl, screen, show.base_price)
            rows.append(
                {
                    "show_id": show.id,
                    "row_label": rl,
                    "seat_number": n,
                    "seat_label": f"{rl}{n}",
                    "seat_class": "PREMIUM" if rl >= screen.premium_from_row else "STANDARD",
                    "price": price,
                    "status": "AVAILABLE",
                    "hold_id": None,
                    "booking_ref": None,
                    "reserved_until": None,
                }
            )
    if not rows:
        return 0
    # Use INSERT ... ON CONFLICT DO NOTHING to be safe across re-runs.
    stmt = pg_insert(ShowSeat).values(rows).on_conflict_do_nothing(
        index_elements=["show_id", "seat_label"]
    )
    await session.execute(stmt)
    return len(rows)


async def pre_book_seats_for_premiere(session: AsyncSession, show_id: int, percent: float = 0.20):
    """Pre-book ~20% of seats on the premiere show with synthetic bookings.

    Skip F12 — that's the Scenario A target. This is what makes the seat
    map visibly non-uniform and the demo believable.

    ★ Idempotent — sentinel-prefixed IDs are deterministic, so re-running
    this is a no-op on existing rows.

    ★ Splits seats across MULTIPLE holds (each ≤ 10 seats) because
    `holds.seat_count` is CHECKed BETWEEN 1 AND 10. Each hold gets its
    own booking. With 96 seats and 20% (19 seats), we end up with 2
    synthetic holds + 2 synthetic bookings.
    """
    # Skip if we already pre-booked this show.
    sentinel_prefix = f"seed_premiere_{show_id}_"
    already = await session.execute(
        select(Booking.booking_ref).where(Booking.hold_id.like(f"{sentinel_prefix}%"))
    )
    if already.first() is not None:
        return 0

    seats = (
        (
            await session.execute(
                select(ShowSeat)
                .where(ShowSeat.show_id == show_id, ShowSeat.status == "AVAILABLE")
                .order_by(ShowSeat.id)
            )
        )
        .scalars()
        .all()
    )
    if not seats:
        return 0

    skip_labels = {"F12"}
    eligible = [s for s in seats if s.seat_label not in skip_labels]
    n = max(1, int(len(eligible) * percent))
    target = eligible[:n]
    if not target:
        return 0

    now = datetime.now(tz=timezone.utc)
    booked_count = 0
    MAX_PER_HOLD = 10  # the CHECK constraint is 1..10

    # Split into chunks of MAX_PER_HOLD.
    for chunk_idx, chunk in enumerate(_chunks(target, MAX_PER_HOLD)):
        chunk_amount = sum((s.price for s in chunk), Decimal("0"))
        booking_ref = new_booking_ref()
        hold_id = f"{sentinel_prefix}{chunk_idx:02d}"

        # Hold row (FK target for bookings.hold_id).
        hold = Hold(
            id=hold_id,
            show_id=show_id,
            phone="+8801700000099",
            status="CONVERTED",
            seat_count=len(chunk),
            total_amount=chunk_amount,
            currency="BDT",
            expires_at=now,
        )
        session.add(hold)
        await session.flush()

        # Booking row.
        booking = Booking(
            booking_ref=booking_ref,
            hold_id=hold_id,
            show_id=show_id,
            phone="+8801700000099",
            status="CONFIRMED",
            total_amount=chunk_amount,
            currency="BDT",
            ticket_code=f"CS-{show_id}-SEED-{chunk_idx:02d}",
            confirmed_at=now,
        )
        session.add(booking)
        await session.flush()

        # Flip the seats in this chunk.
        for s in chunk:
            s.status = "BOOKED"
            s.booking_ref = booking.booking_ref
            s.hold_id = None
            s.reserved_until = None
        booked_count += len(chunk)

    return booked_count


def _chunks(items: list, size: int) -> Iterable[list]:
    """Yield `items` split into lists of at most `size`."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
async def main(argv: list[str]) -> int:
    configure_logging("INFO")
    settings = get_settings()
    logger.info(
        "seed starting",
        extra={
            "hold_ttl_seconds": settings.hold_ttl_seconds,
            "public_base_url": settings.public_base_url,
        },
    )

    factory = get_session_factory()
    summary = {}
    try:
        async with factory() as session:
            # Movies
            movies_by_title = {}
            for m in MOVIES:
                obj = await get_or_create_movie(session, m)
                movies_by_title[m["title"]] = obj

            # Theatres
            theatres_by_name = {}
            for t in THEATRES:
                obj = await get_or_create_theatre(session, t)
                theatres_by_name[t["name"]] = obj

            # Screens
            screens_by_label = {}
            for theatre_name, screen_name, rows, per_row, premium_from in SCREENS:
                t = theatres_by_name[theatre_name]
                s = await get_or_create_screen(session, t.id, screen_name, rows, per_row, premium_from)
                screens_by_label[(theatre_name, screen_name)] = s

            # Shows
            now = datetime.now(tz=timezone.utc).replace(microsecond=0)
            base_price = Decimal("350.00")
            shows = []
            for theatre_name, screen_name, movie_title, days_from_now, hour in SHOWS:
                screen = screens_by_label[(theatre_name, screen_name)]
                movie = movies_by_title[movie_title]
                starts_at = (now + timedelta(days=days_from_now)).replace(hour=hour)
                show = await get_or_create_show(
                    session,
                    screen_id=screen.id,
                    movie_id=movie.id,
                    starts_at=starts_at,
                    base_price=base_price,
                    currency="BDT",
                )
                shows.append(show)

            await session.commit()

            # Re-open session for the next pass (read-after-commit).
            async with factory() as session2:
                total_seats = 0
                for show in shows:
                    screen = screens_by_label[
                        (
                            next(k[0] for k, v in screens_by_label.items() if v.id == show.screen_id),
                            next(k[1] for k, v in screens_by_label.items() if v.id == show.screen_id),
                        )
                    ]
                    n = await seed_show_seats(session2, show, screen)
                    total_seats += n
                await session2.commit()

            # Pre-book the premiere show.
            async with factory() as session3:
                premiere = shows[0]
                pre_booked = await pre_book_seats_for_premiere(session3, premiere.id)
                await session3.commit()
                summary["pre_booked"] = pre_booked

            summary.update(
                {
                    "movies": len(movies_by_title),
                    "theatres": len(theatres_by_name),
                    "screens": len(screens_by_label),
                    "shows": len(shows),
                    "show_seats": total_seats,
                }
            )
            logger.info("seed done", extra=summary)
            print("seed done:", summary, file=sys.stdout)
    finally:
        await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
