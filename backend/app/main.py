"""FastAPI app factory.

Outer-first middleware order:
    RequestID → AccessLog → CORS → RateLimit → router

Exception handlers: AppError → envelope; RequestValidationError → 422
envelope; StarletteHTTPException → envelope; catch-all → 500 envelope.

`POST /payments/callback` bypasses ALL of this — its handler returns 200
for every input (REQ-13).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routers import bookings, catalog, health, holds, payments, seats
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import (
    configure_logging,
    get_request_id,
    logger,
    set_request_id,
)
from app.core.middleware import (
    AccessLogMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
)
from app.db.session import dispose_engine, get_engine
from app.schemas.common import envelope
from app.services.gateway import GatewayClient
from app.tasks.reconcile import run_reconcile
from app.tasks.sweeper import run_sweeper


def _cors_origins(settings):
    origins = settings.cors_origins_list
    # Default: allow our public origin in addition to whatever was set.
    public = settings.public_base_url
    if public and public not in origins:
        origins = list(origins) + [public]
    return origins


def _install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        body = envelope(exc.code, exc.message, get_request_id(), exc.details)
        return JSONResponse(status_code=exc.http_status, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        details = []
        for err in exc.errors():
            loc = [str(x) for x in err.get("loc", []) if x not in ("body", "query", "path")]
            field = ".".join(loc) or "(root)"
            issue = err.get("msg", "invalid")
            details.append({"field": field, "issue": issue})
        body = envelope(
            "VALIDATION_ERROR",
            "Request validation failed.",
            get_request_id(),
            details,
        )
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=body)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        # Map common codes into the envelope.
        code_map = {
            404: "NOT_FOUND",
            405: "BAD_REQUEST",
            429: "RATE_LIMITED",
        }
        code = code_map.get(exc.status_code, "BAD_REQUEST")
        body = envelope(code, str(exc.detail) or "Request failed.", get_request_id(), None)
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("unhandled exception", extra={"exc_type": type(exc).__name__})
        body = envelope(
            "INTERNAL_ERROR",
            "An unexpected error occurred.",
            get_request_id(),
            None,
        )
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=body)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Ensure engine exists.
        get_engine()
        # Start the gateway client.
        gateway = GatewayClient(settings)
        await gateway.start()
        app.state.gateway = gateway

        sweeper_task = asyncio.create_task(run_sweeper())
        reconcile_task = asyncio.create_task(run_reconcile())
        logger.info(
            "app started",
            extra={
                "environment": settings.environment,
                "hold_ttl_seconds": settings.hold_ttl_seconds,
            },
        )
        try:
            yield
        finally:
            for t in (sweeper_task, reconcile_task):
                t.cancel()
            await asyncio.gather(sweeper_task, reconcile_task, return_exceptions=True)
            await gateway.aclose()
            await dispose_engine()

    app = FastAPI(
        title="CinemaSeat API",
        version=settings.app_version,
        docs_url="/docs",
        openapi_url="/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
        redirect_slashes=False,
    )

    # Middleware: outer-first.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(settings),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "X-Mock-Mode", "X-Mock-Force"],
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)

    _install_exception_handlers(app)

    # Routers at the root — no prefix (`04-api-contract.md`).
    app.include_router(health.router)
    app.include_router(catalog.router)
    app.include_router(seats.router)
    app.include_router(holds.router)
    app.include_router(bookings.router)
    app.include_router(payments.router)

    return app


app = create_app()