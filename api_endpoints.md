# 04 — API Contract ★ CANONICAL

**Purpose:** the exact wire contract for CinemaSeat. This file is the **single source of truth for
the interface**. Code conforms to it; it does not conform to code.
**Read this when:** writing a router, the frontend client, a test, the README API section, or a
load script. If a document and this file disagree, this file wins.
**Status:** **FINAL** — derived from `01-problem-analysis.md` REQ/INF IDs.

**Base URL — the only one that exists:**
```
https://poridhi-hackathon.shadathossainrony.dev
```

**There is no path prefix.** No `/api`, no `/v1`. Routes are mounted at the root.
Driven by REQ-18 (`problem_statement.md:141` — *"`GET /health` returns 200 in under one second"*)
and REQ-20 (`:143` — *"Judges will point tests at these"*): the judges' literal request must work.

> **Consequence for Nginx and the frontend, do not skip this.** Because the API lives at the root,
> the reverse proxy routes by an **explicit allow-list of first path segments**, and the SPA
> therefore **has no client-side router** — a UI route named `/shows` would be shadowed by the API.
> Full Nginx block: `08-deployment-runbook.md` §5.3. Frontend rule: `06-frontend-plan.md` §3.

---

## §1 — Conventions

| Aspect | Rule |
| :--- | :--- |
| Versioning | **None.** REQ-26: *"Implement only the endpoints you actually need."* A version prefix for a one-day system with one client is ceremony, and it breaks REQ-18. Documented as a conscious trade-off in `DECISIONS.md`. |
| Content type | `application/json` in and out. |
| Casing | `snake_case` for every JSON key. |
| Timestamps | ISO-8601 **UTC with `Z`**: `2026-08-08T12:03:11Z`. `TIMESTAMPTZ` in the DB, always. |
| Money | **String-encoded decimal**: `"450.00"`. Never a float — `NUMERIC(10,2)` in the DB, `Decimal` in Python, string on the wire. A judge asking about money precision gets a one-word answer. |
| Currency | `"BDT"`, on every monetary response. |
| Public identifiers | `hold_id` and `booking_ref` are **opaque unguessable strings** (`hld_…`, `bk_…`), not sequential integers. Rationale in §7. `seat` is a human label (`"A1"`); `show_id`/`movie_id` are integers. |
| Trailing slashes | **None** on any route. `redirect_slashes=False` — a 307 through Nginx is a classic mystery bug. |
| Request ID | Client may send `X-Request-ID`; the server **always** returns one. |
| Method semantics | `GET` read-only · `POST` create or state transition (201/202) · no `PUT`, no `PATCH`, no `DELETE` — nothing in the problem requires them (REQ-26). |
| Idempotency | `POST /payments/callback` is **strictly idempotent** on `event_id` (REQ-14). `POST /holds` is deliberately **not** — that is the whole point of REQ-07. |
| Auth | See §6. There is **no login**. |

### Status codes we use, and only these

| Code | When |
| :-: | :--- |
| `200` | Successful read, or an accepted callback |
| `201` | Hold or booking created |
| `202` | **Accepted, work continues asynchronously** — `/pay` and `/otp` (REQ-12) |
| `400` | Semantically invalid but schema-valid (bad OTP code) |
| `404` | Resource does not exist |
| `409` | Conflict — **seat taken**, or an illegal state transition |
| `410` | **Gone** — the hold expired. Distinct from 409 on purpose: the client must restart, not retry. |
| `422` | Schema/validation failure, reshaped into our envelope |
| `429` | Rate limit exceeded (`Retry-After` set) |
| `500` | Unhandled. Generic message, never a trace. **REQ-44 target: zero of these when the gateway is down.** |
| `503` | Dependency unavailable — `/ready` (DB down) or a gateway call |

---

## §2 — Standard error envelope — used **everywhere**, no exceptions

Every non-2xx response from the API has exactly this shape. Handlers for `AppError`,
`RequestValidationError`, `StarletteHTTPException` and the catch-all `Exception` all produce it.
Satisfies REQ-60 (*"sensible status codes and response shapes"*) and INF-02.

```json
{
  "error": {
    "code": "SEAT_UNAVAILABLE",
    "message": "One or more seats are no longer available.",
    "details": [
      { "field": "seats", "issue": "seat A1 is not available" }
    ],
    "request_id": "6f1c2f0a-6f5d-4a1f-9a53-2c4f9b8e77d1"
  }
}
```

| Field | Type | Notes |
| :--- | :--- | :--- |
| `error.code` | string enum | Machine-readable, stable. The frontend switches on this, never on `message`. |
| `error.message` | string | Human-readable and safe to display. **Never** contains SQL, traces, file paths, internal hostnames, or a phone number. |
| `error.details` | array \| null | Per-field issues; `null` otherwise. |
| `error.request_id` | string (uuid4) | Matches the `X-Request-ID` response header and every server log line for this request. One grep finds the whole story (`11-observability.md` §2). |

