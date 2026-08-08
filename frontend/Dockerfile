# ---------- stage 1: build ----------
FROM node:20-slim AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---------- stage 2: artifact carrier ----------
FROM busybox:1.36 AS artifact
COPY --from=build /app/dist /dist
# Keep the container alive so the bind-mounted /dist survives until
# deploy.sh copies it to /var/www/cinemaseat on the host.
CMD ["tail", "-f", "/dev/null"]
