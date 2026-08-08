# CinemaSeat

> *Movie ticket booking that stays calm when Brand New Day drops, and never sells the same seat twice.*

A FastAPI + PostgreSQL backend that holds a seat atomically, integrates with the provided
misbehaving payment gateway, and recovers from dropped callbacks. Includes a single-page
React demo and a clean-clone `docker compose up` story.

## At a glance

- **Backend** — FastAPI 0.110, SQLAlchemy 2.0, Alembic, asyncpg/psycopg, Pydantic v2, httpx
- **Database** — PostgreSQL 16 (containerised)
- **Frontend** — React 18 + Vite + Tailwind, single-page, no router
- **Gateway** — `asifmahmoud414/mock-gateway:latest` (REQUIRED — do not mock)
- **Reverse proxy** — Nginx (host) acting as TLS terminator + load balancer
- **CI/CD** — GitHub Actions: CI on PR/push, CD on `main` only

## Quick start

```bash
git clone <repo>
cd <repo>
docker compose up -d --build
curl -fsS localhost:8000/health
# → {"status":"ok","service":"cinemaseat-api",...}
```

That is the whole local story. The `migrate` container runs `alembic upgrade head` and
seeds the catalogue on first boot; the `api` containers wait for it to finish.

## The two endpoints judges will point tests at

### `GET /shows/{show_id}/seats` — fetch the seat map

```bash
curl -s https://poridhi-hackathon.shadathossainrony.dev/shows/1/seats
```

```json
{
  "show": {
    "id": 1,
    "starts_at": "2026-08-09T00:00:00Z",
    "currency": "BDT",
    "movie":   { "id": 1, "title": "Spider-Man: Brand New Day", "duration_minutes": 128 },
    "theatre": { "id": 1, "name": "Star Cineplex Chattogram", "city": "Chattogram" },
    "screen":  { "id": 1, "name": "Screen 1", "row_count": 8, "seats_per_row": 12 }
  },
  "hold_ttl_seconds": 120,
  "summary": { "total": 96, "available": 71, "held": 5, "booked": 20 },
  "seats": [
    { "seat": "A1", "row": "A", "number": 1, "seat_class": "STANDARD", "price": "350.00", "status": "AVAILABLE" },
    { "seat": "F12", "row": "F", "number": 12, "seat_class": "PREMIUM", "price": "450.00", "status": "HELD",
      "held_until": "2026-08-08T12:05:11Z" }
  ]
}
```

### `POST /holds` — hold one or more seats

```bash
curl -s -X POST https://poridhi-hackathon.shadathossainrony.dev/holds \
  -H 'Content-Type: application/json' \
  -d '{"show_id": 1, "seats": ["F12"], "phone": "+8801700000001"}'
```

```json
{
  "hold_id": "hld_01K2Q7M4V8ZC3N6RJH0YB5TXWD",
  "show_id": 1,
  "status": "ACTIVE",
  "seats": [ { "seat": "F12", "seat_class": "PREMIUM", "price": "450.00" } ],
  "total_amount": "450.00",
  "currency": "BDT",
  "expires_at": "2026-08-08T12:05:11Z",
  "expires_in_seconds": 120
}
```

Conflicts return `409 SEAT_UNAVAILABLE` with the standard error envelope. **All-or-nothing**
multi-seat — if any seat is taken, the whole request is rejected and nothing is held.

## How oversell is prevented

A single SQL statement in `backend/app/repositories/seat.py::claim_seats`:

```sql
UPDATE show_seats
   SET status = 'HELD', hold_id = :hold_id, reserved_until = :expires_at
 WHERE show_id = :show_id
   AND seat_label = ANY(:seat_labels)
   AND (status = 'AVAILABLE'
        OR (status IN ('HELD','PAYMENT_PENDING') AND reserved_until <= now()))
RETURNING id, seat_label, seat_class, price;
```

There is no read-then-write. The predicate and the write are one statement. 100 concurrent
requests for the same seat produce exactly one success and 99 clean rejections.

## How duplicate callbacks are handled

The gateway delivers the same `event_id` twice 8% of the time. We make that harmless:

```sql
INSERT INTO gateway_events (event_id, ...) ON CONFLICT (event_id) DO NOTHING;
```

