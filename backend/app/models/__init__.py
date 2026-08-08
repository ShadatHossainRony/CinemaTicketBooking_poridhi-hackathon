"""Re-export every model so Alembic autogenerate can see them.

A model not imported here is invisible to autogenerate and produces an
empty migration — failure mode #1 from `03-data-model.md` §5.
"""
from app.models.catalog import Movie, Theatre, Screen, Show  # noqa: F401
from app.models.seat import ShowSeat  # noqa: F401
from app.models.hold import Hold  # noqa: F401
from app.models.booking import Booking, Payment, GatewayEvent  # noqa: F401

__all__ = [
    "Movie",
    "Theatre",
    "Screen",
    "Show",
    "ShowSeat",
    "Hold",
    "Booking",
    "Payment",
    "GatewayEvent",
]
