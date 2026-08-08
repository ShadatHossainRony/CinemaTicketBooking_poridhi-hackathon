"""Catalogue models. Read-only after seed (REQ-10)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Movie(Base):
    __tablename__ = "movies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    synopsis: Mapped[str] = mapped_column(String, nullable=False, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    rating: Mapped[str] = mapped_column(String(10), nullable=False, default="NR")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    shows: Mapped[list["Show"]] = relationship(back_populates="movie")

    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
    )


class Theatre(Base):
    __tablename__ = "theatres"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    screens: Mapped[list["Screen"]] = relationship(
        back_populates="theatre", cascade="all, delete-orphan"
    )


class Screen(Base):
    __tablename__ = "screens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    theatre_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("theatres.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    seats_per_row: Mapped[int] = mapped_column(Integer, nullable=False)
    premium_from_row: Mapped[str] = mapped_column(String(1), nullable=False, default="E")

    theatre: Mapped[Theatre] = relationship(back_populates="screens")
    shows: Mapped[list["Show"]] = relationship(back_populates="screen")

    __table_args__ = (
        UniqueConstraint("theatre_id", "name", name="uq_screens_theatre_name"),
        CheckConstraint("row_count BETWEEN 1 AND 26", name="row_count_range"),
        CheckConstraint("seats_per_row BETWEEN 1 AND 30", name="seats_per_row_range"),
        CheckConstraint("row_count * seats_per_row <= 400", name="capacity_capped"),
    )


class Show(Base):
    __tablename__ = "shows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    movie_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("movies.id", ondelete="RESTRICT"), nullable=False
    )
    screen_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("screens.id", ondelete="RESTRICT"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    base_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BDT")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="SCHEDULED"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    movie: Mapped[Movie] = relationship(back_populates="shows")
    screen: Mapped[Screen] = relationship(back_populates="shows")

    __table_args__ = (
        UniqueConstraint("screen_id", "starts_at", name="uq_shows_screen_starts_at"),
        CheckConstraint("base_price > 0", name="base_price_positive"),
        CheckConstraint(
            "status IN ('SCHEDULED','STARTED','CANCELLED')", name="show_status_valid"
        ),
    )
