// k6-load.js — the two k6-driven proof scenarios for CinemaSeat.
//
//   ★ Scenario A (REQ-38, REQUIRED): 100 concurrent holds on ONE seat.
//       k6 run --env SCENARIO=oversell --env SEAT=F12 tests/load/k6-load.js
//
//     Scenario C (REQ-40, bonus): ramp seat-map + hold to the breakpoint.
//       k6 run --env SCENARIO=breakpoint tests/load/k6-load.js
//
// 🔴 RUN THIS FROM A LAPTOP, NOT FROM THE VM.
//    problem_statement.md:213 — "Do not run the load generator on the same machine as your
//    application. If k6 and your app compete for the same 2 vCPUs, you are measuring your load
//    tool fighting your own service."
//
// 🔴 THE MAGNITUDE IS NOT THE POINT.
//    problem_statement.md:212 — "We judge your methodology, your breakpoint, and your explanation
//    of the bottleneck. Never the raw magnitude." Scenario A's pass/fail is OVERSELL == 0.
//
// Plan, report templates and the two traps: agent/10-testing-and-load.md §4 and §6.
// Endpoints: agent/04-api-contract.md  (canonical — the API is mounted at the ROOT, no prefix).
//
// SCREENSHOT THE END-OF-RUN SUMMARY. It goes in README.md and docs/proof.md.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || 'https://poridhi-hackathon.shadathossainrony.dev';
const SCENARIO = __ENV.SCENARIO || 'oversell';   // 'oversell' (REQ-38) | 'breakpoint' (REQ-40)

// --- Scenario A ---
const SHOW_ID   = __ENV.SHOW_ID || '1';
// The contended seat. F12 is left AVAILABLE by the seeder on purpose
// (agent/03-data-model.md §8) and is the seat named in problem_statement.md:21.
const SEAT      = __ENV.SEAT || 'F12';
const BURST_VUS = parseInt(__ENV.BURST_VUS || '100', 10);

// --- Scenario C ---
const RAMP_PEAK = parseInt(__ENV.RAMP_PEAK || '200', 10);
// Seats the breakpoint run may claim. Unlike Scenario A these must NOT collide,
// or we would be measuring contention instead of capacity.
const RAMP_SHOW_ID = __ENV.RAMP_SHOW_ID || '3';

const PHONE_PREFIX = __ENV.PHONE_PREFIX || '+88017';

// ---------------------------------------------------------------------------
// Metrics
// ---------------------------------------------------------------------------

// Scenario A — these five ARE the report (problem_statement.md:195).
const holdsAttempted    = new Counter('a_requests_sent');
const holdsSucceeded    = new Counter('a_holds_succeeded');       // MUST be exactly 1
const holdsSeatConflict = new Counter('a_rejected_seat_taken');   // MUST be BURST_VUS - 1
const holdsRateLimited  = new Counter('a_rejected_rate_limited'); // MUST be 0  <-- the trap
const holdsOther        = new Counter('a_rejected_other');        // MUST be 0

// Scenario C
const seatmapLatency = new Trend('c_latency_seatmap', true);
const holdLatency    = new Trend('c_latency_hold', true);
const businessErrors = new Rate('c_business_errors');   // 409 is NOT an error here
const rampConflicts  = new Counter('c_seat_conflicts');

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------

