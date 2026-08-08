"""/holds and /holds/{id} (REQ-03, REQ-06, REQ-20)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.hold import HoldCreate
from app.services import hold as hold_service

router = APIRouter(tags=["holds"])


@router.post("/holds", status_code=status.HTTP_201_CREATED)
async def create_hold(payload: HoldCreate, session: AsyncSession = Depends(get_db)):
    return await hold_service.create_hold_for_seats(
        session,
        show_id=payload.show_id,
        seat_labels=payload.seats,
        phone=payload.phone,
    )


@router.get("/holds/{hold_id}")
async def get_hold(hold_id: str, session: AsyncSession = Depends(get_db)):
    return await hold_service.get_hold_payload(session, hold_id=hold_id)
