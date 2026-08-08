"""Seat map response schema (REQ-02, REQ-20)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


SeatStatus = Literal["AVAILABLE", "HELD", "BOOKED"]
SeatClass = Literal["STANDARD", "PREMIUM"]


class MovieMeta(BaseModel):
    id: int
    title: str
    duration_minutes: int


class TheatreMeta(BaseModel):
    id: int
    name: str
    city: str


class ScreenMeta(BaseModel):
    id: int
    name: str
    row_count: int
    seats_per_row: int


class ShowMeta(BaseModel):
    id: int
    starts_at: datetime
    currency: str
    movie: MovieMeta
    theatre: TheatreMeta
    screen: ScreenMeta


class SeatSummary(BaseModel):
    total: int
    available: int
    held: int
    booked: int


class SeatItem(BaseModel):
    seat: str
    row: str
    number: int
    seat_class: SeatClass
    price: str = Field(..., description="Decimal as string")
    status: SeatStatus
    held_until: datetime | None = None


class SeatMapResponse(BaseModel):
    show: ShowMeta
    hold_ttl_seconds: int
    summary: SeatSummary
    seats: list[SeatItem]