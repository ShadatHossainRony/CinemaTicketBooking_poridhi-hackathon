"""Sweeper task — release expired holds (REQ-06).

Lazy expiry in the claim predicate is the correctness backstop; this task
is for tidiness, observability, and accurate `seats_available` aggregates.
Idempotent — safe to run in every uvicorn worker of every replica.
"""
from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import logger
from app.db.session import get_session_factory
from app.repositories import hold as hold_repo
from app.repositories import seat as seat_repo


async def _tick() -> None:
    factory = get_session_factory()
    async with factory() as session:
        try:
            seats = await seat_repo.sweep_expired(session)
            holds = await hold_repo.expire_active_holds(session)
            await session.commit()
            if seats or holds:
                logger.info(
                    "sweep",
                    extra={"seats_released": seats, "holds_expired": holds},
                )
        except Exception:
            logger.exception("sweeper tick failed")
            await session.rollback()


async def run_sweeper() -> None:
    settings = get_settings()
    interval = settings.sweep_interval_seconds
    while True:
        try:
            await _tick()
        except Exception:
            logger.exception("sweeper loop error")
        await asyncio.sleep(interval)
