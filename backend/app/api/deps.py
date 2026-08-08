"""FastAPI dependencies shared across routers."""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db  # re-export for cleaner router imports
from app.services.gateway import GatewayClient


def get_gateway(request: Request) -> GatewayClient:
    """Return the shared GatewayClient stored on app.state."""
    gw: GatewayClient | None = getattr(request.app.state, "gateway", None)
    if gw is None:
        raise RuntimeError("GatewayClient not initialised")
    return gw


__all__ = ["get_db", "get_gateway"]