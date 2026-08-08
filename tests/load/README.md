# Load Testing

Load and stress tests for the deployed system. Two equivalent tools — use whichever is available on the day.

Full plan, thresholds and interpretation guide: `agent/10-testing-and-load.md` §4–§6.

---

## Before you run anything

**1. The target must be healthy.** A load test against a broken deploy produces noise.

```bash
bash tests/smoke.sh
# expected: ALL CHECKS PASSED, exit 0
```

**2. Set the domain endpoint.** Both scripts have a `TODO:` constant at the top. Until `RESOURCE_PATH` is set, the domain scenarios fall back to `/health` and `/ready`, so the run only measures baseline framework overhead — useful, but not the real number.

```bash
export RESOURCE_PATH=/items                     # from agent/04-api-contract.md §7
export RESOURCE_CREATE_BODY='{"name":"loadtest"}'
```

**3. Open the observation terminals** (`agent/10-testing-and-load.md` §5) **before** starting the run:

```bash
docker stats                                             # T1: CPU / memory per container
docker compose logs -f api | grep -iE '"status": 5|error' # T2: application errors
watch -n2 'docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"'   # T3: connection pool
sudo tail -f /var/log/nginx/error.log                    # T4: proxy + rate limiter
```

---

## k6 (preferred)

**Install:**
```bash
# Debian/Ubuntu
sudo gpg -k && sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
  --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
  | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6

# Or, no install at all:
docker run --rm -i grafana/k6 run - < tests/load/k6-load.js
```

**Run:**
```bash
k6 run tests/load/k6-load.js                                   # against production (default)
BASE_URL=http://localhost:8000 k6 run tests/load/k6-load.js    # against local
k6 run --summary-export=k6-results.json tests/load/k6-load.js  # machine-readable output too
k6 run --vus 5 --duration 30s tests/load/k6-load.js            # quick sanity run, ignores stages
```

**Profile** (built into the script — total ~4.5 min):

| Stage | Duration | Target VUs |
| :--- | :-: | :-: |
| Warm-up | 30 s | 5 |
| Ramp | 1 m | 25 |
| **Sustain** | **2 m** | **25** ← the reported number |
| Spike | 30 s | 60 |
| Ramp-down | 30 s | 0 |

**Exit code 0 = all thresholds passed. Non-zero = a threshold was breached** (the breached line is marked in the summary).

---

## Locust (fallback)

**Install:** `pip install locust`

**Run headless, matching the k6 ramp** (the built-in `RampProfile` shape drives the stages automatically when you omit `-u`/`-r`):
```bash
locust -f tests/load/locustfile.py --headless \
       --host https://poridhi-hackathon.shadathossainrony.dev \
       -t 5m --csv=locust-results
```

**Run with fixed users** (overrides the shape):
```bash
locust -f tests/load/locustfile.py --headless \
       --host https://poridhi-hackathon.shadathossainrony.dev \
       -u 25 -r 1 -t 4m --csv=locust-results
```

**Run with the web UI** (drive the ramp by hand, watch the live charts — good for a screenshot):
```bash
locust -f tests/load/locustfile.py --host https://poridhi-hackathon.shadathossainrony.dev
# open http://localhost:8089
```

Locust prints its own pass/fail summary against the same thresholds and sets a non-zero exit code on breach.

**CSV output:** `locust-results_stats.csv`, `locust-results_failures.csv`, `locust-results_stats_history.csv`.

---

## Reading the output

### k6 summary — the lines that matter

```
     ✓ http_req_duration..............: avg=142ms  min=38ms  med=118ms  p(90)=245ms  p(95)=310ms
     ✓ http_req_failed................: 0.24%  ✓ 12    ✗ 4988
     ✓ checks.........................: 99.76% ✓ 4988  ✗ 12
       http_reqs......................: 5000   62.1/s
       rate_limited_429...............: 43
       latency_list...................: avg=180ms  p(95)=420ms
```

| Line | What to read |
| :--- | :--- |
| `http_req_duration p(95)` | **The headline number.** Threshold < 500 ms. |
| `http_req_failed` | Error rate. Threshold < 1%. |
| `checks` | Correct status *and* correct body shape. A fast 500 is not a pass. |
| `http_reqs` … `/s` | Sustained throughput. Target ≥ 50 RPS. |
| `rate_limited_429` | **Expected, not a failure.** The rate limiter working (`agent/09-security-hardening.md` §4). |
| `latency_*` | Per-endpoint breakdown for the results table. |
| `✓` / `✗` in the left margin | Threshold pass/fail. |

### Diagnosing what you see

| Observation | Diagnosis | What to say |
| :--- | :--- | :--- |
| p95 flat as VUs rise | Below saturation | "We had headroom; the next constraint is DB connections." |
| p95 climbs linearly with VUs | Queueing at a fixed-capacity resource | "The connection pool is the ceiling — pool 5 + overflow 10." |
| Errors before latency climbs | Hard limit — pool timeout or rate limiter | "That's the limiter shedding load deliberately." |
| `docker stats` api CPU ≈ 200% | Compute-bound (2 cores) | "Two uvicorn workers on 2 vCPU. Scaling out works because the API is stateless." |
| Memory climbs and never returns | Leak or unbounded query | Investigate — usually a missing `LIMIT`, which is why `page_size` is capped at 100. |
| List slow, detail fast | Missing or unused index | `EXPLAIN ANALYZE` (`agent/03-data-model.md` §6) — look for `Seq Scan`. |
| Lots of `429` on `/auth/login` | Working as designed | "Login is bcrypt cost 12 and rate-limited to 1 r/s — that's intentional." |

---

## 📸 What to screenshot (for the pitch — `agent/14-pitch.md` slide 8)

Capture these **while the run is finishing**. They cannot be recreated afterwards.

1. **The k6 end-of-run summary** (or the Locust statistics table) — the whole block, thresholds visible.
2. **`docker stats` at peak load** — shows CPU and memory per container under pressure.
3. **The `pg_stat_activity` connection count at peak** — this is the evidence behind "the connection pool saturates first."
4. **The Locust web UI charts**, if you used it — the RPS and response-time graphs read well on a slide.
5. **`EXPLAIN ANALYZE` output** showing an `Index Scan` rather than a `Seq Scan` — proof the index plan works.

Then fill in the results table in `agent/10-testing-and-load.md` §6 and write the three conclusion sentences. Those sentences go straight into the pitch and into the answer to *"what breaks first under load?"*

---

## After the run

```bash
# Remove rows created by the load test before the demo (agent/03-data-model.md §7)
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "DELETE FROM <table> WHERE name LIKE 'k6-%' OR name LIKE 'locust-%';"

# Confirm the system recovered
bash tests/smoke.sh
docker compose ps          # expected: both (healthy)
```

**If thresholds were breached: report the real numbers.** A measured limitation with a diagnosis scores far better than a fabricated success or no data at all.
