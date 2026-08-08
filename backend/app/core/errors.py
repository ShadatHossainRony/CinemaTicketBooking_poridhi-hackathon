"""Domain exceptions and the error-code enum.

Services raise these; `main.py` registers a handler per family that maps each
to the standard envelope. Routers never build error responses by hand.
"""
from __future__ import annotations

from typing import Any

# ----- Error codes (machine-readable; the frontend switches on these) -----
class ErrorCode:
    VALIDATION_ERROR = "VALIDATION_ERROR"
    TOO_MANY_SEATS = "TOO_MANY_SEATS"
    BAD_REQUEST = "BAD_REQUEST"
    OTP_INVALID = "OTP_INVALID"
    NOT_FOUND = "NOT_FOUND"
    SEAT_UNAVAILABLE = "SEAT_UNAVAILABLE"
    HOLD_NOT_ACTIVE = "HOLD_NOT_ACTIVE"
    BOOKING_NOT_PAYABLE = "BOOKING_NOT_PAYABLE"
    OTP_NOT_VERIFIED = "OTP_NOT_VERIFIED"
    HOLD_EXPIRED = "HOLD_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    GATEWAY_UNAVAILABLE = "GATEWAY_UNAVAILABLE"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UNAUTHENTICATED = "UNAUTHENTICATED"


# ----- Base + subclasses --------------------------------------------------
class AppError(Exception):
    """Domain error. Subclasses set http_status and a stable `code`."""

    http_status: int = 500
    code: str = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status


class ValidationError(AppError):
    http_status = 422
    code = ErrorCode.VALIDATION_ERROR


class TooManySeatsError(AppError):
    http_status = 422
    code = ErrorCode.TOO_MANY_SEATS


class BadRequestError(AppError):
    http_status = 400
    code = ErrorCode.BAD_REQUEST


class OtpInvalidError(AppError):
    http_status = 400
    code = ErrorCode.OTP_INVALID


class NotFoundError(AppError):
    http_status = 404
    code = ErrorCode.NOT_FOUND


class SeatUnavailableError(AppError):
    """REQ-07 — the contention response."""

    http_status = 409
    code = ErrorCode.SEAT_UNAVAILABLE


class HoldNotActiveError(AppError):
    http_status = 409
    code = ErrorCode.HOLD_NOT_ACTIVE


class BookingNotPayableError(AppError):
    http_status = 409
    code = ErrorCode.BOOKING_NOT_PAYABLE


class OtpNotVerifiedError(AppError):
    http_status = 409
    code = ErrorCode.OTP_NOT_VERIFIED


class HoldExpiredError(AppError):
    http_status = 410
    code = ErrorCode.HOLD_EXPIRED


class RateLimitedError(AppError):
    http_status = 429
    code = ErrorCode.RATE_LIMITED


class GatewayUnavailableError(AppError):
    """REQ-44 — when the gateway is down, this is the only response code we use."""

    http_status = 503
    code = ErrorCode.GATEWAY_UNAVAILABLE


class DependencyUnavailableError(AppError):
    http_status = 503
    code = ErrorCode.DEPENDENCY_UNAVAILABLE


class UnauthenticatedError(AppError):
    http_status = 401
    code = ErrorCode.UNAUTHENTICATED


__all__ = [
    "ErrorCode",
    "AppError",
    "ValidationError",
    "TooManySeatsError",
    "BadRequestError",
    "OtpInvalidError",
    "NotFoundError",
    "SeatUnavailableError",
    "HoldNotActiveError",
    "BookingNotPayableError",
    "OtpNotVerifiedError",
    "HoldExpiredError",
    "RateLimitedError",
    "GatewayUnavailableError",
    "DependencyUnavailableError",
    "UnauthenticatedError",
]
