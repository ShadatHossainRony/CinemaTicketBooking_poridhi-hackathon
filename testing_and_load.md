# 10 — Testing and Proof

**Purpose:** what we test, what we deliberately do not, and how the **two required proof scenarios**
are executed and reported.
**Read this when:** writing tests, after every deploy (§1), and during the proof phase.
**Status:** **FINAL**, conforming to `04-api-contract.md`.

Two sentences govern this whole file:

> `problem_statement.md:168` — *"Write unit tests for your core logic, **especially the concurrency
> and duplicate-callback paths**."* (REQ-28)
>
> `problem_statement.md:190` — *"Claims are worth nothing. **Numbers are worth marks.**"*

And one that reframes everything the pre-reveal plan assumed:

> `problem_statement.md:212` — *"We are **not** comparing throughput numbers between teams… We judge
> your **methodology**, your **breakpoint**, and your **explanation of the bottleneck**. **Never the
> raw magnitude.**"* (REQ-41)

**So there is no RPS target in this document.** A big number scores nothing. The scored outputs are
**oversell = 0**, a **timeline**, and an **explanation**.

---

## §1 — Smoke test (~20 seconds, after **every** deploy)

Automated as `tests/smoke.sh`, run as `make smoke`. Run it manually once so you know what it asserts.

| # | Check | Proves |
| :-: | :--- | :--- |
| 1 | DNS resolves to the VM IP | deploy target |
| 2 | `http://` → 301 → `https://` | INF-04 |
| 3 | TLS valid, not expired, CN matches | INF-04 |
| 4 | `GET /health` → 200, `status == "ok"`, **< 1 s** | **REQ-18** |
| 5 | `GET /ready` → 200, `checks.database.status == "ok"` | INF-03 |
| 6 | Security headers: HSTS, nosniff, X-Frame, Referrer-Policy, CSP | §3 of doc 09 |
| 7 | `GET /` → 200 `text/html` | frontend deployed |
| 8 | `GET /movies` → 200, `items` **non-empty** | **REQ-10** — seeding ran |
| 9 | `GET /shows/1/seats` → 200, `seats[]` non-empty, `summary` present | **REQ-02** |
| 10 | `hold_ttl_seconds` in that response == `$HOLD_TTL_SECONDS` | ★ **REQ-19** |
| 11 | `POST /holds` → **201** with `hold_id` and a future `expires_at` | **REQ-03** |
| 12 | `POST /holds` **same seat again** → **409 `SEAT_UNAVAILABLE`** | ★ **REQ-07** |
| 13 | Seat map now shows that seat `HELD` with `held_until` | REQ-02/REQ-03 consistency |
| 14 | `POST /holds {}` → 422 `VALIDATION_ERROR` with `details[]` | REQ-61, INF-02 |
| 15 | Unknown route → standard envelope, `NOT_FOUND` | INF-02 |
| 16 | `X-Request-ID` header **and** `error.request_id` present | INF-05 |
| 17 | `POST /payments/callback` from the internet is **not** routed | ★ T-01 |
| 18 | Ports 8000 / 8001 / 9000 / 5432 unreachable from the internet | T-09, T-10 |

Exit non-zero on any failure, with a `passed / skipped / failed` summary.
**Non-zero exit = do not proceed to the next phase**, and it fails the CD workflow.

**The manual check the script cannot do:** open the site, hard-refresh, click through
browse → seat map → hold. Ten seconds; catches CSP breakage, cache staleness and bundle-URL bugs
that curl never sees.

---

## §2 — Pytest structure

```
backend/tests/
├── conftest.py
├── test_health.py                       /health (incl. gateway-down), /ready (incl. the 503 path)
├── unit/
│   ├── test_seat_claim.py           ★★ CONCURRENCY — the highest-value file in the repo
│   ├── test_hold_expiry.py          ★  REQ-06 / REQ-19
│   ├── test_callback_idempotency.py ★★ DUPLICATE CALLBACK — REQ-14
│   ├── test_booking_transitions.py     the state machine, incl. illegal transitions
│   └── test_gateway_client.py          timeout, retry, circuit breaker (respx)
└── api/
    ├── test_seatmap.py                  shape, effective status, hold_ttl echo
    ├── test_holds.py                    201 / 409 / 410 / 422 / TOO_MANY_SEATS
    └── test_payments.py             ★  callback always-200, force-header passthrough
```

