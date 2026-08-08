# ---------- stage 1: build ----------
# This image intentionally uses a small, network-free bootstrap trick so we
# never have to commit a `package-lock.json` to the repo. We DO respect a
# checked-in lockfile if one exists (typical for production), and we fall
# back to a one-shot `npm install --package-lock-only` that produces the
# same lockfile deterministically for the given package.json range.
#
# Why: in a fresh clone `npm ci` fails with EUSAGE because there is no
# lockfile. `npm install` would work but produces a non-reproducible
# install. Generating the lockfile inside the container keeps the repo
# lean while still giving us reproducible builds.

FROM node:20-slim AS build

WORKDIR /app

# Copy package.json first. If package-lock.json is present (production
# clones), `npm ci` uses it as-is. Otherwise we generate one on the fly.
COPY package.json ./
# Match package-lock.json OR a non-existent sentinel. On Docker 23+ /
# BuildKit the wildcard COPY of a missing file is a no-op (with a warning),
# not an error, so this works whether or not the lockfile is committed.
COPY package-lock.json* ./
RUN if [ -f package-lock.json ]; then \
        echo ">>> using committed package-lock.json (npm ci)"; \
        npm ci --no-audit --no-fund; \
    else \
        echo ">>> no lockfile found — generating one (npm install --package-lock-only)"; \
        npm install --no-audit --no-fund --package-lock-only; \
        npm ci --no-audit --no-fund; \
    fi

COPY . .
RUN npm run build

# ---------- stage 2: artifact carrier ----------
FROM busybox:1.36 AS artifact
COPY --from=build /app/dist /dist
# Keep the container alive so the bind-mounted /dist survives until
# deploy.sh copies it to /var/www/cinemaseat on the host.
CMD ["tail", "-f", "/dev/null"]