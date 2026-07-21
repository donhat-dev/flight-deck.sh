# Retired container mode. Kept valid/coherent; host mode (Makefile/demo.sh) is
# the supported path. Build context is the REPO ROOT (compose sets `context: ..`
# + `dockerfile: docker/Dockerfile`), so COPY paths below are repo-root-relative.
# The in-container tree mirrors the monorepo (/app/backend/flightdeck +
# /app/frontend/dist) so server.py's ../../frontend/dist static mount resolves.

# ---- stage 1: build the frontend (also our source of Node for ccusage) ----
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 2: python backend ----
FROM python:3.12-slim
WORKDIR /app

# git: powers the read-only "Diff" instrument (backend/flightdeck/repodiff.py)
# against the workspace repos bind-mounted at /workspace. Only diff/log/compare
# run in the container; fetch/pull are host operations.
RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r ./backend/requirements.txt

# Bring Node (from the node:20 stage) + install ccusage globally, so the
# backend can shell out to `ccusage` for the 5h-block / weekly usage windows.
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && npm install -g ccusage @anthropic-ai/claude-code \
 && npm cache clean --force

COPY backend/flightdeck/ ./backend/flightdeck/
COPY backend/config.toml ./backend/
# Built SPA (from stage 1) served at / by the backend's static mount, at the
# repo-root-relative path server.py expects (../../frontend/dist from the pkg).
COPY --from=frontend /fe/dist ./frontend/dist

# Run with cwd=backend so the `flightdeck` package imports.
WORKDIR /app/backend

EXPOSE 8010
ENV TOKEN_AUDIT_CONFIG=/app/backend/config.toml \
    TOKEN_AUDIT_CCUSAGE_CMD=ccusage \
    TOKEN_AUDIT_CLAUDE_BIN=claude \
    PYTHONUNBUFFERED=1

# Default command (no reload). docker-compose overrides with --reload for dev.
CMD ["uvicorn", "flightdeck.server:app", "--host", "0.0.0.0", "--port", "8010"]
