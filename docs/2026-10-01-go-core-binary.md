# FlightDeck core as one Go binary

Decision record, 2026-10-01. Slice 1 is planned in
`docs/superpowers/plans/2026-10-01-go-core-ledger-cli.md`.

## What it is

`fd` is a single static Go binary that owns the Claude Code spend ledger on one host.
The same binary is the CLI (`fd spend`, `fd sessions`, `fd ingest`, `fd import`) and
the daemon (`fd serve`), which keeps the ledger current and answers the spend API the
existing React UI reads. It runs beside the Python backend until parity is shown, then
replaces its spend path.

## Decisions carried from the 2026-09-29/30 discussion

| Topic | Decision |
|---|---|
| Core | Go single binary, `CGO_ENABLED=0`, daemon and CLI in one |
| Node storage | SQLite through `modernc.org/sqlite` (pure Go). Postgres stays for the team control plane (Odoo 19 CE), not for a node |
| UI | The React/Vite UI is reused; `fd serve --ui frontend/dist` serves it. An Electron shell comes later |
| Scope | The slim core: ingest, ledger, spend, quota, burn, thinking, tasks, transcript, runner. Treasures, Radar, the MCP domains, Systems and Charts move out (see the split table of 2026-09-30) |
| CLI contract | Kept from `backend/flightdeck/cli.py`: with `--json`, one JSON document on stdout and nothing else; exit 0 ok, 2 refusal (`{"error": …}` on stdout), 3 unknown command, 1 crash (stderr) |
| Build | No Go on the host. `make go-test` / `make go-build` run `golang:1.26-alpine` in Docker as the calling user |
| Prices | A table in the ledger, seeded from defaults and edited with `fd pricing` (decided 2026-10-01) |

## Revised: the ledger is owned data, not a cache

The earlier plan split storage into `derived.db` (rebuildable from transcripts) and
`owned.db`, with billed rows on the derived side. The evidence says otherwise:

- Claude Code deletes old transcripts. On 2026-09-23, 66 of 166 sessions in the
  ledger had no transcript left.
- Postgres empties UNLOGGED tables after an unclean stop. `pgcore-17` logged crash
  recovery on 2026-08-25 and on 2026-09-29. After the 2026-09-29 one the live
  ledger dropped to 12 sessions; `agent_activity` (a LOGGED table) still lists 221.
  The ledger now starts on 2026-07-10, while `backend/audit.db` holds rows from
  2026-06-22, which fits the 2026-08-25 recovery having cut June too.
- The Python reader kept a process-local "file unchanged" cache, so after the
  2026-09-29 truncation it never re-read the 520 files that had not changed since.

So, for `fd`:

1. Billed rows live in one durable SQLite file, `~/.flightdeck/fd/ledger.db` (WAL).
   A re-read never deletes a row; a deleted transcript leaves its rows in place.
2. The per-file read cursor lives in the same file and commits in the same
   transaction as the rows it covers. There is no in-memory skip state: the
   cursor row is the only "already read" signal.
3. A rebuildable `cache.db` arrives with the search slice, for the bulky text
   mirror. Slice 1 has no cache.

## Ingest contract (the one every reader follows)

- Files: `<projects>/**/*.jsonl`, skipping names that start with `.`, read oldest
  mtime first (then by path). Subagent files carry the parent `sessionId`.
- Read by byte offset. A trailing line without `\n` is left for the next pass. A
  line that does not parse (one real file has a run of NUL bytes) is skipped.
- A file shorter than the bytes already read was rewritten: read it from the top.
- Billed unit: `(message.id, requestId)`; a line without `message.id` is keyed
  `uuid:<line uuid>`. A line without `uuid` is ignored. `usage` that is absent,
  `null` or `{}` is not billed.
