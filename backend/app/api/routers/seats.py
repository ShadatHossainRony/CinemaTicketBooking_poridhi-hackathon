"""/shows/{show_id}/seats — the live seat map (REQ-02, REQ-20)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.repositories import seat as seat_repo
from app.schemas.seat import (
    MovieMeta,
    ScreenMeta,
    SeatItem,
    SeatMapResponse,
    SeatSummary,
    ShowMeta,
    TheatreMeta,
)

router = APIRouter(tags=["seats"])


@router.get("/shows/{show_id}/seats", response_model=SeatMapResponse)
async def get_seat_map(show_id: int, session: AsyncSession = Depends(get_db)):
    settings = get_settings()
    result = await seat_repo.get_seat_map(session, show_id)
    if result is None:
        raise NotFoundError(f"Show {show_id} not found.")
    show, screen, rows = result

    # Producer-side metadata.
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    from app.models.catalog import Movie, Screen, Show, Theatre

    row = (
        await session.execute(
            select(Show, Movie, Screen, Theatre)
            .join(Movie, Movie.id == Show.movie_id)
            .join(Screen, Screen.id == Show.screen_id)
            .join(Theatre, Theatre.id == Screen.theatre_id)
            .where(Show.id == show_id)
        )
    ).first()

    seats: list[SeatItem] = []
    for r in rows:
        seats.append(
            SeatItem(
                seat=r["seat_label"],
                row=r["row_label"],
                number=int(r["seat_number"]),
                seat_class=r["seat_class"],
                price=str(r["price"]),
                status=r["effective_status"],
                held_until=r["held_until"],
            )
        )

    summary = await seat_repo.get_summary(session, show_id)
    return SeatMapResponse(
        show=ShowMeta(
            id=show.id,
            starts_at=show.starts_at,
            currency=show.currency,
            movie=MovieMeta(
                id=row[1].id,
                title=row[1].title,
                duration_minutes=row[1].duration_minutes,
            ),
            theatre=TheatreMeta(
                id=row[3].id,
                name=row[3].name,
                city=row[3].city,
            ),
            screen=ScreenMeta(
                id=row[2].id,
                name=row[2].name,
                row_count=row[2].row_count,
                seats_per_row=row[2].seats_per_row,
            ),
        ),
        hold_ttl_seconds=settings.hold_ttl_seconds,
        summary=SeatSummary(**summary),
        seats=seats,
    )
