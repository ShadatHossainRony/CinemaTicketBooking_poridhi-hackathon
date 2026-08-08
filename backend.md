# 05 — Backend Plan

**Purpose:** the exact FastAPI structure, the dependency list, the ordered build sequence with a
verification command per step, and the full env-var surface.
**Read this when:** starting any backend step, or adding a dependency.
**Status:** **FINAL**, conforming to `04-api-contract.md` (canonical) and `03-data-model.md`.

---

## §1 — Directory tree

```
backend/
├── Dockerfile
├── .dockerignore                   MUST contain .env
├── pyproject.toml                  deps + ruff + pytest config
├── alembic.ini                     NO hardcoded sqlalchemy.url
├── alembic/
│   ├── env.py                      reads settings; imports app.models
│   └── versions/                   one migration per commit
├── app/
│   ├── main.py                     create_app(): middleware → handlers → routers → lifespan
│   ├── seed.py                     idempotent catalogue seed (03-data-model.md §8)  ★ REQ-10
│   ├── core/
│   │   ├── config.py               pydantic-settings Settings + cached get_settings()
│   │   ├── logging.py              JSON formatter, request-id contextvar, uvicorn log config
│   │   ├── errors.py               AppError hierarchy + the error-code enum
│   │   ├── ids.py                  opaque public references (hld_… / bk_…)
│   │   └── middleware.py           RequestID, AccessLog, RateLimit
│   ├── db/
│   │   ├── base.py                 DeclarativeBase + naming_convention
│   │   └── session.py              engine, sessionmaker, get_db()
│   ├── models/
│   │   ├── __init__.py             imports EVERY model — Alembic autogenerate depends on it
│   │   ├── catalog.py              Movie, Theatre, Screen, Show
│   │   ├── seat.py                 ShowSeat                       ★ the contention table
│   │   ├── hold.py                 Hold
│   │   └── booking.py              Booking, Payment, GatewayEvent
│   ├── schemas/
│   │   ├── common.py               ErrorEnvelope, HealthResponse, ReadyResponse
│   │   ├── catalog.py              MovieRead, TheatreRead, ShowRead
│   │   ├── seat.py                 SeatMapResponse, SeatRead
│   │   ├── hold.py                 HoldCreate, HoldRead
│   │   ├── booking.py              BookingCreate, BookingRead, OtpVerify, PayResponse
│   │   └── gateway.py              GatewayCallback  ← the inbound webhook body
│   ├── repositories/
│   │   ├── catalog.py
│   │   ├── seat.py                 ★ THE ATOMIC CLAIM. The only place that UPDATE exists.
│   │   ├── hold.py
│   │   └── booking.py              bookings + payments + gateway_events
│   ├── services/
│   │   ├── hold.py                 ★ claim rules, TTL, all-or-nothing
│   │   ├── booking.py              hold→booking, OTP orchestration, transition map
│   │   ├── payment.py              ★ /pay orchestration + callback application (idempotent)
│   │   └── gateway.py              ★ httpx client: timeouts, retry, circuit breaker (INF-12)
│   ├── tasks/
│   │   ├── sweeper.py              expire holds + release seats     (REQ-06)
│   │   └── reconcile.py            retry gateway_events with processed_at IS NULL (REQ-44)
│   └── api/
│       ├── deps.py                 get_db, pagination, (later) session token
│       └── routers/
│           ├── health.py           /health, /ready
│           ├── catalog.py          /movies, /theatres, /shows
│           ├── seats.py            /shows/{id}/seats
│           ├── holds.py            /holds, /holds/{id}
│           ├── bookings.py         /bookings, /bookings/{ref}, /otp, /otp/verify, /pay
│           └── payments.py         /payments/callback
└── tests/
    ├── conftest.py                 fixtures
    ├── test_health.py
    ├── unit/
    │   ├── test_seat_claim.py      ★ CONCURRENCY — the highest-value test in the repo (REQ-28)
    │   ├── test_hold_expiry.py     ★ REQ-06
    │   ├── test_callback_idempotency.py  ★ DUPLICATE CALLBACK (REQ-14, REQ-28)
    │   └── test_booking_transitions.py
    └── api/
        ├── test_seatmap.py
        ├── test_holds.py           happy path, 409, 422, 410
        └── test_payments.py        callback always-200, force headers
```

