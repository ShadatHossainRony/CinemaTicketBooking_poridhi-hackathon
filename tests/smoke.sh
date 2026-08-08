#!/usr/bin/env bash
#
# smoke.sh — post-deploy smoke test for CinemaSeat.
#
# Runs in ~20 seconds. Run it after EVERY deploy. Exits non-zero on any failure.
# deploy.sh runs it as its last step, so a failure here fails the CD workflow.
#
#   ./tests/smoke.sh
#   BASE_URL=http://localhost:8000 ./tests/smoke.sh     # local (skips TLS/header checks)
#   VERBOSE=1 ./tests/smoke.sh                          # print response bodies
#   SMOKE_SEAT=H4 ./tests/smoke.sh                      # use a different scratch seat
#
# Requires: bash 4+, curl. Uses python3 for JSON parsing if present, and falls
# back to grep so it still works on a bare VM.
#
# What each check proves: agent/10-testing-and-load.md §1
# The contract it asserts:  agent/04-api-contract.md   (canonical)
#
# NOTE: the API is mounted at the ROOT. There is no /api or /v1 prefix.

set -uo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL="${BASE_URL:-https://poridhi-hackathon.shadathossainrony.dev}"
DOMAIN="$(printf '%s' "$BASE_URL" | sed -E 's#^https?://##; s#/.*$##; s#:.*$##')"
TIMEOUT="${TIMEOUT:-10}"
VERBOSE="${VERBOSE:-0}"

# Seeded demo data — see agent/03-data-model.md §8 and the README.
SHOW_ID="${SHOW_ID:-1}"
DEMO_PHONE="${DEMO_PHONE:-+8801700000001}"
DEMO_PHONE_2="${DEMO_PHONE_2:-+8801700000002}"

# A scratch seat for the hold/conflict checks. It is HELD for HOLD_TTL_SECONDS
# after every run and then released automatically, so repeated runs are safe.
# Deliberately NOT F12 — that seat is reserved for Scenario A and the live demo.
SMOKE_SEAT="${SMOKE_SEAT:-H4}"

# Set this to assert REQ-19 exactly (the value the stack was started with).
# Leave empty to only check that the field is present and numeric.
EXPECT_HOLD_TTL="${EXPECT_HOLD_TTL:-}"

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

if [[ -t 1 ]]; then
  RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'
  BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; NC=''
fi

PASSED=0
FAILED=0
SKIPPED=0
declare -a FAILURES=()

pass() { PASSED=$((PASSED + 1)); printf '  %s✓%s %s\n' "$GREEN" "$NC" "$1"; }
fail() {
  FAILED=$((FAILED + 1))
  FAILURES+=("$1")
  printf '  %s✗%s %s\n' "$RED" "$NC" "$1"
  [[ -n "${2:-}" ]] && printf '      %s→ %s%s\n' "$YELLOW" "$2" "$NC"
  return 0
}
skip() { SKIPPED=$((SKIPPED + 1)); printf '  %s−%s %s %s(skipped: %s)%s\n' "$YELLOW" "$NC" "$1" "$YELLOW" "${2:-}" "$NC"; }
section() { printf '\n%s%s%s\n' "$BOLD" "$1" "$NC"; }
debug() { [[ "$VERBOSE" == "1" ]] && printf '      %s%s%s\n' "$BLUE" "$1" "$NC"; return 0; }

# json_get <json> <dotted.key> — best-effort scalar extraction, python3 then grep.
json_get() {
  local json="$1" key="$2"
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$json" | python3 -c "
import json,sys
def walk(o, parts):
    for p in parts:
        if isinstance(o, dict) and p in o: o = o[p]
        else: return ''
    return o if o is not None else ''
try: print(walk(json.load(sys.stdin), '$key'.split('.')))
except Exception: print('')
" 2>/dev/null
  else
    printf '%s' "$json" | grep -oE "\"${key##*.}\"[[:space:]]*:[[:space:]]*\"?[^,\"}]*" |
      head -1 | sed -E 's/.*:[[:space:]]*"?//'
  fi
}

# seat_status <seatmap-json> <seat-label>
seat_status() {
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$1" | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
    m = [s for s in d.get('seats', []) if s.get('seat') == '$2']
    print(m[0]['status'] if m else '')
except Exception: print('')
" 2>/dev/null
  else
    printf '%s' "$1" | grep -oE "\"seat\"[^}]*\"$2\"[^}]*" | grep -oE '"status"[^,}]*' | head -1
  fi
}

require() {
  command -v "$1" >/dev/null 2>&1 || { printf '%sFATAL: %s is required but not installed.%s\n' "$RED" "$1" "$NC"; exit 2; }
}