### Fixtures (`conftest.py`)

| Fixture | Scope | What it does |
| :--- | :-: | :--- |
| `settings` | session | `get_settings` overridden with the test DB URL and a **short `HOLD_TTL_SECONDS`** (2 s) so expiry tests do not sleep for two minutes |
| `engine` | session | Engine against `cinemaseat_test`; `Base.metadata.create_all()` / `drop_all()` |
| `db_session` | function | Session inside a transaction, **rolled back** after each test → isolation without truncation |
| `client` | function | `TestClient(app)` with `get_db` overridden to `db_session` |
| `seeded_show` | function | One show with a small screen (2 rows × 3 seats) — enough for contention, fast to build |
| `fake_gateway` | function | `respx` router mocking `/charge`, `/otp/*`. **Unit tests only** — integration tests use the real container (REQ-08 forbids replacing it, not isolating from it) |
| `callback` | function | Helper that POSTs a well-formed gateway callback body |

### Test DB strategy

| Option | Verdict |
| :--- | :--- |
| **Separate database `cinemaseat_test` on the same Postgres container** | ✅ **Chosen.** Identical engine to production, no extra infrastructure, fast. |
| SQLite in-memory | ❌ **Disqualifying here.** No `ON CONFLICT` on partial indexes, no `TIMESTAMPTZ`, and crucially **no real row-level locking** — the concurrency tests would pass against a database that cannot exhibit the bug we are guarding. |
| testcontainers | ❌ Extra dependency and startup cost when a Postgres container is already running. |
| Transaction rollback per test | ✅ …**except** the concurrency tests, which need **real committed transactions from separate sessions**. `test_seat_claim.py` opens its own connections and cleans up explicitly. |

**In CI:** a `postgres:16-alpine` service container; `alembic upgrade head` runs before pytest, so
**every push tests the migrations** (`07-containerization.md` §8).

---

## §3 — What is worth testing

**Worth it — and the top two are quoted requirements, not our judgement:**

| Pri | Test | Why it earns points |
| :-: | :--- | :--- |
| **1** | ★★ **`test_seat_claim.py`: N concurrent claims on one seat ⇒ exactly one succeeds** — real threads/connections, real commits, asserted with `SELECT count(*) … WHERE status='HELD'` = 1 | REQ-28 names *"the concurrency… path"* explicitly. It is the literal core of the problem. If one test in this repo is opened by a judge, it is this one. |
| **2** | ★★ **`test_callback_idempotency.py`: the same `event_id` twice ⇒ one payment row, one confirmation, `amount` counted once** | REQ-28 names *"the duplicate-callback path"* explicitly. Assert **all three** of REQ-14's clauses separately — payments count, booking `confirmed_at` unchanged, summed revenue unchanged. |
| **3** | ★ **Callback handler returns 200 even when the service layer raises** | REQ-13. Force an exception with a monkeypatched service and assert `response.status_code == 200`. Proves the requirement rather than assuming it. |
| **4** | ★ **Hold expiry: after `HOLD_TTL_SECONDS`, a second claim on the same seat succeeds** — *without running the sweeper* | Proves the lazy predicate is what guarantees correctness (`03-data-model.md` §4.3), not a background job. |
| 5 | Multi-seat hold is **all-or-nothing**: 3 seats where 1 is taken ⇒ 409 and **zero** rows changed | The rollback path. A partial hold would be silently corrupting. |
| 6 | Illegal booking transitions raise `InvalidStateError` → 409 (pay before OTP, pay twice, book an expired hold) | Proves the workflow is modelled, not implied. |
| 7 | `X-Mock-Force` is forwarded verbatim to the gateway | REQ-17. Trivially cheap, and forgetting it silently breaks every judge test. |
| 8 | Gateway timeout / 5xx ⇒ `503 GATEWAY_UNAVAILABLE`, **never 500**, and the seats stay held | REQ-15, REQ-44. |
| 9 | `/health` returns 200 with the gateway client raising | REQ-18. |
| 10 | Validation: bad seat label, bad phone, 7 seats ⇒ 422 with per-field `details` | REQ-61, INF-02. |
| 11 | **Integration: the whole path against the real gateway container** — hold → book → OTP → pay → callback → `CONFIRMED` | **REQ-29** (*"Integration tests are a plus"* — SHOULD). One test, marked `@pytest.mark.integration`, skipped unless the gateway is reachable so it never breaks CI. Run it with `X-Mock-Mode: deterministic`. |

