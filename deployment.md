# 08 — Deployment Runbook

**Purpose:** get from "nothing on the VM" to "`https://poridhi-hackathon.shadathossainrony.dev` serves 200" by executing this file top to bottom.
**Read this when:** any deploy — especially the tired ones. Do not improvise; run the steps.
**Status:** **FINAL.** Assumes the GCP VM is already provisioned and running. No provisioning steps
exist here by design.

**REQ-63** (`rulebook.md` §8) — *"The application deployed and reachable at a **public URL**"* — is
what this file delivers. **REQ-31** (`problem_statement.md:177`, *"Provision on Poridhi VM or AWS"*)
is answered by **Q-01**: we deploy to our own GCP VM under `rulebook.md` §8's *"any other deployment
option… as long as the application is publicly accessible for judging"* and
`problem_statement.md:221`'s *"Judges check that it is deployed and reachable, **not where**."*
The whole procedure is compose + host Nginx, so retargeting it to a Poridhi VM is ~4 edits in §0
and §5.

> **REQ-43** — `problem_statement.md:230`: *"Your deployment must be **reproducible from a clean
> clone**. **No hand-configured servers, no manual steps you did not write down.**"*
> Everything in this file is either (a) a one-time host prerequisite in §0/§5, version-controlled
> under `nginx/` in the repo, or (b) inside **`deploy.sh`** (§8), which is what both a human and
> the CD workflow run. There is no third category. If you find yourself typing an ad-hoc command
> on the VM, it belongs in `deploy.sh` or in this file — put it there before you forget.

**Variables used throughout:**
```bash
export DOMAIN=poridhi-hackathon.shadathossainrony.dev
export APP_DIR=/opt/cinemaseat
export WEB_ROOT=/var/www/cinemaseat
```

---

## §0 — Pre-flight (do this at check-in, BEFORE the problem reveal)

Everything here is problem-independent and is the highest-variance part of the day. Get it green early.

```bash
# 0.1 SSH reachability — do this on venue Wi-Fi. If 22 is blocked, switch to mobile hotspot NOW.
ssh <user>@<VM_IP>
# expected: a shell prompt. Timeout/refused -> Q-06, use hotspot.

# 0.2 Docker + Compose present and modern
docker --version                 # expected: Docker version 24.x or newer
docker compose version           # expected: Docker Compose version v2.x  (SPACE, not docker-compose)
docker run --rm hello-world      # expected: "Hello from Docker!" — proves the daemon works and egress is open

# 0.3 Permission: are we in the docker group?
docker ps
# expected: a table header. "permission denied ... /var/run/docker.sock" ->
#   sudo usermod -aG docker $USER && exit    (then SSH back in — the group only applies to a new session)

# 0.4 Resources
free -h                          # expected: >= 1.5 Gi available. Under 1 Gi -> build frontend locally, not on the VM.
df -h /                          # expected: >= 8 G available. Under 4 G -> docker builder prune -f
nproc                            # informs uvicorn --workers (currently 2)

# 0.5 DNS — the single most likely early blocker
dig +short $DOMAIN
# expected: the VM's public static IP, exactly.
#   empty        -> A record missing. Create it, TTL 300. Propagation: minutes to ~30.
#   wrong IP     -> record points elsewhere. Certbot WILL fail. Fix before continuing.
curl -4 -s ifconfig.me; echo     # run ON THE VM; must match the dig output

# 0.6 Nginx + Certbot installed
sudo apt-get update && sudo apt-get install -y nginx certbot python3-certbot-nginx
nginx -v                         # expected: nginx version: nginx/1.2x.x
certbot --version                # expected: certbot 2.x
sudo systemctl enable --now nginx
curl -sI http://localhost | head -1   # expected: HTTP/1.1 200 OK  (default Nginx page)

# 0.7 Pre-pull every image, INCLUDING the provided gateway (REQ-11).
#     problem_statement.md:71 — "Pull the image before the event."
docker pull python:3.12-slim
docker pull postgres:16-alpine
docker pull node:20-slim
docker pull asifmahmoud414/mock-gateway:latest
# expected: four "Status: Downloaded"/"Image is up to date" lines.
# 🔴 If the gateway image will not pull, escalate to the organisers IMMEDIATELY — REQ-08 forbids
#    writing our own mock, so this is a blocker for the entire payment path, not an inconvenience.

# 0.8 Answer the gateway unknowns (Q-05 .. Q-09 in 01-problem-analysis.md §8). ~10 minutes.
docker run --rm -d --name gw -p 9000:9000 asifmahmoud414/mock-gateway:latest
curl -s localhost:9000/health
curl -s -X POST localhost:9000/otp/send -H 'content-type: application/json' \
     -d '{"phone":"+8801700000001","ref":"probe-1"}' -i        # <-- does the response carry a code?
docker logs gw | tail -20                                      # <-- or does the container log it?
docker rm -f gw
# RECORD THE ANSWERS in 01-problem-analysis.md §8 before writing any gateway code.
```

🚩 **GATE 0:** SSH works, Docker works, `dig +short $DOMAIN` returns the VM IP, Nginx serves on :80,
**and the gateway image pulls and answers `/health`.**
**If DNS is wrong, fix it now** — it is the only step with a propagation delay you cannot compress.

---

## §1 — Firewall: exactly 22, 80, 443

Check what is actually reachable. Both the GCP-level firewall and any host firewall matter.

