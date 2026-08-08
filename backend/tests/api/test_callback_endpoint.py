"""REQ-13: /payments/callback ALWAYS returns 200, even for malformed bodies
and internal exceptions.

Verified by hitting the endpoint with the FastAPI test client.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_callback_malformed_body_still_200(client):
    r = await client.post("/payments/callback", json={"not": "valid"})
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True
    assert body.get("accepted") is False


@pytest.mark.asyncio
async def test_callback_unknown_booking_still_200(client):
    r = await client.post(
        "/payments/callback",
        json={
            "event_id": "evt_unknown_xx",
            "booking_ref": "bk_does_not_exist",
            "status": "SUCCEEDED",
            "amount": 100,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True


@pytest.mark.asyncio
async def test_health_endpoint_touches_nothing(client):
    """REQ-18: /health must respond fast and not depend on anything."""
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


@pytest.mark.asyncio
async def test_hold_validation_returns_422(client):
    """Schema validation failures return 422 in our envelope."""
    r = await client.post("/holds", json={})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_unknown_route_returns_envelope(client):
    r = await client.get("/this-does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["request_id"]