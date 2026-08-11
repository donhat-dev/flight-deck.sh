# Agent surface plan — one core, CLI first, MCP as a shim

Status: PLAN, not started. Decision requested: consolidate the two FlightDeck MCP
servers, or replace them with a CLI in the agent-browser mould. This plan recommends a
third shape that subsumes both, with the evidence for it.

## The problem, measured

FlightDeck ships two stdio MCP servers — `treasures` (13 tools, 570 lines) and `radar`
(15 tools, 540 lines). Seven infrastructure functions are name-for-name identical across
them: `configure`, `_conn`, `handle`, `_load_dotenv`, `main`, `release_idle`,
`_start_reaper`. A third domain would copy the same ~130 lines again.

Four pains, all observed rather than predicted:

1. **Stale code until session restart.** `.mcp.json` is read at session start, so an MCP
   server runs the code from whenever the session began. On 2026-08-07 alone this bit
   twice: `radar_blip_update` refused `description` (the running server predated the
   field), and the markdown guard was unreachable over MCP for the whole session that
   added it. Every real data operation that day fell back to `python -c` scripts calling
   `service` directly — the revealed usage is already a CLI with no name.
2. **Registration sprawl.** A server must be declared in TWO `.mcp.json` files (workspace
   root and `flight-deck.sh/`) because a project file only covers sessions started in
   that directory. The radar server initially shipped into only one and was invisible
   from the root (memory note `mcp-registration-locations`).
3. **Idle connections.** Each server in each session holds a Postgres write connection.
   Measured before the reaper existed: ~12 idle connections across parked sessions. The
   reaper mitigates; a per-invocation process would remove the class.
4. **Two-session write races.** Not transport-specific — the 2026-08-07 reclassification
   collision happened through direct writes — but any plan here should not make it worse.

What MCP does well and must not be lost: typed schemas the agent can read before calling
(enums, required fields, the refusal contract in prose), zero shell-quoting hazards for
rich content (this session's own logs are full of heredoc/quoting failures), and per-tool
permission entries instead of blanket Bash.

There is no session id in the Bash environment (`CLAUDE_CODE_CHILD_SESSION=1` only), so a
CLI cannot infer attribution — `--session` stays an explicit flag, taught by the skill.

## Options

| | A. Merge the two MCP servers | B. Replace with a CLI (agent-browser mould) | C. One core, two thin frontends |
|---|---|---|---|
| Duplication | fixed | fixed | fixed |
| Stale-code-per-session | **still broken** | fixed | fixed via CLI path |
| Registration | still 2 files, 1 entry | none needed | 2 files, 1 entry (MCP shim only) |
| Idle connections | halved | zero | zero when CLI, reaper when MCP |
| Typed schemas for the agent | kept | lost (help text only) | kept (MCP) + `--schema` (CLI) |
| Shell-quoting hazard | none | high unless stdin-JSON | none (MCP) / stdin-JSON (CLI) |
| New-domain cost (missions, systems…) | add to one file | add to one file | add one registry module |

**Recommendation: C.** Both existing servers already have the right shape for it — a
`TOOLS` registry of `(fn, description, schema, required)` and a pure `handle(request)`
over it. Extracting that registry is the consolidation; the CLI and the MCP server both
become ~60-line frontends over it. A is strictly weaker (keeps the worst observed pain);
B pays the schema and quoting costs to gain nothing C does not also gain.

**Flip-fact.** If per-tool permissioning becomes load-bearing — e.g. a policy that agents
may `radar_get` but never `radar_delete` without a human — the MCP frontend must stay the
primary surface, because CLI calls are indistinguishable inside blanket Bash permissions.
Today no such policy exists.

## Target shape

```
backend/flightdeck/
  agentsurface/
    runtime.py      dotenv, db config, connection lifecycle (open-per-call for CLI,
                    reaper-managed for MCP), commit-after-every-call
    registry.py     Tool dataclass + collect(): merges domain registries
  radar/tools.py    the 15 radar tools      (moved from radar/mcp_server.py)
  treasures/tools.py the 13 treasure tools  (moved from treasures/mcp_server.py)
  mcp_server.py     ONE stdio server, name "flightdeck", serving every registered tool
  cli.py            `flightdeck` entrypoint
```

CLI grammar, stdin-JSON to kill the quoting class:

```
flightdeck radar_get --slug flightdeck
flightdeck radar_move --json - <<'JSON'
{"slug": "flightdeck", "num": 13, "ring": "adopt", "period": "Q4 2026",
 "why": "…", "session_id": "5d924437"}
JSON
flightdeck --list            # tool names, one per line
flightdeck --schema radar_move   # the same JSON schema the MCP advertises
```

Contract, same as `scripts/`: JSON on stdout, non-zero exit when the payload is
`{"error": …}` (2 = refusal, 3 = unknown tool), errors as data on stdout not stderr.
`flightdeck --version` prints the git sha it runs from — the honesty agent-browser lacks
(its silent drift cost a wrong conclusion once); a fresh-spawned CLI makes drift
impossible but should still say what it is.

Install: a `bin/flightdeck` shim committed to the repo (`exec .venv/bin/python -m
flightdeck.cli "$@"` with a repo-relative path), symlinked into `~/.local/bin` by
`make cli`. No PATH assumptions inside the code; works from any cwd like the MCP
bootstrap does today.

## Phases

1. **Extract the core** — move both `TOOLS` tables into `radar/tools.py` /
   `treasures/tools.py`; move the seven duplicated functions into `agentsurface/`.
   The existing `handle()` unit tests (55 radar, treasures suite) retarget with only
   import changes: they already test the core, not the transport.
2. **CLI frontend** — `cli.py` + `bin/flightdeck` + `make cli`. Smoke tests via
   subprocess: stdin-JSON round trip, `--schema` output validates, refusal exits 2,
   unknown tool exits 3.
3. **MCP shim** — one `mcp_server.py` named `flightdeck`; update BOTH `.mcp.json` files
   to the single entry; keep `radar/mcp_server.py` and `treasures/mcp_server.py` as
   two-line import shims for one release so parked sessions keep working, then queue
   them in `TEMP_FILES_SHOULD_BE_REMOVE.MD`.
4. **Teach the surfaces** — `radar-blips` and treasures skill sections gain the CLI
   invocation with the `--session` rule; MEMORY note updates the registration map to the
   single server name.

Estimated effort: one working session (phases 1–2 are the bulk; 3–4 are small).

## Out of scope, named

- The two-writer race: transport-neutral. If it recurs, the fix is optimistic locking on
  `radar.updated_at`, not a transport choice.
- `odoo-debugger` (different repo), `pencil`/`jira-nakivo` (third-party): untouched.
- WYSIWYG editing: unaffected — it rides the HTTP API, not this surface.

## Open questions for the owner

1. Binary name: `flightdeck` (explicit) vs `fd` (collides with the `fd` file finder —
   recommend `flightdeck`, alias personally if wanted).
2. Does the MCP shim stay long-term, or is it removed once CLI habits settle? Plan keeps
   it: it is ~60 lines over the shared core, and it is the only surface with typed
   schemas in-session.