export const options = SCENARIO === 'oversell'
  ? {
      // All BURST_VUS are initialised before the scenario starts, then released
      // together — as close to a single burst as k6 gets. See §4.2 for why the
      // correctness claim does not depend on perfect simultaneity.
      scenarios: {
        oversell: {
          executor: 'per-vu-iterations',
          vus: BURST_VUS,
          iterations: 1,
          maxDuration: '60s',
        },
      },
      thresholds: {
        // The ONLY pass/fail that matters. problem_statement.md:196.
        'a_holds_succeeded':      ['count==1'],
        'a_rejected_rate_limited': ['count==0'],   // Nginx must not shed the burst
        'a_rejected_other':        ['count==0'],
      },
      summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
      noConnectionReuse: false,
      userAgent: 'k6-cinemaseat/1.0 (scenario-a oversell)',
    }
  : {
      scenarios: {
        breakpoint: {
          executor: 'ramping-vus',
          startVUs: 5,
          stages: [
            { duration: '30s', target: 5 },              // warm-up: fill the pool
            { duration: '2m',  target: RAMP_PEAK / 2 },  // ramp: WHERE DOES p95 TURN UP?
            { duration: '1m',  target: RAMP_PEAK / 2 },  // hold: is the knee real?
            { duration: '1m',  target: RAMP_PEAK },      // push: WHERE DO ERRORS BEGIN?
            { duration: '30s', target: 0 },              // ramp-down: does it recover?
          ],
          gracefulRampDown: '10s',
        },
      },
      // Deliberately NO pass/fail thresholds on latency or throughput.
      // REQ-41: raw magnitude is never compared. We are looking for the KNEE.
      thresholds: {
        'c_business_errors': ['rate<0.05'],   // sanity only: this is not the deliverable
      },
      summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
      noConnectionReuse: false,
      userAgent: 'k6-cinemaseat/1.0 (scenario-c breakpoint)',
    };

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

export function setup() {
  console.log(`Target: ${BASE_URL}   scenario: ${SCENARIO}`);

  const health = http.get(`${BASE_URL}/health`, { timeout: '10s' });
  if (health.status !== 200) {
    throw new Error(
      `Target is not healthy before the run: /health returned ${health.status}. ` +
      `Fix the deployment first (agent/08-deployment-runbook.md §7).`);
  }

  const showId = SCENARIO === 'oversell' ? SHOW_ID : RAMP_SHOW_ID;
  const map = http.get(`${BASE_URL}/shows/${showId}/seats`, { timeout: '10s' });
  if (map.status !== 200) {
    throw new Error(`Seat map for show ${showId} returned ${map.status}. Is the catalogue seeded?`);
  }

  const body = map.json();
  const free = (body.seats || []).filter((s) => s.status === 'AVAILABLE').map((s) => s.seat);
  console.log(`hold_ttl_seconds=${body.hold_ttl_seconds}  available=${free.length}/${(body.seats || []).length}`);

  if (SCENARIO === 'oversell') {
    const target = (body.seats || []).find((s) => s.seat === SEAT);
    if (!target) {
      throw new Error(`Seat ${SEAT} does not exist on show ${showId}.`);
    }
    if (target.status !== 'AVAILABLE') {
      // Refusing to run is correct: starting from a HELD seat would produce
      // 0 successes / 100 conflicts and look like a pass while proving nothing.
      throw new Error(
        `Seat ${SEAT} is '${target.status}', not AVAILABLE. Reset with ` +
        `\`docker compose exec api python -m app.seed --reset-shows\` or wait for the TTL.`);
    }
    console.log(`Scenario A: ${BURST_VUS} concurrent holds on show ${showId} seat ${SEAT}`);
  }

  return { showId, freeSeats: free };
}

// ---------------------------------------------------------------------------
// Scenario A — one seat, many buyers   (REQ-38)
// ---------------------------------------------------------------------------

function oversell(data) {
  const payload = JSON.stringify({
    show_id: parseInt(data.showId, 10),
    seats: [SEAT],
    phone: `${PHONE_PREFIX}${String(1000000 + __VU).slice(-7)}`,
  });

  holdsAttempted.add(1);
  const res = http.post(`${BASE_URL}/holds`, payload, {
    headers: { 'Content-Type': 'application/json' },
    tags: { endpoint: 'hold_contended' },
  });

  let code = '';
  try { code = res.json('error.code') || ''; } catch (e) { code = ''; }

  if (res.status === 201) {
    holdsSucceeded.add(1);
  } else if (res.status === 409 && code === 'SEAT_UNAVAILABLE') {
    holdsSeatConflict.add(1);
  } else if (res.status === 429) {
    // 🔴 If this fires, the run measured Nginx, not the seat claim.
    // Retune hold_zone: agent/08-deployment-runbook.md §5.3.
    holdsRateLimited.add(1);
  } else {
    holdsOther.add(1);
    console.error(`unexpected: ${res.status} ${String(res.body).slice(0, 200)}`);
  }

  check(res, {
    'hold is 201 or 409': (r) => r.status === 201 || r.status === 409,
    'rejection is SEAT_UNAVAILABLE': (r) => r.status !== 409 || code === 'SEAT_UNAVAILABLE',
    'no 5xx': (r) => r.status < 500,
  });
}

