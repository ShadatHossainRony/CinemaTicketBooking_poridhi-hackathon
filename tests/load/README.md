# Proof scenarios (Milestone 4)

> `problem_statement.md:190` — *"**Claims are worth nothing. Numbers are worth marks.**"*

Three scenarios. **A and B are required. C is bonus.**
Full plan, report templates and the traps: `agent/10-testing-and-load.md` §4–§6.

| | Scenario | What it proves | Tool | Status |
| :-: | :--- | :--- | :--- | :--- |
| **A** | 100 concurrent holds on **one** seat | **Oversell = 0** (REQ-38) | k6 / Locust | **REQUIRED** |
| **B** | Abandon a hold, watch it expire, another user books it | Auto-release + env-driven TTL (REQ-39) | shell + `curl` | **REQUIRED** |
| C | Ramp seat map + holds to the breakpoint | Where p95 turns, where errors begin, **why** (REQ-40) | k6 / Locust | bonus |

---

## 🔴 Two rules that override everything else here

**1. Run the generator from a laptop, never from the VM.**
> `problem_statement.md:213` — *"Do not run the load generator on the same machine as your
> application. If k6 and your app compete for the same 2 vCPUs, you are measuring your load tool
> fighting your own service."*

**2. The magnitude is not judged.**
> `problem_statement.md:212` — *"We are **not** comparing throughput numbers between teams… We judge
> your **methodology**, your **breakpoint**, and your **explanation of the bottleneck**. **Never the
> raw magnitude.** Your VM size is not your engineering."*

So there is no RPS target anywhere in this directory. Scenario A's pass/fail is `oversell == 0`;
Scenario C's deliverable is a **sentence**, not a number.

---

## Before you run anything

```bash
export HOST=https://poridhi-hackathon.shadathossainrony.dev

bash tests/smoke.sh                 # expected: ALL CHECKS PASSED, exit 0
curl -s $HOST/shows/1/seats | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print([s for s in d['seats'] if s['seat']=='F12'])"
# expected: status AVAILABLE   <-- Scenario A refuses to start otherwise, and it is right to
```

`F12` is left available by the seeder on purpose (`agent/03-data-model.md` §8) — it is the seat
named in the story on line 21 of the problem statement. If a previous run left it held, either wait
out `HOLD_TTL_SECONDS` or reset:
```bash
# on the VM
docker compose exec api python -m app.seed --reset-shows
```

**Open the four observation terminals first** (`agent/10-testing-and-load.md` §6.3) — they cannot be
recreated after the run:
```bash
docker stats                                                      # T1: CPU / memory per container
docker compose logs -f api api2 | grep -iE '"status": 5|error'    # T2: application errors
watch -n2 'docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT state, count(*) FROM pg_stat_activity GROUP BY state;"' # T3: ★ the connection pool
sudo tail -f /var/log/nginx/error.log                             # T4: proxy + rate limiter
```

---

## ★ Scenario A — one seat, many buyers (REQUIRED)

**k6** (preferred):
```bash
k6 run --env SCENARIO=oversell --env BASE_URL=$HOST --env SHOW_ID=1 --env SEAT=F12 \
       tests/load/k6-load.js
```

**Locust** (fallback — `-r 100` spawns all users in one second):
```bash
SCENARIO=oversell SEAT=F12 locust -f tests/load/locustfile.py --headless \
    --host $HOST -u 100 -r 100 -t 40s
```

**Pass/fail is encoded in the script**, so a non-zero exit means the requirement failed:

| Metric | Required |
| :--- | :--- |
| `a_holds_succeeded` | **exactly 1** |
| `a_rejected_seat_taken` | 99 |
| `a_rejected_rate_limited` | **0** |
| `a_rejected_other` | **0** |

### 🔴 If you see 429s, stop and fix Nginx before believing anything

A wall of `429` means Nginx shed the burst, so the run measured the rate limiter rather than the
seat claim. Raise `hold_zone` in `agent/08-deployment-runbook.md` §5.3 and re-run. **A "pass" with
rate-limited rejections is not a pass.**

### Then verify at the database — the API alone is not evidence

```bash
# on the VM
docker compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB -c \
  "SELECT status, count(*) FROM show_seats WHERE show_id=1 AND seat_label='F12' GROUP BY status;"
# expected: HELD | 1

docker compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB -c \
  "SELECT count(*) FROM holds WHERE show_id=1 AND created_at > now() - interval '5 minutes';"
# expected: 1      <-- 🔴 THIS IS THE OVERSELL NUMBER
```

And re-fetch the seat map, exactly as `problem_statement.md:196` instructs:
```bash
curl -s $HOST/shows/1/seats | grep -o '"seat": "F12"[^}]*'
# expected: one entry, "status": "HELD"
```

Report table → `agent/10-testing-and-load.md` §4.5 → `README.md` and `docs/proof.md`.

---

