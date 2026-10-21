# The runner spool protocol

How Odoo (Flightdoo) drives `fd-runner`: a shared directory, one JSON file per
command, append-only event files. No network and no token — whoever can write
into `cmd/` drives the runner, and the mount is what grants that
(clients/odoo/flight_deck/connectors/spool.py:17-18). One runner per spool
directory; two sharing a `cmd/` would race on the same file
(go/runner/main.go:9-10). The transcript itself never passes through the
spool: `claude` writes its own JSONL and Odoo reads that file directly
(go/runner/main.go:6-7).

`fd-runner` is `go/bin/fd-runner`, a thin `os.Exit(runner.Main(os.Args[1:]))`
(go/cmd/fd-runner/main.go:11-13). Flags (go/runner/main.go:26-42): `-spool`
(required), `-claude` (default `claude`), `-idle` 15m, `-poll` 200ms,
`-permission-timeout` 10m, `-usage-every` 0 (off; quota is then reported only
when Odoo asks). Exit codes: 0 on `-h` or a clean signal stop, 2 on bad flags
or a missing `-spool`, 1 when the spool cannot be opened (main.go:33-49,68-71).

## Layout

Under `runner.spool_dir` (spool.py:3-15; `OpenSpool` creates the three
subdirectories at go/runner/spool.go:91-96):

| Path | Written by | Read by | Contents |
|---|---|---|---|
| `cmd/<seq>-<cmd_id>.json` | Odoo `SpoolConnector.submit` — dot-prefixed temp, fsync, rename (spool.py:31-40) | runner `Pending()` (spool.go:108-141) | one Command (below) |
| `done/<same filename>` | runner `finish()` — `os.Rename` after handling (spool.go:157-171) | runner `Seen()` dedupe check (spool.go:146-154) | the handled command file |
| `state/<session_id>.json` | runner `SetState` — temp + rename (spool.go:194-202); removed by `ClearState` on process exit (spool.go:204-206,296-310) | Odoo `live_states()` (spool.py:77-92), reconciled in runner.py:172-184 | one SessionState (below) |
| `events.jsonl` | runner `Emit` — one `O_APPEND` write per line (spool.go:173-192) | Odoo `read_events`, byte offset kept on `runner.events_offset` (spool.py:42-43; models/runner.py:100,161-168) | one Event per line (below) |
| `deltas.jsonl` | runner `DeltaWriter.writeLine` (go/runner/delta.go:299-328) | Odoo `read_deltas`, byte offset in memory only (spool.py:45-46; models/follow_watcher.py:180-232) | one delta line per snapshot (below) |

Stale state files from a previous runner are deleted at open — they describe
processes that died with it (spool.go:97-103).

## Command JSON (Odoo → runner)

Built in `flightdeck.runner._submit` (models/runner.py:119-147), shaped by
`Command`/`CommandArgs` (go/runner/spool.go:17-62). Filename is
`%012d-<cmd_id>.json`: the zero-padded `seq` makes file order command order
(spool.py:32, spool.go:106-107).

| Field | Type | Notes |
|---|---|---|
| `cmd_id` | string | uuid4 from Odoo (runner.py:125) |
| `seq` | int | per-runner counter `next_seq` (runner.py:122-123) |
| `op` | string | one of the ops below |
| `session_id` | string | the Claude session id |
| `expires_at` | string, optional | RFC3339; honored by `send` only — a late send is refused (go/runner/process.go:315-318); other ops ignore it |
| `args` | object | per op: `cwd`, `resume`, `allow_live`, `text`, `permission_mode`, `allowed_tools` [s], `model`, `append_system_prompt`, `request_id`, `behavior` allow\|deny, `scope` once\|session\|always, `message`, `updated_input`, `mode`, `effort`, `name`, `json_schema`, `max_budget_usd`, `max_turns`, `tools` [s]\|null (spool.go:27-62). Odoo fills `cwd`, `resume`, `allow_live`, `permission_mode`, `model`, `effort`, `allowed_tools`, `name` for start (models/session_send.py:91-117) |

Ops (dispatch at process.go:104-127): `start`, `send`, `interrupt` (SIGINT),
`stop`, `decide`, `set_mode`, `set_model`, `set_effort`, `usage`. Unknown op →
ack with error (process.go:126-133). `start` spawns
`claude -p --input-format stream-json --output-format stream-json --verbose
--permission-prompt-tool stdio --include-partial-messages` plus
`--resume|--session-id`, `--permission-mode` (default `dontAsk`), and the
args that map to CLI flags (process.go:167-201, start_options.go:13-32).
`start` on an already-running session just re-emits its state
(process.go:147-150); `resume` without `allow_live` refuses when an
interactive process holds the session (process.go:151-159).