> **Routers are mounted at the root** — `app.include_router(holds.router)` with no prefix
> (`04-api-contract.md`). There is no `api/v1/` package.

**Layer rules** (enforced by review, and by the fact that violations look ugly):

| Layer | May import | May **not** |
| :--- | :--- | :--- |
| `api/routers` | schemas, services, deps | models, repositories, `Session` for queries |
| `services` | repositories, schemas, core, models (as types) | `fastapi`, `Request`, `HTTPException` |
| `repositories` | models, `Session`, `select()`/`update()` | services, schemas, business rules |
| `models` | `db.base`, sqlalchemy | everything above |
| `tasks` | repositories, core | routers, schemas |

> A service raising `HTTPException` is the seam breaking. Services raise `AppError` subclasses;
> only `main.py` knows about HTTP status codes.

---

## §2 — Dependencies (lean; one line of justification each)

**Runtime**

| Package | Why |
| :--- | :--- |
| `fastapi` | The framework. OpenAPI/Swagger free = a Documentation-score asset (REQ-64). |
| `uvicorn[standard]` | ASGI server. `[standard]` adds uvloop/httptools — real throughput for Scenario C. |
| `sqlalchemy>=2.0` | ORM + Core. Typed 2.0 style; bound parameters ⇒ no SQL-injection surface (REQ-61). |
| `alembic` | Migrations. The only sane way to evolve schema on a live VM. |
| `psycopg[binary]` | Postgres driver. `[binary]` avoids a build toolchain in the runtime image. |
| `pydantic>=2` | Validation on every input (REQ-61). |
| `pydantic-settings` | Typed env config in one place. Removes every scattered `os.getenv` — and REQ-19 makes env-driven config a graded requirement. |
| **`httpx`** | **Outbound HTTP to the gateway (REQ-08).** Async, timeouts and connection pooling built in; `requests` is sync and would block the event loop on a 5 s gateway timeout — which would violate REQ-12. Also the FastAPI `TestClient` transport, so it is not a new dependency for tests. |

**Dev / CI only — must never appear in the runtime image**

| Package | Why |
| :--- | :--- |
| `pytest` | Test runner (REQ-28). |
| `pytest-asyncio` | The gateway client and the sweeper are async; their tests must be too. |
| `ruff` | Lint + format in one tool, sub-second. Runs in CI; reads as code quality. |
| `respx` | Mocks `httpx` at the transport layer, so unit tests for the callback/idempotency paths (REQ-14) run without the gateway container. Integration tests still use the **real** container — we are not mocking the gateway (REQ-08), we are isolating unit tests from it. |

**Explicitly NOT added**

| Not added | Why |
| :--- | :--- |
| `redis` | The seat invariant must live in Postgres or it is not an invariant. A second store is a second source of truth for the one thing we are judged on. |
| `celery` / any broker | The two async needs are fire-and-forget `/charge` (an `httpx` call with a timeout) and periodic expiry (an `asyncio` task in the lifespan). A broker adds a container, a worker process and a failure mode for zero requirements. `02-architecture.md` ADR-006. |
| `passlib`, `pyjwt`, `python-jose`, `python-multipart` | There is no login (`04-api-contract.md` §6). If INF-08 ships, the token is signed with `hmac` + `hashlib` from the stdlib. |
| `python-ulid` | `secrets.token_urlsafe(16)` gives 128 bits of URL-safe entropy with zero dependencies. We do not need lexicographic sortability. |
| `gunicorn` | `uvicorn --workers` is sufficient for one VM. |
| `sqlmodel` | Mixes the model and schema layers — breaks the separation we are scored on (REQ-59). |
| `python-dotenv` | `pydantic-settings` reads `.env` natively. |
| `slowapi` | The rate limiter is ~40 lines of in-process counter (INF-09). A dependency for that is not worth explaining. |