### Error code enum — the real domain

| `code` | HTTP | Meaning | Raised by |
| :--- | :-: | :--- | :--- |
| `VALIDATION_ERROR` | 422 | Body/query failed schema validation | framework |
| `TOO_MANY_SEATS` | 422 | More than `MAX_SEATS_PER_HOLD` requested | hold service |
| `BAD_REQUEST` | 400 | Schema-valid but semantically wrong | any service |
| `OTP_INVALID` | 400 | Gateway rejected the code | booking service |
| `NOT_FOUND` | 404 | Show, seat, hold or booking does not exist | any |
| `SEAT_UNAVAILABLE` | 409 | ★ **One or more seats already held or booked** (REQ-07) | hold service |
| `HOLD_NOT_ACTIVE` | 409 | Hold already converted, released, or belongs to another booking | booking service |
| `BOOKING_NOT_PAYABLE` | 409 | Wrong state for `/pay` (already paid, or OTP not verified) | payment service |
| `OTP_NOT_VERIFIED` | 409 | `/pay` called before OTP verification | payment service |
| `HOLD_EXPIRED` | 410 | ★ **The reservation window elapsed; the seats are gone** (REQ-06) | hold/booking service |
| `RATE_LIMITED` | 429 | Too many requests. `Retry-After` header set. | Nginx / middleware |
| `PAYLOAD_TOO_LARGE` | 413 | Above `client_max_body_size` | Nginx |
| `GATEWAY_UNAVAILABLE` | 503 | ★ **Payment/OTP gateway timed out, 5xx'd, or the breaker is open** (REQ-15, REQ-44) | gateway client |
| `DEPENDENCY_UNAVAILABLE` | 503 | `/ready` only — the database is unreachable | health router |
| `INTERNAL_ERROR` | 500 | Unhandled. Message is always `"An unexpected error occurred."` | catch-all |
| `UNAUTHENTICATED` | 401 | Missing/invalid session token — **only exists if INF-08 ships** | session dependency |

> **Implementation note (blueprint, not code):** `AppError(Exception)` in `app/core/errors.py`
> carries `code`, `http_status`, `message`, `details`. Subclasses: `SeatUnavailableError`,
> `HoldExpiredError`, `InvalidStateError`, `GatewayUnavailableError`, `NotFoundError`.
> Services raise those; `main.py` registers one handler per family.
> **Routers never build an error response by hand** — that is how envelopes drift.

---

## §3 — Endpoint table (complete — nothing else exists)

Every row cites the requirement that forces it. **No REQ-ID → the endpoint does not exist.**

| # | Method | Path | Auth | Purpose | Satisfies |
| :-: | :--- | :--- | :-: | :--- | :--- |
| 1 | `GET` | `/health` | no | Liveness. Touches **nothing** — not the DB, not the gateway. | REQ-18 |
| 2 | `GET` | `/ready` | no | Readiness: DB + migration revision + gateway (advisory). | INF-03, REQ-44 |
| 3 | `GET` | `/docs` · `/openapi.json` | no | Swagger UI + schema. Living API documentation. | REQ-64 |
| 4 | `GET` | `/movies` | no | Browse the catalogue. | REQ-01, REQ-10 |
| 5 | `GET` | `/theatres` | no | Browse theatres. | REQ-01, REQ-10 |
| 6 | `GET` | `/shows` | no | Showtimes, filterable by movie / theatre / date. | REQ-01 |
| 7 | `GET` | `/shows/{show_id}/seats` | no | ★ **The live seat map.** | REQ-02, **REQ-20** |
| 8 | `POST` | `/holds` | no† | ★ **Atomically claim seats.** The contention endpoint. | REQ-03, **REQ-07**, **REQ-20** |
| 9 | `GET` | `/holds/{hold_id}` | no† | Hold status + remaining seconds. Evidence for Scenario B. | REQ-06, **REQ-39** |
| 10 | `POST` | `/bookings` | no† | Convert a hold into a booking. | REQ-05 |
| 11 | `GET` | `/bookings/{booking_ref}` | no† | Booking + payment status. The frontend polls this. | REQ-05, REQ-15 |
| 12 | `POST` | `/bookings/{booking_ref}/otp` | no† | Ask the gateway to send an OTP. | REQ-08 |
| 13 | `POST` | `/bookings/{booking_ref}/otp/verify` | no† | Verify the code through the gateway. | REQ-08 |
| 14 | `POST` | `/bookings/{booking_ref}/pay` | no† | ★ **Start payment and return immediately.** | REQ-04, **REQ-12**, REQ-17 |
| 15 | `POST` | `/payments/callback` | **network** | ★ **Gateway webhook. Always 200. Idempotent.** | **REQ-13**, **REQ-14**, REQ-16 |

