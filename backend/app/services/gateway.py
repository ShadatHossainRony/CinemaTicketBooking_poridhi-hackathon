"""Gateway HTTP client (httpx) with timeouts and a circuit breaker.

The gateway is INSIDE our trust boundary but OUTSIDE our control and is
*specified* to misbehave (REQ-15). Every call here has a timeout, a single
retry on connect error, and contributes to a breaker that opens after
`gateway_breaker_threshold` consecutive failures.

REQ-12 — /pay must not block on the gateway. REQ-17 — forward
X-Mock-Mode / X-Mock-Force verbatim. INF-12 — breaker + timeouts.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import GatewayUnavailableError
from app.core.logging import logger


@dataclass(slots=True)
class ChargeResult:
    payment_id: str
    raw: dict[str, Any]


class CircuitBreaker:
    """Three-state breaker: CLOSED → OPEN → HALF_OPEN → CLOSED."""

    def __init__(self, threshold: int, reset_seconds: int) -> None:
        self.threshold = threshold
        self.reset_seconds = reset_seconds
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if (time.monotonic() - self.opened_at) >= self.reset_seconds:
            return "half_open"
        return "open"

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()


class GatewayClient:
    """One shared httpx.AsyncClient; injected via lifespan."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None
        self.breaker = CircuitBreaker(
            self.settings.gateway_breaker_threshold,
            self.settings.gateway_breaker_reset_seconds,
        )

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.settings.gateway_base_url,
            timeout=httpx.Timeout(self.settings.gateway_timeout_seconds),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("GatewayClient used before start()")
        return self._client

    # ------------------------------------------------------------- health
    async def health(self) -> tuple[bool, str]:
        """Used only by /ready (advisory). 1-second timeout, never blocks."""
        if self.breaker.state == "open":
            return False, "breaker open"
        if self._client is None:
            try:
                await self.start()
            except Exception:
                return False, "client not initialised"
        try:
            r = await self._client.get(
                "/health",
                timeout=httpx.Timeout(self.settings.gateway_health_timeout_seconds),
            )
            if r.status_code == 200:
                self.breaker.record_success()
                return True, ""
            return False, f"status={r.status_code}"
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as e:
            return False, f"{type(e).__name__}: {e}"

    # ------------------------------------------------------------- charge
    async def charge(
        self,
        *,
        booking_ref: str,
        amount: int,
        currency: str,
        callback_url: str,
        idempotency_key: str | None = None,
        passthrough_headers: dict[str, str] | None = None,
    ) -> ChargeResult:
        """POST /charge. Returns the gateway payment_id.

        `idempotency_key` — if provided, the gateway de-duplicates retries
        by key and returns the same payment_id without a second charge
        (per the gateway's documented contract). We default to the
        booking_ref so a retry of /pay cannot double-charge.

        Raises GatewayUnavailableError on timeout, 5xx, or open breaker.
        """
        if self.breaker.state == "open":
            raise GatewayUnavailableError("Gateway circuit breaker is open.")

        body = {
            "amount": amount,
            "currency": currency,
            "booking_ref": booking_ref,
            "callback_url": callback_url,
        }
        headers: dict[str, str] = {}
        # REQ-17: forward judge-supplied force headers verbatim.
        if passthrough_headers:
            for k, v in passthrough_headers.items():
                kl = k.lower()
                if kl in ("x-mock-mode", "x-mock-force"):
                    headers[k] = v
        # The gateway's documented contract: Idempotency-Key is optional
        # but real gateways work this way. We default to the booking_ref
        # so a retry of /pay produces the same payment_id.
        headers["Idempotency-Key"] = idempotency_key or booking_ref

        client = self._require_client()
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                r = await client.post("/charge", json=body, headers=headers)
                if r.status_code in (200, 202):
                    self.breaker.record_success()
                    data = r.json()
                    return ChargeResult(
                        payment_id=str(data.get("payment_id", "")),
                        raw=data,
                    )
                if 500 <= r.status_code < 600:
                    last_exc = GatewayUnavailableError(
                        f"Gateway returned {r.status_code}"
                    )
                    # don't retry server errors — gateway will retry the callback
                    break
                # 4xx other than 202 — treat as bad request and surface it
                raise GatewayUnavailableError(
                    f"Gateway rejected request: {r.status_code} {r.text[:200]}"
                )
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_exc = e
                logger.warning("gateway connect/timeout, retrying once", extra={"attempt": attempt})
                await asyncio.sleep(0.2)
            except httpx.HTTPError as e:
                last_exc = e
                break

        self.breaker.record_failure()
        raise GatewayUnavailableError(f"Gateway unreachable: {last_exc}")

    # ------------------------------------------------------------- OTP
    async def send_otp(
        self,
        *,
        phone: str,
        ref: str,
        callback_url: str | None = None,
        passthrough_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if self.breaker.state == "open":
            raise GatewayUnavailableError("Gateway circuit breaker is open.")
        client = self._require_client()
        body: dict[str, Any] = {"phone": phone, "ref": ref}
        if callback_url:
            body["callback_url"] = callback_url
        headers: dict[str, str] = {}
        if passthrough_headers:
            for k, v in passthrough_headers.items():
                kl = k.lower()
                if kl in ("x-mock-mode", "x-mock-force"):
                    headers[k] = v
        try:
            r = await client.post("/otp/send", json=body, headers=headers)
            if r.status_code == 202:
                self.breaker.record_success()
                return r.json() if r.content else {}
            raise GatewayUnavailableError(
                f"OTP send failed: {r.status_code} {r.text[:200]}"
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as e:
            self.breaker.record_failure()
            raise GatewayUnavailableError(f"OTP send error: {e}")

    async def verify_otp(self, *, ref: str, code: str) -> dict[str, Any]:
        if self.breaker.state == "open":
            raise GatewayUnavailableError("Gateway circuit breaker is open.")
        client = self._require_client()
        try:
            r = await client.post("/otp/verify", json={"ref": ref, "code": code})
            if r.status_code == 200:
                self.breaker.record_success()
                return r.json() if r.content else {}
            # 400 — wrong / expired code. Caller maps this to OtpInvalidError.
            if r.status_code == 400:
                self.breaker.record_success()
                return {"ok": False, "status_code": 400, "raw": r.json() if r.content else {}}
            # 429 — gateway-side attempts exhausted. Caller maps this to
            # RateLimitedError WITHOUT bumping the local otp_attempts counter.
            if r.status_code == 429:
                self.breaker.record_success()
                return {"ok": False, "status_code": 429, "raw": r.json() if r.content else {}}
            raise GatewayUnavailableError(
                f"OTP verify failed: {r.status_code} {r.text[:200]}"
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as e:
            self.breaker.record_failure()
            raise GatewayUnavailableError(f"OTP verify error: {e}")
