# api/ — the contracts

The agreements between FlightDeck's servers and clients, written from the code
as it is — not from intent. Where a doc and the code disagree, the code is
right and the doc is stale.

| File | Contract | Serves / writes | Reads |
|---|---|---|---|
| `spend-http.md` | The `/api/*` HTTP surface | Go `fd serve` serves five routes plus a JSON 404 for the rest (go/cmd/fd/serve.go:21, go/server/server.go:32-64); the Python FastAPI app serves the full surface (python/flightdeck/server.py:64-115). Both mount the built web client when its `dist/` exists. | The React web client, through `get/post/patch/del` and `EventSource` (clients/web/src/api.js:23-93). Flightdoo calls **none** of it — Odoo talks to the runner through the spool only (clients/odoo/flight_deck/models/runner.py:119-147). |
| `cli.md` | The `fd --json` contract: one JSON document on stdout, exit codes 0/1/2/3 | The `fd` binary (go/cmd/fd/main.go) | Shell scripts, skills and agents that shell out to `fd`. |
| `runner-spool.md` | The spool-directory protocol between `fd-runner` and Odoo | Odoo writes commands and reads events/state/deltas (clients/odoo/flight_deck/connectors/spool.py); `fd-runner` does the reverse (go/runner/spool.go, delta.go) | Each side reads the other's files. |

## The rule

A change to a contract — a route, a JSON key, an exit code, a filename in the
spool — changes this folder in the same commit. A contract doc that lags its
code is worse than none, because readers trust it.

## Out of scope

- Odoo's own HTTP controllers (`/flight_deck/...`) serve the Odoo web client
  inside Odoo, not these clients (clients/odoo/flight_deck/controllers/main.py:8-114).
- The MCP stdio servers (`python/flightdeck/*/mcp_server.py`,
  `python/flightdeck/mcp_server/`) are Claude-facing tools, not HTTP contracts.