**Rule:** a new dependency requires a line in this table and a `chore(deps):` commit. No exceptions.
Every third-party package also goes in the README acknowledgements (REQ-54).

---

## §3 — Ordered build sequence

Each step ends with a command and its expected output. **Do not start step N+1 until step N
verifies.** Steps map onto the phases in `12-execution-plan.md`.

### Step 1 — Scaffold
Tree from §1 with empty modules. `pyproject.toml` with runtime deps + ruff + pytest. `.dockerignore`.
```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
python -c "import fastapi, sqlalchemy, alembic, httpx; print('deps ok')"
# expected: deps ok
```

### Step 2 — Settings + logging
`core/config.py` (§4 table — **including `HOLD_TTL_SECONDS`**), `core/logging.py`.
```bash
python -c "from app.core.config import get_settings; s=get_settings(); print(s.app_name, s.environment, s.hold_ttl_seconds)"
# expected: cinemaseat-api local 120
```

### Step 3 — App factory + `/health`
`main.py::create_app()`, `routers/health.py` with `/health` **only**. No DB, no gateway.
```bash
uvicorn app.main:app --reload --port 8000 &
curl -s -w ' %{time_total}s\n' localhost:8000/health
# expected: {"status":"ok","service":"cinemaseat-api",...}  0.00Xs      <-- REQ-18: < 1s
```
🚩 **This is what Phase 2 deploys.** Nothing else is needed to get TLS live.

### Step 4 — DB session + Alembic baseline
`db/base.py` with a constraint `naming_convention` (**before** the first migration), `db/session.py`,
`alembic init`, `env.py` wired to settings + `app.models`.
```bash
docker compose up -d db
docker compose exec api alembic revision --autogenerate -m "baseline"
docker compose exec api alembic upgrade head
docker compose exec api alembic heads
# expected: exactly ONE head line ending in (head)
```
🚩 **Gate:** two heads → `03-data-model.md` §5.4 immediately. Do not proceed.

### Step 5 — `/ready`
DB `SELECT 1` (2 s timeout) + alembic revision check + gateway probe (1 s, **advisory only**).
```bash
curl -s localhost:8000/ready | python3 -m json.tool
# expected: {"status":"ready","checks":{"database":{...},"migrations":{...},"gateway":{...}}}
docker compose stop db && curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/ready
# expected: 503                                            <-- prove the failure path
docker compose start db
```

### Step 6 — Models + migration
All nine models (`03-data-model.md` §3) in one pass — one schema migration, not nine. Every model
imported in `models/__init__.py`.
```bash
docker compose exec api alembic revision --autogenerate -m "cinemaseat schema"
# READ THE FILE. Confirm every CHECK constraint and every partial index is present —
# autogenerate does NOT emit partial indexes; add uq_payments_live_per_booking,
# ix_show_seats_sweep and ix_gateway_events_unprocessed by hand with op.create_index(..., postgresql_where=...)
docker compose exec api alembic upgrade head && docker compose exec api alembic heads
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '\dt'
# expected: alembic_version, movies, theatres, screens, shows, show_seats, holds,
#           bookings, payments, gateway_events
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '\di uq_payments_live_per_booking'
# expected: the partial unique index exists  <-- REQ-14 depends on it
```

### Step 7 — Seed  ★ REQ-10
`app/seed.py`, idempotent, get-or-create on natural keys.
```bash
docker compose exec api python -m app.seed && docker compose exec api python -m app.seed
# expected: identical summary both times, no duplicate-key error
#           "movies 4 | theatres 2 | screens 3 | shows 12 | show_seats 1152 | pre-booked 19"
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT status, count(*) FROM show_seats GROUP BY status;"
# expected: AVAILABLE ~1133, BOOKED ~19
```

