# Missions MCP server

Exposes the Missions board to agent sessions. It shares the FlightDeck web
backend's SQLite store, so a `mission_claim` here appears in the web kanban
within the UI's 2s poll.

## Store sharing (important)

The server loads the SAME config + engine as the web backend (`config.load` +
`db.configure`). To share the live service's store you must give it the same
environment — chiefly `TOKEN_AUDIT_DATABASE_URL` (in the repo-root `.env`). With
it set, the MCP talks to **PostgreSQL** (the live engine) and a claim here is
seen by the running service. Without it, it falls back to the SQLite
`cfg["db_path"]` (standalone/local only, which the live PG service does NOT read).

## Run

From the `backend/` directory, sourcing the same `.env` the service uses:

```bash
cd backend
set -a && . ../.env && set +a
../.venv/bin/python -m flightdeck.mcp_server
```

## Register with Claude Code (`.mcp.json`)

Wrap in bash so the service `.env` (PG url) is sourced — this is what makes the
MCP share the live store:

```json
{
  "mcpServers": {
    "missions": {
      "command": "bash",
      "args": ["-lc", "cd /home/nathando/Documents/Projects/flight-deck.sh/backend && set -a && . ../.env && set +a && exec ../.venv/bin/python -m flightdeck.mcp_server"]
    }
  }
}
```

## Tools

`missions_list(status?)` · `mission_get(id)` · `mission_create(title, note?, tags?, priority?, status?, claim_session?)` ·
`mission_claim(id, session_id)` · `mission_release(id)` · `mission_land(id)` · `sessions_list()`