// ---------------------------------------------------------------------------
// Scenario C — find the breakpoint   (REQ-40)
// 70% seat map (read) / 30% hold (write), spread across seats so we measure
// CAPACITY, not contention. Contention is Scenario A's job.
// ---------------------------------------------------------------------------

function breakpoint(data) {
  if (Math.random() < 0.70) {
    const res = http.get(`${BASE_URL}/shows/${data.showId}/seats`, { tags: { endpoint: 'seatmap' } });
    seatmapLatency.add(res.timings.duration);
    businessErrors.add(res.status >= 400);
    check(res, {
      'seatmap 200': (r) => r.status === 200,
      'seatmap has seats': (r) => r.status !== 200 || Array.isArray(r.json('seats')),
    });
  } else {
    const pool = data.freeSeats;
    if (!pool || pool.length === 0) {
      // Every seat is held. Keep exercising the read path rather than emitting noise.
      const res = http.get(`${BASE_URL}/shows/${data.showId}/seats`, { tags: { endpoint: 'seatmap' } });
      seatmapLatency.add(res.timings.duration);
      sleep(0.3);
      return;
    }
    const seat = pool[Math.floor(Math.random() * pool.length)];
    const payload = JSON.stringify({
      show_id: parseInt(data.showId, 10),
      seats: [seat],
      phone: `${PHONE_PREFIX}${String(2000000 + __VU).slice(-7)}`,
    });
    const res = http.post(`${BASE_URL}/holds`, payload, {
      headers: { 'Content-Type': 'application/json' },
      tags: { endpoint: 'hold' },
    });
    holdLatency.add(res.timings.duration);

    // 409 means another VU took that seat first — expected and correct, not an error.
    if (res.status === 409) rampConflicts.add(1);
    businessErrors.add(res.status >= 400 && res.status !== 409 && res.status !== 429);

    check(res, {
      'hold 201/409/429': (r) => [201, 409, 429].includes(r.status),
      'no 5xx': (r) => r.status < 500,
    });
  }

  sleep(Math.random() * 0.5 + 0.2);
}

// ---------------------------------------------------------------------------

export default function (data) {
  if (SCENARIO === 'oversell') oversell(data);
  else breakpoint(data);
}

// ---------------------------------------------------------------------------
// Teardown
// ---------------------------------------------------------------------------

export function teardown() {
  console.log('');
  if (SCENARIO === 'oversell') {
    console.log('SCENARIO A COMPLETE — now verify at the DATABASE, not just here:');
    console.log('  (on the VM)');
    console.log("  docker compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB -c \\");
    console.log(`    "SELECT status, count(*) FROM show_seats WHERE show_id=${SHOW_ID} AND seat_label='${SEAT}' GROUP BY status;"`);
    console.log('    expected: HELD | 1');
    console.log("  docker compose exec db psql -U $POSTGRES_USER -d $POSTGRES_DB -c \\");
    console.log(`    "SELECT count(*) FROM holds WHERE show_id=${SHOW_ID} AND created_at > now() - interval '5 minutes';"`);
    console.log('    expected: 1   <-- THIS IS THE OVERSELL NUMBER');
    console.log('');
    console.log('  Then re-fetch the seat map, as problem_statement.md:196 instructs:');
    console.log(`    curl -s ${BASE_URL}/shows/${SHOW_ID}/seats | grep -o '"seat": "${SEAT}"[^}]*'`);
    console.log('');
    console.log('  Report table: agent/10-testing-and-load.md §4.5 -> README.md + docs/proof.md');
    console.log('  a_holds_succeeded MUST be 1. a_rejected_rate_limited MUST be 0.');
  } else {
    console.log('SCENARIO C COMPLETE — the numbers are not the deliverable, the explanation is.');
    console.log('  1. At what VU count did p95 turn upward?');
    console.log('  2. At what VU count did errors begin, and which error?');
    console.log('  3. What was the bottleneck, and what is your evidence?');
    console.log('     (pg_stat_activity pegged? docker stats CPU? Nginx upstream timeouts?)');
    console.log('  Fill agent/10-testing-and-load.md §6.5 and screenshot this summary.');
  }
  console.log('');
}
