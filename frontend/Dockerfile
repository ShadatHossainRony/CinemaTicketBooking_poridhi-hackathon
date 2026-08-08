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

# ---------- stage 2: artifact carrier ----------
# This image is NEVER started with `docker compose up` (see the
# `build-only` profile on the `frontend` service in docker-compose.yml).
# It exists purely so `docker create` + `docker cp` can pull /dist out to
# the host — see deploy.sh and `make build-frontend`.
#
# We deliberately do NOT bind-mount a host directory onto /dist here.
# A host bind mount replaces the container's target directory with
# whatever is at the host path — it does not merge with and does not
# get pre-populated from the image's baked content. That auto-populate
# behaviour is a *named volume* feature, not a bind-mount one. Wiring
# this as `volumes: - /some/host/path:/dist` (as three earlier commits on
# this branch tried, under /var/www/html and then /srv/cinemaseat-dist)
# silently discards the built assets on every container start: Nginx's
# root ends up serving an empty directory forever. See DECISIONS.md.
FROM busybox:1.36 AS artifact
COPY --from=build /app/dist /dist
# Confirm the COPY succeeded before declaring the image healthy. If
# `/dist` is empty the build fails loudly here instead of silently.
RUN test -f /dist/index.html || (echo ">>> ERROR: /dist/index.html missing after COPY" && exit 1)
CMD ["true"]