```bash
# Host firewall (if ufw is in use)
sudo ufw status verbose
# expected: 22/tcp ALLOW, 80/tcp ALLOW, 443/tcp ALLOW, default deny (incoming)
#
# If inactive and you want it on:
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw --force enable          # 🔴 confirm 22 is allowed FIRST or you lock yourself out

# What is actually listening on the box
sudo ss -tlnp
# expected: :22 sshd, :80 and :443 nginx, 127.0.0.1:8000 docker-proxy
# 🔴 0.0.0.0:5432 or 0.0.0.0:8000 = MISCONFIGURATION. Fix compose before continuing.

# From OUTSIDE the VM (run on the laptop) — proves the GCP firewall too
for p in 80 443 5432 8000 8001 9000; do nc -z -w3 $DOMAIN $p && echo "$p OPEN" || echo "$p closed"; done
# expected: 80 OPEN, 443 OPEN, everything else closed.
# 🔴 5432 open = the database is on the internet.  9000 open = the gateway is on the internet,
#    which means anyone can POST forged callbacks at it. Both are critical findings.
```

> GCP firewall rules are managed in the console/`gcloud` by whoever owns the project. We do not create VMs, but we must **verify** the ingress rules allow only 22/80/443. If 5432 or 8000 is reachable from outside, close it before anything else.

---

## §2 — Get the code onto the VM

```bash
sudo mkdir -p $APP_DIR && sudo chown $USER:$USER $APP_DIR
git clone https://github.com/<org>/<repo>.git $APP_DIR
cd $APP_DIR
# expected: cloned, on the default branch

# Subsequent deploys:
cd $APP_DIR && git pull --ff-only
# expected: "Fast-forward" or "Already up to date."
# "fatal: Not possible to fast-forward" -> the VM has local commits. NEVER edit code on the VM.
#   git stash && git pull --ff-only     (then investigate the stash)

git rev-parse --short HEAD       # RECORD THIS. It is the deployed SHA. Write it in the submission notes.
```

---

## §3 — Create `.env` on the server

`.env` **never** comes from git (REQ-58). It is created on the VM, once.

> **The stack runs without it** — compose supplies dev-safe defaults so a clean clone boots
> (REQ-21, `07-containerization.md` §3). `.env` exists on the VM for exactly one reason: to replace
> the dev defaults with real secrets and production settings. **Do not skip it** — running
> production on `cinemaseat_dev_pw` is a Code Quality finding a judge can see in the repo.

**Scripted, not hand-edited** (REQ-43 — *"no hand-configured servers"*). This block is `make-env.sh`
in the repo; run it once:

```bash
cd $APP_DIR
test -f .env && { echo "refusing to overwrite an existing .env"; exit 1; }

cat > .env <<EOF
ENVIRONMENT=production
LOG_LEVEL=INFO
POSTGRES_USER=cinemaseat
POSTGRES_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')
POSTGRES_DB=cinemaseat
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=10
HOLD_TTL_SECONDS=120
PAYMENT_WINDOW_SECONDS=90
MAX_SEATS_PER_HOLD=6
OTP_REQUIRED=true
CORS_ORIGINS=https://$DOMAIN
PUBLIC_BASE_URL=https://$DOMAIN
SESSION_SECRET=$(openssl rand -hex 32)
EOF

chmod 600 .env
ls -la .env                      # expected: -rw------- 1 <user> <user>
git check-ignore -v .env         # expected: .gitignore:NN:.env   <-- proves it cannot be committed
ls docker-compose.override.yml 2>/dev/null && echo "🔴 REMOVE THIS ON THE VM"
# expected: no output
```

| Var | Production value | Note |
| :--- | :--- | :--- |
| `ENVIRONMENT` | `production` | |
| `POSTGRES_PASSWORD` | freshly generated | ⚠️ **Set it before the first `up`.** Postgres reads it only when the volume is initialised; changing it afterwards needs `ALTER USER` (`15-troubleshooting.md` B2). |
| `POSTGRES_HOST` | `db` | service name — **not** localhost |
| `CORS_ORIGINS` | `https://poridhi-hackathon.shadathossainrony.dev` | single origin, never `*` |
| `HOLD_TTL_SECONDS` | `120` | ★ **temporarily set to `20` for the Scenario B demo, then set back.** REQ-19/REQ-39. |
| `GATEWAY_BASE_URL` / `GATEWAY_CALLBACK_URL` | **leave at the compose defaults** | Both are internal Docker service names. Overriding `GATEWAY_CALLBACK_URL` to the public hostname would expose the callback to the internet and break the control in `09-security-hardening.md` T-04. |

---

## §4 — Bring the stack up

```bash
cd $APP_DIR

docker compose config | grep -E 'ports:|image:|--reload' -A1
# expected: db has NO ports; api 127.0.0.1:8000; api2 127.0.0.1:8001; gateway 127.0.0.1:9000;
#           gateway image = asifmahmoud414/mock-gateway:latest; no --reload anywhere

docker compose up -d --build
# expected (roughly):
#   [+] Building ... naming to docker.io/library/cinemaseat-api
#   [+] Running 6/6  Network cinemaseat_appnet   Created
#                    Volume  "cinemaseat_pgdata" Created
#                    Container cinemaseat-db-1      Healthy
#                    Container cinemaseat-gateway-1 Started
#                    Container cinemaseat-migrate-1 Exited (0)     <-- migrations + seed ran here
#                    Container cinemaseat-api-1     Started
#                    Container cinemaseat-api2-1    Started

docker compose ps -a
# expected: db, gateway, api, api2 = running (healthy) ; migrate = Exited (0)
# 🔴 migrate "Exited (1)" -> docker compose logs migrate  -> 03-data-model.md §5.4 / 15-troubleshooting.md §A
# 🔴 api "restarting"     -> docker compose logs --tail=100 api

docker compose logs migrate
# expected: "Running upgrade ... " lines, then
#           "movies 4 | theatres 2 | screens 3 | shows 12 | show_seats 1152 | pre-booked 19"
```

