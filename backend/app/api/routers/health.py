"""/health and /ready.

REQ-18: /health touches NOTHING. Not the DB, not the gateway.
INF-03: /ready gives a real dependency signal but stays 200 when only the
        gateway is down (REQ-44).
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_gateway
from app.core.config import get_settings
from app.core.errors import DependencyUnavailableError
from app.db.session import get_engine
from app.schemas.common import CheckStatus, HealthResponse, ReadyChecks, ReadyResponse, envelope
from app.services.gateway import GatewayClient

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
async def health() -> HealthResponse:
    """Liveness. Touches nothing. < 10 ms. (REQ-18)"""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        timestamp=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    )


@router.get("/ready")
async def ready(response: Response, gateway: GatewayClient = Depends(get_gateway)):
    """Readiness. DB down → 503. Gateway down → 200 degraded."""
    settings = get_settings()

    # --- Database ---
    db_status: CheckStatus
    db_ok = False
    db_err: str | None = None
    db_latency_ms: int | None = None
    try:
        engine = get_engine()
        start = time.perf_counter()
        async with asyncio.timeout(2.0):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        db_latency_ms = int((time.perf_counter() - start) * 1000)
        db_ok = True
        db_status = CheckStatus(status="ok", latency_ms=db_latency_ms)
    except Exception as e:
        db_err = f"{type(e).__name__}: {e}"
        db_status = CheckStatus(status="error", error=db_err)

    # --- Migrations ---
    migrations_status: CheckStatus
    mig_revision = "unknown"
    try:
        engine = get_engine()
        async with asyncio.timeout(2.0):
            async with engine.connect() as conn:
                row = (await conn.execute(text("SELECT version_num FROM alembic_version"))).first()
                if row is not None:
                    mig_revision = row[0]
        migrations_status = CheckStatus(status="ok", revision=mig_revision)
    except Exception as e:
        migrations_status = CheckStatus(status="error", error=f"{type(e).__name__}: {e}")

    # --- Gateway (advisory; never blocks) ---
    gw_ok, gw_err = await gateway.health()
    if gw_ok:
        gateway_status = CheckStatus(status="ok", latency_ms=None)
    else:
        gateway_status = CheckStatus(
            status="unavailable",
            error=gw_err or "unreachable",
            breaker=gateway.breaker.state,
        )

    overall_status = "ready"
    if not db_ok:
        # DB is mandatory; failure here means we really are not ready.
        response.status_code = 503
        return {
            "status": "not_ready",
            "checks": {
                "database": db_status.model_dump(),
                "migrations": migrations_status.model_dump(),
                "gateway": gateway_status.model_dump(),
            },
            "error": envelope(
                "DEPENDENCY_UNAVAILABLE",
                "One or more required dependencies are unavailable.",
                request_id="",
            )["error"],
            "timestamp": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        }

    if gateway_status.status != "ok":
        # Gateway down: degraded, but we are still serving traffic.
        overall_status = "degraded"

    return ReadyResponse(
        status=overall_status,
        checks=ReadyChecks(
            database=db_status,
            migrations=migrations_status,
            gateway=gateway_status,
        ),
        timestamp=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    )