† *"no" today; becomes a bearer session token if INF-08 ships (see §6). The contract is written so
that adding it is a header, not a redesign.*

**Endpoint 15 is not routed by Nginx.** `/payments/callback` is deliberately absent from the
public location allow-list, so it is reachable **only from inside the Docker network** — which is
where the gateway container lives. This is the callback-forgery control (`09-security-hardening.md`
T-04). A public `POST /payments/callback` hits the SPA fallback and returns HTML.

**MUST requirements satisfied by infrastructure, not by an endpoint** — recorded here so the
coverage check in `01-problem-analysis.md` §5 closes:

| REQ | Satisfied by |
| :-- | :--- |
| REQ-06 (auto-release) | Lazy-expiry predicate in the claim statement **+** the sweeper task. Observable via #7 and #9. |
| REQ-10 (pre-populated data) | `migrate` one-shot container running `app.seed`. |
| REQ-19 (`HOLD_TTL_SECONDS`) | Config; **echoed in #7's response** so judges can verify it without reading code. |
| REQ-21 / REQ-30 (clean-clone `up`) | `docker-compose.yml`. |
| REQ-27 (one base URL) | Nginx: one hostname serves both the SPA and the API. |
| REQ-32–37 (CI/CD) | `.github/workflows/`. |

---

## §4 — ★ THE TWO ENDPOINTS JUDGES WILL POINT TESTS AT (REQ-20)

`problem_statement.md:143` — *"Your `README` lists the exact request for holding a seat and for
fetching a seat map. Judges will point tests at these."*
**These two blocks are copied verbatim into `README.md`. Keep them in sync or lose the point.**

### 4.1 — `GET /shows/{show_id}/seats` — fetch the seat map

```bash
curl -s https://poridhi-hackathon.shadathossainrony.dev/shows/1/seats
```

**200 OK**
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
    { "seat": "A1",  "row": "A", "number": 1,  "seat_class": "STANDARD", "price": "350.00", "status": "AVAILABLE" },
    { "seat": "A2",  "row": "A", "number": 2,  "seat_class": "STANDARD", "price": "350.00", "status": "BOOKED"    },
    { "seat": "F12", "row": "F", "number": 12, "seat_class": "PREMIUM",  "price": "450.00", "status": "HELD",
      "held_until": "2026-08-08T12:05:11Z" }
  ]
}
```

| Field | Notes |
| :--- | :--- |
| `seats[].status` | `AVAILABLE` \| `HELD` \| `BOOKED`. **Effective** status: a `HELD` row whose `reserved_until` has passed is reported `AVAILABLE` (REQ-06 without waiting for the sweeper — see `03-data-model.md` §4). |
| `seats[].held_until` | Present only when `status == "HELD"`. Lets the UI render a countdown and lets a judge watch an expiry. |
| `hold_ttl_seconds` | **Echoes `HOLD_TTL_SECONDS` from the environment** (REQ-19). A judge restarting the stack with `HOLD_TTL_SECONDS=15` sees `15` here — proof it is not hardcoded, without reading the source. |
| `summary` | Cheap aggregate; the Scenario A verification reads `summary.held` and expects exactly the seats it claimed. |
| ordering | Row label ascending, then seat number ascending. Stable, so a diff between two fetches is meaningful. |

Errors: `404 NOT_FOUND` (unknown `show_id`).
**Never** paginated — a screen is at most a few hundred seats and the UI needs the whole grid.
`seats_per_row × row_count` is capped at 400 by a seed-time check.

### 4.2 — `POST /holds` — hold a seat

```bash
curl -s -X POST https://poridhi-hackathon.shadathossainrony.dev/holds \
  -H 'Content-Type: application/json' \
  -d '{"show_id": 1, "seats": ["F12"], "phone": "+8801700000001"}'
```

**Request**

| Field | Type | Required | Validation |
| :--- | :--- | :-: | :--- |
| `show_id` | int | yes | ≥ 1; must exist and not have started |
| `seats` | array of string | yes | 1 … `MAX_SEATS_PER_HOLD` (default **6**); each matches `^[A-Z]{1,2}[0-9]{1,3}$`; **no duplicates** |
| `phone` | string | yes | E.164-ish, `^\+?[0-9]{10,15}$`. The customer identity (INF-07). |

**201 Created**
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

**409 Conflict — `SEAT_UNAVAILABLE`. This is the response 99 of 100 requests get in Scenario A.**
```json
{
  "error": {
    "code": "SEAT_UNAVAILABLE",
    "message": "One or more seats are no longer available.",
    "details": [ { "field": "seats", "issue": "seat F12 is not available" } ],
    "request_id": "9c1f2b70-8d4e-4a11-b0c2-7f5a1e33d902"
  }
}
```
**All-or-nothing.** A multi-seat hold either claims every requested seat or claims none; there is
no partial hold. The claim is one `UPDATE … RETURNING` inside one transaction, and a
`RETURNING` row count below the requested count rolls the transaction back
(`03-data-model.md` §4 — this single statement is the answer to REQ-07).

**422 — `TOO_MANY_SEATS`**
```json
{ "error": { "code": "TOO_MANY_SEATS", "message": "A hold may cover at most 6 seats.",
             "details": [ { "field": "seats", "issue": "8 requested, maximum is 6" } ],
             "request_id": "…" } }
