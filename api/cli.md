# The `fd` CLI contract

`fd` is the FlightDeck core for one host: it reads Claude Code transcripts
into a ledger and answers from it, as a CLI or a daemon (`fd serve`)
(go/cmd/fd/main.go:1-2). The binary is `go/bin/fd`; `go/bin/fd-runner` is a
separate binary covered by runner-spool.md.

## The `--json` contract

Kept from the Python `flightdeck` CLI (go/cmd/fd/main.go:4-6):

- `--json` anywhere in the arguments (also `-json`, `--json=true`,
  `-json=true`) switches stdout to exactly one JSON document
  (main.go:97-105). Every command accepts it because it is registered on every
  flag set (main.go:119-125) — commands just return `Data`, and `run` prints
  it indented when `--json` is set or no text view exists (main.go:74-82).
- Exit codes (main.go:44-95):
  - `0` — ok, or `-h`/`--help`/`help` (usage text on stdout, main.go:52-54,62-66,132-140)
  - `2` — the command refused (a `refusal`); `{"error": "<msg>"}` on stdout
    with `--json`, `fd: <msg>` on stderr without (main.go:67-70,87-95)
  - `3` — unknown command, same error shape (main.go:56-58)
  - `1` — crash or unexpected error; `fd: crash: …` or `fd <cmd>: …` on
    **stderr**, never stdout (main.go:46-51,71-72)
- Errors are data on stdout; stderr is reserved for the unexpected. Warnings
  go to stderr so stdout keeps one document (go/cmd/fd/thinking.go:22-28).

Common flags, on every command that touches the ledger (go/cmd/fd/flags.go:15-29):
`--db` (env `FD_DB`, default `~/.flightdeck/fd/ledger.db`) and `--projects`
(env `FD_PROJECTS`, default `$CLAUDE_CONFIG_DIR/projects` or
`~/.claude/projects`). `--range` takes `today|7d|30d|all`, anything else is a
refusal (flags.go:58-64). `--no-ingest` answers from the ledger as it is.

## Commands and their JSON shapes

| Command | Flags | `--json` output |
|---|---|---|
| `fd ingest` | paths | `{"files": i, "new_rows": i, "seconds": f, "db": s}` (spend.go:20-40) |
| `fd spend` | paths, `--range`, `--no-ingest`, `--subscription-usd` (env `FD_SUBSCRIPTION_USD`) | `{"range": s, "summary": Summary, "by_model": [ModelRow], "daily": [Day]}` — same shapes as the HTTP API, see spend-http.md (spend.go:70-101) |
| `fd sessions` | paths, `--range`, `--limit` 20, `--offset`, `--no-ingest` | `[SessionRow]` — the `/api/sessions` row without `subagents` (spend.go:104-129) |
| `fd thinking` | paths, `--days` 7, `--from`, `--to`, `--by` day\|model\|effort\|agent\|split, `--split-at`, `--model`, `--effort`, `--agent` main\|subagent, `--raw`, `--no-ingest` | `{"source": s, "since": s, "until": s, "days": i, "by": s, "split_at": s, "model": s, "effort": s, "agent": s, "buckets": [ThinkingBucket], "trends": [Trend], "notes": [s], "warnings": [s], "coverage"?: {"complete_from": s, "oldest_line"?: s}}` (thinking.go:31-160; bucket: go/core/metrics/thinking.go:116-130 `key`, `responses`, `tokens_reported`, `thought`, `thinking_tokens`, `thinking_seconds`, `wait_seconds` — the three `Stat`s are `{n,sum,mean,p50,p90}` thinking.go:88-94; trend: thinking.go:218-226) |
| `fd import` | paths, `--pg-copy` (required; file or `-` = stdin), `--dry-run`, `--no-ingest` | `{"dry_run": b, "stats": {"read": i, "billed": i, "inserted": i, "skipped_key_present": i, "skipped_session_on_disk": i, "possible_overlap_rows": i, "possible_overlap_cost": f}}` (importcmd.go:17-65; go/core/importer/pgcopy.go:24-30) |
| `fd pricing list` | paths | `Config`: `{"models": [Row], "geo": {name: f}}`; Row = `prefix` s, `valid_from` s, `input` f, `output` f, `cache_read_mult` f, `write_5m_mult` f, `write_1h_mult` f, `fast_input`? f, `fast_output`? f, `note`? s (pricing.go:38-54; go/core/pricing/pricing.go:34-51) |
| `fd pricing set <prefix>` | paths, `--input`, `--output` (required), `--valid-from`, `--cache-read-mult`, `--write-5m-mult`, `--write-1h-mult`, `--fast-input`, `--fast-output`, `--note` | `{"set": Row}` (pricing.go:57-137) |
| `fd pricing import <file\|->` | paths | `{"models": i, "geo": i}`; input is the `Config` shape, unknown fields refused (pricing.go:139-188) |
| `fd pricing defaults` | — | `Config` (pricing.go:190-196) |
| `fd serve` | paths, `--addr` (env `FD_ADDR`, default `127.0.0.1:8011`), `--ui` (env `FLIGHTDECK_WEB_DIST`, empty = API only), `--subscription-usd`, `--debounce` 2s, `--sweep` 5m | daemon; on shutdown prints `{"stopped": "<addr>"}` (serve.go:21-74) |
| `fd version` | — | `{"version": s, "go": s}` (version.go:12-21) |
| `fd help` | — | usage text on stdout, exit 0 (main.go:52-54,107-117) |

## `python/flightdeck/cli.py` — same contract family, not the same CLI

The envelope discipline is identical: one JSON document on stdout; exit 0 ok,
2 when the tool answered `{"error": …}`, 3 for an unknown tool, 1 for a crash
(python/flightdeck/cli.py:18-21,44-50). But the commands and the flag grammar
differ: it dispatches `flightdeck <tool> --key value` through the agentsurface
registry (radar_get, radar_move, …; cli.py:121-139), coercing each value as
JSON when it parses (cli.py:58-67). And `--json` there is an **input**
channel — `--json -` reads a JSON object from stdin, explicit `--key` flags
override it (cli.py:24-28,100-113) — where in `fd` it is an **output** flag.
Same exit-code contract, different CLI.
