"""Catalogue read schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---- Movies ---------------------------------------------------------------
class MovieItem(BaseModel):
    id: int
    title: str
    synopsis: str = ""
    duration_minutes: int
    rating: str
    show_count: int = 0


class MovieList(BaseModel):
    items: list[MovieItem]


# ---- Theatres -------------------------------------------------------------
class ScreenItem(BaseModel):
    id: int
    name: str
    row_count: int
    seats_per_row: int


class TheatreItem(BaseModel):
    id: int
    name: str
    city: str
    screens: list[ScreenItem]


class TheatreList(BaseModel):
    items: list[TheatreItem]


# ---- Shows ----------------------------------------------------------------
class MovieStub(BaseModel):
    id: int
    title: str


class TheatreStub(BaseModel):
    id: int
    name: str


class ScreenStub(BaseModel):
    id: int
    name: str


class ShowItem(BaseModel):
    id: int
    starts_at: datetime
    currency: str
    base_price: str = Field(..., description="Decimal as string")
    movie: MovieStub
    theatre: TheatreStub
    screen: ScreenStub
    seats_available: int
    seats_total: int


class ShowList(BaseModel):
    items: list[ShowItem]
