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
WEB_ROOT="${WEB_ROOT:-/srv/cinemaseat-dist}"

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

# 4. Build & extract the SPA. The `frontend` image is build-only (never
#    started — see the `build-only` profile in docker-compose.yml). We
#    pull the built /dist out with `docker create` + `docker cp` into a
#    scratch directory, then rsync --delete it into $WEB_ROOT so stale
#    hashed asset files from a previous build don't accumulate forever.
log "docker compose build frontend"
docker compose build frontend

log "extract SPA -> $WEB_ROOT"
EXTRACT_CID="$(docker create cinemaseat-frontend:local)"
EXTRACT_TMP="$(mktemp -d)"
docker cp "${EXTRACT_CID}:/dist/." "$EXTRACT_TMP/"
docker rm "$EXTRACT_CID" >/dev/null
[[ -f "$EXTRACT_TMP/index.html" ]] || { log "frontend extraction did not produce index.html"; rm -rf "$EXTRACT_TMP"; exit 1; }

mkdir -p "$WEB_ROOT"
rsync -a --delete "$EXTRACT_TMP/" "$WEB_ROOT/"
rm -rf "$EXTRACT_TMP"
chmod -R a+rX "$WEB_ROOT"
chown -R "${DEPLOY_USER}:www-data" "$WEB_ROOT" 2>/dev/null || true

# 6. Restart API replicas one at a time so the app stays reachable.
log "restart api"
docker compose up -d --no-deps --force-recreate api
sleep 5
docker compose up -d --no-deps --force-recreate api2
sleep 3

# 7. Reload Nginx so it picks up the new SPA files (asset hashes change).
if command -v nginx >/dev/null 2>&1; then
  log "reload nginx"
  nginx -t && systemctl reload nginx || log "nginx reload failed (non-fatal)"
fi

# 8. Health check.
log "GET /health"
curl -fsS --max-time 10 http://127.0.0.1:8000/health || { log "/health failed"; exit 1; }

# 9. Smoke test (optional — fail loudly if BASE_URL is set).
if [[ -n "${BASE_URL:-}" ]]; then
  log "smoke test against $BASE_URL"
  BASE_URL="$BASE_URL" ./tests/smoke.sh
fi

SHA=$(git rev-parse --short HEAD)
log "deploy done (sha=$SHA)"
echo "$SHA" > "$LOG_DIR/last_deploy_sha"
