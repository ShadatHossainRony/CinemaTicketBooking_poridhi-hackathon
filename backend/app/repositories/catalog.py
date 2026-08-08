"""Catalogue read queries."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import Movie, Screen, Show, Theatre


async def list_movies(session: AsyncSession, q: str | None = None) -> Sequence[tuple[Movie, int]]:
    """Return (Movie, show_count)."""
    stmt = select(Movie, func.count(Show.id).label("show_count")).outerjoin(
        Show, Show.movie_id == Movie.id
    )
    if q:
        stmt = stmt.where(Movie.title.ilike(f"%{q}%"))
    stmt = stmt.group_by(Movie.id).order_by(Movie.id)
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def list_theatres(session: AsyncSession) -> Sequence[tuple[Theatre, list[Screen]]]:
    """Return (Theatre, [Screen]) — one query per screen group."""
    result = await session.execute(select(Theatre).order_by(Theatre.id))
    theatres = result.scalars().all()
    out: list[tuple[Theatre, list[Screen]]] = []
    for t in theatres:
        screens = (
            (
                await session.execute(
                    select(Screen).where(Screen.theatre_id == t.id).order_by(Screen.id)
                )
            )
            .scalars()
            .all()
        )
        out.append((t, list(screens)))
    return out


async def list_shows(
    session: AsyncSession,
    *,
    movie_id: int | None = None,
    theatre_id: int | None = None,
    date_: date | None = None,
) -> Sequence[Show]:
    stmt = select(Show).order_by(Show.starts_at)
    if movie_id is not None:
        stmt = stmt.where(Show.movie_id == movie_id)
    if theatre_id is not None:
        stmt = stmt.join(Screen, Screen.id == Show.screen_id).where(
            Screen.theatre_id == theatre_id
        )
    if date_ is not None:
        start = datetime.combine(date_, time.min, tzinfo=timezone.utc)
        end = datetime.combine(date_, time.max, tzinfo=timezone.utc)
        stmt = stmt.where(and_(Show.starts_at >= start, Show.starts_at <= end))
    result = await session.execute(stmt)
    return result.scalars().unique().all()


async def get_show(session: AsyncSession, show_id: int) -> Show | None:
    return await session.get(Show, show_id)