**Not worth it under this clock — say so if asked, do not pretend it was an oversight:**

| Skipped | Why |
| :--- | :--- |
| Pydantic's own validation (does the phone regex reject `abc`?) | Testing a library, not our code. |
| SQLAlchemy CRUD in isolation | Testing the ORM. Covered transitively. |
| Catalogue read endpoints beyond one shape assertion | Seeded, read-only, no logic. |
| Frontend component tests | REQ-28 names backend paths. Business logic is in the services. |
| Coverage-percentage targets | `rulebook.md` §8: *"Depth over count."* A 90% target manufactures trivial tests. |
| Property-based / fuzz testing | High value in general, wrong ROI today. |
| Load testing in CI | Runs against the deployed URL, from off-box (REQ-42). |

**Rule of thumb:** if a test would still pass after deleting the business rule, it is not testing the
business rule.

```bash
docker compose exec api pytest -q                                    # expected: all pass
docker compose exec api pytest -q tests/unit/test_seat_claim.py -v   # the one that matters
docker compose exec api pytest -q -k "idempot or concurren"          # targeted reruns
```

---

## §4 — ★ SCENARIO A: one seat, many buyers (REQ-38 — **required**)

> `problem_statement.md:194` — *"Pick one seat on one showtime. Fire **100 concurrent hold requests
> for that exact seat** in a single burst."*
> `:196` — *"**Oversell must be zero. Exactly one request may succeed. Ninety-nine must be cleanly
> rejected.** Then fetch the seat map and confirm the seat is held once, not twice."*
> `:198` — *"A test that spreads users across many seats does not count. **The seats must fight.**"*

### 4.1 — Setup

```bash
# ⚠️ REQ-42: run this from a LAPTOP, not from the VM.
export TARGET=https://poridhi-hackathon.shadathossainrony.dev
export SHOW_ID=1
export SEAT=F12          # deliberately left AVAILABLE by the seeder (03-data-model.md §8)

# 1. Confirm the seat is genuinely free before you start.
curl -s $TARGET/shows/$SHOW_ID/seats | python3 -c "
import json,sys; d=json.load(sys.stdin)
s=[x for x in d['seats'] if x['seat']=='$SEAT'][0]; print(s)"
# expected: 'status': 'AVAILABLE'
```

### 4.2 — 🔴 Two ways to accidentally measure the wrong thing

| Trap | Symptom | Fix |
| :--- | :--- | :--- |
| **Nginx sheds the burst** | The report shows ~90 × `429` instead of 99 × `409` | `hold_zone` is `rate=50r/s burst=200 nodelay` for exactly this reason (`08-deployment-runbook.md` §5.3). **Check the report: `rejected_rate_limited` must be 0.** If it is not, you have proved Nginx works, not that the seat claim is correct. |
| **The requests are not actually concurrent** | 100 sequential requests: 1 × 201, 99 × 409 — the *same output*, but it proves nothing | k6 initialises all 100 VUs before the scenario starts, so they release together. **Also** verify with the DB: `SELECT count(*) FROM show_seats WHERE …` must be 1 *regardless* of arrival order — the correctness claim rests on the constraint, not on timing. |

