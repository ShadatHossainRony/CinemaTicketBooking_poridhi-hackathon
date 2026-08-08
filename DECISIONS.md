# DECISIONS

Three decisions we genuinely argued about. Each lists options, the choice, the
reason, and what we gave up.

## 1. One FastAPI service, not microservices

**Options considered**

- **A. Single FastAPI service** — one process, modular layout, one Postgres.
- **B. Two services** — `booking` and `gateway-orchestrator`, talking over HTTP.
- **C. Three+ services** — split `holds`, `bookings`, `payments` further.

**Chosen: A.** A single service.

**Why.**

- The seat invariant is the one thing we are judged on. It must live in one
  place. Two services means two transactional boundaries around the same row —
  exactly the class of bug the problem is built around.
- The traffic profile is dominated by one path (hold) and one secondary path
  (callback). Splitting those into two services adds latency without removing
  any single point of failure.
- The async needs are tiny (`/charge` is fire-and-forget, expiry is a 5 s
  loop). They neither need a broker nor a worker.
- The architecture diagram is something we can draw on a whiteboard and
  defend in 90 seconds.

**What we gave up.** Genuine service isolation (a crash in payment handling
can still take down reads). Independent deployment of write paths. The
ability to scale parts independently. We admitted this is a deliberate
trade-off, not a missed requirement.

## 2. Postgres + Alembic, not Postgres + Redis for the seat lock

**Options considered**

- **A. Postgres only**, with the atomic UPDATE … RETURNING as the claim.
- **B. Postgres + Redis** with `SETNX` per seat.
- **C. Postgres + advisory lock** + per-seat Redis cache.

**Chosen: A.** Postgres only.

**Why.**

- The seat row lives in Postgres. The claim predicate reads the same row it
  writes. Postgres's row-level lock + `EvalPlanQual` is exactly what we need.
- Redis introduces a second source of truth for the one thing we are judged
  on. If the two diverge under partition, the seat is oversold.
- The atomic statement is one line of SQL; the alternative is a multi-step
  protocol across two stores with a failure mode in every step.

**What we gave up.** Sub-millisecond reads against warm cache. Pre-baked
metrics for "seats held right now". The Redis fine-grained TTL story. None
of these were required.

## 3. SQLAlchemy 2.0 + core `text()` for the claim, not full ORM

**Options considered**

- **A. SQLAlchemy 2.0 ORM for everything.**
- **B. SQLAlchemy core (`text()` + `bindparam`) for the hot path, ORM for the rest.**
- **C. Raw asyncpg.**

**Chosen: B.** Core SQL for the claim; ORM for setup.

**Why.**

- The claim is one statement. The point is to make it impossible to read
  then write by accident. A `text()` statement with `bindparam("seat_labels",
  expanding=True)` is the only path that ensures the bound parameter is
  passed as an array, not interpolated.
- For everything else, the ORM buys us migrations, type hints, and tighter
  test fixtures.
- SQLAlchemy 2.0 typed style + `Mapped[]` keeps the velocity-up the ORM
  gives without giving up the explicit SQL where it matters.

**What we gave up.** Pure ORM portability. A small amount of SQLAlchemy
magic we needed to read for the first time. We masked the raw SQL behind
a single function so no caller has to think about it.
