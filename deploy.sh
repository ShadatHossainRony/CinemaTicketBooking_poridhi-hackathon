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
WEB_ROOT="${WEB_ROOT:-/var/www/cinemaseat}"

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

# 4. Build & publish the SPA. The `frontend` container's Dockerfile writes
#    its Vite dist to ./frontend/dist via a bind mount.
log "docker compose up -d --no-deps frontend"
docker compose up -d --no-deps frontend
# Wait for the build to finish.
for _ in $(seq 1 60); do
  if [[ -f frontend/dist/index.html ]]; then break; fi
  sleep 1
done
[[ -f frontend/dist/index.html ]] || { log "frontend build did not produce dist/index.html"; exit 1; }

# 5. Publish to Nginx's doc root (host-installed Nginx serves from here).
log "publish SPA to $WEB_ROOT"
mkdir -p "$WEB_ROOT"
rsync -a --delete frontend/dist/ "$WEB_ROOT/"
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