### 4.3 — Run

```bash
k6 run --env SCENARIO=oversell --env BASE_URL=$TARGET \
       --env SHOW_ID=$SHOW_ID --env SEAT=$SEAT tests/load/k6-load.js
```
100 VUs × 1 iteration, all firing `POST /holds` at the same `{show_id, seat}`.

### 4.4 — Verify at the database, not just at the API

```bash
# On the VM:
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  SELECT status, count(*) FROM show_seats
   WHERE show_id = 1 AND seat_label = 'F12' GROUP BY status;"
# expected: HELD | 1        <-- exactly one row, and it is one row by construction (UNIQUE)

docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  SELECT count(*) AS holds_created FROM holds
   WHERE show_id = 1 AND created_at > now() - interval '5 minutes';"
# expected: 1              <-- 🔴 THIS IS THE OVERSELL NUMBER. Anything above 1 is a failure.
```
Then re-fetch the seat map, as the requirement literally instructs:
```bash
curl -s $TARGET/shows/1/seats | python3 -c "
import json,sys; d=json.load(sys.stdin)
print([x for x in d['seats'] if x['seat']=='F12'])
print('held total:', d['summary']['held'])"
# expected: exactly one entry, status HELD, with a held_until
```

### 4.5 — The report (copy this table into `README.md` and `docs/proof.md`)

| Field | Required by | Value |
| :--- | :--- | :--- |
| Requests sent | `:195` | 100 |
| **Successful holds (201)** | `:195` | **1** |
| **Rejections (409 `SEAT_UNAVAILABLE`)** | `:195` | **99** |
| Rejections for any other reason (429/5xx) | — | **0** ← must be zero, §4.2 |
| **Oversell count** | `:195`, `:196` | **0** |
| Seat map after the run | `:196` | `F12` appears once, `HELD` |
| `holds` rows created | our check | 1 |
| Wall time of the burst | context | _____ ms |
| p95 of the 100 requests | context | _____ ms |

**The sentence that goes with it:** *"One `UPDATE … WHERE status='AVAILABLE' … RETURNING` inside one
transaction. There is no read-then-write window, so there is nothing to race. The 99 losers
re-evaluate the predicate after the winner commits, match zero rows, and get a 409. We didn't
serialise this in application code — Postgres row locks do it, and the unique constraint on
`(show_id, seat_label)` means even a bug couldn't produce two rows."*

---

## §5 — ★ SCENARIO B: the abandoned hold (REQ-39 — **required**)

> `problem_statement.md:200` — *"Hold a seat and walk away without paying. Wait for the hold to
> expire."*
> `:202` — *"**Report:** the timeline you observed, and **evidence that the seat returned to
> available and was then successfully booked by a different user.**"*
> `:203` — *"Run this with a **short `HOLD_TTL_SECONDS`** so it completes in under a minute."*

This is a **timed, scripted, narrated sequence**, not a load test. Run it live in the demo — it takes
40 seconds and it demonstrates REQ-06 and REQ-19 at once.

### 5.1 — Arrange the short TTL (REQ-19 in action)

```bash
# On the VM. This is the whole point of the requirement: no rebuild, no code change.
cd /opt/cinemaseat
sed -i 's/^HOLD_TTL_SECONDS=.*/HOLD_TTL_SECONDS=20/' .env
docker compose up -d api api2
curl -s https://$DOMAIN/shows/2/seats | python3 -c 'import json,sys;print(json.load(sys.stdin)["hold_ttl_seconds"])'
# expected: 20        <-- proof it came from the environment
```

### 5.2 — The sequence (record every timestamp)

