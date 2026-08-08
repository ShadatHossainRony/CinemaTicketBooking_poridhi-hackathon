"""Locust equivalent of the k6 proof scenarios — the fallback for when k6 is unavailable.

Two modes, selected with the SCENARIO environment variable:

  ★ Scenario A (REQ-38, REQUIRED) — 100 concurrent holds on ONE seat, oversell must be 0::

        SCENARIO=oversell SEAT=F12 locust -f tests/load/locustfile.py --headless \\
            --host https://poridhi-hackathon.shadathossainrony.dev -u 100 -r 100 -t 40s

    (``-r 100`` spawns all 100 users in one second — as close to a burst as Locust gets.)

    Scenario C (REQ-40, bonus) — ramp seat map + holds to the breakpoint::

        SCENARIO=breakpoint locust -f tests/load/locustfile.py --headless \\
            --host https://poridhi-hackathon.shadathossainrony.dev -t 5m --csv=locust-results

    Web UI (good for a screenshot of the ramp)::

        SCENARIO=breakpoint locust -f tests/load/locustfile.py \\
            --host https://poridhi-hackathon.shadathossainrony.dev

🔴 RUN FROM A LAPTOP, NOT THE VM — problem_statement.md:213.
🔴 The magnitude is not judged (problem_statement.md:212). Scenario A's pass/fail is OVERSELL == 0.

Plan and report templates: agent/10-testing-and-load.md §4 and §6.
Endpoints: agent/04-api-contract.md (canonical — the API is at the ROOT, no prefix).
"""

from __future__ import annotations

import json
import os
import random
import threading

from locust import HttpUser, LoadTestShape, between, constant, events, task

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCENARIO = os.getenv("SCENARIO", "oversell")          # "oversell" | "breakpoint"

SHOW_ID = int(os.getenv("SHOW_ID", "1"))
SEAT = os.getenv("SEAT", "F12")                       # left AVAILABLE by the seeder on purpose
RAMP_SHOW_ID = int(os.getenv("RAMP_SHOW_ID", "3"))
RAMP_PEAK = int(os.getenv("RAMP_PEAK", "200"))
PHONE_PREFIX = os.getenv("PHONE_PREFIX", "+88017")

# ---------------------------------------------------------------------------
# Scenario A counters — these five ARE the report (problem_statement.md:195)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_counts = {
    "requests_sent": 0,
    "holds_succeeded": 0,        # MUST be exactly 1
    "rejected_seat_taken": 0,    # MUST be requests_sent - 1
    "rejected_rate_limited": 0,  # MUST be 0  <-- if not, we measured Nginx, not the seat claim
    "rejected_other": 0,         # MUST be 0
}
_free_seats: list[str] = []


def _bump(key: str) -> None:
    with _lock:
        _counts[key] += 1


# ---------------------------------------------------------------------------
# Load shape — only used by the breakpoint scenario
# ---------------------------------------------------------------------------