```

**422 — `VALIDATION_ERROR`** (unknown seat label, malformed phone)
```json
{ "error": { "code": "VALIDATION_ERROR", "message": "Request validation failed.",
             "details": [ { "field": "seats", "issue": "seat 'Z99' does not exist on this screen" },
                          { "field": "phone", "issue": "string does not match regex" } ],
             "request_id": "…" } }
```

Other errors: `404 NOT_FOUND` (unknown show), `409 BAD_REQUEST` (show already started),
`429 RATE_LIMITED`.

---

## §5 — The remaining endpoints

### `GET /movies` → 200

```json
{ "items": [ { "id": 1, "title": "Spider-Man: Brand New Day", "synopsis": "…",
               "duration_minutes": 128, "rating": "PG-13", "show_count": 6 } ] }
```
`?q=` optional case-insensitive title search, ≤ 60 chars, parameterized `ILIKE`.
No pagination — the seeded catalogue is single-digit (INF-10 demoted; see `RECONCILIATION.md` DEL-18).

### `GET /theatres` → 200

```json
{ "items": [ { "id": 1, "name": "Star Cineplex Chattogram", "city": "Chattogram",
               "screens": [ { "id": 1, "name": "Screen 1", "row_count": 8, "seats_per_row": 12 } ] } ] }
```

### `GET /shows` → 200

Query: `movie_id` (int), `theatre_id` (int), `date` (`YYYY-MM-DD`, UTC). All optional, all validated.
```json
{ "items": [ { "id": 1, "starts_at": "2026-08-09T00:00:00Z", "currency": "BDT",
               "base_price": "350.00",
               "movie":   { "id": 1, "title": "Spider-Man: Brand New Day" },
               "theatre": { "id": 1, "name": "Star Cineplex Chattogram" },
               "screen":  { "id": 1, "name": "Screen 1" },
               "seats_available": 71, "seats_total": 96 } ] }
```
> `seats_available` is a live aggregate over `show_seats` with the lazy-expiry predicate applied.
> It is the **most expensive query in the catalogue** and the first candidate for the Scenario C
> bottleneck discussion (`10-testing-and-load.md` §6). Index: `ix_show_seats_show_status`.

### `GET /holds/{hold_id}` → 200

```json
{ "hold_id": "hld_01K2Q…", "show_id": 1, "status": "ACTIVE",
  "seats": ["F12"], "total_amount": "450.00", "currency": "BDT",
  "expires_at": "2026-08-08T12:05:11Z", "expires_in_seconds": 43,
  "booking_ref": null }
```
`status` ∈ `ACTIVE` · `EXPIRED` · `CONVERTED` (a booking was created) · `RELEASED`.
`expires_in_seconds` is `0` once expired — never negative.
**Scenario B reads this endpoint before and after the TTL** (REQ-39).
Errors: `404 NOT_FOUND`.

### `POST /bookings` → 201

```jsonc
// request
{ "hold_id": "hld_01K2Q7M4V8ZC3N6RJH0YB5TXWD" }
// 201
{ "booking_ref": "bk_01K2Q7NB3F5D8QW1YH6ZR4MTGA",
  "status": "PENDING_OTP",
  "show_id": 1,
  "seats": ["F12"],
  "total_amount": "450.00", "currency": "BDT",
  "phone_masked": "+88017*****01",
  "reserved_until": "2026-08-08T12:05:11Z",
  "created_at": "2026-08-08T12:03:31Z" }
```
The booking inherits the hold's phone; the client never re-sends it. The hold moves to `CONVERTED`.
**Creating a booking does not extend the reservation** — the original TTL still governs, so
Scenario B works unchanged. The window is extended only by `/pay` (see below).

Errors: `404 NOT_FOUND` · `409 HOLD_NOT_ACTIVE` (already converted/released) ·
`410 HOLD_EXPIRED` (with `details` naming the seats that were released).

### `POST /bookings/{ref}/otp` → 202

Body: none. Calls the gateway `POST /otp/send { phone, ref }`.
```json
{ "booking_ref": "bk_01K2Q…", "otp_sent": true, "otp_ref": "otp_8f2a…",
  "expires_in_seconds": 300, "resend_available_in_seconds": 30 }