```bash
export T=https://poridhi-hackathon.shadathossainrony.dev
ts() { date -u '+%H:%M:%S'; }

echo "[$(ts)] T+0   user A holds C5"
curl -s -X POST $T/holds -H 'content-type: application/json' \
  -d '{"show_id":2,"seats":["C5"],"phone":"+8801700000001"}' | tee /tmp/holdA.json
HOLD_A=$(python3 -c 'import json;print(json.load(open("/tmp/holdA.json"))["hold_id"])')

echo "[$(ts)] T+2   user B tries the same seat"
curl -s -o /dev/null -w '%{http_code}\n' -X POST $T/holds -H 'content-type: application/json' \
  -d '{"show_id":2,"seats":["C5"],"phone":"+8801700000002"}'
# expected: 409   <-- the seat is genuinely locked to user A

echo "[$(ts)] T+3   seat map says HELD"
curl -s $T/shows/2/seats | grep -o '"seat": "C5"[^}]*'

echo "[$(ts)] ...waiting past the TTL (user A walks away and never pays)..."
sleep 25

echo "[$(ts)] T+28  hold status"
curl -s $T/holds/$HOLD_A | python3 -m json.tool
# expected: "status": "EXPIRED", "expires_in_seconds": 0

echo "[$(ts)] T+29  seat map says AVAILABLE again"
curl -s $T/shows/2/seats | grep -o '"seat": "C5"[^}]*'
# expected: "status": "AVAILABLE"   <-- REQ-06

echo "[$(ts)] T+30  user B (a DIFFERENT phone) now succeeds"
curl -s -X POST $T/holds -H 'content-type: application/json' \
  -d '{"show_id":2,"seats":["C5"],"phone":"+8801700000002"}' | tee /tmp/holdB.json
# expected: 201   <-- this line is literally what :202 asks to be evidenced

echo "[$(ts)] T+31  ...and books it through to CONFIRMED"
HOLD_B=$(python3 -c 'import json;print(json.load(open("/tmp/holdB.json"))["hold_id"])')
REF=$(curl -s -X POST $T/bookings -H 'content-type: application/json' \
  -d "{\"hold_id\":\"$HOLD_B\"}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["booking_ref"])')
curl -s -X POST $T/bookings/$REF/otp >/dev/null
curl -s -X POST $T/bookings/$REF/otp/verify -H 'content-type: application/json' -d '{"code":"123456"}' >/dev/null
curl -s -X POST $T/bookings/$REF/pay -H 'X-Mock-Force: success' >/dev/null
sleep 8
echo "[$(ts)] T+40  final"
curl -s $T/bookings/$REF | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["status"], d["seats"])'
# expected: CONFIRMED ['C5']
```

**Restore the TTL afterwards.** `sed -i 's/^HOLD_TTL_SECONDS=.*/HOLD_TTL_SECONDS=120/' .env && docker compose up -d api api2`

### 5.3 — The report

| T+ | Event | Observed | Evidence |
| :-: | :--- | :--- | :--- |
| 0 s | User A (`…0001`) holds `C5` | 201, `expires_at` = T+20 | `hold_id` |
| 2 s | User B (`…0002`) tries `C5` | **409 `SEAT_UNAVAILABLE`** | |
| 3 s | Seat map | `C5` = `HELD`, `held_until` = T+20 | |
| 20 s | TTL elapses; A never paid | — | |
| 28 s | `GET /holds/{A}` | **`EXPIRED`** | |
| 29 s | Seat map | **`C5` = `AVAILABLE`** ← REQ-06 | |
| 30 s | User B holds `C5` | **201** ← *"booked by a different user"* | |
| 40 s | Booking | **`CONFIRMED`** | `booking_ref`, `ticket_code` |

**The sentence that goes with it:** *"`HOLD_TTL_SECONDS` is read from the environment, so we set it
to 20 and restarted — no rebuild. And expiry isn't a cron job we hope ran: the claim statement
itself treats a hold whose `reserved_until` has passed as available, so the seat is reclaimable the
instant it expires. The sweeper only tidies up the status column so the seat map and `GET /holds`
read honestly."*

---

## §6 — SCENARIO C: find the breakpoint (REQ-40 — **bonus**)