This is the one and only duplicate check. The database decides, not application logic. A second
delivery receives `200 {received: true, duplicate: true}` and changes nothing.

Every callback also carries an `X-Signature` header — HMAC-SHA256 of the raw body with
`GATEWAY_SECRET`. We verify it before parsing JSON (over the exact bytes the gateway sent,
not a re-serialised object). On mismatch we still answer `200` (REQ-13 — a non-2xx triggers
the gateway's exponential-backoff retry up to 8 times), but log loudly.

## Gateway quirks we handle

`payment_gateway.md` calls these "documented misbehaviour", not bugs:

- **2–15 s callback delay** — `/pay` returns `PENDING` immediately, client polls.
- **8% duplicate callback rate** — `INSERT … ON CONFLICT (event_id) DO NOTHING`.
- **2% `/charge` 5xx** — circuit breaker (5 fails / 30 s) + `Idempotency-Key` so retries
  return the same `payment_id` instead of double-charging.
- **10% OTP never delivered** — `X-Mock-Mode: deterministic` sets the code to `123456`
  while building. Real mode can drop it; that's the spec.
- **Callbacks arrive before /pay responds** — we write the `PENDING` payment row BEFORE
  calling `/charge`, so the early callback has a row to find.
- **Retries on non-2xx, up to 8×** — the callback handler ALWAYS returns `200`.

## How the gateway going down is handled

- `GET /health` **still 200** when the gateway is down (REQ-18).
- `GET /ready` returns `200 degraded` (not 503) when only the gateway is unavailable.
- A circuit breaker on the gateway client opens after 5 failures for 30 s, so `/pay`
  returns `503 GATEWAY_UNAVAILABLE` instead of burning 5 s on each call.
- Browsing, seat maps, and holds keep working — they never touch the gateway.

## Environment variables

See `.env.example`. Every variable has a safe default in `docker-compose.yml`, so a clean
clone boots without an `.env` (REQ-21). The notable ones:

| Variable | Default | Why |
| --- | --- | --- |
| `HOLD_TTL_SECONDS` | `120` | REQ-19. Judges may override and watch a hold expire. |
| `PAYMENT_WINDOW_SECONDS` | `90` | Extension when `/pay` is called. Must exceed the gateway's 15 s max callback delay. |
| `GATEWAY_BASE_URL` | `http://gateway:9000` | Internal service name. |
| `GATEWAY_CALLBACK_URL` | `http://api:8000/payments/callback` | Internal — never the public hostname. |
| `GATEWAY_SECRET` | `z2p-2026-secret` | Shared HMAC key with the gateway. We verify `X-Signature` on every callback. |
| `OTP_REQUIRED` | `true` | Escape hatch if the gateway's OTP code is unobtainable. |

## Demo phone numbers

No login, no accounts. Use these seeded numbers to test:

```
+8801700000001
+8801700000002
+8801700000003
+8801700000004
+8801700000005
```

## Repo layout

```
.
├── backend/                FastAPI app, one service
├── frontend/               Vite + React single-page UI
├── docker-compose.yml      db · gateway · migrate · api · api2
├── nginx/cinemaseat.conf   reverse proxy + LB (host-installed)
├── tests/                  smoke.sh + load test scripts
├── docs/                   architecture.md · api.md · deployment.md · proof.md
└── .github/workflows/      ci.yml · cd.yml
```

## What is built vs deferred

Built (full path):

- All 12 endpoints in `04-api-contract.md`.
- The atomic claim, idempotent callback, hold expiry, OTP flow, payment window.
- Frontend: browse → pick seats → hold → OTP → pay → confirm.
- CI + CD; smoke.sh; Scenario A + B.

Deferred (honest list — see `agent/01-problem-analysis.md` §6):

- AWS deployment (we deploy to the GCP VM).
- Prometheus / Grafana / OpenTelemetry tracing.
- Seat release endpoint (`DELETE /holds/{id}`); expiry is the documented release path.
- Cinema admin portal (none required).

## Acknowledgements

- Gateway: `asifmahmoud414/mock-gateway` — required by the problem statement.
- Python: FastAPI, SQLAlchemy, Alembic, Pydantic, httpx.
- Frontend: React, Vite, TailwindCSS.
- This README was assembled with help from an AI coding assistant.