class BreakpointRamp(LoadTestShape):
    """5 -> peak/2 (find the knee) -> hold -> peak (find the errors) -> 0.

    Mirrors the k6 ramping-vus stages. Active only when SCENARIO=breakpoint and
    ``-u``/``-r`` are omitted.
    """

    stages = [
        {"duration": 30, "users": 5, "spawn_rate": 2},                 # warm-up
        {"duration": 150, "users": RAMP_PEAK // 2, "spawn_rate": 2},   # ramp: WHERE DOES p95 TURN?
        {"duration": 210, "users": RAMP_PEAK // 2, "spawn_rate": 5},   # hold: is the knee real?
        {"duration": 270, "users": RAMP_PEAK, "spawn_rate": 10},       # push: WHERE DO ERRORS START?
        {"duration": 300, "users": 0, "spawn_rate": 20},               # ramp-down: recovery?
    ]

    def tick(self):
        if SCENARIO != "breakpoint":
            return None
        run_time = self.get_run_time()
        for stage in self.stages:
            if run_time < stage["duration"]:
                return stage["users"], stage["spawn_rate"]
        return None


# ---------------------------------------------------------------------------
# Scenario A — one seat, many buyers  (REQ-38)
# ---------------------------------------------------------------------------


class OversellUser(HttpUser):
    """Fires exactly one hold at the contended seat, then stops."""

    wait_time = constant(0)
    fixed_count = 0  # set below when the scenario is active
    _fired = False

    @task
    def claim_the_seat(self) -> None:
        if self._fired:
            return
        self._fired = True

        _bump("requests_sent")
        payload = {
            "show_id": SHOW_ID,
            "seats": [SEAT],
            "phone": f"{PHONE_PREFIX}{random.randint(1000000, 9999999)}",
        }
        with self.client.post(
            "/holds", json=payload, name=f"POST /holds [contended {SEAT}]", catch_response=True
        ) as res:
            code = ""
            try:
                code = (res.json() or {}).get("error", {}).get("code", "")
            except Exception:
                pass

            if res.status_code == 201:
                _bump("holds_succeeded")
                res.success()
            elif res.status_code == 409 and code == "SEAT_UNAVAILABLE":
                _bump("rejected_seat_taken")
                res.success()          # a clean rejection is the CORRECT outcome for 99 of 100
            elif res.status_code == 429:
                _bump("rejected_rate_limited")
                res.failure("429 — Nginx shed the burst; retune hold_zone (§4.2)")
            else:
                _bump("rejected_other")
                res.failure(f"unexpected {res.status_code}: {res.text[:200]}")


# ---------------------------------------------------------------------------
# Scenario C — breakpoint  (REQ-40)
# 70% seat map / 30% hold, spread across seats: capacity, not contention.
# ---------------------------------------------------------------------------


class BreakpointUser(HttpUser):
    wait_time = between(0.2, 0.7)

    @task(70)
    def seat_map(self) -> None:
        with self.client.get(
            f"/shows/{RAMP_SHOW_ID}/seats", name="GET /shows/{id}/seats", catch_response=True
        ) as res:
            if res.status_code != 200:
                res.failure(f"expected 200, got {res.status_code}")
            elif "seats" not in (res.json() or {}):
                res.failure("response is not a seat map")

    @task(30)
    def hold_a_seat(self) -> None:
        if not _free_seats:
            self.seat_map()
            return
        seat = random.choice(_free_seats)
        payload = {
            "show_id": RAMP_SHOW_ID,
            "seats": [seat],
            "phone": f"{PHONE_PREFIX}{random.randint(1000000, 9999999)}",
        }
        with self.client.post("/holds", json=payload, name="POST /holds", catch_response=True) as res:
            # 409 = another user got there first. Expected and correct, not a failure.
            # 429 = the rate limiter working as designed.
            if res.status_code in (201, 409, 429):
                res.success()
            else:
                res.failure(f"expected 201/409/429, got {res.status_code}: {res.text[:200]}")


# Locust instantiates every HttpUser subclass it finds; restrict to the active scenario.
if SCENARIO == "oversell":
    BreakpointUser.abstract = True  # type: ignore[attr-defined]
else:
    OversellUser.abstract = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Pre-flight and reporting
# ---------------------------------------------------------------------------


@events.test_start.add_listener
def on_test_start(environment, **_kwargs) -> None:
    import urllib.request

    host = environment.host
    print(f"\nTarget: {host}   scenario: {SCENARIO}")

    show = SHOW_ID if SCENARIO == "oversell" else RAMP_SHOW_ID
    try:
        with urllib.request.urlopen(f"{host}/shows/{show}/seats", timeout=10) as r:
            body = json.load(r)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Pre-flight failed: cannot read the seat map for show {show}: {exc}")

    seats = body.get("seats", [])
    _free_seats.extend(s["seat"] for s in seats if s.get("status") == "AVAILABLE")
    print(f"hold_ttl_seconds={body.get('hold_ttl_seconds')}  available={len(_free_seats)}/{len(seats)}")

    if SCENARIO == "oversell":
        target = next((s for s in seats if s.get("seat") == SEAT), None)
        if target is None:
            raise SystemExit(f"Seat {SEAT} does not exist on show {show}.")
        if target.get("status") != "AVAILABLE":
            # Starting from a HELD seat would give 0 successes / N conflicts and
            # look like a pass while proving nothing.
            raise SystemExit(
                f"Seat {SEAT} is '{target['status']}', not AVAILABLE. Reset with "
                "`docker compose exec api python -m app.seed --reset-shows` or wait for the TTL."
            )
        print(f"Scenario A: all users will contend for show {show} seat {SEAT}\n")


@events.test_stop.add_listener
def on_test_stop(environment, **_kwargs) -> None:
    stats = environment.stats.total
    print("\n" + "=" * 68)

    if SCENARIO == "oversell":
        print("  SCENARIO A — ONE SEAT, MANY BUYERS   (problem_statement.md:194)")
        print("=" * 68)
        print(f"  requests sent            : {_counts['requests_sent']}")
        print(f"  successful holds         : {_counts['holds_succeeded']}      (required: exactly 1)")
        print(f"  rejected SEAT_UNAVAILABLE: {_counts['rejected_seat_taken']}")
        print(f"  rejected rate-limited    : {_counts['rejected_rate_limited']}      (required: 0)")
        print(f"  rejected other           : {_counts['rejected_other']}      (required: 0)")
        print(f"  p95 latency              : {stats.get_response_time_percentile(0.95):.0f} ms")
        print("=" * 68)

        failed = False
        if _counts["holds_succeeded"] != 1:
            print(f"  🔴 FAIL: {_counts['holds_succeeded']} holds succeeded. OVERSELL. Required: exactly 1.")
            failed = True
        if _counts["rejected_rate_limited"] != 0:
            print("  🔴 FAIL: Nginx shed part of the burst. This measured the rate limiter,")
            print("           not the seat claim. Retune hold_zone (agent/10-testing-and-load.md §4.2).")
            failed = True
        if _counts["rejected_other"] != 0:
            print("  🔴 FAIL: unexpected rejections — see the failure list above.")
            failed = True

        environment.process_exit_code = 1 if failed else 0
        if not failed:
            print("  ✅ EXACTLY ONE HOLD SUCCEEDED — OVERSELL 0")

        print("\n  Now verify at the DATABASE, not just here (on the VM):")
        print("    docker compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB -c \\")
        print(f'      "SELECT count(*) FROM holds WHERE show_id={SHOW_ID} '
              "AND created_at > now() - interval '5 minutes';\"")
        print("      expected: 1   <-- THIS IS THE OVERSELL NUMBER")
        print(f"    curl -s $HOST/shows/{SHOW_ID}/seats | grep -o '\"seat\": \"{SEAT}\"[^}}]*'")
        print("      expected: exactly one entry, status HELD")
        print("\n  Report table: agent/10-testing-and-load.md §4.5 -> README.md + docs/proof.md")
    else:
        print("  SCENARIO C — BREAKPOINT   (problem_statement.md:205)")
        print("=" * 68)
        print(f"  requests   : {stats.num_requests}")
        print(f"  failures   : {stats.num_failures}")
        print(f"  throughput : {stats.total_rps:.1f} RPS   (context only — REQ-41: never compared)")
        print(f"  p50 / p95 / p99 : {stats.median_response_time:.0f} / "
              f"{stats.get_response_time_percentile(0.95):.0f} / "
              f"{stats.get_response_time_percentile(0.99):.0f} ms")
        print("=" * 68)
        print("  The numbers above are NOT the deliverable. These three answers are:")
        print("    1. At what user count did p95 turn upward?")
        print("    2. At what user count did errors begin, and which error?")
        print("    3. What was the bottleneck, and what is the evidence?")
        print("       (pg_stat_activity pegged? docker stats CPU? Nginx upstream timeouts?)")
        print("  Fill agent/10-testing-and-load.md §6.5 and screenshot the charts.")
        environment.process_exit_code = 0

    print()