# ---------------------------------------------------------------------------

require curl

IS_HTTPS=0
[[ "$BASE_URL" == https://* ]] && IS_HTTPS=1

printf '%s╔══════════════════════════════════════════════════════════════╗%s\n' "$BOLD" "$NC"
printf '%s║  CINEMASEAT — SMOKE TEST                                     ║%s\n' "$BOLD" "$NC"
printf '%s╚══════════════════════════════════════════════════════════════╝%s\n' "$BOLD" "$NC"
printf '  target : %s\n' "$BASE_URL"
printf '  show   : %s   scratch seat: %s\n' "$SHOW_ID" "$SMOKE_SEAT"
printf '  time   : %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# ===========================================================================
section "1. DNS & transport"
# ===========================================================================

if [[ "$IS_HTTPS" == "1" ]] && command -v dig >/dev/null 2>&1; then
  resolved="$(dig +short "$DOMAIN" 2>/dev/null | grep -E '^[0-9]+\.' | head -1)"
  if [[ -n "$resolved" ]]; then pass "DNS resolves ($DOMAIN → $resolved)"
  else fail "DNS resolves" "dig +short $DOMAIN returned no A record"; fi
else
  skip "DNS resolves" "local target or dig unavailable"
fi

if [[ "$IS_HTTPS" == "1" ]]; then
  redirect_hdrs="$(curl -sS -I -m "$TIMEOUT" "http://${DOMAIN}/health" 2>/dev/null)"
  redirect_code="$(printf '%s' "$redirect_hdrs" | head -1 | awk '{print $2}')"
  redirect_loc="$(printf '%s' "$redirect_hdrs" | grep -i '^location:' | tr -d '\r' | awk '{print $2}')"
  if [[ "$redirect_code" =~ ^30[1278]$ ]] && [[ "$redirect_loc" == https://* ]]; then
    pass "HTTP redirects to HTTPS ($redirect_code → $redirect_loc)"
  else
    fail "HTTP redirects to HTTPS" "got status='$redirect_code' location='$redirect_loc'"
  fi
else
  skip "HTTP redirects to HTTPS" "target is not https"
fi

if [[ "$IS_HTTPS" == "1" ]] && command -v openssl >/dev/null 2>&1; then
  cert="$(echo | timeout "$TIMEOUT" openssl s_client -connect "${DOMAIN}:443" -servername "$DOMAIN" 2>/dev/null |
          openssl x509 -noout -subject -dates -checkend 0 2>/dev/null)"
  if printf '%s' "$cert" | grep -q 'Certificate will not expire'; then
    not_after="$(printf '%s' "$cert" | grep '^notAfter=' | cut -d= -f2-)"
    pass "TLS certificate valid (expires $not_after)"
    if printf '%s' "$cert" | grep -qi "CN *= *$DOMAIN"; then
      pass "TLS certificate CN matches $DOMAIN"
    else
      fail "TLS certificate CN matches $DOMAIN" "$(printf '%s' "$cert" | grep '^subject')"
    fi
  else
    fail "TLS certificate valid" "expired, missing, or handshake failed"
  fi
else
  skip "TLS certificate valid" "local target or openssl unavailable"
fi

# ===========================================================================
section "2. Health & readiness    (REQ-18, INF-03)"
# ===========================================================================

health_raw="$(curl -sS -m "$TIMEOUT" -w '\n%{http_code}\n%{time_total}' "${BASE_URL}/health" 2>/dev/null)"
health_time="$(printf '%s' "$health_raw" | tail -1)"
health_code="$(printf '%s' "$health_raw" | tail -2 | head -1)"
health_json="$(printf '%s' "$health_raw" | head -n -2)"
debug "$health_json"

if [[ "$health_code" == "200" ]] && [[ "$(json_get "$health_json" status)" == "ok" ]]; then
  pass "GET /health → 200 {\"status\":\"ok\"}"
else
  fail "GET /health → 200" "status=$health_code body=$health_json"
fi

# REQ-18 verbatim: "returns 200 in under one second"
if awk "BEGIN{exit !($health_time < 1.0)}" 2>/dev/null; then
  pass "GET /health under 1s (${health_time}s)  [REQ-18]"
else
  fail "GET /health under 1s" "took ${health_time}s — REQ-18 requires < 1s"
fi

ready_body="$(curl -sS -m "$TIMEOUT" -w '\n%{http_code}' "${BASE_URL}/ready" 2>/dev/null)"
ready_code="$(printf '%s' "$ready_body" | tail -1)"
ready_json="$(printf '%s' "$ready_body" | sed '$d')"
debug "$ready_json"
ready_status="$(json_get "$ready_json" status)"

if [[ "$ready_code" == "200" ]]; then
  pass "GET /ready → 200 (status=$ready_status)"
  if [[ "$(json_get "$ready_json" checks.database.status)" == "ok" ]]; then
    pass "readiness: database check ok"
  else
    fail "readiness: database check ok" "$ready_json"
  fi
  # 'degraded' is a PASS: the gateway is down but we can still serve the core path (REQ-44).
  gw="$(json_get "$ready_json" checks.gateway.status)"
  if [[ "$gw" == "ok" ]]; then
    pass "readiness: gateway reachable"
  else
    printf '  %s!%s readiness: gateway is "%s" — /ready is degraded, NOT a failure (REQ-44)\n' "$YELLOW" "$NC" "$gw"
  fi
else
  fail "GET /ready → 200" "status=$ready_code body=$ready_json"
fi

# ===========================================================================
section "3. Security headers"
# ===========================================================================

if [[ "$IS_HTTPS" == "1" ]]; then
  headers="$(curl -sS -I -m "$TIMEOUT" "$BASE_URL" 2>/dev/null | tr '[:upper:]' '[:lower:]')"
  for h in "strict-transport-security" "x-content-type-options" "x-frame-options" \
           "referrer-policy" "content-security-policy"; do
    if printf '%s' "$headers" | grep -q "^${h}:"; then
      pass "header present: $h"
    else
      fail "header present: $h" "not returned by ${BASE_URL} (agent/09-security-hardening.md §3)"
    fi
  done
else
  skip "security headers" "local target — headers are set by Nginx"
fi

# ===========================================================================
section "4. Frontend"
# ===========================================================================

if [[ "$IS_HTTPS" == "1" ]]; then
  fe="$(curl -sS -m "$TIMEOUT" -w '\n%{http_code}' "$BASE_URL/" 2>/dev/null)"
  fe_code="$(printf '%s' "$fe" | tail -1)"
  fe_body="$(printf '%s' "$fe" | sed '$d')"
  if [[ "$fe_code" == "200" ]] && printf '%s' "$fe_body" | grep -qi '<!doctype html'; then
    pass "GET / → 200 text/html"
  else
    fail "GET / → 200 text/html" "status=$fe_code"
  fi

  # Anything outside the API allow-list must fall through to index.html.
  deep_code="$(curl -sS -o /dev/null -m "$TIMEOUT" -w '%{http_code}' "$BASE_URL/some/deep/route" 2>/dev/null)"
  if [[ "$deep_code" == "200" ]]; then
    pass "SPA fallback → 200 (index.html)"
  else
    fail "SPA fallback → 200" "got $deep_code — try_files fallback likely missing"
  fi
else
  skip "frontend checks" "local API target"
fi

# ===========================================================================
section "5. Catalogue & seat map    (REQ-01, REQ-02, REQ-10, REQ-19, REQ-20)"
# ===========================================================================

movies="$(curl -sS -m "$TIMEOUT" "${BASE_URL}/movies" 2>/dev/null)"
movie_count="$(printf '%s' "$movies" | grep -o '"id"' | wc -l | tr -d ' ')"
if [[ "$movie_count" -gt 0 ]]; then
  pass "GET /movies → non-empty catalogue ($movie_count entries)  [REQ-10 seeding ran]"
else
  fail "GET /movies → non-empty catalogue" "the migrate container did not seed — see docker compose logs migrate"
fi

seatmap="$(curl -sS -m "$TIMEOUT" -w '\n%{http_code}' "${BASE_URL}/shows/${SHOW_ID}/seats" 2>/dev/null)"
seatmap_code="$(printf '%s' "$seatmap" | tail -1)"
seatmap_json="$(printf '%s' "$seatmap" | sed '$d')"
seat_count="$(printf '%s' "$seatmap_json" | grep -o '"seat"' | wc -l | tr -d ' ')"
debug "seats in map: $seat_count"

if [[ "$seatmap_code" == "200" ]] && [[ "$seat_count" -gt 0 ]]; then
  pass "GET /shows/${SHOW_ID}/seats → 200 with $seat_count seats  [REQ-20]"
else
  fail "GET /shows/${SHOW_ID}/seats → 200" "status=$seatmap_code seats=$seat_count"
fi

ttl="$(json_get "$seatmap_json" hold_ttl_seconds)"
if [[ "$ttl" =~ ^[0-9]+$ ]]; then
  if [[ -n "$EXPECT_HOLD_TTL" ]]; then
    if [[ "$ttl" == "$EXPECT_HOLD_TTL" ]]; then
      pass "hold_ttl_seconds = $ttl, matches the environment  [REQ-19]"
    else
      fail "hold_ttl_seconds matches the environment" "response says $ttl, expected $EXPECT_HOLD_TTL — is it hardcoded?"
    fi
  else
    pass "hold_ttl_seconds present and numeric ($ttl)  [REQ-19]"
  fi
else
  fail "hold_ttl_seconds present" "missing from the seat map — judges verify REQ-19 through this field"
fi

if [[ -n "$(json_get "$seatmap_json" summary.available)" ]]; then
  pass "seat map carries a summary block"
else
  fail "seat map carries a summary block" "expected summary.{total,available,held,booked}"
fi

# ===========================================================================
section "6. The atomic hold    ★ REQ-03, REQ-07, REQ-20"
# ===========================================================================

hold_payload="$(printf '{"show_id":%s,"seats":["%s"],"phone":"%s"}' "$SHOW_ID" "$SMOKE_SEAT" "$DEMO_PHONE")"
hold_res="$(curl -sS -m "$TIMEOUT" -w '\n%{http_code}' -X POST "${BASE_URL}/holds" \
  -H 'Content-Type: application/json' -d "$hold_payload" 2>/dev/null)"
hold_code="$(printf '%s' "$hold_res" | tail -1)"
hold_json="$(printf '%s' "$hold_res" | sed '$d')"
HOLD_ID="$(json_get "$hold_json" hold_id)"
debug "$hold_json"

if [[ "$hold_code" == "201" ]] && [[ -n "$HOLD_ID" ]]; then
  pass "POST /holds ${SMOKE_SEAT} → 201 (hold_id=${HOLD_ID:0:16}…)"
elif [[ "$hold_code" == "409" ]]; then
  # A previous run's hold has not expired yet. Not a failure of the system.
  skip "POST /holds ${SMOKE_SEAT} → 201" "${SMOKE_SEAT} still held from a recent run — set SMOKE_SEAT to another seat"
else
  fail "POST /holds ${SMOKE_SEAT} → 201" "status=$hold_code body=$hold_json"
fi

if [[ -n "$(json_get "$hold_json" expires_at)" ]]; then
  pass "hold response carries expires_at"
else
  [[ "$hold_code" == "201" ]] && fail "hold response carries expires_at" "$hold_json"
fi

# ★ THE CHECK THAT MATTERS: the same seat must not be claimable twice.
if [[ -n "$HOLD_ID" ]] || [[ "$hold_code" == "409" ]]; then
  dup_payload="$(printf '{"show_id":%s,"seats":["%s"],"phone":"%s"}' "$SHOW_ID" "$SMOKE_SEAT" "$DEMO_PHONE_2")"
  dup_res="$(curl -sS -m "$TIMEOUT" -w '\n%{http_code}' -X POST "${BASE_URL}/holds" \
    -H 'Content-Type: application/json' -d "$dup_payload" 2>/dev/null)"
  dup_code="$(printf '%s' "$dup_res" | tail -1)"
  dup_json="$(printf '%s' "$dup_res" | sed '$d')"
  if [[ "$dup_code" == "409" ]] && [[ "$(json_get "$dup_json" error.code)" == "SEAT_UNAVAILABLE" ]]; then
    pass "★ second hold on ${SMOKE_SEAT} → 409 SEAT_UNAVAILABLE  [REQ-07]"
  else
    fail "★ second hold on ${SMOKE_SEAT} → 409 SEAT_UNAVAILABLE" \
         "got $dup_code / $(json_get "$dup_json" error.code) — THE CORE REQUIREMENT IS BROKEN"
  fi
else
  skip "★ second hold → 409" "no hold to conflict with"
fi

# The seat map must agree with what just happened.
seatmap2="$(curl -sS -m "$TIMEOUT" "${BASE_URL}/shows/${SHOW_ID}/seats" 2>/dev/null)"
st="$(seat_status "$seatmap2" "$SMOKE_SEAT")"
if [[ "$st" == "HELD" ]]; then
  pass "seat map now reports ${SMOKE_SEAT} as HELD"
else
  fail "seat map reports ${SMOKE_SEAT} as HELD" "got '$st' — the read and write paths disagree"
fi

if [[ -n "$HOLD_ID" ]]; then
  hold_get="$(curl -sS -m "$TIMEOUT" -w '\n%{http_code}' "${BASE_URL}/holds/${HOLD_ID}" 2>/dev/null)"
  if [[ "$(printf '%s' "$hold_get" | tail -1)" == "200" ]]; then
    pass "GET /holds/{hold_id} → 200"
  else
    fail "GET /holds/{hold_id} → 200" "$(printf '%s' "$hold_get" | sed '$d')"
  fi
else
  skip "GET /holds/{hold_id}" "no hold_id"
fi

# ===========================================================================
section "7. Error envelope contract    (INF-02)"
# ===========================================================================

val_res="$(curl -sS -m "$TIMEOUT" -w '\n%{http_code}' -X POST "${BASE_URL}/holds" \
  -H 'Content-Type: application/json' -d '{}' 2>/dev/null)"
val_code="$(printf '%s' "$val_res" | tail -1)"
val_json="$(printf '%s' "$val_res" | sed '$d')"
debug "$val_json"
if [[ "$val_code" == "422" ]] && [[ "$(json_get "$val_json" error.code)" == "VALIDATION_ERROR" ]]; then
  pass "POST /holds {} → 422 VALIDATION_ERROR (standard envelope)"
else
  fail "POST /holds {} → 422 VALIDATION_ERROR" "status=$val_code body=$val_json"
fi

nf_res="$(curl -sS -m "$TIMEOUT" "${BASE_URL}/shows/99999999/seats" 2>/dev/null)"
if [[ "$(json_get "$nf_res" error.code)" == "NOT_FOUND" ]]; then
  pass "unknown show → 404 NOT_FOUND (standard envelope)"
else
  fail "unknown show → 404 NOT_FOUND" "body=$nf_res"
fi

if [[ -n "$(json_get "$val_json" error.request_id)" ]]; then
  pass "error envelope carries request_id"
else
  fail "error envelope carries request_id" "see agent/11-observability.md §2"
fi

req_id_hdr="$(curl -sS -I -m "$TIMEOUT" "${BASE_URL}/health" 2>/dev/null | tr '[:upper:]' '[:lower:]' | grep '^x-request-id:' || true)"
if [[ -n "$req_id_hdr" ]]; then
  pass "X-Request-ID response header present"
else
  fail "X-Request-ID response header present" "RequestIDMiddleware not wired"
fi

# ===========================================================================
section "8. Network exposure    ★ T-01, T-09, T-10"
# ===========================================================================

if [[ "$IS_HTTPS" == "1" ]]; then
  # The gateway callback must NOT be routable from the internet. If it is,
  # anyone can confirm bookings for free.  agent/09-security-hardening.md T-01.
  cb="$(curl -sS -m "$TIMEOUT" -X POST "${BASE_URL}/payments/callback" \
        -H 'Content-Type: application/json' \
        -d '{"event_id":"smoke-forged","payment_id":"p","booking_ref":"bk_none","status":"SUCCEEDED","amount":1}' 2>/dev/null)"
  if [[ -n "$(json_get "$cb" received)" ]]; then
    fail "★ /payments/callback NOT publicly routable" \
         "it answered as the API — forged callbacks are possible. Fix the Nginx allow-list."
  else
    pass "★ /payments/callback not publicly routable (falls through to the SPA)"
  fi

  for port in 8000 8001 9000 5432; do
    code="$(curl -sS -o /dev/null -m 5 -w '%{http_code}' "http://${DOMAIN}:${port}/" 2>/dev/null || echo "000")"
    if [[ "$code" == "000" ]]; then
      pass "port $port not reachable from the internet"
    else
      fail "port $port not reachable from the internet" "got HTTP $code — CRITICAL exposure"
    fi
  done
else
  skip "network exposure checks" "local target"
fi

# ===========================================================================
# Summary
# ===========================================================================

printf '\n%s──────────────────────────────────────────────────────────────%s\n' "$BOLD" "$NC"
if [[ "$FAILED" -eq 0 ]]; then
  printf '%s  ALL CHECKS PASSED%s   passed=%d skipped=%d failed=0\n' "$GREEN$BOLD" "$NC" "$PASSED" "$SKIPPED"
  printf '%s──────────────────────────────────────────────────────────────%s\n' "$BOLD" "$NC"
  exit 0
else
  printf '%s  SMOKE TEST FAILED%s   passed=%d skipped=%d failed=%d\n' "$RED$BOLD" "$NC" "$PASSED" "$SKIPPED" "$FAILED"
  printf '\n  Failed checks:\n'
  for f in "${FAILURES[@]}"; do printf '    %s✗%s %s\n' "$RED" "$NC" "$f"; done
  printf '\n  Next: agent/15-troubleshooting.md\n'
  printf '%s──────────────────────────────────────────────────────────────%s\n' "$BOLD" "$NC"
  exit 1
fi
