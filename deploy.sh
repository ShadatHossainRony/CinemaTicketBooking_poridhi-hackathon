#!/usr/bin/env bash
#
# deploy.sh — the ONE deploy path. Human and CD run this.
#
# Pulls the latest image, migrates, restarts the API replicas one at a time
# (so the app stays reachable — REQ-36), and runs the smoke test.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/cinemaseat}"
BRANCH="${BRANCH:-main}"
LOG_DIR="${LOG_DIR:-/var/log/cinemaseat}"
DEPLOY_USER="${DEPLOY_USER:-cinemaseat}"

mkdir -p "$LOG_DIR"
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_DIR/deploy.log"; }

log "deploy start (branch=$BRANCH)"

cd "$REPO_DIR"

# 1. Pull latest.
log "git pull"
sudo -u "$DEPLOY_USER" git pull --ff-only origin "$BRANCH" || { log "git pull failed"; exit 1; }

# 2. Build & bring up.
log "docker compose pull"
docker compose pull gateway || true

log "docker compose build api api2"
docker compose build api api2

# 3. Run migrations + seed (one-shot).
log "docker compose up migrate"
docker compose up migrate

# 4. Restart API replicas one at a time so the app stays reachable.
log "restart api"
docker compose up -d --no-deps --force-recreate api
sleep 5
docker compose up -d --no-deps --force-recreate api2
sleep 3

# 5. Health check.
log "GET /health"
curl -fsS --max-time 10 http://127.0.0.1:8000/health || { log "/health failed"; exit 1; }

# 6. Smoke test (optional — fail loudly if BASE_URL is set).
if [[ -n "${BASE_URL:-}" ]]; then
  log "smoke test against $BASE_URL"
  BASE_URL="$BASE_URL" ./tests/smoke.sh
fi

SHA=$(git rev-parse --short HEAD)
log "deploy done (sha=$SHA)"
echo "$SHA" > "$LOG_DIR/last_deploy_sha"