### Verify the stack from the VM itself, before Nginx exists

```bash
curl -fsS http://127.0.0.1:8000/health
# expected: {"status":"ok","service":"cinemaseat-api",...}
curl -fsS http://127.0.0.1:8001/health          # the second replica (REQ-47)
# expected: the same

docker compose exec api alembic current
# expected: <rev> (head)          -- exactly one, matching the code

curl -fsS http://127.0.0.1:8000/ready | python3 -m json.tool
# expected: "status":"ready", database ok, migrations ok, gateway ok

curl -fsS http://127.0.0.1:8000/movies | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["items"]))'
# expected: 4        <-- REQ-10 seeding actually happened

curl -fsS http://127.0.0.1:8000/shows/1/seats | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(d["hold_ttl_seconds"], d["summary"])'
# expected: 120 {'total': 96, ...}   <-- REQ-19 proven from env var to wire
```

🚩 **GATE 1:** `docker compose ps -a` shows `migrate` exited 0 and everything else healthy;
`/ready` returns 200; `/movies` is non-empty.
**Fallback if it fails:** do not touch Nginx. Fix the stack first — a broken upstream makes every
Nginx symptom a red herring.

---

## §5 — Nginx server block

Written at `nginx/$DOMAIN.conf` **in the repo** (tracked), then installed to the VM. Version-controlling this is part of "repeatable deployment" (Rulebook §7.1).

### 5.1 Initial HTTP-only config (Certbot needs :80 working first)

```bash
sudo tee /etc/nginx/sites-available/$DOMAIN >/dev/null <<'EOF'
upstream cinemaseat_api {
    server 127.0.0.1:8000 max_fails=2 fail_timeout=5s;
    server 127.0.0.1:8001 max_fails=2 fail_timeout=5s;
    keepalive 32;
}

server {
    listen 80;
    listen [::]:80;
    server_name poridhi-hackathon.shadathossainrony.dev;
    root /var/www/cinemaseat;
    index index.html;

    location ^~ /.well-known/acme-challenge/ { root /var/www/html; }

    # The API lives at the ROOT (04-api-contract.md). Route by an explicit
    # first-segment allow-list. /payments/callback is DELIBERATELY ABSENT.
    location ~ ^/(health|ready|movies|theatres|shows|holds|bookings|docs|redoc|openapi\.json)(/|$) {
        proxy_pass http://cinemaseat_api;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location ^~ /assets/ { }
    location / { try_files $uri /index.html; }
}
EOF

sudo mkdir -p $WEB_ROOT /var/www/html
echo "<h1>CinemaSeat — deploying</h1>" | sudo tee $WEB_ROOT/index.html
sudo ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN
sudo rm -f /etc/nginx/sites-enabled/default        # the default site can shadow ours
sudo nginx -t
# expected: syntax is ok / test is successful
sudo systemctl reload nginx

curl -sI http://$DOMAIN | head -1            # expected: HTTP/1.1 200 OK  (from OUTSIDE the VM)
curl -fsS http://$DOMAIN/health              # expected: {"status":"ok",...}  -- proxy works
```

🚩 **GATE 2:** `http://$DOMAIN` returns 200 from the laptop. **Do not run Certbot until this passes** — HTTP-01 validation uses exactly this path, and a failed attempt burns rate-limit budget.

### 5.2 Certbot

```bash
sudo certbot --nginx -d $DOMAIN --email <your-email> --agree-tos --no-eff-email --redirect
# expected:
#   Successfully received certificate.
#   Certificate is saved at: /etc/letsencrypt/live/<domain>/fullchain.pem
#   Deploying certificate ... Congratulations! You have successfully enabled HTTPS

sudo certbot certificates
# expected: VALID: 89 days

systemctl list-timers | grep certbot
# expected: certbot.timer active — auto-renewal is scheduled
sudo certbot renew --dry-run
# expected: "Congratulations, all simulated renewals succeeded"
```

🔴 **Let's Encrypt rate limits — read before retrying anything:**

| Limit | Value | Meaning for us |
| :--- | :--- | :--- |
| Certificates per registered domain | **50 / week** | Not a practical risk. |
| **Duplicate certificate** (same exact name set) | **5 / week** | **This is the one that bites.** Five successful issuances for the same domain and you are locked out for a week. |
| **Failed validations** | **5 per account per hostname per hour** | Certbot failing five times → a 1-hour lockout. |

**Therefore: if Certbot fails, do NOT immediately re-run it.**
1. Read the exact error.
2. Verify `curl -sI http://$DOMAIN` returns 200 from outside (GATE 2).
3. Verify `dig +short $DOMAIN` matches the VM IP.
4. Test the *fix* with `--dry-run` (staging — does **not** count against limits):
   `sudo certbot --nginx -d $DOMAIN --dry-run`
5. Only after a clean dry run, run the real command.

**Fallback if TLS cannot be obtained:** serve over HTTP, put a plain note in the README explaining the exact blocker, and demo over `http://`. Rulebook §12: an honest account beats a misrepresentation. A working HTTP deploy scores far more than an unreachable HTTPS one.

### 5.3a — 📌 CURRENT STATE ON THE SERVER (as of 8 Aug 2026, TLS already issued)

**This is what is actually installed at `/etc/nginx/sites-available/poridhi-hackathon` right now.**
Recorded verbatim so the gap analysis in §5.3b is against reality, not against a plan.

