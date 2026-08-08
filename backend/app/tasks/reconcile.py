"""Reconciliation task — re-apply unprocessed gateway events (REQ-44).

If the API crashed between ledger-insert and effect-application, the row
sits with processed_at IS NULL. We pick those up older than 60 s and
re-apply the transition.
"""
from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import logger
from app.db.session import get_session_factory
from app.repositories import booking as booking_repo
from app.schemas.gateway import GatewayCallback
from app.services.payment import apply_callback


async def _tick() -> None:
    factory = get_session_factory()
    async with factory() as session:
        try:
            events = await booking_repo.find_unprocessed_events(
                session, older_than_seconds=60
            )
        except Exception:
            logger.exception("reconcile: failed to list events")
            await session.rollback()
            return

    for ev in events:
        try:
            # Re-run the transition via the normal service path.
            payload = GatewayCallback(
                event_id=ev.event_id,
                payment_id=ev.gateway_payment_id,
                booking_ref=ev.booking_ref,
                status=ev.status,
                amount=ev.amount,
            )
            async with factory() as session:
                await apply_callback(session, payload=payload)
            logger.info(
                "reconcile: re-applied event",
                extra={"event_id": ev.event_id},
            )
        except Exception:
            logger.exception(
                "reconcile: failed to apply event", extra={"event_id": ev.event_id}
            )


async def run_reconcile() -> None:
    settings = get_settings()
    interval = settings.reconcile_interval_seconds
    while True:
        try:
            await _tick()
        except Exception:
            logger.exception("reconcile loop error")
        await asyncio.sleep(interval)