### Step 8 — Catalogue + seat map (read path)
`repositories/catalog.py`, `repositories/seat.py` (read side), schemas, `routers/catalog.py`,
`routers/seats.py`.
```bash
curl -s localhost:8000/movies | python3 -m json.tool | head
curl -s localhost:8000/shows/1/seats | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(d['hold_ttl_seconds'], d['summary'], len(d['seats']))"
# expected: 120 {'total': 96, 'available': 77, 'held': 0, 'booked': 19} 96
```

### Step 9 — ★ THE ATOMIC CLAIM
`repositories/seat.py::claim_seats()` — the one `UPDATE … RETURNING` from `03-data-model.md` §4.1.
`services/hold.py`, `schemas/hold.py`, `routers/holds.py`.
```bash
curl -s -X POST localhost:8000/holds -H 'content-type: application/json' \
  -d '{"show_id":1,"seats":["F12"],"phone":"+8801700000001"}' | python3 -m json.tool
# expected: 201 with hold_id, expires_at, expires_in_seconds == HOLD_TTL_SECONDS

curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/holds \
  -H 'content-type: application/json' -d '{"show_id":1,"seats":["F12"],"phone":"+8801700000002"}'
# expected: 409        <-- the second claim on the same seat MUST fail
```
🚩 **Gate:** the second call returns 409 with `error.code == "SEAT_UNAVAILABLE"`. If it returns 201,
stop everything — the core requirement of the entire problem is broken.

### Step 10 — Middleware + exception handlers
Order (outermost first): `RequestIDMiddleware` → `AccessLogMiddleware` → `CORSMiddleware` →
`RateLimitMiddleware`. All four handlers from §5.
```bash
curl -si localhost:8000/health | grep -i x-request-id
# expected: x-request-id: <uuid4>
curl -s -X POST localhost:8000/holds -H 'content-type: application/json' -d '{}' | python3 -m json.tool
# expected: {"error":{"code":"VALIDATION_ERROR","message":...,"details":[...],"request_id":"..."}}
docker compose logs --tail=1 api
# expected: ONE JSON line carrying that same request_id, method, path, status, duration_ms
```
🚩 **Gate:** any endpoint returning FastAPI's default `{"detail": ...}` is a bug. Grep for it.

### Step 11 — Expiry: lazy predicate + sweeper  ★ REQ-06, REQ-19
The `reserved_until <= now()` branch in the claim, the `CASE` in the seat-map read, and
`tasks/sweeper.py` started in the lifespan.
```bash
HOLD_TTL_SECONDS=5 docker compose up -d api                 # short TTL on purpose
curl -s -X POST localhost:8000/holds -H 'content-type: application/json' \
  -d '{"show_id":1,"seats":["A1"],"phone":"+8801700000001"}' >/dev/null
curl -s localhost:8000/shows/1/seats | grep -o '"seat": "A1"[^}]*'   # expected: "status": "HELD"
sleep 8
curl -s localhost:8000/shows/1/seats | grep -o '"seat": "A1"[^}]*'   # expected: "status": "AVAILABLE"
```
🚩 This is a dry run of **Scenario B** (REQ-39). It must work before Phase 8.

### Step 12 — Gateway client  ★ REQ-08, INF-12
`services/gateway.py`: `charge()`, `send_otp()`, `verify_otp()`, `health()`. `httpx.AsyncClient`
with a **5 s** timeout, one retry on connect error only, and a circuit breaker
(5 consecutive failures → open 30 s → half-open).
```bash
docker compose up -d gateway
curl -s localhost:9000/health                     # expected: 200 from the gateway container
docker compose exec api python -c "
import asyncio; from app.services.gateway import GatewayClient
print(asyncio.run(GatewayClient().health()))"
# expected: True

docker compose stop gateway
curl -s -w ' %{time_total}s\n' localhost:8000/health   # expected: still 200, still fast  <-- REQ-18
curl -s localhost:8000/ready | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])"
# expected: degraded          (NOT not_ready — 04-api-contract.md §7)
docker compose start gateway
```