```nginx
upstream api_backend {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    listen [::]:80;
    server_name poridhi-hackathon.shadathossainrony.dev;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name poridhi-hackathon.shadathossainrony.dev;

    ssl_certificate     /etc/letsencrypt/live/poridhi-hackathon.shadathossainrony.dev/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/poridhi-hackathon.shadathossainrony.dev/privkey.pem;
    ssl_trusted_certificate /etc/letsencrypt/live/poridhi-hackathon.shadathossainrony.dev/chain.pem;

    ssl_protocols TLSv1.2 TLSv1.3;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    client_max_body_size 25m;

    access_log /var/log/nginx/poridhi-access.log;
    error_log  /var/log/nginx/poridhi-error.log warn;

    location / {
        proxy_pass http://api_backend;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 10s;
        proxy_send_timeout    60s;
        proxy_read_timeout    60s;
    }

    location = /health {
        access_log off;
        proxy_pass http://api_backend;
        proxy_set_header Host $host;
    }
}
```

**What it gets right, and it is most of the hard part:** TLS 1.2/1.3 with a full chain and OCSP
stapling material, HTTP→HTTPS with ACME excluded from the redirect, four of the security headers,
correct `X-Forwarded-*` for `--proxy-headers`, sane timeouts, `http2 on`, an `upstream` block already
in place, and **`proxy_pass` with no trailing slash and no URI part** — the classic bug, avoided.
It is a correct API-at-the-root reverse proxy. GATE 2 passes on this.

> ⚠️ **Verify how the cert was issued.** The `/.well-known/acme-challenge/ { root /var/www/certbot; }`
> block implies `certbot --webroot -w /var/www/certbot`, not `certbot --nginx` as §5.2 assumes.
> If it was `--nginx`, that block is vestigial and harmless; if it was `--webroot`, **renewal depends
> on that directory continuing to exist** and on :80 staying open.
> ```bash
> sudo certbot certificates                 # note the "Certificate Path" and authenticator
> grep -r "authenticator\|webroot" /etc/letsencrypt/renewal/*.conf
> sudo certbot renew --dry-run              # expected: all simulated renewals succeeded
> ```
> **Do this once, now, while it is cheap** — a failed renewal at 60 days is not our problem, but a
> broken `renew --dry-run` today usually means the challenge path is wrong *today* too.

---

### 5.3b — GAP ANALYSIS: what must change, and exactly when

**Do not apply all of this now.** Each row has a trigger. Changing Nginx before its trigger is
touching a green deployment for a nice-to-have (`12-execution-plan.md` rule 5).

| # | Gap | Consequence today | Consequence when triggered | **Trigger** |
| :-: | :--- | :--- | :--- | :--- |
| 🔴 **P1** | **`location /` proxies *everything*, so `/payments/callback` is publicly routable** | none — the endpoint does not exist yet | **Anyone on the internet can POST a forged `SUCCEEDED` callback and confirm bookings for free.** Destroys threat control T-01 and smoke check 8. | **Phase 7**, the moment `POST /payments/callback` ships |
| 🟠 **P2** | No static root, no `try_files`, no `^~ /assets/` — the SPA cannot be served at all | none — no frontend deployed | The frontend 404s; `/assets/*.js` gets proxied to FastAPI and returns JSON | **Phase 10**, when `dist/` is rsynced |
| 🟡 **P3** | No `Content-Security-Policy`, no `Permissions-Policy`, no `server_tokens off` | **`tests/smoke.sh` §3 fails on CSP** (it asserts five headers) | — | **Phase 9** (or sooner, to make smoke green) |
| 🟡 **P4** | No `limit_req_zone` / `limit_req` anywhere | none — and **this is currently a good thing**: nothing can shed Scenario A's burst | INF-09 / REQ-48 unmet | **Phase 9**, *after* Scenario A has been captured |
| 🟢 **P5** | `upstream` has one server | none | REQ-47 (load balancing) and REQ-36 (reachable during deploy) unmet | **Phase 9**, when `api2` is added |
| 🔵 **P6** | No `proxy_set_header X-Request-ID $request_id` and no JSON `log_format` | the app mints its own ID, so tracing still works — but the **Nginx access log and the app log share no key** | — | any time; 2 lines |
| ⚪ **P7** | No `gzip` | slightly larger seat-map responses (~96 seats of JSON) | — | free, bundle with P3 |
| ⚪ **P8** | `location = /health` drops `X-Forwarded-Proto` | harmless — `/health` builds no URLs | — | bundle with P3 |
| ⚪ **P9** | `client_max_body_size 25m` vs the planned 10m | none; our largest body is a hold with ≤ 6 seats | — | optional |

> **P1 is the only one that is a defect rather than an absence.** Everything else is "not yet".

#### P1 — the minimal patch, applied in Phase 7

Two lines, inserted **above** `location /` so it wins by specificity. This preserves the current
"everything else goes to the API" behaviour and closes only the hole:

```nginx
    # 🔴 The gateway callback reaches us on the internal Docker network only
    #    (GATEWAY_CALLBACK_URL=http://api:8000/payments/callback). Nothing on the
    #    public internet may reach it. 09-security-hardening.md T-01.
    location ^~ /payments/ {
        return 404;
    }
```
Verify immediately after `systemctl reload nginx`:
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$DOMAIN/payments/callback \
  -H 'content-type: application/json' -d '{"event_id":"forged","status":"SUCCEEDED"}'