## Event JSON (runner → Odoo), one line of `events.jsonl`

`Event` fields: `ts` (RFC3339Nano, filled if empty), `kind`, `session_id`?,
`cmd_id`?, `state`?, `error`?, `payload`? (spool.go:64-74). Kinds:

| `kind` | `payload` | Emitted from |
|---|---|---|
| `ack` | — (top-level `cmd_id`, `session_id`, `error`?) | every handled command, error or not (process.go:128-135); also for an unreadable command file (spool.go:131-135) |
| `state` | `{"pid": i}`; top-level `state` = idle\|busy\|stopped | `setState` (process.go:411-424) and process exit (process.go:296-310) |
| `result` | `result` s, `structured_output`? obj, `usage`? {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}, `total_cost_usd` f, `is_error` b, `num_turns` i, `duration_ms` i, `subtype` s, `first_content_frame_ms` i, `command`? b | end of one turn (process.go:273-291, start_options.go:73-91) |
| `permission` | `request_id`, `tool_name`, `tool_use_id`, `input`, `title`, `description`, `can_remember` b, `blocked_path` | a `can_use_tool` control_request parks a turn (control.go:81-107) |
| `decision` | `request_id`, `behavior`, `scope`, `remembered` b, `waited_ms` i — or `request_id`, `behavior:"deny"`, `reason` | `answer` (control.go:207-213), a dead process's pending requests (control.go:311-323) |
| `mode` | `{"mode": s}` | `SetMode` (control.go:218-235) |
| `model` | `{"model": s}` | `SetModel` (control.go:240-258) |
| `effort` | `{"effort": s}` | `SetEffort` (control.go:265-288) |
| `usage` | `{"text": s, "captured_at": s}`; top-level `cmd_id`?, `error`? | `reportUsage` — raw `claude -p /usage` text (usage.go:24-46) |

Odoo applies these in `runner.py:_apply_event` (models/runner.py:197-248).

## `state/<session_id>.json`

`SessionState`: `session_id` s, `pid` i, `state` s (idle\|busy), `cwd` s,
`since` s, `last` s — RFC3339Nano (spool.go:76-83,194-202; written at
process.go:411-419).

## `deltas.jsonl`

One line per streaming-text snapshot: `ts`, `session_id` (the Go-tracked
session), `stream` (frame session id, `:<parent_tool_use_id>` for a
subagent's), `seq` i, `text` s (delta.go:83-89,284-297). **Snapshots, not
increments** — every line carries the full accumulated text, so a missed line
self-heals on the next one (delta.go:31-34). Only `text_delta` content is
published (delta.go:141-145); flush at most once per 60 ms window per stream
(delta.go:39,186-213); a stream is capped at 256 KiB (delta.go:45,171-185).
The file is truncated to 0 once it exceeds 4 MiB — safe because nothing is
replayed and every live stream rewrites its snapshot within a window
(delta.go:54-61,323-327). Odoo publishes each line to the bus and never stores
the offset durably (follow_watcher.py:180-232; base.py:48-55).

## Atomicity rules, as the code does them

- **Commands**: written to `.<seq>-<cmd_id>.json.tmp` in `cmd/`, fsynced, then
  `os.rename`d into place (spool.py:35-40). The reader skips dot-prefixed
  names, so it never sees a partial file (spool.go:14-16,117).
- **State**: written to `.<session>.tmp` then renamed (spool.go:196-201).
- **events.jsonl / deltas.jsonl**: one small `O_APPEND` write per line lands
  whole (spool.go:173-191, delta.go:299-318). The reader takes only complete
  lines — a partial tail line waits for the next read — and resets its offset
  to 0 if the file shrank underneath it (spool.py:52-75).
- **Dedupe**: a command is skipped when its id was handled in this process or
  its file already sits in `done/` — the case a crash between handling and
  moving leaves behind (spool.go:143-165).
- **No locks** between processes: ordering inside the runner is mutexes on
  the emit path (spool.go:87,183); across processes the directory itself is
  the lock — one writer per file, one runner per spool (main.go:9-10).
- **Ordering for readers**: before a `state`, `permission` or `result` event
  the runner flushes that session's pending deltas, so no durable event can
  overtake the text that led to it (process.go:275-277,420-422;
  control.go:93-95; delta.go:240-259).
