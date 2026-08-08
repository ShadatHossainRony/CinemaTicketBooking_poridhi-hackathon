"""Common schemas: error envelope, health, readiness."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    field: str
    issue: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorBody


def envelope(
    code: str, message: str, request_id: str, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
    timestamp: str


class CheckStatus(BaseModel):
    status: str
    latency_ms: int | None = None
    error: str | None = None
    revision: str | None = None
    breaker: str | None = None


class ReadyChecks(BaseModel):
    database: CheckStatus
    migrations: CheckStatus
    gateway: CheckStatus


class ReadyResponse(BaseModel):
    status: str
    checks: ReadyChecks
    timestamp: str