# expected: 404      🔴 200 with {"received":true} means free tickets for anyone
docker compose logs gateway | tail -5   # confirm real callbacks still land (they use the internal URL)
```

#### P2 — the routing flip, applied in Phase 10

This is the only change that alters the shape of the file. `location /` stops being the API and
becomes the SPA; the API moves to an explicit first-path-segment allow-list. **Matching order makes
this work:** `= exact` → `^~ prefix` → `~ regex` → `/ prefix`, so `^~ /assets/` beats the API regex,
and the API regex beats `/`.

```nginx
    root /var/www/cinemaseat;
    index index.html;

    location ^~ /assets/ { expires 1y; add_header Cache-Control "public, immutable"; }
    location ^~ /payments/ { return 404; }                       # P1 stays

    location ~ ^/(health|ready|movies|theatres|shows|holds|bookings|docs|redoc|openapi\.json)(/|$) {
        proxy_pass http://api_backend;
        include /etc/nginx/proxy_common.conf;
    }

    location / { try_files $uri /index.html; }                   # the SPA
```
🚩 **After this flip, adding an endpoint means editing three lists** — this regex, the Vite dev proxy
(`06-frontend-plan.md` §4), and `04-api-contract.md` §3. That is the price of REQ-18's literal
`GET /health`.

---

### 5.3c — TARGET server block (the end state, after P1–P7)

Keep a copy at `nginx/$DOMAIN.conf` **in the repo** — REQ-43 forbids hand-configured servers, and
version-controlling this file is what makes the deployment reproducible.

```nginx
# ---------- http context: /etc/nginx/conf.d/cinemaseat-http.conf ----------
#
# ⚠️ SCENARIO A HAZARD (04-api-contract.md §8): the hold zone must NOT shed the 100-request
#    burst. If Nginx 429s 90 of them we have proved Nginx works, not that our seat claim is
#    correct. rate=50r/s with burst=200 nodelay lets the whole burst reach the application,
#    where the DATABASE rejects it — which is the thing being tested.
limit_req_zone $binary_remote_addr zone=api_zone:10m  rate=30r/s;
limit_req_zone $binary_remote_addr zone=hold_zone:10m rate=50r/s;

# REQ-47 — two backends, so this is genuinely load balancing and not a diagram.
upstream cinemaseat_api {
    server 127.0.0.1:8000 max_fails=2 fail_timeout=5s;
    server 127.0.0.1:8001 max_fails=2 fail_timeout=5s;
    keepalive 32;          # reuse upstream connections; measurable at Scenario C volumes
}

# ---------- HTTP -> HTTPS ----------
server {
    listen 80;
    listen [::]:80;
    server_name poridhi-hackathon.shadathossainrony.dev;

    # Leave ACME reachable over HTTP so renewal never breaks.
    location ^~ /.well-known/acme-challenge/ { root /var/www/html; }

    location / { return 301 https://$host$request_uri; }
}

# ---------- HTTPS ----------
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name poridhi-hackathon.shadathossainrony.dev;

    # --- TLS (managed by Certbot) ---
    ssl_certificate     /etc/letsencrypt/live/poridhi-hackathon.shadathossainrony.dev/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/poridhi-hackathon.shadathossainrony.dev/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;      # TLS 1.2+ only, modern cipher suite
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # --- Security headers (09-security-hardening.md) ---
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options    "nosniff" always;
    add_header X-Frame-Options           "DENY" always;
    add_header Referrer-Policy           "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy        "geolocation=(), microphone=(), camera=()" always;
    add_header Content-Security-Policy   "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
    server_tokens off;                                   # hide the nginx version

    # --- Limits & compression ---
    client_max_body_size 10m;
    client_body_timeout  30s;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript application/xml image/svg+xml;
    gzip_min_length 1024;
    gzip_vary on;

    # --- Static frontend ---
    root /var/www/cinemaseat;
    index index.html;

    # ========================================================================
    #  ROUTING — the API is mounted at the ROOT (04-api-contract.md).
    #  Nginx therefore routes by an EXPLICIT ALLOW-LIST of first path segments.
    #  Matching order that makes this work:
    #      1. `location =`        exact
    #      2. `location ^~`       prefix, stops regex evaluation
    #      3. `location ~`        regex, first match wins      <-- the API
    #      4. `location /`        prefix fallback              <-- the SPA
    #  So `^~ /assets/` beats the API regex, and the API regex beats `/`.
    # ========================================================================

    location ^~ /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";   # hashed filenames -> safe to cache hard
    }

    # --- The hold endpoint: its own zone, deliberately generous (see the note above) ---
    location = /holds {
        limit_req zone=hold_zone burst=200 nodelay;
        limit_req_status 429;
        proxy_pass http://cinemaseat_api;
        include /etc/nginx/proxy_common.conf;
    }

    # --- Everything else in the API allow-list ---
    #  🔴 /payments/ IS NOT IN THIS LIST, ON PURPOSE.
    #     The gateway callback is reachable only from inside the Docker network
    #     (GATEWAY_CALLBACK_URL=http://api:8000/payments/callback). A forged callback from the
    #     internet hits the SPA fallback below and gets HTML. This is the callback-forgery
    #     control — 09-security-hardening.md T-04. Adding /payments here removes it.
    location ~ ^/(health|ready|movies|theatres|shows|holds|bookings|docs|redoc|openapi\.json)(/|$) {
        limit_req zone=api_zone burst=100 nodelay;
        limit_req_status 429;
        proxy_pass http://cinemaseat_api;
        include /etc/nginx/proxy_common.conf;
    }

    # --- SPA: everything not matched above ---
    location / {
        try_files $uri /index.html;
    }

    # --- Nginx-level health (does not touch the app) ---
    location = /nginx-health { access_log off; return 200 "ok\n"; }
}
```

> **Keep this list in sync in three places** — this Nginx regex, the Vite dev proxy
> (`06-frontend-plan.md` §4), and the endpoint table in `04-api-contract.md` §3. Adding an endpoint
> without adding its first path segment here produces a 404 that looks like a routing bug and is
> actually a config omission. There is no `/api` prefix to hide behind any more; that is the price
> of REQ-18's literal `GET /health`, and it is worth paying.

`/etc/nginx/proxy_common.conf` — shared proxy headers, written once:

```nginx
proxy_http_version 1.1;

proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;      # uvicorn --proxy-headers reads this
proxy_set_header X-Forwarded-Host  $host;
proxy_set_header X-Request-ID      $request_id;  # nginx-generated; the API reuses it

# WebSocket upgrade — harmless if unused, essential if a P-ID needs realtime (Q-09)
proxy_set_header Upgrade    $http_upgrade;
proxy_set_header Connection $connection_upgrade;

proxy_connect_timeout 5s;
proxy_send_timeout    60s;
proxy_read_timeout    60s;
proxy_buffering       on;
```

`$connection_upgrade` needs a map in the http context (`/etc/nginx/conf.d/upgrade_map.conf`):
```nginx
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
```

**Note on `proxy_pass http://cinemaseat_api;` — no trailing slash, no URI part.** With a **regex**
`location`, `proxy_pass` **must not** carry a URI component at all — Nginx rejects the config with
*"proxy_pass cannot have URI part in location given by regular expression"*. Writing
`proxy_pass http://cinemaseat_api/;` fails `nginx -t` outright here, which is better than the prefix-
location version of this bug where it silently strips the path and 404s every route. Either way:
**no trailing slash.** Classic 30-minute bug, now caught by `nginx -t`.

Apply:
```bash
sudo nginx -t && sudo systemctl reload nginx
# expected: syntax is ok / test is successful, then a silent reload
```

---

## §6 — Deploy the frontend

```bash
cd $APP_DIR/frontend
npm ci && npm run build
# expected: dist/index.html + dist/assets/*
# OOM ("JavaScript heap out of memory")? -> build on the laptop and rsync:
#   rsync -avz --delete frontend/dist/ <user>@<VM_IP>:/tmp/dist/ && ssh ... 'sudo rsync -a --delete /tmp/dist/ /var/www/cinemaseat/'

# 🚩 Non-negotiable pre-deploy check (06-frontend-plan.md §4)
grep -rnE 'localhost|127\.0\.0\.1|:8000' dist/ && { echo "🔴 absolute URL in the bundle — DO NOT DEPLOY"; exit 1; }
# expected: no matches

sudo rsync -a --delete dist/ $WEB_ROOT/
sudo chown -R www-data:www-data $WEB_ROOT
ls $WEB_ROOT
# expected: index.html  assets/  (+ vite.svg etc.)

curl -sI https://$DOMAIN | head -1
# expected: HTTP/2 200
```

---

## §7 — POST-DEPLOY VERIFICATION CHECKLIST

Run **every** line after **every** deploy. `tests/smoke.sh` automates all of it — but know what it checks.

```bash
# 1. TLS certificate valid and for the right name
echo | openssl s_client -connect $DOMAIN:443 -servername $DOMAIN 2>/dev/null \
  | openssl x509 -noout -subject -dates -issuer
# expected: subject=CN=poridhi-hackathon.shadathossainrony.dev
#           notAfter ~90 days out, issuer = Let's Encrypt

# 2. HTTP redirects to HTTPS
curl -sI http://$DOMAIN/health | head -1
# expected: HTTP/1.1 301 Moved Permanently
curl -sI http://$DOMAIN/health | grep -i location
# expected: location: https://poridhi-hackathon.shadathossainrony.dev/health

# 3. Liveness over HTTPS, and FAST  (REQ-18)
curl -fsS -w '\n%{time_total}s\n' https://$DOMAIN/health
# expected: {"status":"ok","service":"cinemaseat-api",...}  and well under 1s

# 4. Readiness confirms DB, migrations and (advisory) gateway
curl -fsS https://$DOMAIN/ready | python3 -m json.tool
# expected: "status":"ready", database ok, migrations ok, gateway ok

# 5. Security headers present
curl -sI https://$DOMAIN | grep -iE 'strict-transport|x-content-type|x-frame|referrer-policy|content-security'
# expected: all five present

# 6. Frontend loads
curl -s https://$DOMAIN | head -5
# expected: <!doctype html> ... <div id="root">

# 7. Seeded catalogue is really there  (REQ-10)
curl -fsS https://$DOMAIN/movies | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["items"]))'
# expected: 4  -- not 0. A 0 means the migrate container did not seed.

# 8. ★ The seat map, and REQ-19 proven from the outside
curl -fsS https://$DOMAIN/shows/1/seats | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(d["hold_ttl_seconds"], d["summary"], len(d["seats"]))'
# expected: 120 {'total': 96, 'available': ..., 'held': 0, 'booked': 19} 96

# 9. ★ THE ONE THAT MATTERS: hold a seat, then fail to hold it again  (REQ-07)
curl -fsS -X POST https://$DOMAIN/holds -H 'content-type: application/json' \
  -d '{"show_id":1,"seats":["H1"],"phone":"+8801700000001"}'
# expected: 201 with hold_id and expires_at
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$DOMAIN/holds \
  -H 'content-type: application/json' -d '{"show_id":1,"seats":["H1"],"phone":"+8801700000002"}'
# expected: 409     <-- if this is 201, the core requirement of the entire problem is broken

# 10. 🔴 Persistence survives a container restart  (INF-01)
docker compose restart api api2 && sleep 20
curl -fsS https://$DOMAIN/shows/1/seats | grep -o '"seat": "H1"[^}]*'
# expected: still "HELD"  <-- proves the named volume works; the demo-killer if it fails

# 11. ★ Fault isolation drill  (REQ-44, bonus)
docker compose stop gateway
curl -fsS -w '\n%{time_total}s\n' https://$DOMAIN/health          # expected: 200, still fast
curl -fsS https://$DOMAIN/shows/1/seats >/dev/null && echo "seat map OK"
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$DOMAIN/holds \
  -H 'content-type: application/json' -d '{"show_id":2,"seats":["A1"],"phone":"+8801700000003"}'
# expected: 201 — holds still work with the gateway down
curl -s https://$DOMAIN/ready | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])'
# expected: degraded   (NOT not_ready)
docker compose start gateway

# 12. Error envelope + request ID
curl -s https://$DOMAIN/shows/99999/seats | python3 -m json.tool
# expected: {"error":{"code":"NOT_FOUND",...,"request_id":"..."}} — no traceback, no file paths
curl -sI https://$DOMAIN/health | grep -i x-request-id
# expected: x-request-id: <uuid4>

# 13. 🔴 The callback is NOT publicly routable  (09-security-hardening.md T-04)
curl -s -X POST https://$DOMAIN/payments/callback -H 'content-type: application/json' \
  -d '{"event_id":"forged","booking_ref":"bk_x","status":"SUCCEEDED","amount":1}' | head -c 80
# expected: HTML (the SPA fallback). If this returns {"received":true}, the callback is exposed
#           to the internet and anyone can confirm bookings for free. Fix the Nginx allow-list.

# 14. Direct port access is NOT possible from outside
for p in 8000 8001 9000 5432; do
  curl -m 5 -s -o /dev/null -w "$p:%{http_code} " http://$DOMAIN:$p/health; done; echo
# expected: all 000 (refused/timeout) — none may be 200
```

