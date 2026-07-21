# Claude Code Token Audit — Design

**Date:** 2026-07-02
**Status:** Approved (design), pending implementation plan
**Location:** `token-audit/` (new subproject in the workspace)

## Goal

A local, realtime dashboard that reads Claude Code's session logs
(`~/.claude/projects/**/*.jsonl`) and reports token usage, cache in/out, cost,
and billing comparisons — modeled on the "Claude Code — Token Audit" reference
dashboard (total cost, amount saved, cache hit rate, input/output tokens,
session count, daily cost chart, per-session breakdown table).

A SQLite database is the incremental store ("codebase memory / ledger" for
Claude Code usage). Adopting the separate `codebase-memory-mcp` tool is out of
scope for this project.

## Decisions locked in

- **Data scope:** global — all projects under `~/.claude/projects/**/*.jsonl`.
- **Billing:** compute API-equivalent cost; show cache savings, saved-vs-subscription,
  and merge RTK savings. Realtime auto-refresh.
- **Stack:** Python FastAPI + SQLite backend, React/Vite/Tailwind frontend.
- **Language:** all artifacts in English (chat replies mirror the user's language).

## Data source: JSONL usage record

Each assistant line in a session file carries `message.usage`. Fields we use
(verified against a live file):

```
input_tokens                              # uncached input, full price
cache_creation_input_tokens               # total cache write (sum of the two below)
cache_creation.ephemeral_5m_input_tokens  # 5-minute-TTL cache write (1.25x input)
cache_creation.ephemeral_1h_input_tokens  # 1-hour-TTL cache write (2x input)
cache_read_input_tokens                   # served from cache (0.1x input)
output_tokens                             # full output price
service_tier                              # "standard" | ...
```

Line-level context: `model`, `timestamp`, `sessionId`, `cwd` (project),
`requestId`, `uuid`, `version`. We ingest lines where `type == "assistant"`
and `message.usage` is present.

## Pricing model

Rates are per 1M tokens (list API prices, cached 2026-06-24). Cache multipliers:
read = 0.1x input, 5m write = 1.25x input, 1h write = 2x input.

| Model family (ID prefix)        | input | output |
|---------------------------------|-------|--------|
| `claude-opus-4-8/4-7/4-6/4-5`   | $5    | $25    |
| `claude-sonnet-5` / `sonnet-4-6`| $3    | $15    |
| `claude-haiku-4-5`              | $1    | $5     |
| `claude-fable-5`/`mythos-5`     | $10   | $50    |

Per-message cost:

```
cost = input*r_in
     + output*r_out
     + cache_read*0.1*r_in
     + eph_5m*1.25*r_in
     + eph_1h*2.0*r_in
```

Unknown model → mark `pricing_status = "unknown"`, exclude from cost totals,
surface count/tokens separately (no guessed price). Pricing lives in
`pricing.py` (not in the DB) so a rate change never requires re-ingest.

## Derived metrics (the numbers in the reference dashboard)

- **Total cost** = sum of per-message API-equivalent cost.
- **Cache savings** = `sum(cache_read) * 0.9 * r_in` — money saved by cache reads
  vs. paying full input price. Explains "high $ saved at 90% cache hit".
- **Cache hit rate** = `sum(cache_read) / (sum(cache_read) + sum(cache_creation) + sum(input))`.
- **Saved vs subscription** = `total_cost - subscription_fee` (fee from `config.toml`;
  monthly fee prorated to the log's date range).
- **RTK savings** = tokens saved reported by `rtk gain` (CLI-layer), shown as a
  separate line — distinct from cache/API savings, not double-counted.
- **Input / output tokens**, **session count**, **daily cost series**.

## Architecture — 5 units

### 1. `ingest.py` — parser + incremental loader
- Walk `~/.claude/projects/**/*.jsonl`.
- For each file, resume from `files.bytes_ingested` (append-only JSONL ⇒ only
  new bytes parsed). If a file shrank/rotated (mtime older or size < recorded),
  re-ingest from 0 for that file.
- Parse each new line; on assistant+usage lines, upsert a `messages` row keyed by
  `uuid` (idempotent — re-ingesting a line is a no-op).
- Update `files(path, mtime, size, bytes_ingested, lines_ingested)`.
- Callable as one-shot (`python -m token_audit.ingest`) and from the watcher.

### 2. SQLite store — `audit.db`
```sql
CREATE TABLE messages (
  uuid TEXT PRIMARY KEY,
  session_id TEXT, project TEXT, model TEXT,
  ts TEXT,                       -- ISO-8601
  input_tokens INTEGER, cache_read INTEGER,
  cache_create_5m INTEGER, cache_create_1h INTEGER,
  output_tokens INTEGER, service_tier TEXT
);
CREATE INDEX idx_messages_session ON messages(session_id);
CREATE INDEX idx_messages_ts ON messages(ts);
CREATE INDEX idx_messages_model ON messages(model);

CREATE TABLE files (
  path TEXT PRIMARY KEY,
  mtime REAL, size INTEGER,
  bytes_ingested INTEGER, lines_ingested INTEGER
);
```
Only raw token counts stored — cost is computed in the API layer via `pricing.py`.

### 3. `pricing.py` — rate table + cost functions
- `RATES` dict keyed by model-ID prefix; `rate_for(model)` resolves prefix.
- `message_cost(row)` and `cache_savings(row)` pure functions.
- `CACHE_READ_MULT=0.1`, `WRITE_5M_MULT=1.25`, `WRITE_1H_MULT=2.0`.

### 4. `server.py` — FastAPI backend
Endpoints (all read `audit.db`, compute cost on the fly):
- `GET /api/summary` → totals: total_cost, cache_savings, saved_vs_subscription,
  rtk_savings, cache_hit_rate, input_tokens, output_tokens, session_count,
  unknown_model_tokens.
- `GET /api/daily` → `[{date, cost, input, output, cache_read}]`.
- `GET /api/sessions?limit=&offset=` → per-session rows (project, model(s), first/last ts,
  tokens, cost) for the breakdown table.
- `GET /api/by-model` → per-model tokens + cost.
- `GET /api/rtk` → parsed `rtk gain` savings (best-effort; empty if rtk absent).
- `GET /api/stream` (SSE) → pushes a `summary-updated` event after each ingest cycle.
- **Watcher**: `watchdog` observer on `~/.claude/projects/`; on change, debounce
  (~1s), run incremental ingest, emit SSE. Started in FastAPI lifespan.
- `config.toml`: `subscription_monthly_usd`, `projects_dir` (default `~/.claude/projects`),
  `db_path`.

### 5. Frontend — React/Vite/Tailwind
- Dark theme matching the reference. Top row: stat cards
  (Total cost, Saved, Cache hit rate, Input tokens, Output tokens, Sessions).
- Daily cost bar chart (lightweight — Recharts or hand-rolled SVG; decided in plan).
- Per-session breakdown table (sortable, paginated).
- By-model panel.
- Auto-refresh: subscribe to `/api/stream`; on `summary-updated`, refetch.

## Running
`token-audit/demo.sh`:
1. Create venv, install deps (`fastapi`, `uvicorn`, `watchdog`).
2. Run initial full ingest.
3. Start uvicorn (random-ish port, e.g. 8010) with the watcher.
4. `npm install && npm run dev` for the Vite frontend (e.g. 5190).
`token-audit/README.md` documents ports and commands.

## Non-goals
- No write access to `~/.claude/` — read-only ingest.
- No adoption/wiring of `codebase-memory-mcp` (separate effort).
- No upload of usage data anywhere — 100% local.

## Testing
- `pricing.py`: unit tests for each model family + cache multiplier math.
- `ingest.py`: fixture JSONL (a few crafted lines incl. unknown model, missing
  usage, 5m/1h split) → assert `messages` rows and idempotent re-ingest.
- Incremental: ingest a fixture, append lines, re-ingest, assert only new rows added.
- API: `/api/summary` against a seeded `audit.db` → assert derived metrics.
```
