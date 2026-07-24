# FlightDeck — Host + PostgreSQL Migration Plan

Move FlightDeck from the containerized app model to **app-on-host + shared
durable PostgreSQL**, cleanly, without hurting the local dev loop.

> Status: in progress. Owner: nathan. Last updated: 2026-07-21.
>
> **Progress:** Phase 0 (FastAPI restructure + DB read seam) — **done, merged**
> (`server.py` 617→88, routers/ + runtime.py). Host runner (Makefile + systemd
> `--user` unit + host `.venv`) — **done, verified** (boots on host, 89 tests
> pass). `TOKEN_AUDIT_DATABASE_URL` knob added (defaults to SQLite).
>
> **Phase 2 — DONE (live on PostgreSQL).** `db.py` is engine-aware (psycopg3 +
> pool when `database_url` set, SQLite otherwise/for tests). PG schema splits
> UNLOGGED (messages/tool_calls/files) vs LOGGED (session_meta/credentials).
> The systemd service now serves :8010 from PostgreSQL (:5442); SQLite kept for
> the test suite. DB provisioning stays the owner's; the URL lives in a
> gitignored `.env`. Phase 3 (drop the SQLite single-writer lock/WAL) is moot —
> that code is guarded to the SQLite path and simply not used on PG.

## Guiding principles (locked)

1. **Single process / 1 worker is the target.** Scaling out is an explicit
   **non-goal**. The in-process snapshot cache, the `asyncio.Event` SSE fan-out,
   and the in-process watcher thread all assume one process — we keep them. This
   removes any need for a shared cache / pub-sub right now.
2. **No Valkey yet.** Use PostgreSQL **UNLOGGED tables** for the high-churn
   derived data. FlightDeck connects to **one durable PostgreSQL shared by the
   machine's services** (odoo, discount-service, FlightDeck, …) via its own
   database/role. Valkey is adopted only if a **real** pain point appears
   (multi-worker, or cross-service cache), not preemptively.
3. **Clean conversion, local-dev-first.** Isolate the DB dialect behind one
   thin layer so the engine swap is localized and reversible. The build/dev
   pipeline optimizes for **fast local iteration first** (hot reload front +
   back); durable/prod running is a thin second mode.

## Scope note — this is a restructure, not a framework swap

The backend is **already FastAPI** (`server.py` builds a `FastAPI` app; `hub/`,
`systems/`, `agui/` are already `APIRouter`s). So there is no framework to adopt
— the "clean conversion" is **restructuring the existing FastAPI app** into an
idiomatic shape. **Staying sync is a decision, not an accident**: the app is
sync (1 worker + threads), and psycopg3-sync keeps it that way. Converting to
async (or swapping to Litestar/Django-Ninja) would touch ~the whole backend for
near-zero benefit at 1 worker — explicit **non-goal**.

**Blast radius of the restructure** (folded into Phase 0 below):

| Area | Impact | Why |
|---|---|---|
| `server.py` (617 LOC) | **heavy — split** | mega `create_app()`: 32 inline endpoints + lifespan + watcher/threads + cache in one file |
| ~3 new scaffolding files (~150 LOC) | new | `settings.py`, `dependencies.py` (DB session), app factory |
| 9 logic modules (`metrics/ingest/transcript/route/repodiff/pulse/quota/ccusage/usage_poll`, ~2000 LOC) | **light** | pure functions taking a `conn`; only the DB-access call sites change — same sweep as the DB layer below |
| `hub/` · `systems/` · `agui/` routers | **~none** | already `APIRouter`, already the target pattern |
| Frontend (all of React) | **none** | API paths + shapes unchanged → the SPA is the firewall that caps the blast radius |

Net: **one file restructured heavily + ~3 small new files + a mechanical
DB-access sweep**; ~85% of backend logic and 100% of the frontend untouched. The
DB-access sweep is shared with the SQLite→Postgres swap, so both are done once.

## Target architecture