```
**The gateway loses or delays 10% of OTPs (REQ-15).** Calling this endpoint again resends;
it is rate-limited to one call per 30 s per booking, returning `429` with `Retry-After`.
Errors: `409 BOOKING_NOT_PAYABLE` (already confirmed) · `410 HOLD_EXPIRED` · `503 GATEWAY_UNAVAILABLE`.

### `POST /bookings/{ref}/otp/verify` → 200

```jsonc
{ "code": "123456" }          // 4–8 digits, validated
// 200
{ "booking_ref": "bk_01K2Q…", "status": "OTP_VERIFIED", "verified_at": "2026-08-08T12:04:02Z" }
```
Errors: `400 OTP_INVALID` (gateway returned 400) · `410 HOLD_EXPIRED` · `429 RATE_LIMITED`
(**5 attempts per booking**, then locked — OTP brute-force control, `09-security-hardening.md` T-06)
· `503 GATEWAY_UNAVAILABLE`.

### ★ `POST /bookings/{ref}/pay` → 202 — the fast-return endpoint (REQ-12)

```bash
curl -s -X POST https://poridhi-hackathon.shadathossainrony.dev/bookings/bk_01K2Q…/pay \
  -H 'X-Mock-Force: duplicate'
```

**202 Accepted**
```json
{ "booking_ref": "bk_01K2Q…",
  "payment_id": "pay_xyz",
  "status": "PENDING",
  "poll_url": "/bookings/bk_01K2Q…",
  "poll_after_seconds": 2,
  "reserved_until": "2026-08-08T12:05:41Z" }
```

Four things this endpoint must do, in this order — **each one is a stated requirement**:

| # | Behaviour | Why |
| :-: | :--- | :--- |
| 1 | **Write the `payments` row first**, keyed on our own `booking_ref`, *before* calling the gateway. | `X-Mock-Force: race` delivers the callback **before `/charge` returns**. Matching the callback on `booking_ref` (which the gateway echoes back) instead of on `payment_id` makes the race a non-event. REQ-17. |
| 2 | Extend the seat reservation to `now() + PAYMENT_WINDOW_SECONDS` (default **90 s**) and move the seats to `PAYMENT_PENDING`. | The callback is delayed 2–15 s **always** (REQ-15). Without this a short `HOLD_TTL_SECONDS` would release seats mid-payment and oversell them. |
| 3 | **Forward `X-Mock-Mode` and `X-Mock-Force` verbatim** to the gateway's `/charge`. | REQ-17: *"Judges will use the force headers."* They send them to **us**. If we drop them, every judge test collapses to the default random behaviour. **This is the single easiest requirement to miss.** |
| 4 | Return **202 within ~200 ms**, never blocking on the gateway. Client timeout on `/charge` is **5 s**; on timeout the payment stays `PENDING` and 202 is still returned. | REQ-12 verbatim. |

Gateway call: `POST {GATEWAY_BASE_URL}/charge` with
`{ "amount": <int minor-unit-free>, "currency": "BDT", "booking_ref": "bk_…",
"callback_url": "{GATEWAY_CALLBACK_URL}" }`.

Errors: `409 OTP_NOT_VERIFIED` · `409 BOOKING_NOT_PAYABLE` (already `CONFIRMED`/`PAYMENT_PENDING`) ·
`410 HOLD_EXPIRED` · `503 GATEWAY_UNAVAILABLE` (breaker open, or `/charge` 5xx'd — **seats stay
reserved**, client may retry).

### `GET /bookings/{ref}` → 200 — the poll target

```json
{ "booking_ref": "bk_01K2Q…",
  "status": "CONFIRMED",
  "payment": { "payment_id": "pay_xyz", "status": "SUCCEEDED", "amount": "450.00",
               "currency": "BDT", "settled_at": "2026-08-08T12:04:19Z" },
  "show": { "id": 1, "starts_at": "2026-08-09T00:00:00Z",
            "movie": "Spider-Man: Brand New Day", "theatre": "Star Cineplex Chattogram",
            "screen": "Screen 1" },
  "seats": ["F12"],
  "total_amount": "450.00", "currency": "BDT",
  "phone_masked": "+88017*****01",
  "ticket_code": "CS-1-F12-8QW1",
  "created_at": "2026-08-08T12:03:31Z",
  "confirmed_at": "2026-08-08T12:04:19Z" }
```

**Booking status machine** — the transition map lives in `services/booking.py`; an illegal
transition raises `InvalidStateError` → `409`.

```
PENDING_OTP ──otp verified──▶ OTP_VERIFIED ──/pay──▶ PAYMENT_PENDING ─┬─ SUCCEEDED ─▶ CONFIRMED
     │                             │                                  ├─ FAILED    ─▶ FAILED
     └──────── reservation lapses ─┴──────────────────────────────────┴─ (no callback, window
                                   ▼                                      lapses)     ─▶ EXPIRED
                                EXPIRED                          CONFIRMED ─REFUNDED─▶ REFUNDED