> `:206` — *"Ramp virtual users on your **seat map and hold** endpoints until the system degrades."*
> `:207` — *"**Report:** where p95 latency turns upward, where errors begin, and your explanation of
> what the bottleneck was."*
> `:209` — *"**The explanation is what earns the marks.** The number on its own tells us nothing."*

**Only after Scenarios A and B are reported.** This is the one place a load profile belongs.

### 6.1 — Rules

| Rule | Source |
| :--- | :--- |
| Run the generator **from a laptop**, never on the VM | REQ-42, `:213` — *"you are measuring your load tool fighting your own service"* |
| Report the **breakpoint and the cause**, not the peak RPS | REQ-41 |
| Endpoints: **seat map (read) and holds (write)** — those two only | `:206` |
| Spread holds across **many** seats here (unlike Scenario A) so we measure capacity, not contention | the two scenarios ask different questions |

### 6.2 — Profile

| Stage | Duration | VUs | Purpose |
| :--- | :-: | :-: | :--- |
| Warm-up | 30 s | 5 | Fill the pool, prime the page cache |
| Ramp | 2 m | 5 → 100 | **Find where p95 turns upward** — the number `:207` asks for |
| Hold | 1 m | at the knee | Confirm the knee is real, not noise |
| Push | 1 m | → 200 | **Find where errors begin** |
| Ramp-down | 30 s | 0 | Does it recover? |

Mix: 70% `GET /shows/{id}/seats`, 30% `POST /holds` on random free seats.

```bash
k6 run --env SCENARIO=breakpoint --env BASE_URL=$TARGET tests/load/k6-load.js
```

### 6.3 — Watch these four things while it runs (`tmux`, started **before** k6)

```bash
# T1 — per-container CPU / memory
docker stats
# watch: api + api2 CPU approaching 100% each on a 2-vCPU box

# T2 — application errors only
docker compose logs -f api api2 | grep -iE '"status": 5|error|exception'
# expected during a healthy ramp: silence

# T3 — ★ the connection pool, every 2s. THIS is usually the answer.
watch -n2 'docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT state, count(*) FROM pg_stat_activity GROUP BY state;"'
# ceiling: (DB_POOL_SIZE 10 + DB_MAX_OVERFLOW 10) x 2 replicas x 2 workers = 80, vs max_connections 100
# 🔴 if this pegs at 80 while CPU has headroom, the pool IS the breakpoint — say so with the graph

# T4 — Nginx
sudo tail -f /var/log/nginx/error.log
# "limiting requests, excess:" = a rate zone engaging (must NOT happen during Scenario A)
# "upstream timed out"          = the app is saturated
```

### 6.4 — Diagnosing what you see — the explanation is the deliverable

| Observation | Diagnosis | The answer to give |
| :--- | :--- | :--- |
| p95 flat while VUs rise | Below saturation | *"We had headroom at this level; the next constraint is the connection pool."* |
| **p95 climbs linearly, CPU still has headroom, `pg_stat_activity` pegged** | **Queueing at the pool** — the most likely outcome | *"The pool saturates first: 10 + 10 overflow per process, four processes. Raising it trades memory for concurrency up to Postgres's `max_connections` of 100 — which is why 80 is deliberate, not accidental."* |
| Errors appear before latency climbs | A hard limit — `DB_POOL_TIMEOUT` or a rate zone | *"That's load-shedding, deliberately. We'd rather fail fast than queue."* |
| `api` CPU at 100% on both replicas | Compute-bound | *"Two replicas × two workers on 2 vCPU. The API is stateless, so scaling out works — the thing that doesn't scale out is our in-process rate limiter."* |
| **Holds slow down much faster than seat-map reads** | **Row-lock contention on the hot show** | *"That's the honest cost of the design: correctness is enforced by a row lock, so the same seat serialises. Different seats don't contend. That's why Scenario A and Scenario C measure different things."* |
| Seat map slow, holds fine | Index or plan problem | `EXPLAIN ANALYZE` (`03-data-model.md` §7) — look for `Seq Scan` or a `Sort` node |
| Memory climbing and not returning | Leak or an unbounded query | Investigate — the seat map is bounded by a `CHECK`, so suspect the sweeper |