### Step 13 — Booking + OTP
`services/booking.py` with the transition map, `routers/bookings.py`.
```bash
HOLD=$(curl -s -X POST localhost:8000/holds -H 'content-type: application/json' \
  -d '{"show_id":1,"seats":["B3"],"phone":"+8801700000001"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["hold_id"])')
REF=$(curl -s -X POST localhost:8000/bookings -H 'content-type: application/json' \
  -d "{\"hold_id\":\"$HOLD\"}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["booking_ref"])')
curl -s -X POST localhost:8000/bookings/$REF/otp                     # expected: 202, otp_ref
curl -s -X POST localhost:8000/bookings/$REF/otp/verify -H 'content-type: application/json' \
  -d '{"code":"123456"}'                                             # expected: 200 OTP_VERIFIED  (Q-05!)
```

### Step 14 — ★ `/pay` + the callback
`services/payment.py`, `routers/payments.py`. The four behaviours in `04-api-contract.md` §5:
payment row **before** the charge, reservation extended, **force headers forwarded**, 202 fast.
```bash
time curl -s -X POST localhost:8000/bookings/$REF/pay -H 'X-Mock-Mode: deterministic'
# expected: 202 {"status":"PENDING",...} in well under 1s        <-- REQ-12
sleep 5
curl -s localhost:8000/bookings/$REF | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])'
# expected: CONFIRMED                                            <-- the callback did the work
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT event_id, status, processed_at IS NOT NULL AS done FROM gateway_events;"
# expected: one row, done = t
```
Then the requirement that actually scores:
```bash
# Duplicate callback must change nothing  -- REQ-14
curl -s -X POST localhost:8000/bookings/$REF2/pay -H 'X-Mock-Force: duplicate'
sleep 20
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT (SELECT count(*) FROM payments WHERE booking_ref='$REF2') AS payments,
          (SELECT count(*) FROM gateway_events WHERE booking_ref='$REF2') AS events;"
# expected: payments = 1, events = 1        <-- the second delivery was absorbed by ON CONFLICT
```
🚩 **Gate:** `payments = 1`. If it is 2, REQ-14 is broken and 25 Functionality points are at risk.

### Step 15 — Reconciliation sweep
`tasks/reconcile.py`: re-process `gateway_events` with `processed_at IS NULL` older than 60 s.
```bash
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "UPDATE gateway_events SET processed_at = NULL WHERE event_id = (SELECT event_id FROM gateway_events LIMIT 1);"
sleep 70
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT count(*) FROM gateway_events WHERE processed_at IS NULL;"
# expected: 0        <-- the sweep healed it (REQ-44 "pending payments recover")
```

### Step 16 — Tests + CI
```bash
docker compose exec api pytest -q --tb=short
# expected: all pass, including test_seat_claim.py and test_callback_idempotency.py
ruff check . && ruff format --check .
# expected: All checks passed!
```

---

## §4 — Environment variables (`pydantic-settings`)

One root `.env` for production. **`.env.example` is committed** (REQ-58) with descriptions and safe
placeholders, never real values.

> 🔴 **REQ-21: `docker compose up` must work from a clean clone with no manual steps.** Therefore
> every variable below has a **dev-safe default in `docker-compose.yml`** via `${VAR:-default}`
> substitution. A missing `.env` is a supported, tested state — not an error.
> Only `POSTGRES_PASSWORD` and `SESSION_SECRET` differ in production, and both are generated on the
> VM. See `07-containerization.md` §3.

### Application