- The first session to bring a response keeps it (a resumed or forked session
  copies its parent's responses). A later line may raise `output_tokens`, never
  lower it.
- Titles: `custom-title` beats `ai-title` whatever the record order.

## Metrics semantics (as `backend/flightdeck/metrics.py`)

- Pricing as `pricing.py`: longest prefix wins, cache-read overrides (Opus 5.5
  0.05, Fable/Mythos 5.1 0.025, others 0.1), writes 1.25× and 2×, fast-mode rates,
  US inference ×1.1, promotions dated by the message and ending on a bare date.
  In `fd` these numbers are data in the price table (next section), not code.
- Grain `(UTC day, model, session, speed, geo)`, priced once per cell.
- `cache_savings` = uncached − cached cost (net of the write premium).
  `input_tokens_raw` = `total_context_tokens` = input + cache read + cache write.
  `saved_vs_subscription` uses `(max_ts − min_ts).days + 1` over 30.4375-day months.
- `<synthetic>` is excluded from by-model, counted in daily and in a session's models.
- Ranges use the local calendar date compared against UTC timestamps, as
  `runtime.since()` does. This is a quirk, kept for parity and fixed after cut-over.

## Price table (owner's decision, 2026-10-01)

Prices are a table, not constants in code.

- Stored in the ledger: `price_models` (one row per model prefix and start date)
  and `price_geo` (inference_geo multipliers). A new ledger is seeded once from
  the binary's `defaults.json`; after that the owner's edits are never overwritten.
- A row: prefix, `valid_from` (empty, or a bare UTC date judged by the message's
  timestamp), input and output per MTok, cache-read and cache-write multipliers
  (published 0.1 / 1.25 / 2.0 when left out), an optional fast-mode pair, a note.
  A price change is a new dated row, so history before it keeps its price;
  promotions are dated rows too.
- Rate choice: the longest matching prefix, then its latest row in effect at the
  message's time; an undated message takes the latest row. A model with no row
  in effect is unpriced, never priced from a shorter prefix.
- Edited with `fd pricing list | set | import | defaults`. `import` replaces the
  whole table from JSON in the `list --json` shape and refuses unknown fields.
  Costs are computed at read time, so an edit restates every total at once.
- Defaults match `pricing.py` and Anthropic's table read 2026-10-01, including
  `claude-sonnet-5-5` at $2/$10 (now also an explicit row in `pricing.py`).

## API compatibility in slice 1

`GET /api/summary`, `/api/daily`, `/api/by-model`, `/api/sessions` with the same
query parameters and JSON keys as FastAPI. Gaps:

- `rtk_savings` is 0, and `daily[].agent_hours*` are 0 until the activity slice.
- `sessions[].subagents` is not returned.
- Every other `/api/*` path answers 404 with `{"error": …}`.
- Found on 2026-10-01: the current UI loads summary, daily, sessions and
  by-model in one `Promise.all` with `/api/usage-windows` and `/api/quota`
  (`App.jsx` `load`), so one 404 leaves the whole Spend view on skeletons.
  `fd serve --ui` shows the Spend view only after the quota slice serves those
  two, or after the UI loads them separately. The four spend endpoints answer 200
  (screenshot and request list: `devtools_mcp_trace/fd-serve-2026-10-01/`).

## Acceptance

The oracle is the Python backend reading the same transcript snapshot into a fresh
SQLite file in the same file order, never the live service. `summary`, `daily`,
`by_model` and `sessions` must agree for all four ranges (floats within 1e-9
relative), apart from the gaps above.

## History import

`fd import --pg-copy` reads the `pg_restore -f - --data-only --table=messages`
output of `~/.flightdeck/backups/flightdeck-2026-09-23-pre-idefix.dump`: 47,031
billed rows from 2026-07-10 to 2026-09-23 across 185 sessions, 14,024 of them
without `message.id`.

- A row with `message.id` is skipped when the key is already in the ledger.
- A row without one is imported only when its session has no transcript row.
- Imported rows carry `source = 'import'`, so `DELETE FROM responses WHERE
  source = 'import'` reverses an import.
- The command reports how many imported rows match a transcript row on the
  input-side token tuple, the double-count risk, and does not remove them.

## Not in slice 1

- Turns, activity and agent-hours.
- Thinking and response timing.
- Quota polling and burn rate.
- Task labels and the logbook.
- Transcript search.
- The runner merge (fd-runner), the hook spool, in-process MCP.
- The UI embed and the Electron shell.

Each of these gets its own plan, built on the same ledger and ingest contract.

## Open

- The binary name for other machines: `fd` is free on this host but collides with
  fd-find elsewhere. Decide in the packaging slice.
- 2026-06-22 to 2026-07-10 exists only in `backend/audit.db`, keyed by line uuid
  and not deduplicated (about 3.7× over). Importing it needs the heuristic key; not
  in slice 1.
- How much to trust the 14,024 heuristic-deduplicated rows: the import reports
  the overlap; the decision stays with the owner. Measured on the Python ledger
  merge of 2026-10-01: 7 rows, $2.63.

Resolved 2026-10-01: Sonnet 5.5's published rate is $2/$10, the same as Sonnet 5,
so the prefix match had priced it correctly; both tables now list it explicitly.

## Measured (parity run, 2026-10-01)

| Check | Result |
|---|---|
| Corpus snapshot | 673 files, 1.9 GB (`rsync` of `~/.claude/projects`, mtimes kept) |
| Python cold ingest (oracle, SQLite) | 14.5 s |
| `fd` cold ingest | 17.6 s, 45,474 responses |
| `fd` second ingest (nothing new) | 0.009 s |
| Parity, 4 ranges × 4 answers | 0 differences (`scripts/go-parity/compare.py`); all-time $8,761.23, 141 sessions on both sides |
| `GET /api/summary?range=all` p50 | 71 ms (10 requests) |

`fd`'s cold read is slower than Python's: each billed line runs a SELECT and an
INSERT without prepared statements. It only matters on the first read of a
ledger; preparing the two statements per file is the obvious first fix.