**Run `bash tests/smoke.sh` and confirm it exits 0.** Then record the deployed SHA.

🚩 **GATE 3 (the hard one, from `12-execution-plan.md`):** items 1–8 green. Nothing else in the plan
proceeds until this passes. Items 9–14 gate the feature phases that introduce them.

---

## §8 — `deploy.sh` — the ONE deploy path (REQ-34, REQ-36, REQ-43)

`problem_statement.md:230` forbids *"manual steps you did not write down"*, and `:181` requires CD.
Both are satisfied by putting the deploy in a **script that lives in the repo** and having *both*
a human and the CD workflow run exactly that script. There is no second procedure.

```bash
#!/usr/bin/env bash
# deploy.sh — run on the VM, by a human or by .github/workflows/cd.yml. Idempotent.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> pulling"
git pull --ff-only                                    # expected: Fast-forward / Already up to date
SHA=$(git rev-parse --short HEAD); echo "==> deploying $SHA"

echo "==> building"
docker compose build

echo "==> migrations + seed (one-shot container, exits 0)"
docker compose run --rm migrate                       # 03-data-model.md §5.5
docker compose run --rm api alembic heads             # 🔴 must print exactly ONE (head)

# --- REQ-36: rolling restart. Nginx keeps serving from whichever replica is up. ---
echo "==> rolling api"
docker compose up -d --no-deps api
until curl -fsS http://127.0.0.1:8000/health >/dev/null; do sleep 2; done
echo "==> rolling api2"
docker compose up -d --no-deps api2
until curl -fsS http://127.0.0.1:8001/health >/dev/null; do sleep 2; done

echo "==> frontend"
if [ -d frontend ]; then
  ( cd frontend && npm ci --silent && npm run build \
    && ! grep -rqE 'localhost|127\.0\.0\.1|:8000' dist/ )   # fail the deploy if a URL leaked
  sudo rsync -a --delete frontend/dist/ /var/www/cinemaseat/
  sudo chown -R www-data:www-data /var/www/cinemaseat
fi

echo "==> smoke"
bash tests/smoke.sh                                   # non-zero here fails the CD workflow

echo "==> deployed $SHA"
```

**Routine redeploy, by hand:**
```bash
ssh <user>@<VM_IP> 'cd /opt/cinemaseat && ./deploy.sh'
# expected: ends with "ALL CHECKS PASSED" then "==> deployed <sha>"
```
**By CD:** the same line, from `.github/workflows/cd.yml`, on a push to `main`
(`07-containerization.md` §8).

> **Why the rolling restart matters and is nearly free:** `docker compose up -d --no-deps api`
> recreates one replica; Nginx's `max_fails=2 fail_timeout=5s` marks it down and serves everything
> from `api2` until it is healthy again. That is REQ-36 — *"Keep services reachable during
> deployment where possible"* — answered with a mechanism rather than a hope. Demo it: run
> `deploy.sh` while `watch -n0.5 curl -s https://$DOMAIN/health` is on screen and show no gap.

> ⚠️ **In-flight holds survive a rolling restart** because all state is in Postgres — there is no
> in-memory hold table. The two things that do **not** survive are the in-process rate-limit
> counters and the sweeper's tick, both of which are stateless-by-design and self-healing. Worth
> saying out loud; it is the payoff for keeping the invariant in the database.

---

## §9 — Rollback

```bash
cd $APP_DIR
git log --oneline -10                     # find the last known-good SHA
git checkout <good-sha>
docker compose up -d --build
docker compose exec api alembic current   # is the DB ahead of this code?
```

| Situation | Action |
| :--- | :--- |
| Code-only regression | `git checkout <good-sha>` + rebuild. Schema unchanged, nothing else to do. |
| Bad migration, **reversible** | `docker compose exec api alembic downgrade -1`, then check out the good SHA and `upgrade head`. |
| Bad migration, **destructive** (dropped a column) | Restore from the `pg_dump` (`07-containerization.md` §4). **This is why we back up before every risky migration.** |
| Container will not start at all | `docker compose logs --tail=200 api`. Do not rebuild blindly — read the error first. |
| Nginx broken | `sudo nginx -t` reports the exact file and line. Fix, `sudo systemctl reload nginx`. Config is in git, so `git checkout` restores it. |

