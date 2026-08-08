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

log "docker compose build api api2 frontend"
docker compose build api api2 frontend

# 3. Run migrations + seed (one-shot).
log "docker compose up migrate"
docker compose up migrate

# 4. Rolling-restart all three user-facing services, one at a time, so
#    the app stays reachable throughout (REQ-36). The frontend is a
#    normal service now — nginx:alpine serving the built SPA — so it
#    redeploys exactly like api/api2 do. No extraction step, no rsync,
#    no host directory to keep in sync.
log "restart api"
docker compose up -d --no-deps --force-recreate api
sleep 5
log "restart api2"
docker compose up -d --no-deps --force-recreate api2
sleep 3
log "restart frontend"
docker compose up -d --no-deps --force-recreate frontend
sleep 3

# 5. Reload Nginx. Its own config didn't change on a routine deploy, but
#    this is cheap and catches the rare case where nginx/cinemaseat.conf
#    itself changed in this pull.
if command -v nginx >/dev/null 2>&1; then
  log "reload nginx"
  nginx -t && systemctl reload nginx || log "nginx reload failed (non-fatal)"
fi

# 6. Health check.
log "GET /health"
curl -fsS --max-time 10 http://127.0.0.1:8000/health || { log "/health failed"; exit 1; }
log "frontend reachable"
curl -fsS --max-time 10 -o /dev/null http://127.0.0.1:8080/ || { log "frontend container unreachable"; exit 1; }

# 7. Smoke test (optional — fail loudly if BASE_URL is set).
if [[ -n "${BASE_URL:-}" ]]; then
  log "smoke test against $BASE_URL"
  BASE_URL="$BASE_URL" ./tests/smoke.sh
fi

SHA=$(git rev-parse --short HEAD)
log "deploy done (sha=$SHA)"
echo "$SHA" > "$LOG_DIR/last_deploy_sha"