| Var | Type | Default | Consumed by | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `APP_NAME` | str | `cinemaseat-api` | api | Appears in logs and `/health` |
| `APP_VERSION` | str | `0.1.0` | api | Returned by `/health` |
| `ENVIRONMENT` | `local\|production` | `local` | api | Gates log verbosity |
| `LOG_LEVEL` | `DEBUG\|INFO\|WARNING\|ERROR` | `INFO` | api | `DEBUG` locally, `INFO` on the VM |
| `PUBLIC_BASE_URL` | str | `https://poridhi-hackathon.shadathossainrony.dev` | api, tests | Smoke tests and any absolute link |
| `CORS_ORIGINS` | csv | `http://localhost:5173` | api | Production: the single public origin. **Never `*`** (INF-11) |

### ★ Booking rules — REQ-19 lives here

| Var | Type | Default | Notes |
| :--- | :--- | :--- | :--- |
| **`HOLD_TTL_SECONDS`** | int | **`120`** | ★ **REQ-19, verbatim requirement.** How long a hold survives unpaid. **Judges will restart the stack with a small value** (e.g. `15`) and watch a seat come back. It is read once in `config.py`, used by `services/hold.py`, and **echoed in the seat-map response** so it is verifiable without reading source. **It must appear nowhere else as a literal.** |
| `PAYMENT_WINDOW_SECONDS` | int | `90` | Reservation extension granted by `/pay`. Must exceed the gateway's 15 s maximum callback delay with margin (REQ-15), or a short `HOLD_TTL_SECONDS` would release seats mid-payment. |
| `SWEEP_INTERVAL_SECONDS` | int | `5` | Sweeper cadence. Correctness does not depend on it (`03-data-model.md` §4.3). |
| `RECONCILE_INTERVAL_SECONDS` | int | `30` | Unprocessed-callback retry cadence (REQ-44) |
| `MAX_SEATS_PER_HOLD` | int | `6` | Inventory-denial control (`09-security-hardening.md` T-05) |
| `OTP_MAX_ATTEMPTS` | int | `5` | Brute-force lock per booking |
| `OTP_RESEND_COOLDOWN_SECONDS` | int | `30` | Gateway abuse control |
| `OTP_REQUIRED` | bool | `true` | Escape hatch for **Q-05**. If the mock gateway's OTP code turns out to be unobtainable, set `false`, **and say so in the README**. Never flip it silently. |

### ★ Gateway — REQ-08, REQ-11

| Var | Type | Default | Notes |
| :--- | :--- | :--- | :--- |
| `GATEWAY_BASE_URL` | str | `http://gateway:9000` | **Docker service name.** `localhost` here is the API container itself — failure mode B1 in `15-troubleshooting.md`. |
| `GATEWAY_CALLBACK_URL` | str | `http://api:8000/payments/callback` | Sent to the gateway as `callback_url`. **Internal only** — this is why the callback is not publicly routable (`09-security-hardening.md` T-04). Behind the Nginx upstream this stays internal; it is never the public hostname. |
| `GATEWAY_TIMEOUT_SECONDS` | float | `5.0` | `/charge` must not block `/pay` (REQ-12) |
| `GATEWAY_HEALTH_TIMEOUT_SECONDS` | float | `1.0` | `/ready`'s advisory probe; must never stall the response |
| `GATEWAY_BREAKER_THRESHOLD` | int | `5` | Consecutive failures before the breaker opens (INF-12) |
| `GATEWAY_BREAKER_RESET_SECONDS` | int | `30` | Half-open retry interval |

### Database

