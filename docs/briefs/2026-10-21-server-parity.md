# Go and Python servers answer the same

Feature: Go and Python servers answer the same
Goal: `fd serve` returns the same spend data as the FastAPI app for the shared routes, so the web client can move to the Go server route by route, and no core Python module depends on FastAPI.
Now: `api/openapi/spend.yaml` records four differences — `rtk_savings` is 0 in Go, `agent_hours*` are 0 in Go, `/api/sessions` rows have `subagents` only in FastAPI, errors are `{"error"}` in Go and `{"detail"}` in FastAPI (the web client reads only `detail`); four Python modules outside the server layer import FastAPI (`agui.routes`, `systems.containers`, `systems.mcp`, `systems.skills`).
Direction: port the missing pieces to the Go core from the Python code (rtk reading, per-turn busy time, subagent usage), switch Go's HTTP errors to `{"detail"}`, move the four modules' HTTP parts under `python/flightdeck/routers/`, and prove parity with one transcript fixture ingested by both servers.
Constraints: the CLI's `{"error"}` envelope stays; the FastAPI app keeps its routes and responses; live services keep running from `main`; work on `feat/server-parity` in `.worktrees/parity`, merged only with `make check` green, worktree removed after merge.
Done when: SP-1..SP-6 hold.
Not now: `agent_hours_est` in Go (it rebuilds busy time for sessions whose transcripts are gone, from stored prompt texts the Go ledger does not keep); moving the web client to `fd serve`; other FastAPI routes.
Refs: `api/openapi/spend.yaml`, `docs/briefs/2026-10-21-monorepo-l3.md`, map `devtools_mcp_trace/2026-10-21-server-parity/devin/explore-1.md`.

## Done when

- SP-1 `/api/summary` from `fd serve` reports `rtk_savings` the way FastAPI does (`rtk gain`, tokens saved × 5 USD per million, 0 when `rtk` is missing or fails), refreshed on the server's ingest tick, never blocking a request.
- SP-2 `fd` ingest records per-turn busy time per session, agent and UTC day (Python `activity.py` rules: turns open at a human prompt, gaps capped at the idle cap — `TOKEN_AUDIT_AGENT_IDLE_CAP_MIN`, else 15 min — subagent files split by agent id, replayed and copied stretches skipped, split at midnight UTC); `/api/daily` fills `agent_hours` (main + subagent) and `agent_hours_sub` (the subagent part); `agent_hours_est` stays 0 and says so in the contract.
- SP-3 `/api/sessions` rows from `fd serve` carry `subagents` with the same keys and values as FastAPI's for the same transcripts.
- SP-4 `fd serve` HTTP errors are `{"detail": "<msg>"}`; the CLI keeps `{"error"}`.
- SP-5 The four modules no longer import FastAPI or Starlette; their routes live under `python/flightdeck/routers/` with the same paths and responses; `import_layers_allowlist.txt` is empty.
- SP-6 A conformance test ingests one transcript fixture (main agent with idle gaps across midnight UTC, one subagent, one resumed copy) into both servers and requires equal `agent_hours`, `agent_hours_sub` and `subagents` (costs within 1e-6 USD); `spend.yaml` marks these keys shared; `make check` is green.

## Decisions
