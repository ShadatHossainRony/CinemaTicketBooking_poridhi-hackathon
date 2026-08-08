"""/bookings, /bookings/{ref}, OTP, /pay."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_gateway
from app.core.config import get_settings
from app.schemas.booking import (
    BookingCreate,
    OtpVerifyRequest,
    OtpVerifyResponse,
    PayResponse,
    OtpSendResponse,
)
from app.services import booking as booking_service
from app.services import payment as payment_service
from app.services.gateway import GatewayClient

router = APIRouter(tags=["bookings"])


@router.post("/bookings", status_code=status.HTTP_201_CREATED)
async def create_booking(payload: BookingCreate, session: AsyncSession = Depends(get_db)):
    return await booking_service.create_booking_from_hold(session, hold_id=payload.hold_id)


@router.get("/bookings/{booking_ref}")
async def get_booking(booking_ref: str, session: AsyncSession = Depends(get_db)):
    return await booking_service.get_booking_payload(session, booking_ref=booking_ref)


@router.post("/bookings/{booking_ref}/otp", response_model=OtpSendResponse)
async def send_otp(
    booking_ref: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    gateway: GatewayClient = Depends(get_gateway),
    x_mock_mode: str | None = Header(default=None, alias="X-Mock-Mode"),
    x_mock_force: str | None = Header(default=None, alias="X-Mock-Force"),
):
    passthrough: dict[str, str] = {}
    if x_mock_mode:
        passthrough["X-Mock-Mode"] = x_mock_mode
    if x_mock_force:
        passthrough["X-Mock-Force"] = x_mock_force
    return await booking_service.send_otp(
        session,
        booking_ref=booking_ref,
        gateway=gateway,
        passthrough_headers=passthrough,
    )


@router.post("/bookings/{booking_ref}/otp/verify", response_model=OtpVerifyResponse)
async def verify_otp(
    booking_ref: str,
    payload: OtpVerifyRequest,
    session: AsyncSession = Depends(get_db),
    gateway: GatewayClient = Depends(get_gateway),
):
    settings = get_settings()
    return await booking_service.verify_otp(
        session,
        booking_ref=booking_ref,
        code=payload.code,
        gateway=gateway,
        otp_required=settings.otp_required,
    )


@router.post("/bookings/{booking_ref}/pay", response_model=PayResponse)
async def pay_booking(
    booking_ref: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    gateway: GatewayClient = Depends(get_gateway),
    x_mock_mode: str | None = Header(default=None, alias="X-Mock-Mode"),
    x_mock_force: str | None = Header(default=None, alias="X-Mock-Force"),
):
    passthrough: dict[str, str] = {}
    if x_mock_mode:
        passthrough["X-Mock-Mode"] = x_mock_mode
    if x_mock_force:
        passthrough["X-Mock-Force"] = x_mock_force
    return await payment_service.start_payment(
        session,
        booking_ref=booking_ref,
        gateway=gateway,
        passthrough_headers=passthrough,
    )