| Var | Type | Default | Notes |
| :--- | :--- | :--- | :--- |
| `POSTGRES_USER` | str | `cinemaseat` | |
| `POSTGRES_PASSWORD` | str | `cinemaseat_dev_pw` | ⚠️ Dev default so a clean clone boots (REQ-21). **Generated fresh on the VM** — `08-deployment-runbook.md` §3. |
| `POSTGRES_DB` | str | `cinemaseat` | |
| `POSTGRES_HOST` | str | `db` | **Docker service name.** `localhost` here is the #1 connection bug. |
| `POSTGRES_PORT` | int | `5432` | Internal only; never published |
| `DATABASE_URL` | str | *assembled* | Built in `config.py` from the parts above. One source of truth. |
| `DB_POOL_SIZE` | int | `10` | Raised from the old plan's 5: two API replicas × two workers each contend for the seat rows, and pool starvation would look like a correctness problem. Tune from Scenario C, not from vibes. |
| `DB_MAX_OVERFLOW` | int | `10` | Ceiling per process = 20; 4 processes = 80, under Postgres's default `max_connections` 100. **Check this arithmetic before raising either number.** |
| `DB_POOL_TIMEOUT` | int | `10` | Fail fast rather than queue forever under Scenario A |

### Rate limiting and (optional) sessions

| Var | Type | Default | Notes |
| :--- | :--- | :--- | :--- |
| `RATE_LIMIT_HOLD_PER_MINUTE` | int | `120` | ⚠️ Deliberately generous — **must not shed Scenario A's 100-request burst** (`04-api-contract.md` §8) |
| `RATE_LIMIT_DEFAULT_PER_MINUTE` | int | `600` | |
| `SESSION_SECRET` | str | *dev literal* | **Only if INF-08 ships.** `openssl rand -hex 32` on the VM. |

### 🔴 The `.env` file-scoping gotcha — read before the first `docker compose up`

*Preserved verbatim. Three different mechanisms read `.env`, and confusing them costs 20 minutes
every time:*

1. **Compose variable substitution.** `docker compose` reads a file named exactly `.env` **in the
   directory containing `docker-compose.yml`** (the project directory) — *not* the current working
   directory, *not* `backend/`. It uses it to expand `${VAR}` inside the compose file itself.
   `--env-file` overrides which file.
2. **Container environment.** `env_file: .env` under a service injects those vars *into the
   container*. This path is relative to the compose file's directory. **Substitution and injection
   are separate** — a var used in `${...}` is not automatically in the container, and vice versa.
3. **Pydantic settings.** `BaseSettings(model_config=SettingsConfigDict(env_file=".env"))` reads
   relative to the **process working directory inside the container** (`/app`). If the file was
   never copied or mounted there, this silently reads nothing and falls back to defaults.

**Our rule, one line:** **one `.env` at repo root, and it is optional.** Compose uses
`${VAR:-default}` substitution so the stack boots without it (REQ-21); when the file exists its
values win. Inside the container, `Settings` reads **real environment variables**, which compose has
set. `.env` is **never** baked into the image (`.dockerignore`) and never committed.

**Verify it actually landed, every deploy:**
```bash
docker compose exec api env | grep -E 'POSTGRES_HOST|ENVIRONMENT|HOLD_TTL_SECONDS|GATEWAY_BASE_URL'
# expected: POSTGRES_HOST=db, ENVIRONMENT=production, HOLD_TTL_SECONDS=120, GATEWAY_BASE_URL=http://gateway:9000
curl -s https://$DOMAIN/shows/1/seats | python3 -c 'import json,sys;print(json.load(sys.stdin)["hold_ttl_seconds"])'
# expected: the same number  <-- REQ-19 proven end to end, from env var to wire
```

---

## §5 — Middleware, handlers, lifespan — blueprint

Signatures and behaviour only. Implementation happens in the build session.

### Middleware (registration order = outermost first)

```python
# app/core/middleware.py

class RequestIDMiddleware(BaseHTTPMiddleware):
    """Read X-Request-ID (set by Nginx) or mint uuid4; bind to a contextvar;
    echo it on the response."""

class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit exactly ONE JSON line per request on completion:
    {ts, level, msg:"request", request_id, method, path, status, duration_ms, client_ip}
    Never logs the body, headers, query values, or a phone number."""

class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-process fixed-window counter keyed on (client_ip, bucket).
    Buckets: 'hold' for POST /holds, 'default' otherwise.
    /payments/callback is EXEMPT — rate-limiting the gateway causes infinite retries (REQ-13).
    Exceeded -> 429 RATE_LIMITED with Retry-After.
    Per-process, therefore per-replica: an honest limitation, stated in ADR-008."""
```