```

`EXPIRED` and `FAILED` both release the seats back to `AVAILABLE`.
`phone_masked` — the full number is **never** returned and **never** logged
(`11-observability.md` §3).

### ★ `POST /payments/callback` → **always 200** (REQ-13, REQ-14, REQ-16)

Called by the gateway container over the internal Docker network. Not publicly routable.

```jsonc
// request (gateway → us)
{ "event_id": "evt_001", "payment_id": "pay_xyz", "booking_ref": "bk_001",
  "status": "SUCCEEDED", "amount": 450 }

// 200 — first delivery
{ "received": true, "duplicate": false }

// 200 — second delivery of the SAME event_id  (8% of the time, by spec)
{ "received": true, "duplicate": true }

// 200 — unparseable or unknown booking. Logged at WARNING. Still 200.
{ "received": true, "accepted": false, "reason": "unknown_booking_ref" }
```

**This handler returns 200 for every input.** `problem_statement.md:116` — *"A non-200 tells the
gateway that delivery failed, and it will retry forever."* That includes malformed bodies, unknown
refs, and **our own internal exceptions**: the catch-all is inside the handler, not outside it.
An anomaly is a `WARNING` log line with the `event_id`, never a non-200.

**Idempotency, in three steps (REQ-14):**

| Step | Action | Guarantees |
| :-: | :--- | :--- |
| 1 | `INSERT INTO gateway_events (event_id, …) ON CONFLICT (event_id) DO NOTHING` → **commit** | `UNIQUE(event_id)` is the enforcement. 0 rows inserted ⇒ already seen ⇒ return `duplicate: true` and stop. The DB decides, not application logic. |
| 2 | Apply the transition in a **second** transaction, guarded on the booking's current state | Even if step 1's uniqueness were bypassed, `PAYMENT_PENDING → CONFIRMED` fires once; a second attempt hits `CONFIRMED` and is a no-op. **Two independent defences.** |
| 3 | `UPDATE gateway_events SET processed_at = now()` | Any row with `processed_at IS NULL` older than 60 s is retried by the reconciliation sweep — so a crash between 1 and 2 self-heals (REQ-44 *"pending payments recover"*). |

Effects by `status`:

| `status` | Booking | Seats | Payment |
| :--- | :--- | :--- | :--- |
| `SUCCEEDED` | → `CONFIRMED`, `confirmed_at` set, `ticket_code` generated | `PAYMENT_PENDING` → `BOOKED`, `reserved_until` cleared | → `SUCCEEDED` |
| `FAILED` | → `FAILED` | released → `AVAILABLE` | → `FAILED` |
| `REFUNDED` | → `REFUNDED` | released → `AVAILABLE` | → `REFUNDED` |

> We never *initiate* a refund — nothing in the problem requires it (REQ-26) — but the gateway can
> send `REFUNDED` (REQ-16), so we handle it. `POST /refund` is in `02-architecture.md` DEFERRED.

---

## §6 — Authentication and authorization

**There is no login.** No accounts, no passwords, no roles.
`problem_statement.md` never mentions a user account; `:50` states *"You do not need a cinema admin
portal."* The only actor is a customer. See `RECONCILIATION.md` DEL-01 … DEL-06 for what this
removed and how to reinstate it.

**Identity (INF-07):** a **phone number**, supplied on `POST /holds` and carried through the
booking. It is what the OTP is sent to, and it is what makes REQ-39's *"a different user"*
meaningful.

**Access control today (capability URLs):** `hold_id` and `booking_ref` are opaque
`secrets.token_urlsafe(16)` values — **128 bits of entropy**, URL-safe, not enumerable — behind a
`hld_` / `bk_` prefix. Possessing the reference *is* the authorization to read or act on it. This is a deliberate, stated trade-off, not an omission:

| | |
| :--- | :--- |
| **Why it is acceptable here** | Sequential integer IDs would be trivially enumerable (IDOR); ULIDs are not. There is no cross-customer data to reach even with a valid ref beyond that one booking. |
| **What it costs** | A leaked URL (browser history, a screenshot, a proxy log) grants access. Real systems bind the reference to a verified session. |
| **How we would fix it** | INF-08, below. |

**INF-08 — OTP session token (SHOULD, REQ-48 bonus).** Build only after every MUST is deployed
and green:
`POST /bookings/{ref}/otp/verify` also returns `{"session_token": "…", "expires_in": 900}`;
subsequent `/pay` and `GET /bookings/{ref}` accept `Authorization: Bearer <token>`; the token is
bound to `(phone, booking_ref)` and lives 15 minutes. Signed with `SESSION_SECRET`, stateless.
**This is authentication by possession factor**, delivered through the gateway we are already
required to integrate — it costs ~25 minutes because the OTP machinery already exists.

Rulebook §9.3 asks for demo credentials *"if the application requires authentication"*. It does
not. The README lists **seeded demo phone numbers** instead.

---

## §7 — Health and readiness

### `GET /health` — liveness (REQ-18, verbatim requirement)

```http
GET /health   →   200 OK
{ "status": "ok", "service": "cinemaseat-api", "version": "0.1.0",
  "timestamp": "2026-08-08T12:03:11Z" }
