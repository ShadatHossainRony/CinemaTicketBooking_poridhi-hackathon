"""Catalogue routers: /movies, /theatres, /shows."""
from __future__ import annotations

from datetime import date as _date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.repositories import catalog as catalog_repo
from app.schemas.catalog import (
    MovieItem,
    MovieList,
    MovieStub,
    ScreenItem,
    ScreenStub,
    ShowItem,
    ShowList,
    TheatreItem,
    TheatreList,
    TheatreStub,
)
from app.repositories import seat as seat_repo

router = APIRouter(tags=["catalog"])


@router.get("/movies", response_model=MovieList)
async def list_movies(
    q: str | None = Query(default=None, max_length=60),
    session: AsyncSession = Depends(get_db),
):
    rows = await catalog_repo.list_movies(session, q=q)
    return MovieList(
        items=[
            MovieItem(
                id=m.id,
                title=m.title,
                synopsis=m.synopsis,
                duration_minutes=m.duration_minutes,
                rating=m.rating,
                show_count=count,
            )
            for m, count in rows
        ]
    )


@router.get("/theatres", response_model=TheatreList)
async def list_theatres(session: AsyncSession = Depends(get_db)):
    rows = await catalog_repo.list_theatres(session)
    return TheatreList(
        items=[
            TheatreItem(
                id=t.id,
                name=t.name,
                city=t.city,
                screens=[
                    ScreenItem(
                        id=s.id,
                        name=s.name,
                        row_count=s.row_count,
                        seats_per_row=s.seats_per_row,
                    )
                    for s in screens
                ],
            )
            for t, screens in rows
        ]
    )


@router.get("/shows", response_model=ShowList)
async def list_shows(
    movie_id: int | None = Query(default=None, ge=1),
    theatre_id: int | None = Query(default=None, ge=1),
    date: _date | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    from app.models.catalog import Movie, Screen, Show, Theatre

    stmt = (
        select(Show)
        .options(joinedload(Show.movie), joinedload(Show.screen).joinedload(Screen.theatre))
        .order_by(Show.starts_at)
    )
    if movie_id is not None:
        stmt = stmt.where(Show.movie_id == movie_id)
    if theatre_id is not None:
        stmt = stmt.join(Screen, Screen.id == Show.screen_id).where(
            Screen.theatre_id == theatre_id
        )
    if date is not None:
        from datetime import datetime, time, timezone

        start = datetime.combine(date, time.min, tzinfo=timezone.utc)
        end = datetime.combine(date, time.max, tzinfo=timezone.utc)
        stmt = stmt.where(Show.starts_at >= start, Show.starts_at <= end)

    shows = (await session.execute(stmt)).unique().scalars().all()
    items: list[ShowItem] = []
    for show in shows:
        total = await seat_repo.seats_total(session, show.id)
        available = (await seat_repo.get_summary(session, show.id))["available"]
        items.append(
            ShowItem(
                id=show.id,
                starts_at=show.starts_at,
                currency=show.currency,
                base_price=str(show.base_price),
                movie=MovieStub(id=show.movie.id, title=show.movie.title),
                theatre=TheatreStub(id=show.screen.theatre.id, name=show.screen.theatre.name),
                screen=ScreenStub(id=show.screen.id, name=show.screen.name),
                seats_available=available,
                seats_total=total,
            )
        )
    return ShowList(items=items)