Return to the newest good state: `git checkout main && git pull --ff-only && docker compose up -d --build`.

---

## §10 — 🔴 "DEPLOYMENT IS BROKEN, 30 MINUTES LEFT" — emergency triage

Work strictly top to bottom. **Do not skip ahead, do not try two fixes at once**, and set a 5-minute timer per step.

```
0. BREATHE. Open a second terminal for logs. Do not start editing code.

1. IS IT DNS OR THE NETWORK?  (60 seconds)
   dig +short $DOMAIN                    -> right IP?
   curl -sI http://$DOMAIN | head -1     -> anything at all?
   If nothing: is the VM up? Can you SSH? -> VM/network problem, not an app problem.

2. IS NGINX UP?  (60 seconds)
   sudo systemctl status nginx           -> active (running)?
   sudo nginx -t                         -> syntax ok?
   sudo tail -30 /var/log/nginx/error.log
   Broken config -> git checkout nginx/ && reinstall && reload.

3. IS THE APP UP?  (2 minutes)
   docker compose ps                     -> both (healthy)?
   curl -fsS http://127.0.0.1:8000/health   ON THE VM
   -> 200 here but 502 through Nginx = a PROXY problem (step 4).
   -> fails here = an APP problem (step 5).

4. PROXY PROBLEM (502)  (5 minutes)
   Common causes, in order of likelihood:
     - proxy_pass has a trailing slash (strips /api/) -> remove it
     - wrong port in proxy_pass
     - api container unhealthy, so nothing is listening
     - SELinux/AppArmor blocking the loopback connect (rare on Debian/Ubuntu)
   sudo tail -30 /var/log/nginx/error.log   -> names the upstream and the exact reason

5. APP PROBLEM  (10 minutes)
   docker compose ps -a                  -> is `migrate` Exited (0), or Exited (1)?
   docker compose logs --tail=100 api
     "could not translate host name db"  -> POSTGRES_HOST wrong, or db not started
     "password authentication failed"    -> .env mismatch; the volume kept the OLD password.
                                            Fastest fix: match .env to the volume, do not recreate the volume.
     "Multiple head revisions"           -> 03-data-model.md §5.4
     "ModuleNotFoundError"               -> stale image; docker compose build --no-cache api
     restart-looping                     -> docker compose logs api | head -50 shows the startup traceback
     api never starts, no traceback      -> `migrate` exited non-zero, so depends_on blocks api.
                                            docker compose logs migrate  <-- the real error is HERE
     /health fine but /bookings 5xx      -> the GATEWAY, not us. docker compose ps gateway;
                                            curl localhost:9000/health. REQ-44 says this must
                                            degrade, not 500 — if it 500s, that is our bug.

6. STILL BROKEN AT T-15?  ROLL BACK, DO NOT DEBUG.
   git checkout <last-known-good-sha> && docker compose up -d --build
   A working older version beats a broken newer one. Every time.

7. STILL BROKEN AT T-10?  DEGRADE HONESTLY.
   a) Serve over HTTP if only TLS is broken (still a public URL, still scores).
   b) Demo from local docker compose on the laptop and SAY SO on the slide.
   c) Put the exact failure in the README — what broke, what you tried, what you'd do.
      Rulebook §12: "Judges reward an honest account of a broken deploy far more than a
      misrepresented one." Misrepresenting it is a §6.4 disqualification item.

8. SUBMIT THE FORM REGARDLESS. A submitted broken deploy scores; an unsubmitted one does not.
```

**Pre-emptive insurance, done while calm (target 15:30 — see Q-02 on the clock):**
- [ ] `pg_dump` backup taken (`07-containerization.md` §4)
- [ ] Last known-good SHA written down, **on paper**
- [ ] Demo video recorded as a fallback `[DELEGABLE]` (`13-demo-script.md`)
- [ ] **Clean-clone `docker compose up` verified on a teammate's laptop** — REQ-21/REQ-52 is judged
      independently of the VM, so this is a *second, separate* submission that can still score
      even if the VM dies
- [ ] Scenario A + B reports written up (`10-testing-and-load.md`) — **they score whether or not
      the deployment is up**
- [ ] Submission form already submitted with the current state

---

## Appendix A — Containerized Nginx (fallback only)

Use **only** if host Nginx is unavailable. Strictly worse for Certbot (ADR-004).

```yaml
  nginx:
    image: nginx:1.27-alpine
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    volumes:
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - ./frontend/dist:/var/www/cinemaseat:ro
      - certbot_www:/var/www/certbot:ro
      - certbot_conf:/etc/letsencrypt:ro
    depends_on: [api]
    networks: [appnet]
```
- `proxy_pass` becomes `http://api:8000` (service name), **not** `127.0.0.1:8000` — inside the nginx container, loopback is the nginx container.
- Certificates via a one-shot certbot container using the **webroot** plugin against the shared `certbot_www` volume.
- Renewal needs a cron/timer that runs certbot **and then** `docker compose exec nginx nginx -s reload` — the extra moving part that made us choose host Nginx.
- The `api` service must then drop its host port binding and be reachable only on `appnet`.

---

**Cross-links:** compose/Dockerfiles → `07-containerization.md` · migrations → `03-data-model.md` §5 · headers & TLS rationale → `09-security-hardening.md` · smoke script → `tests/smoke.sh` · failures → `15-troubleshooting.md`