```

- **Touches nothing.** No database, no gateway, no disk. Target **< 10 ms**, hard requirement < 1 s.
- **Stays 200 when the gateway container is stopped.** `problem_statement.md:141`, verbatim.
  This is asserted by `tests/smoke.sh` and demoed live (`13-demo-script.md` step 7).
- Consumed by the Docker `HEALTHCHECK` and `depends_on: service_healthy`.
- **Why it must not check dependencies:** a liveness probe that touches the DB will restart a
  perfectly good API container during a 5-second Postgres blip, turning a blip into an outage.
  `11-observability.md` §4.

### `GET /ready` — readiness

```http
GET /ready   →   200 OK
{ "status": "ready",
  "checks": {
    "database":   { "status": "ok", "latency_ms": 3 },
    "migrations": { "status": "ok", "revision": "a1b2c3d4e5f6" },
    "gateway":    { "status": "ok", "latency_ms": 12 }
  },
  "timestamp": "2026-08-08T12:03:11Z" }
```

**Gateway down — 200, `degraded`. Deliberately not 503:**
```http
GET /ready   →   200 OK
{ "status": "degraded",
  "checks": {
    "database":   { "status": "ok", "latency_ms": 3 },
    "migrations": { "status": "ok", "revision": "a1b2c3d4e5f6" },
    "gateway":    { "status": "unavailable", "error": "connection refused", "breaker": "open" }
  },
  "timestamp": "2026-08-08T12:03:11Z" }
```
> Browsing, seat maps and holds all still work without the gateway (REQ-44), so the instance **is**
> ready to serve traffic. Reporting 503 here would be a lie, and it would make our own smoke test
> fail during the fault-isolation demo. `status: "degraded"` is the honest signal, and explaining
> why is a 20-second answer worth having.

**Database down — 503:**
```http
GET /ready   →   503 Service Unavailable
{ "status": "not_ready",
  "checks": { "database": { "status": "error", "error": "connection refused" } },
  "error": { "code": "DEPENDENCY_UNAVAILABLE",
             "message": "One or more required dependencies are unavailable.",
             "details": null, "request_id": "…" } }
```

- DB check is `SELECT 1` with a **2-second timeout**; gateway check is `GET {GATEWAY}/health` with a
  **1-second timeout** and is never allowed to block the response.
- Migration check compares `alembic_version.version_num` to the code's head — catches the
  "deployed but forgot to migrate" failure, which is exactly what would break a demo.
- `error` never contains the DSN (it embeds the password).

---

## §8 — Rate limiting

Two layers (INF-09, REQ-48). Nginx sheds cheap floods; the app enforces semantics.

| Scope | Limit | Where | Why |
| :--- | :--- | :--- | :--- |
| Global per IP | 30 r/s, burst 60 | Nginx `zone=api_zone` | Baseline |
| `POST /holds` | 5 r/s per IP, burst 20 | Nginx `zone=hold_zone` | ⚠️ **Must not be so tight that Scenario A's 100-request burst is shed by Nginx instead of rejected by the database.** The burst allowance is deliberately generous, and the load generator runs from **one** IP. See `10-testing-and-load.md` §4.2. |
| `POST /bookings/{ref}/otp` | 1 per 30 s **per booking_ref** | app | Gateway abuse + cost |
| `POST /bookings/{ref}/otp/verify` | 5 attempts **per booking_ref**, then locked | app | OTP brute force |
| `POST /payments/callback` | **unlimited** | — | Rate-limiting the gateway causes infinite retries (REQ-13). Not routed publicly anyway. |

`429` responses carry `Retry-After` and the `RATE_LIMITED` envelope.

> **Scenario A hazard, stated once and loudly:** if Nginx returns 429 to 90 of the 100 burst
> requests, we have proved that Nginx works, not that our seat claim is correct. The burst must
> reach the application. Verify by checking that the Scenario A report shows
> `rejected_seat_unavailable == 99` and `rejected_rate_limited == 0`.

---

## §9 — Contract test hooks

Asserted by `tests/smoke.sh` and driven by `tests/load/`. **When a path changes here, both scripts
change in the same commit.**

| Check | Endpoint | Expected |
| :--- | :--- | :--- |
| Liveness | `GET /health` | 200, `status == "ok"` |
| Liveness with gateway down | `GET /health` | **still 200** (REQ-18) |
| Readiness | `GET /ready` | 200, `checks.database.status == "ok"` |
| TLS | any | valid cert, not expired, CN/SAN matches the domain |
| Redirect | `http://…/health` | 301 → `https://` |
| Headers | any | HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, CSP |
| Catalogue | `GET /movies` | 200, `items` non-empty (proves REQ-10 seeding ran) |
| Seat map | `GET /shows/1/seats` | 200, `seats[]` non-empty, `hold_ttl_seconds` present |
| TTL from env | `GET /shows/1/seats` | `hold_ttl_seconds` equals `$HOLD_TTL_SECONDS` (REQ-19) |
| Hold happy path | `POST /holds` | 201, `hold_id`, `expires_at` in the future |
| **Hold conflict** | `POST /holds` same seat again | **409 `SEAT_UNAVAILABLE`** |
| Validation | `POST /holds` `{}` | 422 `VALIDATION_ERROR` with `details[]` |
| Seat-map reflects the hold | `GET /shows/1/seats` | that seat now `HELD` with `held_until` |
| Unknown route | `GET /definitely-not-real` | standard envelope, `NOT_FOUND` |
| Request ID | any | `X-Request-ID` response header **and** `error.request_id` |
| Callback not public | `POST /payments/callback` from the internet | **not** 200-JSON — must not be routed |
| Raw ports closed | `:8000`, `:5432`, `:9000` from the internet | connection fails |

