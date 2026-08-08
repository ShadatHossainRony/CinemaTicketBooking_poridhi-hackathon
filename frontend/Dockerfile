# ---------- stage 1: build ----------
# Optimised for a fresh clone: no `package-lock.json` is required.
# We always run `npm install` (which is reproducible-enough for a
# hackathon and produces a lockfile as a side effect). If a lockfile
# is committed later, switch the `npm install` to `npm ci`.

FROM node:20-slim AS build

WORKDIR /app

# Copy package.json first so this layer is cached independently of the
# rest of the source. Any source change won't bust the install layer.
COPY package.json ./
# Bring a lockfile along if one is committed (`*.json*` is a no-op if
# missing on BuildKit / Docker 23+).
COPY package-lock.json* ./

# Always run `npm install`. This is robust whether or not a lockfile
# is present: with one, it uses it; without one, it resolves the
# ranges in package.json and writes a fresh lockfile (so a subsequent
# `npm ci` works too).
#
# `--no-audit --no-fund` skp the noisy network calls.
# `--loglevel=warn` keeps the build log readable.
RUN npm install --no-audit --no-fund --loglevel=warn && \
    echo ">>> installed packages:" && \
    ls node_modules/.bin/vite 2>&1 && \
    echo ">>> node_modules size:" && \
    du -sh node_modules

# Now copy the rest of the source and build.
COPY . .
RUN npm run build 2>&1 | tee /tmp/build.log && \
    echo ">>> build log tail:" && \
    tail -20 /tmp/build.log && \
    echo ">>> build output:" && \
    ls -la /app/dist && \
    test -f /app/dist/index.html && \
    echo ">>> /app/dist/index.html present"

# ---------- stage 2: runtime — serves the built SPA ----------
# A real, tiny, well-known static file server: nginx:alpine. Runs as a
# normal `restart: unless-stopped` compose service, on par with api/api2
# — not a special-cased build-only image. Host Nginx (nginx/cinemaseat.conf)
# proxies `location /` to this container over loopback, exactly the way
# it already proxies API paths to the api/api2 upstream: the frontend is
# just another upstream now, not a directory host Nginx reads from disk.
#
# (An earlier iteration tried to avoid running a frontend container at
# all — build once, `docker cp` the artifact out, have host Nginx serve
# it from a plain directory. That's a defensible design, and it's
# documented as the alternative in agent/07-containerization.md §2 — but
# it traded a real, battle-tested reverse-proxy target for a hand-rolled
# host-bind-mount deploy step, which is exactly where the "empty
# directory forever" bug came from. Running nginx:alpine here is a
# few MB and gets a healthcheck, a restart policy, and `docker compose
# logs frontend` for free.)
FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD wget --spider -q http://localhost/ || exit 1