```
HOST
  app (no container)
    dev  : uvicorn --reload  +  vite HMR (:5190, proxy /api → :8010)
    prod : systemd --user → uvicorn serving frontend/dist (:8010)
    supervisor.py → tmux 3.4 native  (agent sessions, Tier 2)
        │ psycopg3 (sync) + pool
        ▼
SHARED INFRA (Docker, durable)
  postgresql   :5432   ← shared by odoo / discount-service / flightdeck / …
                         FlightDeck uses db `flightdeck` + role `flightdeck`
  (valkey :6379 — DEFERRED, only if a pain point forces it)
```

- **No app container.** Docker stays only for shared stateful infra.
- **Runner**: `systemd --user` for the durable instance (native boot-persist via
  `loginctl enable-linger`, explicit `Environment=PATH=` to fix the nvm trap).
  Dev never runs under systemd.

## Why UNLOGGED is safe here (the load-bearing rationale)

`audit.db` is a **derived index of `~/.claude/projects/**.jsonl`** — every row is
re-creatable by re-ingesting the transcripts. So durability of the bulk tables
is not required: a crash that truncates them is repaired by a re-ingest on
startup (the `files` table gates incremental reads; empty tables force a full
re-scan). UNLOGGED skips the WAL → faster bulk ingest, at the cost of "emptied on
unclean shutdown", which we can always rebuild.

**But split by durability need** — not everything is derived:

| Table | Derived from JSONL? | Logging |
|---|---|---|
| `messages`, `tool_calls`, `files` | yes (rebuildable) | **UNLOGGED** |
| `session_meta` (user custom titles) | **no** (user input) | **LOGGED** |
| Hub `credentials` | **no** (secrets) | **LOGGED** |
| Hub flows | not in DB (JSON files) | n/a |

On startup, if the UNLOGGED tables are empty but transcripts exist → force a full
re-ingest (the existing backfill mechanism already does this shape for
`tool_calls`).

## Phase 0 — Clean FastAPI restructure + DB layer (still on SQLite, no behavior change)

Two refactors that touch the same surface (`server.py` + DB call sites), done
together. **API paths/shapes stay identical** → frontend untouched, and pytest +
a UI drive are the regression guard.

**FastAPI restructure**
- [ ] Create the project's own `.venv`; stop relying on the `uvicorn` on PATH
      (currently resolves to another project's venv).
- [ ] Add `token_audit/settings.py` — a typed settings object (env + config.toml)
      replacing scattered `os.environ.get` reads.
- [ ] Extract the 32 inline `@app` endpoints from `create_app()` into
      `APIRouter` modules by domain (`routers/core.py` summary/daily/by-model,
      `sessions.py`, `charts.py`, `diff.py`, `quota.py`, `stream.py`, …), matching
      the `hub/ systems/ agui/` pattern that already exists.
- [ ] Move the watcher / poll threads / snapshot cache out of `create_app()` into
      a small **lifespan + service** module; `server.py` shrinks to app assembly
      (`FastAPI()` + `include_router(...)` + static mount).
- [ ] Add `dependencies.py` — a FastAPI dependency that yields a DB connection
      (replaces ad-hoc `_read_conn()` / passing `write_conn` around).

**DB layer (still SQLite)**
- [ ] Add `psycopg[binary]`, `psycopg_pool` to `requirements.txt` (keep sqlite
      wired for now).
- [ ] Introduce the **thin DB layer** behind that dependency (`read_conn()`
      context-manager, `write_conn`, `dict` row factory) and route
      `metrics.py` / `ingest.py` / `systems/*` / `agui/routes.py` through it —
      **while still backed by SQLite**. Land the structure now; swap the engine
      in Phase 2.
- [ ] Keep SYNC throughout. Use **psycopg3 sync + `psycopg_pool`**, NOT asyncpg —
      an async rewrite of every query is unnecessary churn and out of scope.

_Exit check for Phase 0: pytest green + UI drive identical, on SQLite still._

## Phase 1 — Shared PostgreSQL bring-up

- [ ] `compose.infra.yml`: `postgres:16` + named volume `pgdata`, published
      `127.0.0.1:5432`. This is the machine's shared, always-on PG (its own
      lifecycle, not tied to FlightDeck).
- [ ] Bootstrap role + database: `CREATE ROLE flightdeck LOGIN …; CREATE DATABASE
      flightdeck OWNER flightdeck;` (FlightDeck stays in its own DB so it
      coexists with other services cleanly).