---

## §10 — CHANGELOG (pre-reveal draft → this document)

Diff for review. Full rationale per line in `RECONCILIATION.md` §1c.

### Renamed / moved

| Old | New | Why |
| :--- | :--- | :--- |
| `/api/v1/health` | **`/health`** | REQ-18 quotes the literal path |
| `/api/v1/ready` | **`/ready`** | consistency |
| `/api/v1/docs`, `/api/v1/openapi.json` | **`/docs`**, **`/openapi.json`** | consistency |
| `/api/v1/<resource>` (template) | *deleted* | never a real path |

### Added (15 endpoints, all of them)

| Endpoint | REQ |
| :--- | :--- |
| `GET /movies` | REQ-01, REQ-10 |
| `GET /theatres` | REQ-01, REQ-10 |
| `GET /shows` | REQ-01 |
| **`GET /shows/{show_id}/seats`** | REQ-02, REQ-20 |
| **`POST /holds`** | REQ-03, REQ-07, REQ-20 |
| `GET /holds/{hold_id}` | REQ-06, REQ-39 |
| `POST /bookings` | REQ-05 |
| `GET /bookings/{booking_ref}` | REQ-05, REQ-15 |
| `POST /bookings/{ref}/otp` | REQ-08 |
| `POST /bookings/{ref}/otp/verify` | REQ-08 |
| **`POST /bookings/{ref}/pay`** | REQ-04, REQ-12, REQ-17 |
| **`POST /payments/callback`** | REQ-13, REQ-14, REQ-16 |

### Removed

| Removed | Why |
| :--- | :--- |
| `POST /auth/register` · `POST /auth/login` · `GET /auth/me` | No accounts exist in the problem (DEL-01, DEL-05) |
| Entire §5 auth flow (JWT HS256, 60-min expiry, bcrypt 12, password policy, dummy-hash timing) | DEL-02, DEL-03 |
| `GET/POST/PATCH/DELETE /<resource>` template | Placeholder for a domain that is not CRUD (DEL-17) |
| Error codes `INVALID_CREDENTIALS`, `TOKEN_EXPIRED`, `FORBIDDEN` | Subjects deleted (DEL-14) |
| Universal offset pagination on every list | Seeded catalogue is tiny; retained only as a seat-map size cap (DEL-18) |
| `PATCH` / `DELETE` verbs | REQ-26 — nothing requires them |

### Added to the error enum

`SEAT_UNAVAILABLE` (409) · `HOLD_EXPIRED` (410) · `HOLD_NOT_ACTIVE` (409) ·
`BOOKING_NOT_PAYABLE` (409) · `OTP_NOT_VERIFIED` (409) · `OTP_INVALID` (400) ·
`TOO_MANY_SEATS` (422) · `GATEWAY_UNAVAILABLE` (503)

### Structural

- Base URL loses its path prefix entirely; Nginx now routes by a first-segment allow-list.
- `/ready` gains a **`degraded`** state so a gateway outage is not reported as unreadiness.
- `POST /payments/callback` is defined as **internal-network-only** and **always-200**.
- §4 exists because REQ-20 makes two request/response pairs a **judge-facing deliverable**.

---

**Cross-links:** requirements → `01-problem-analysis.md` · tables and the atomic claim statement →
`03-data-model.md` · routers/services → `05-backend-plan.md` · frontend client →
`06-frontend-plan.md` · Nginx routing → `08-deployment-runbook.md` §5.3 · assertions →
`10-testing-and-load.md` · design → `02-architecture.md`