### 6.5 — Results table (screenshot it — it cannot be recreated later)

| Field | Value |
| :--- | :--- |
| Date / time · deployed SHA | |
| Target (public URL) · generator location | must be **off-box** (REQ-42) |
| VM spec | ? vCPU / ? GB (Q-03) |
| **VUs at which p95 turned upward** | ← `:207` |
| **p95 before the knee / at the knee** | |
| **VUs at which errors began, and which error** | ← `:207` |
| **Diagnosed bottleneck + the evidence** | ← `:207`, **the scored field** |
| Peak `pg_stat_activity` count | vs the 80 ceiling |
| Peak `api`/`api2` CPU | |
| Recovery after ramp-down | p95 returns to baseline? |

**Three sentences to write while the numbers are fresh** — these go straight into the README and the
demo:
1. **Where it turned:** ______
2. **What broke first, and how we know:** ______
3. **What we would change:** ______

---

## §7 — The gateway-misbehaviour test matrix (REQ-15, REQ-17)

`problem_statement.md:133` — *"**Judges will use the force headers**, so every team is tested on
identical conditions rather than on luck."* Run every row before the demo. Each is one `curl`.

| `X-Mock-Force` | Expected system behaviour | Assert |
| :--- | :--- | :--- |
| `success` | Booking → `CONFIRMED` within ~15 s | `GET /bookings/{ref}` |
| `fail` | Booking → `FAILED`; **seats released to `AVAILABLE`**; no 500 anywhere | seat map + booking status |
| `duplicate` | **One** payment row, **one** `gateway_events` row, booking confirmed **once** | the SQL in `05-backend-plan.md` step 14 |
| `timeout` | `/pay` returns **`503 GATEWAY_UNAVAILABLE`**, not 500, within ~5 s; **seats stay held**; retry works | status code + seat map |
| `race` | Callback lands before `/charge` returns; booking still confirms exactly once | `payments.gateway_payment_id` backfilled, one row |
| *(none — default)* | Over ~20 attempts: ~10% `FAILED`, ~8% duplicate deliveries, all absorbed | `SELECT status, count(*) FROM payments GROUP BY 1` |
| **gateway stopped** | `/health` 200, seat map 200, `POST /holds` **201**, `/ready` = `degraded`, `/pay` = 503. **Zero 500s.** | REQ-44 — `08-deployment-runbook.md` §7 check 11 |

> `problem_statement.md:135` — *"**Deterministic mode is for building. Turn it off before you believe
> anything.**"* Run the final pre-demo pass with **no** mock headers at all.

---

## §8 — Log-based verification (INF-05)

```bash
# Every request carries one JSON line with a request id
docker compose logs api | grep '"msg":"request"' | tail -3

# Follow one booking end to end across replicas
docker compose logs api api2 | grep "$REF"
# expected: the hold, the booking, the otp, the /pay 202, and the callback minutes later

# Measure the gateway's actual callback delay (REQ-15 says 2-15s — verify it, do not assume)
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  SELECT event_id, received_at - (SELECT created_at FROM payments p WHERE p.booking_ref = g.booking_ref LIMIT 1)
      AS callback_delay FROM gateway_events g ORDER BY received_at DESC LIMIT 10;"
# A real measured distribution is a genuinely good slide, and it costs one query.

# Nothing sensitive ever logged
docker compose logs api | grep -oE '\+?8801[0-9]{9}' ; echo "exit=$?"
# expected: no output
```

---

**Cross-links:** the contract → `04-api-contract.md` · the claim statement and index plan →
`03-data-model.md` §4, §7 · scripts → `tests/load/README.md` · pool config →
`05-backend-plan.md` §4 · demo sequence → `13-demo-script.md` · security checks →
`09-security-hardening.md` §9