## ★ Scenario B — the abandoned hold (REQUIRED)

Not a load test: a **timed, narrated sequence**, ~40 seconds. Full script with the timestamp
harness: `agent/10-testing-and-load.md` §5.

```bash
# 1. On the VM — REQ-19 in action: no rebuild, just an env var
sed -i 's/^HOLD_TTL_SECONDS=.*/HOLD_TTL_SECONDS=20/' /opt/cinemaseat/.env
docker compose up -d api api2
curl -s $HOST/shows/2/seats | python3 -c 'import json,sys;print(json.load(sys.stdin)["hold_ttl_seconds"])'
# expected: 20     <-- proof it came from the environment

# 2. Run the sequence in agent/10-testing-and-load.md §5.2, recording every timestamp

# 3. 🔴 RESTORE THE TTL
sed -i 's/^HOLD_TTL_SECONDS=.*/HOLD_TTL_SECONDS=120/' /opt/cinemaseat/.env
docker compose up -d api api2
```

The two things the report must evidence (`:202`): the seat **returned to available**, and it was
then **successfully booked by a different user** — i.e. a different phone number.

---

## Scenario C — find the breakpoint (bonus)

**Only after A and B are written up.**

```bash
k6 run --env SCENARIO=breakpoint --env BASE_URL=$HOST tests/load/k6-load.js
```
```bash
SCENARIO=breakpoint locust -f tests/load/locustfile.py --headless --host $HOST -t 5m --csv=locust-results
# web UI (good for a ramp screenshot):
SCENARIO=breakpoint locust -f tests/load/locustfile.py --host $HOST     # http://localhost:8089
```

Profile: 5 → peak/2 (find the knee) → hold → peak (find the errors) → 0. Mix: 70% seat map,
30% holds **spread across seats** — collisions here would measure contention, which is Scenario A's
job, not capacity.

### Reading it — the explanation is the deliverable

| Observation | Diagnosis | What to say |
| :--- | :--- | :--- |
| p95 flat as users rise | Below saturation | *"We had headroom; the next constraint is the pool."* |
| **p95 climbs linearly, CPU has headroom, `pg_stat_activity` pegged** | **Queueing at the connection pool** — the likeliest outcome | *"Pool is 10 + 10 overflow per process × 4 processes = 80, against `max_connections` 100. That ceiling is deliberate."* |
| Errors before latency climbs | A hard limit — `DB_POOL_TIMEOUT` or a rate zone | *"Deliberate load-shedding. We'd rather fail fast than queue."* |
| Both API containers at ~100% CPU | Compute-bound | *"Two replicas × two workers on 2 vCPU. The API is stateless, so scaling out works — the in-process rate limiter is what doesn't."* |
| Holds degrade much faster than reads | Row-lock contention | *"That's the honest cost of enforcing correctness with a row lock. Different seats don't contend — which is exactly why A and C measure different things."* |
| Seat map slow, holds fine | Index or plan problem | `EXPLAIN ANALYZE` — look for `Seq Scan` or a `Sort` node |

---

## Install

**k6**
```bash
sudo gpg -k && sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
  --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
  | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6

# or, no install at all (still from a laptop, not the VM):
docker run --rm -i -e SCENARIO=oversell grafana/k6 run - < tests/load/k6-load.js
```

**Locust:** `pip install locust`

---

## 📸 What to screenshot

Capture these **while the run is finishing**. They cannot be recreated afterwards.

1. **The Scenario A summary** — the five counters, with `a_holds_succeeded = 1`.
2. **The `psql` output** showing `HELD | 1` and `holds = 1`. ★ This is the oversell evidence.
3. **The Scenario B terminal**, timestamps visible, `AVAILABLE` → 201 for the second phone.
4. **`docker stats` at peak** during Scenario C.
5. **The `pg_stat_activity` count at peak** — the evidence behind "the pool saturates first".
6. **`EXPLAIN ANALYZE`** showing an `Index Scan` on the seat map, not a `Seq Scan`.
7. The **Locust charts**, if you used the web UI — the ramp reads well in documentation.

---

## After the run

```bash
# Release everything the scenarios created, restore the pre-sold demo pattern
docker compose exec api python -m app.seed --reset-shows

bash tests/smoke.sh          # confirm the system recovered
docker compose ps -a         # expected: db/gateway/api/api2 healthy, migrate Exited (0)
curl -s $HOST/shows/1/seats | python3 -c \
  "import json,sys; print(json.load(sys.stdin)['summary'])"
# expected: the seeded distribution, and F12 available again for the demo
```

**If a scenario fails: report the real numbers and the diagnosis.** A measured, explained limitation
scores; a fabricated success is a `rulebook.md` §6.4 disqualification item.
**The one exception is Scenario A** — `oversell > 0` is not a limitation to report, it is a defect to
fix. Go back to `agent/12-execution-plan.md` Phase 6.
