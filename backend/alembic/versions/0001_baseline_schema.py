"""baseline schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-08 10:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- movies ----------------------------------------------------------
    op.create_table(
        "movies",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("synopsis", sa.Text(), nullable=False, server_default=""),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("rating", sa.String(10), nullable=False, server_default="NR"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("duration_minutes > 0", name="ck_movies_duration_positive"),
    )

    # ---- theatres --------------------------------------------------------
    op.create_table(
        "theatres",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("city", sa.String(80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # ---- screens ---------------------------------------------------------
    op.create_table(
        "screens",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "theatre_id",
            sa.BigInteger(),
            sa.ForeignKey("theatres.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("seats_per_row", sa.Integer(), nullable=False),
        sa.Column(
            "premium_from_row",
            sa.String(1),
            nullable=False,
            server_default="E",
        ),
        sa.UniqueConstraint("theatre_id", "name", name="uq_screens_theatre_name"),
        sa.CheckConstraint("row_count BETWEEN 1 AND 26", name="ck_screens_row_count_range"),
        sa.CheckConstraint(
            "seats_per_row BETWEEN 1 AND 30", name="ck_screens_seats_per_row_range"
        ),
        sa.CheckConstraint(
            "row_count * seats_per_row <= 400", name="ck_screens_capacity_capped"
        ),
    )

    # ---- shows -----------------------------------------------------------
    op.create_table(
        "shows",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "movie_id",
            sa.BigInteger(),
            sa.ForeignKey("movies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "screen_id",
            sa.BigInteger(),
            sa.ForeignKey("screens.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("base_price", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default="BDT",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="SCHEDULED",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("screen_id", "starts_at", name="uq_shows_screen_starts_at"),
        sa.CheckConstraint("base_price > 0", name="ck_shows_base_price_positive"),
        sa.CheckConstraint(
            "status IN ('SCHEDULED','STARTED','CANCELLED')",
            name="ck_shows_status_valid",
        ),
    )
    op.create_index("ix_shows_movie_id", "shows", ["movie_id"])
    op.create_index("ix_shows_screen_id", "shows", ["screen_id"])
    op.create_index("ix_shows_starts_at", "shows", ["starts_at"])

    # ---- holds -----------------------------------------------------------
    op.create_table(
        "holds",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column(
            "show_id",
            sa.BigInteger(),
            sa.ForeignKey("shows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("seat_count", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default="BDT",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','EXPIRED','CONVERTED','RELEASED')",
            name="ck_holds_status_valid",
        ),
        sa.CheckConstraint(
            "seat_count BETWEEN 1 AND 10", name="ck_holds_seat_count_range"
        ),
    )

    # ---- show_seats -----------------------------------------------------
    op.create_table(
        "show_seats",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "show_id",
            sa.BigInteger(),
            sa.ForeignKey("shows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_label", sa.String(2), nullable=False),
        sa.Column("seat_number", sa.Integer(), nullable=False),
        sa.Column("seat_label", sa.String(5), nullable=False),
        sa.Column(
            "seat_class",
            sa.String(20),
            nullable=False,
            server_default="STANDARD",
        ),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="AVAILABLE",
        ),
        sa.Column(
            "hold_id",
            sa.String(30),
            sa.ForeignKey("holds.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "booking_ref",
            sa.String(30),
            nullable=True,  # FK added after bookings table
        ),
        sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "show_id", "row_label", "seat_number", name="uq_show_seats_show_row_num"
        ),
        sa.UniqueConstraint("show_id", "seat_label", name="uq_show_seats_show_label"),
        sa.CheckConstraint("seat_number > 0", name="ck_show_seats_seat_number_positive"),
        sa.CheckConstraint("price > 0", name="ck_show_seats_price_positive"),
        sa.CheckConstraint(
            "seat_class IN ('STANDARD','PREMIUM')",
            name="ck_show_seats_seat_class_valid",
        ),
        sa.CheckConstraint(
            "status IN ('AVAILABLE','HELD','PAYMENT_PENDING','BOOKED')",
            name="ck_show_seats_status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'AVAILABLE' AND reserved_until IS NULL AND hold_id IS NULL) OR "
            "(status IN ('HELD','PAYMENT_PENDING') AND reserved_until IS NOT NULL) OR "
            "(status = 'BOOKED' AND booking_ref IS NOT NULL)",
            name="ck_show_seats_status_expiry_consistent",
        ),
    )
    op.create_index("ix_show_seats_show_status", "show_seats", ["show_id", "status"])
    op.execute(
        "CREATE INDEX ix_show_seats_sweep "
        "ON show_seats (reserved_until) "
        "WHERE status IN ('HELD','PAYMENT_PENDING')"
    )

    # ---- bookings --------------------------------------------------------
    op.create_table(
        "bookings",
        sa.Column("booking_ref", sa.String(30), primary_key=True),
        sa.Column(
            "hold_id",
            sa.String(30),
            sa.ForeignKey("holds.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "show_id",
            sa.BigInteger(),
            sa.ForeignKey("shows.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="PENDING_OTP",
        ),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default="BDT",
        ),
        sa.Column("otp_ref", sa.String(64), nullable=True),
        sa.Column("otp_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("otp_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("otp_last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ticket_code", sa.String(40), nullable=True, unique=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_OTP','OTP_VERIFIED','PAYMENT_PENDING',"
            "'CONFIRMED','FAILED','EXPIRED','REFUNDED')",
            name="ck_bookings_status_valid",
        ),
    )
    # Now wire show_seats.booking_ref → bookings.booking_ref (SET NULL).
    op.create_foreign_key(
        "fk_show_seats_booking_ref_bookings",
        "show_seats",
        "bookings",
        ["booking_ref"],
        ["booking_ref"],
        ondelete="SET NULL",
    )

    # ---- payments --------------------------------------------------------
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "booking_ref",
            sa.String(30),
            sa.ForeignKey("bookings.booking_ref", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gateway_payment_id", sa.String(64), nullable=True, unique=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default="BDT",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("failure_reason", sa.String(200), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        sa.CheckConstraint(
            "status IN ('PENDING','SUCCEEDED','FAILED','REFUNDED')",
            name="ck_payments_status_valid",
        ),
    )
    op.create_index("ix_payments_booking_ref", "payments", ["booking_ref"])
    # REQ-14: at most one live payment per booking.
    op.execute(
        "CREATE UNIQUE INDEX uq_payments_live_per_booking "
        "ON payments (booking_ref) "
        "WHERE status IN ('PENDING','SUCCEEDED')"
    )

    # ---- gateway_events --------------------------------------------------
    op.create_table(
        "gateway_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("booking_ref", sa.String(30), nullable=False),  # no FK (REQ-13)
        sa.Column("gateway_payment_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("process_error", sa.String(300), nullable=True),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED','FAILED','REFUNDED')",
            name="ck_gateway_events_status_valid",
        ),
    )
    op.execute(
        "CREATE INDEX ix_gateway_events_unprocessed "
        "ON gateway_events (received_at) "
        "WHERE processed_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_gateway_events_unprocessed", table_name="gateway_events")
    op.drop_table("gateway_events")
    op.drop_index("uq_payments_live_per_booking", table_name="payments")
    op.drop_index("ix_payments_booking_ref", table_name="payments")
    op.drop_table("payments")
    op.drop_constraint("fk_show_seats_booking_ref_bookings", "show_seats", type_="foreignkey")
    op.drop_table("bookings")
    op.drop_index("ix_show_seats_sweep", table_name="show_seats")
    op.drop_index("ix_show_seats_show_status", table_name="show_seats")
    op.drop_table("show_seats")
    op.drop_table("holds")
    op.drop_index("ix_shows_starts_at", table_name="shows")
    op.drop_index("ix_shows_screen_id", table_name="shows")
    op.drop_index("ix_shows_movie_id", table_name="shows")
    op.drop_table("shows")
    op.drop_table("screens")
    op.drop_table("theatres")
    op.drop_table("movies")