- [ ] Config: `TOKEN_AUDIT_DATABASE_URL` (e.g. `postgresql://flightdeck@127.0.0.1
      /flightdeck`); keep `db_path` for a SQLite fallback during transition.

## Phase 2 — Port the schema + SQL dialect

- [ ] Rewrite `_SCHEMA` as PostgreSQL DDL:
  - `UNLOGGED` on `messages` / `tool_calls` / `files`; `LOGGED` on
    `session_meta` / `credentials`.
  - `TEXT` timestamps stay TEXT (ISO strings) — no type change needed.
  - Indexes carry over 1:1.
- [ ] Dialect fixes (localized to the DB layer + call-site SQL):
  - paramstyle `?` → `%s`.
  - `INSERT OR IGNORE` → `INSERT … ON CONFLICT (pk) DO NOTHING`.
  - drop `PRAGMA journal_mode=WAL` / `busy_timeout`, `executescript`
    (split statements), `sqlite3.Row` → psycopg dict row factory.
- [ ] Schema management: a **lightweight versioned bootstrap** (a
      `schema_version` row; on mismatch: `DROP`/recreate + re-ingest) rather than
      Alembic — justified because the bulk data is derived/rebuildable. (Revisit
      Alembic only once a non-derived table needs a preserving migration.)

## Phase 3 — Concurrency simplification

- [ ] Remove the single-writer `write_conn` + `threading.Lock` + WAL/busy-timeout
      dance. Postgres handles concurrent writes; the watcher remains the de-facto
      sole writer, per-request reads come from the pool.
- [ ] Confirm no double-ingest: still one watcher, one worker.
- [ ] Keep `asyncio.Event` SSE + `app.state.snap` cache **as-is** (1-worker
      assumption holds).

## Phase 4 — Dev / build pipeline (local-dev-first)

- [ ] `make dev` (or an npm script via `concurrently`): run `uvicorn --reload
      --reload-dir token_audit` + `vite` together; both hot-reload. DB is the
      always-on shared PG (not spun up per session).
- [ ] Confirm the vite dev **proxy** forwards `/api` → `:8010` (api.js uses
      relative fetch).
- [ ] `make build` → `vite build` → `frontend/dist`; prod = systemd uvicorn
      serving `dist` (single process, no vite).
- [ ] `~/.config/systemd/user/flightdeck.service` with explicit
      `Environment=PATH=…/.nvm/…/v22/bin:/usr/bin:/bin`, `WorkingDirectory`,
      `.venv/bin/uvicorn … (no --reload)`, `Restart=always`;
      `enable --now` + `loginctl enable-linger`.

## Phase 5 — Cutover + verification

- [ ] Point config at PG, start fresh, let the watcher re-ingest.
- [ ] **Verify by comparison**: row counts + a few `/api/summary`, `/api/by-model`,
      `/api/systems/mcp` responses vs the SQLite baseline (must match).
- [ ] Drive the UI (Spend, Comms, Relay) end-to-end; run pytest (89+ pass).
- [ ] Rollback story: the SQLite path stays behind the config flag for one
      iteration; ultimate rollback is "re-ingest into sqlite" (data is derived).

## Deferred (explicitly gated — do NOT build now)

| Item | Gate that would justify it |
|---|---|
| Valkey (cache + pub/sub) | multi-worker, or a measured cache/cross-service pain |
| Multi-worker uvicorn | single-worker CPU/latency actually saturates |
| Alembic migrations | a non-derived table needs a preserving migration |
| Host broker / agent | only if the app returns to a container |

## Risks / caveats

- **Shared PG vs odoo's version pin.** The `nakivo` odoo 12 stack pins its own
  Postgres 12 and typically runs its own server; "one shared PG" realistically
  means one general-purpose server for the *non-odoo* services (FlightDeck,
  discount-service, …) with odoo kept separate — or match versions deliberately.
  FlightDeck only needs a `DATABASE_URL`, so it points at whatever the shared
  server is.
- **nvm PATH under systemd** — the #1 host-mode trap; fixed by explicit
  `Environment=PATH=`.
- **`session_meta` / `credentials` durability** — must be LOGGED (see table).
- **Dev/prod port clash** on :8010 — stop the systemd unit while developing, or
  give dev its own port.
