"""Pytest configuration and shared fixtures.

These tests require a running Postgres at the configured DATABASE_URL.
In CI we spin one up via docker compose. Locally you can run the same
container, or point at a temporary DB.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import dispose_engine, get_session_factory
from app.main import create_app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture()
async def app_instance():
    """Create the FastAPI app for the test (lifespan is NOT entered; we
    do not want background tasks running during tests)."""
    app = create_app()
    yield app


@pytest_asyncio.fixture()
async def client(app_instance) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _cleanup_engine():
    yield
    await dispose_engine()
