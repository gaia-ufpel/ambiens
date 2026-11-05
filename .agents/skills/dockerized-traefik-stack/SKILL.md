---
name: dockerized-traefik-stack
description: Reproduce a containerized full-stack application — Postgres + PostgREST + Node backend + React/Vite frontend — with a development compose (nginx gateway, no TLS) and a production compose wired into a pre-existing Traefik reverse proxy (TLS via Let's Encrypt). Includes a GitHub Actions deploy workflow that builds images on a self-hosted runner, streams them via SSH to the production host with `docker save | gzip | ssh | docker load`, and recreates only the changed services. Use when the user wants to bootstrap this stack from scratch, add a new service to it, port an existing project into this shape, fix the deploy pipeline, or wire up Traefik routing/labels for a new compose service.
---

# Dockerized Postgres + PostgREST + Backend + Frontend + Traefik

This skill is the playbook for reproducing the **autoblogs-style** stack. It covers two compose files (dev / prod), the Dockerfiles for each service, the nginx reverse proxy used in dev, the Traefik labels used in prod, the database bootstrap, and the GitHub Actions deploy workflow that builds images on a self-hosted runner and ships them to the production host via SSH.

## When to use

- User wants to scaffold a new project using this exact stack shape.
- User wants to add a new service to an existing project of this shape.
- User wants to debug the deploy pipeline (image build, SSH stream, container recreate).
- User wants to wire a new compose service into Traefik with Let's Encrypt.
- User wants to switch a project from "nginx in prod" to "Traefik in prod".

## Architecture at a glance

```
┌─────────────────────────────────────────────────────────────────┐
│ PRODUCTION                                                      │
│                                                                 │
│  Internet ─► Traefik (external "proxy" net) ─► routes by Host  │
│                          │                                      │
│                          ├─► frontend  (nginx serving /dist)    │
│                          ├─► backend   (/api/v1)                │
│                          └─► postgrest (/rest/v1, prefix strip) │
│                                       │                         │
│                                       ▼                         │
│                                  db (postgres, internal only)   │
│                                                                 │
│  db-backup (cron, profile=backup) ─► S3                         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ DEVELOPMENT                                                     │
│                                                                 │
│  localhost:${GATEWAY_PORT} ─► nginx (gateway) ─► routes:        │
│                                      ├─► /          → frontend  │
│                                      ├─► /api/v1/   → backend   │
│                                      └─► /rest/v1/  → postgrest │
│                                                                 │
│  No Traefik, no TLS, hot reload via bind mounts.                │
└─────────────────────────────────────────────────────────────────┘
```

Key invariants:

- **Same URL contract in dev and prod**: frontend always calls `/api/v1` and `/rest/v1`. The proxy in front of the stack (nginx in dev, Traefik in prod) is the only thing that changes.
- **PostgREST stripprefix in prod**: PostgREST is reachable at `/rest/v1` externally but expects requests at `/`. Traefik strips the prefix; dev nginx does the same with `rewrite`.
- **Postgres never gets a Traefik label**: it stays on the internal network only. Same for the backup container.
- **Traefik network is external and pre-existing**: the compose declares `networks.traefik.external: true` with `name: proxy` — Traefik must already be running on the host with that network created.

## Files to produce

When bootstrapping the stack, produce these files in this layout:

```
.
├── .github/workflows/deploy.yaml      # GH Actions deploy (self-hosted)
├── docker-compose.yml                 # PRODUCTION compose (Traefik)
├── docker-compose.dev.yml             # DEV compose (nginx gateway)
├── .env.example                       # documents required vars
├── nginx.dev.conf                     # dev-only gateway
├── db/
│   └── init.sql                       # roles, schemas, bootstrap
├── backend/
│   ├── Dockerfile                     # multi-stage prod
│   └── Dockerfile.dev                 # tsx watch + bind mount
├── frontend/
│   ├── Dockerfile                     # build + nginx serve
│   ├── Dockerfile.dev                 # vite dev server
│   └── nginx.conf                     # in-image SPA fallback
└── backup/                            # optional
    ├── Dockerfile
    ├── entrypoint.sh
    ├── backup.sh
    └── restore.sh
```

Do **not** create README/docs files unless asked. Do **not** add files beyond what's needed.

## 1. Production `docker-compose.yml`

Copy this as the baseline and adapt names. Replace `autoblogs` with the project slug — it is used in image names and Traefik router/service names (each must be globally unique within the Traefik instance).

```yaml
services:
  db:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: ${DATABASE_USER}
      POSTGRES_PASSWORD: ${DATABASE_PASSWORD}
      POSTGRES_DB: ${DATABASE_DB}
    volumes:
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DATABASE_USER} -d ${DATABASE_DB}"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks:
      - internal
    restart: unless-stopped

  backend:
    image: ${PROJECT}-backend:latest
    build:
      context: ./backend
    env_file:
      - .env
    environment:
      - PORT=${BACKEND_PORT}
    depends_on:
      db:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "node -e \"fetch('http://localhost:${BACKEND_PORT}/api/v1').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 15s
    networks:
      - internal
      - traefik
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=proxy"
      - "traefik.http.routers.${PROJECT}-backend.rule=Host(`${DOMAIN}`) && PathPrefix(`/api/v1`)"
      - "traefik.http.routers.${PROJECT}-backend.entrypoints=websecure"
      - "traefik.http.routers.${PROJECT}-backend.tls.certresolver=le"
      - "traefik.http.services.${PROJECT}-backend.loadbalancer.server.port=${BACKEND_PORT}"
    restart: unless-stopped

  postgrest:
    image: postgrest/postgrest
    env_file:
      - .env
    depends_on:
      db:
        condition: service_healthy
      backend:
        condition: service_healthy
    networks:
      - internal
      - traefik
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=proxy"
      - "traefik.http.routers.${PROJECT}-postgrest.rule=Host(`${DOMAIN}`) && PathPrefix(`/rest/v1`)"
      - "traefik.http.routers.${PROJECT}-postgrest.entrypoints=websecure"
      - "traefik.http.routers.${PROJECT}-postgrest.tls.certresolver=le"
      - "traefik.http.routers.${PROJECT}-postgrest.middlewares=${PROJECT}-postgrest-strip"
      - "traefik.http.middlewares.${PROJECT}-postgrest-strip.stripprefix.prefixes=/rest/v1"
      - "traefik.http.services.${PROJECT}-postgrest.loadbalancer.server.port=${PGRST_SERVER_PORT}"
    restart: unless-stopped

  frontend:
    image: ${PROJECT}-frontend:latest
    build:
      context: ./frontend
      args:
        VITE_API_URL: /api/v1
        VITE_POSTGREST_URL: /rest/v1
    networks:
      - traefik
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=proxy"
      - "traefik.http.routers.${PROJECT}-frontend.rule=Host(`${DOMAIN}`)"
      - "traefik.http.routers.${PROJECT}-frontend.entrypoints=websecure"
      - "traefik.http.routers.${PROJECT}-frontend.tls.certresolver=le"
      - "traefik.http.routers.${PROJECT}-frontend.priority=1"
      - "traefik.http.services.${PROJECT}-frontend.loadbalancer.server.port=80"
    restart: unless-stopped

volumes:
  postgres_data:

networks:
  internal:
    driver: bridge
  traefik:
    name: proxy
    external: true
```

Critical details:

- `traefik.docker.network=proxy` is **mandatory** when a container is on multiple networks. Traefik needs to know which IP to route to; without this label it may pick the internal network and the request will hang.
- `priority=1` on the frontend router makes the more specific `PathPrefix(/api/v1)` and `PathPrefix(/rest/v1)` routers win. Without it, the catch-all `Host()` router for the frontend can absorb everything.
- `certresolver=le` assumes Traefik is configured with a resolver named `le` (Let's Encrypt). If the host's Traefik names it differently (`letsencrypt`, `myresolver`), match that name — don't rename Traefik's config to match this file.
- `loadbalancer.server.port` is the **container-internal** port the service listens on. It is not published to the host.

## 2. Development `docker-compose.dev.yml`

Same services, but:
- Adds a `gateway` (nginx) container, the only one that publishes a host port.
- Backend and frontend use `Dockerfile.dev` with bind mounts for hot reload.
- No Traefik labels, no external network.

```yaml
services:
  db:
    image: postgres:17-alpine
    env_file:
      - .env
    volumes:
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DATABASE_USER} -d ${DATABASE_DB}"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped

  backend:
    image: ${PROJECT}-backend-dev:latest
    build:
      context: ./backend
      dockerfile: Dockerfile.dev
    env_file:
      - .env
    environment:
      - PORT=${BACKEND_PORT}
    volumes:
      - ./backend:/app
      - backend_node_modules:/app/node_modules
    depends_on:
      db:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "node -e \"fetch('http://localhost:${BACKEND_PORT}/api/v1').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 15s
    restart: unless-stopped

  postgrest:
    image: postgrest/postgrest
    env_file:
      - .env
    depends_on:
      db:
        condition: service_healthy
      backend:
        condition: service_healthy
    restart: unless-stopped

  frontend:
    image: ${PROJECT}-frontend-dev:latest
    build:
      context: ./frontend
      dockerfile: Dockerfile.dev
    environment:
      - VITE_API_URL=/api/v1
      - VITE_POSTGREST_URL=/rest/v1
    volumes:
      - ./frontend:/app
      - frontend_node_modules:/app/node_modules
    restart: unless-stopped

  gateway:
    image: nginx:alpine
    ports:
      - "${GATEWAY_PORT}:80"
    volumes:
      - ./nginx.dev.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      - postgrest
      - backend
      - frontend
    restart: unless-stopped

volumes:
  postgres_data:
  backend_node_modules:
  frontend_node_modules:
```

The `backend_node_modules` / `frontend_node_modules` named volumes prevent the host's `node_modules` (often built for a different platform — macOS, Windows) from clobbering the container's `node_modules` via the bind mount. This is **required** for cross-platform dev.

Run: `docker compose -f docker-compose.dev.yml up --build`.

## 3. nginx dev gateway (`nginx.dev.conf`)

Mirrors the Traefik routing in dev. PostgREST gets prefix-stripped via `rewrite`. The frontend route supports WebSockets (Vite HMR).

```nginx
events {}

http {
  map $http_upgrade $connection_upgrade {
    default upgrade;
    '' close;
  }

  upstream backend   { server backend:${BACKEND_PORT}; }
  upstream postgrest { server postgrest:${PGRST_SERVER_PORT}; }
  upstream frontend  { server frontend:5173; }

  server {
    listen 80;

    location /rest/v1/ {
      if ($request_method = OPTIONS) {
        add_header Access-Control-Allow-Origin  $http_origin always;
        add_header Access-Control-Allow-Methods "GET,POST,PATCH,DELETE,OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization,Content-Type,Prefer,Accept-Profile,Content-Profile,Range,Range-Unit,X-Client-Info" always;
        add_header Access-Control-Max-Age       86400 always;
        add_header Access-Control-Allow-Credentials "true" always;
        add_header Content-Length 0;
        add_header Content-Type  "text/plain";
        return 204;
      }

      add_header Access-Control-Allow-Origin     $http_origin always;
      add_header Access-Control-Allow-Credentials "true" always;
      add_header Access-Control-Expose-Headers   "Content-Range,Content-Location" always;

      proxy_hide_header Access-Control-Allow-Origin;
      proxy_hide_header Access-Control-Allow-Credentials;
      proxy_hide_header Access-Control-Expose-Headers;
      proxy_hide_header Access-Control-Allow-Methods;
      proxy_hide_header Access-Control-Allow-Headers;

      rewrite ^/rest/v1/(.*) /$1 break;
      proxy_pass http://postgrest;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
    }

    location /api/v1/ {
      proxy_pass http://backend;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_read_timeout 300s;
    }

    location / {
      proxy_pass http://frontend;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection $connection_upgrade;
    }
  }
}
```

The `proxy_hide_header` + `add_header` pair on `/rest/v1/` is intentional: PostgREST emits its own CORS headers, and without hiding them the browser sees duplicates and rejects the response.

## 4. Backend Dockerfiles

**Production** (`backend/Dockerfile`) — multi-stage, runs migrations and seed before the server:

```dockerfile
FROM node:22-alpine AS deps
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev

FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
COPY --from=deps    /app/node_modules ./node_modules
COPY --from=builder /app/dist         ./dist
COPY --from=builder /app/drizzle      ./drizzle
COPY package.json ./

ENV PORT=3000
EXPOSE 3000
CMD ["sh", "-c", "node dist/db/migrate.js && node dist/db/seed.js && node dist/index.js"]
```

If the ORM is not Drizzle, replace the migrate/seed commands but keep the pattern: `migrate → seed → start`. Putting migrations in `CMD` (not at image build time) makes the runtime DB the source of truth, not the build environment.

**Development** (`backend/Dockerfile.dev`) — hot reload via tsx, runs migrations on every start:

```dockerfile
FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
ENV PORT=3000
EXPOSE 3000
CMD ["sh", "-c", "npm ci && npx drizzle-kit migrate && npx tsx watch src/index.ts"]
```

The second `npm ci` at runtime catches the case where the bind mount brought in a fresh `package.json` not yet reflected in the named-volume `node_modules`.

## 5. Frontend Dockerfiles

**Production** (`frontend/Dockerfile`) — build with Vite, serve static files with nginx:

```dockerfile
FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL
ARG VITE_POSTGREST_URL
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_POSTGREST_URL=$VITE_POSTGREST_URL
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

`VITE_*` vars are baked at build time — they cannot be changed at container start. The compose passes them via `build.args`.

In-image `frontend/nginx.conf` (SPA fallback for React Router):

```nginx
server {
  listen 80;
  root /usr/share/nginx/html;
  index index.html;

  location / {
    try_files $uri $uri/ /index.html;
  }
}
```

**Development** (`frontend/Dockerfile.dev`):

```dockerfile
FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
```

`--host 0.0.0.0` is required — Vite binds to localhost by default, which is unreachable from outside the container.

## 6. Database bootstrap (`db/init.sql`)

`init.sql` is executed by `postgres:17-alpine` **only on first start** (empty data directory). It creates schemas, roles, and any objects the backend's migrations cannot create (because they require superuser, e.g. roles, or reference `auth.users` which is not in the ORM schema).

Standard PostgREST bootstrap:

```sql
CREATE SCHEMA IF NOT EXISTS api;
CREATE SCHEMA IF NOT EXISTS auth;

-- anon          → unauthenticated callers (PGRST_DB_ANON_ROLE)
-- authenticated → logged-in users (JWT claim "role": "authenticated")
-- authenticator → connection role PostgREST uses; SET ROLE into the above
CREATE ROLE anon          NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE authenticator LOGIN PASSWORD '<replace-with-strong-secret>' NOINHERIT;
GRANT anon, authenticated TO authenticator;

CREATE TABLE IF NOT EXISTS auth.users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text UNIQUE NOT NULL,
  password_hash text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

GRANT USAGE ON SCHEMA auth   TO anon, authenticated;
GRANT USAGE ON SCHEMA api    TO anon, authenticated;
GRANT USAGE ON SCHEMA public TO anon, authenticated;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;
```

For RLS policies and views, defer to the `postgrest-rls` skill — that's its territory. `init.sql` should only set up what migrations cannot.

If `init.sql` changes after the database is already initialized, **it will not re-run**. Either drop the `postgres_data` volume (destructive) or run the new SQL via a one-off command on the running container.

## 7. Required environment variables (`.env`)

Document these in `.env.example` (do not commit `.env`):

```
# Naming
PROJECT=autoblogs
DOMAIN=app.example.com

# Database
DATABASE_USER=app
DATABASE_PASSWORD=<strong>
DATABASE_DB=app
DATABASE_URL=postgres://app:<strong>@db:5432/app

# Backend
BACKEND_PORT=3001
JWT_SECRET=<32+ random chars>

# PostgREST
PGRST_DB_URI=postgres://authenticator:<authenticator-password>@db:5432/${DATABASE_DB}
PGRST_DB_SCHEMAS=public
PGRST_DB_ANON_ROLE=anon
PGRST_JWT_SECRET=<same as JWT_SECRET>
PGRST_SERVER_PORT=3000

# Dev gateway
GATEWAY_PORT=8080
```

Notes:
- The backend and PostgREST must share `JWT_SECRET` / `PGRST_JWT_SECRET` — both sign/verify the same tokens.
- `PGRST_DB_URI` connects as `authenticator`, **not** the superuser. That password must match what `init.sql` set.
- `BACKEND_PORT` and `PGRST_SERVER_PORT` must match what Traefik labels reference.

## 8. GitHub Actions deploy (`.github/workflows/deploy.yaml`)

The deploy model: a **self-hosted runner** builds images locally, then streams them to the production host over SSH via `docker save | gzip | ssh | docker load`. No registry is involved. This is intentional — it avoids registry credentials, rate limits, and the latency of pull-on-host.

The workflow:
1. Detects which directories changed (`backend/`, `frontend/`, `backup/`, infra).
2. Builds only the changed images.
3. Re-tags the current `:latest` as `:previous` on the host before loading new images (manual rollback path: `docker tag X:previous X:latest && docker compose up -d`).
4. Recreates only the changed services (or all, if infra changed).

```yaml
name: Deploy Produção

on:
  push:
    branches: [main]
  workflow_dispatch:

env:
  DEPLOY_HOST: <production-ip>
  DEPLOY_PORT: <ssh-port>
  DEPLOY_USER: <ssh-user>
  DEPLOY_PATH: <project>/
  BACKEND_IMAGE: <project>-backend
  FRONTEND_IMAGE: <project>-frontend
  DB_BACKUP_IMAGE: <project>-db-backup
  SSH_PRIVATE_KEY: ${{ secrets.SSH_PRIVATE_KEY }}
  ENV_FILE: ${{ secrets.ENV_FILE }}

jobs:
  deploy:
    runs-on: self-hosted

    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 2

      - name: Detect changes by path
        id: changes
        uses: dorny/paths-filter@v3
        with:
          filters: |
            backend:
              - 'backend/**'
            frontend:
              - 'frontend/**'
            backup:
              - 'backup/**'
            infra:
              - 'docker-compose.yml'
              - 'db/**'

      - name: Build plan
        id: plan
        run: |
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            backend=true; frontend=true; backup=true; infra=true
          else
            backend="${{ steps.changes.outputs.backend }}"
            frontend="${{ steps.changes.outputs.frontend }}"
            backup="${{ steps.changes.outputs.backup }}"
            infra="${{ steps.changes.outputs.infra }}"
          fi
          {
            echo "backend=$backend"
            echo "frontend=$frontend"
            echo "backup=$backup"
            echo "infra=$infra"
          } >> "$GITHUB_OUTPUT"

          if [ "$backend" != "true" ] && [ "$frontend" != "true" ] && [ "$backup" != "true" ] && [ "$infra" != "true" ]; then
            echo "skip=true" >> "$GITHUB_OUTPUT"
          else
            echo "skip=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Write .env from secret
        if: steps.plan.outputs.skip != 'true'
        run: printf '%s\n' "$ENV_FILE" > .env

      - name: Set up Docker Buildx
        if: steps.plan.outputs.backend == 'true' || steps.plan.outputs.frontend == 'true' || steps.plan.outputs.backup == 'true'
        uses: docker/setup-buildx-action@v3

      - name: Compute SHA tag
        if: steps.plan.outputs.skip != 'true'
        id: meta
        run: echo "sha=${GITHUB_SHA::7}" >> "$GITHUB_OUTPUT"

      - name: Build backend image
        if: steps.plan.outputs.backend == 'true'
        uses: docker/build-push-action@v5
        with:
          context: ./backend
          load: true
          tags: |
            ${{ env.BACKEND_IMAGE }}:latest
            ${{ env.BACKEND_IMAGE }}:production-${{ steps.meta.outputs.sha }}
          cache-from: type=gha,scope=backend
          cache-to: type=gha,mode=max,scope=backend,ignore-error=true

      - name: Build frontend image
        if: steps.plan.outputs.frontend == 'true'
        uses: docker/build-push-action@v5
        with:
          context: ./frontend
          load: true
          tags: |
            ${{ env.FRONTEND_IMAGE }}:latest
            ${{ env.FRONTEND_IMAGE }}:production-${{ steps.meta.outputs.sha }}
          build-args: |
            VITE_API_URL=/api/v1
            VITE_POSTGREST_URL=/rest/v1
          cache-from: type=gha,scope=frontend
          cache-to: type=gha,mode=max,scope=frontend,ignore-error=true

      - name: Configure SSH
        if: steps.plan.outputs.skip != 'true'
        run: |
          mkdir -p ~/.ssh
          printf '%s\n' "$SSH_PRIVATE_KEY" > ~/.ssh/deploy_key
          chmod 600 ~/.ssh/deploy_key
          ssh-keyscan -p "$DEPLOY_PORT" -H "$DEPLOY_HOST" >> ~/.ssh/known_hosts

      - name: Prepare deploy dir
        if: steps.plan.outputs.skip != 'true'
        run: |
          ssh -i ~/.ssh/deploy_key -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST" \
            "mkdir -p '${DEPLOY_PATH}db'"

      - name: Send compose and env
        if: steps.plan.outputs.skip != 'true'
        run: |
          scp -i ~/.ssh/deploy_key -P "$DEPLOY_PORT" \
            docker-compose.yml .env \
            "$DEPLOY_USER@$DEPLOY_HOST:$DEPLOY_PATH"
          scp -i ~/.ssh/deploy_key -P "$DEPLOY_PORT" \
            db/init.sql \
            "$DEPLOY_USER@$DEPLOY_HOST:${DEPLOY_PATH}db/init.sql"

      - name: Stream and load images
        if: steps.plan.outputs.backend == 'true' || steps.plan.outputs.frontend == 'true' || steps.plan.outputs.backup == 'true'
        run: |
          IMAGES=()
          if [ "${{ steps.plan.outputs.backend }}" = "true" ]; then
            IMAGES+=("${{ env.BACKEND_IMAGE }}:latest" "${{ env.BACKEND_IMAGE }}:production-${{ steps.meta.outputs.sha }}")
          fi
          if [ "${{ steps.plan.outputs.frontend }}" = "true" ]; then
            IMAGES+=("${{ env.FRONTEND_IMAGE }}:latest" "${{ env.FRONTEND_IMAGE }}:production-${{ steps.meta.outputs.sha }}")
          fi

          docker save "${IMAGES[@]}" \
            | gzip \
            | ssh -i ~/.ssh/deploy_key -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST" "
              set -euo pipefail
              for tag in ${{ env.BACKEND_IMAGE }} ${{ env.FRONTEND_IMAGE }}; do
                if docker image inspect \"\$tag:latest\" >/dev/null 2>&1; then
                  docker tag \"\$tag:latest\" \"\$tag:previous\"
                fi
              done
              gzip -d | docker load
            "

      - name: Recreate containers
        if: steps.plan.outputs.skip != 'true'
        run: |
          if [ "${{ steps.plan.outputs.infra }}" = "true" ]; then
            RECREATE_ARGS=""
          else
            RECREATE_ARGS=""
            [ "${{ steps.plan.outputs.backend }}"  = "true" ] && RECREATE_ARGS="$RECREATE_ARGS backend"
            [ "${{ steps.plan.outputs.frontend }}" = "true" ] && RECREATE_ARGS="$RECREATE_ARGS frontend"
          fi

          ssh -i ~/.ssh/deploy_key -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST" "
            set -euo pipefail
            cd '$DEPLOY_PATH'

            # Clean orphans from interrupted --force-recreate (Docker renames with hash prefix)
            docker ps -a --format '{{.Names}}' \
              | grep -E '^[a-f0-9]{12}_${PROJECT}-' \
              | xargs -r docker rm -f \
              || true

            docker compose -f docker-compose.yml up -d --force-recreate --no-build $RECREATE_ARGS
            docker image prune -f
          "
```

Required GitHub secrets:
- `SSH_PRIVATE_KEY` — private key authorized on the production host (`~/.ssh/authorized_keys` of the deploy user).
- `ENV_FILE` — full contents of `.env`. The workflow writes it to disk and `scp`s it. Do not commit the rendered `.env`.

Required on the self-hosted runner: Docker + Buildx, SSH client. The runner does not need to talk to the production DB.

Required on the production host:
- Docker + Compose plugin.
- Traefik already running, with an external network named `proxy`. If not, the compose `up` will fail with `network proxy declared as external, but could not be found`.
- The deploy user must be in the `docker` group (or use sudo with NOPASSWD for the docker commands).
- Inbound port 22 (or custom SSH port) reachable from the runner; 80/443 reachable from the internet for Traefik.

## 9. Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| Traefik returns 404 on `/rest/v1/...` | `stripprefix` middleware not attached to the router | Confirm `routers.<name>.middlewares=<middleware-name>` matches the middleware key exactly. |
| Backend reachable on `/api/v1` but frontend serves index.html for `/api/v1/foo` | Frontend router catching everything | Set `priority=1` on the frontend router, or higher priority on API/PostgREST routers. |
| `network proxy declared as external, but could not be found` | Traefik network not created | `docker network create proxy` (or start Traefik with the right compose). |
| `connect ECONNREFUSED db:5432` from backend on first start | Backend started before DB was actually accepting connections | Confirm `depends_on.db.condition: service_healthy` and the DB healthcheck is correct. |
| Vite dev server unreachable from host | Vite bound to `localhost` only | `--host 0.0.0.0` in the dev `CMD`. |
| Frontend production build calls `localhost:3000` | `VITE_*` vars not passed at build time | Use `build.args` in compose and `ARG` + `ENV` in the Dockerfile. They are baked into the bundle at build, not read at container start. |
| Deploy works first time, second time fails with name conflicts | Previous `--force-recreate` was interrupted, leaving `<hash>_<service>` zombie containers | The deploy step already includes a cleanup; keep it. |
| `init.sql` edits don't take effect after redeploy | `init.sql` only runs on empty data volume | Apply the SQL by hand via `docker compose exec db psql ...`, or drop the volume in a maintenance window (destructive). |
| `docker compose up -d` recreates the DB and wipes data | A breaking change to the `db` service in compose | `db` is a stateful service. Avoid `--force-recreate` for it in routine deploys (the workflow already scopes `$RECREATE_ARGS` to changed app services unless infra changed). |
| CORS errors on PostgREST in dev | Duplicate CORS headers — PostgREST sets its own, nginx adds more | Keep the `proxy_hide_header` block in `nginx.dev.conf` exactly as shown. |
| `Cannot find module '/app/dist/index.js'` in prod | Build stage failed silently or `dist/` is not what the run stage copies | Verify `npm run build` produces `dist/`; match the path in `COPY --from=builder`. |

## 10. Adapting to variations

- **No PostgREST**: drop the `postgrest` service and its Traefik labels. Keep `auth.users` if the backend needs it.
- **Different ORM** (Prisma, Knex, TypeORM): swap the migrate command in the backend `CMD`. Keep `migrate → seed → start` order.
- **Mono-repo with multiple frontends**: duplicate the `frontend` service block with distinct names, different `Host()` rules, distinct image names.
- **Registry-based deploy** (instead of `docker save | ssh`): replace the build+save+load steps with `docker/login-action` + `push: true` on `build-push-action`, and have the host run `docker compose pull && docker compose up -d`. The path-filter and recreate logic stays the same.
- **Multiple environments** (staging + prod): parameterize `DOMAIN`, image tags, and the deploy host; gate by branch or environment in the workflow.

## What this skill does NOT cover

- How to configure the Traefik instance itself (entry points, cert resolvers, dashboard). Assumed to be already running.
- Row-Level Security policies and JWT auth — see the `postgrest-rls` skill.
- Backup/restore mechanics in detail — the `backup/` service is optional and structurally identical to the autoblogs version (`alpine + aws-cli + postgresql-client + cron`).
- Application logic, framework choice details, or schema design.