`CORSMiddleware`: `allow_origins=settings.cors_origins` (explicit list), `allow_credentials=False`,
`allow_methods=["GET","POST","OPTIONS"]`, `allow_headers=["Content-Type","X-Request-ID","X-Mock-Mode","X-Mock-Force"]`.

### Exception handlers (all registered in `create_app()`)

```python
@app.exception_handler(AppError)          # domain -> exc.http_status + envelope(code, message, details)
@app.exception_handler(RequestValidationError)
    # 422 VALIDATION_ERROR. Flatten exc.errors() to [{field, issue}];
    # strip the 'body'/'query' prefix from loc so field names read naturally.
@app.exception_handler(StarletteHTTPException)
    # map 404/405 into the same envelope so even framework errors are consistent
@app.exception_handler(Exception)
    # log.exception(...) with request_id, then 500 INTERNAL_ERROR, generic message.
    # NEVER return str(exc) or a traceback to the client.
```

> 🔴 **`POST /payments/callback` bypasses all of this.** Its handler catches every exception
> **inside itself** and returns **200** regardless (REQ-13). A 500 from the callback route means the
> global handler ran, which means the requirement is broken. `tests/api/test_payments.py` asserts
> that a handler raising `RuntimeError` still produces a 200.

### Gateway client — the shape that satisfies REQ-12, REQ-15, REQ-17, INF-12

```python
# app/services/gateway.py
class GatewayClient:
    """One shared httpx.AsyncClient (connection pooling), created in the lifespan.

    charge(booking_ref, amount, currency, passthrough_headers) -> ChargeResult
        - timeout GATEWAY_TIMEOUT_SECONDS; retries ONLY on connect errors, once
        - forwards X-Mock-Mode / X-Mock-Force verbatim          <-- REQ-17
        - raises GatewayUnavailableError on 5xx/timeout/open breaker -> 503, never 500
    send_otp(phone, ref) / verify_otp(ref, code)
    health() -> bool          # 1s timeout, used by /ready only, never by /health
    """
```

**Circuit breaker (INF-12):** `GATEWAY_BREAKER_THRESHOLD` consecutive failures ⇒ open for
`GATEWAY_BREAKER_RESET_SECONDS`; while open, calls fail immediately with `GATEWAY_UNAVAILABLE`
instead of burning 5 seconds each. This is what keeps `/health` fast and the app 500-free while the
gateway is down (REQ-18, REQ-44).

### Lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings)                     # JSON logs; log settings WITHOUT secrets
    app.state.gateway = GatewayClient(settings)     # one pooled client
    sweeper   = asyncio.create_task(run_sweeper(settings))     # REQ-06
    reconcile = asyncio.create_task(run_reconcile(settings))   # REQ-44
    yield
    for t in (sweeper, reconcile):
        t.cancel()
    await app.state.gateway.aclose()
    await engine.dispose()
```

**No migrations here.** A failing migration in the entrypoint turns a schema problem into a restart
loop with no API to debug through. REQ-21 is satisfied by the one-shot `migrate` service instead
(`03-data-model.md` §5.5, `07-containerization.md` §3).

> ⚠️ Both background tasks run in **every** uvicorn worker and **every** replica. Both statements
> are idempotent, so this is wasteful rather than wrong. Stated openly in `02-architecture.md` §5d
> — it is a better answer than pretending it was free.

---

**Cross-links:** the contract → `04-api-contract.md` (canonical) · schema and the atomic claim →
`03-data-model.md` §4 · Dockerfile/compose → `07-containerization.md` · env on the VM →
`08-deployment-runbook.md` · logging detail → `11-observability.md` · tests →
`10-testing-and-load.md`
