# TODO — Claude nhúng trong container Odoo (MVP 1 → 2)

Working file for the `odoo-19-experiment` branch. Direction and reasons: `docs/2026-09-10-odoo-harness-control-plane.md` §9.
Owner decisions 2026-09-10: embed `claude` in the Odoo container; option 1 then option 2; subscription
login set up by hand, on production too; Odoo never manages that credential.

## Measured

| | |
|---|---|
| `claude -p` one shot on host, no `--bare` | 6.5 s wall, 3.3 s API, 303 MB RSS, 0.48 USD (48k context tokens loaded from the workspace) |
| consequence | option 1 keeps one long-lived process per open session (`--input-format stream-json`); per-turn spawn is out |
| container | `odoo:19.0`, uid 100, HOME `/var/lib/odoo` (volume `odoo-data`), no CPU/RAM limit, entrypoint env carries the DB role and password; the odoo command line itself carries `--db_password`, readable by any process in the container through /proc |
| one process, many turns | `claude -p --input-format stream-json` served two turns on one process; `system/init` comes with each turn, `result` closes each turn; haiku in a bare cwd: 0.078 USD a turn |
| composer → reply on screen (zone B, resume of a stopped session) | stopped → busy 0.3 s → idle with the reply drawn at 4.3 s (`devtools_mcp_trace/2026-09-10-runner-send/`) |
| claude writes the transcript 0600 | a default ACL never reaches it (the mask follows the group bits), so the host runner chmods it 0640 after each turn (`-transcript-mode 0640`) |

## Container (this branch)

- [x] `docker/odoo19/Dockerfile`: `FROM odoo:19.0`, install `claude` (native installer, 2.1.267 verified in the container), `git`; `ENV CLAUDE_CONFIG_DIR=/var/lib/odoo/.claude`
- [x] `compose.yml`: `build: .`, image `flightdeck-odoo19:local`, container recreated, Odoo answers 200. Addons mount stays `ro` until loop D
- [x] **scrub env** (fd-runner drops `PASSWORD`, `PGPASSWORD`, `FD_LEDGER_*`); the odoo command line still shows the password, see Measured
- [x] `runner/` — fd-runner in Go, stdlib only, static binary; spool transport (`cmd/`, `done/`, `state/`, `events.jsonl`); built in `golang:1.26-alpine`, second process in the container and `~/.flightdeck/bin/fd-runner` on the host
- [x] Odoo `connectors/` package (`base.py` registry + `spool.py`), models `flightdeck.runner` and `flightdeck.runner.command`, session runtime fields and actions, runtime chip in `TranscriptBox`
- [ ] **manual, owner:** the host runner is only running as a background process of this session. Install it as a user unit (writing under `~/.config` was blocked by `dcg`):

```ini
# ~/.config/systemd/user/fd-runner.service
[Unit]
Description=fd-runner (host zone)

[Service]
ExecStart=%h/.flightdeck/bin/fd-runner -spool %h/.flightdeck/spool/host -claude %h/.nvm/versions/node/v22.23.0/bin/claude -idle 15m -transcript-mode 0640
WorkingDirectory=%h/.flightdeck/runner-home
Restart=on-failure

[Install]
WantedBy=default.target
```

    then `systemctl --user daemon-reload && systemctl --user enable --now fd-runner` for the agent process (launch with a copy of env minus `PASSWORD`, `FD_LEDGER_PASSWORD`)
- [ ] read-only DB role for the agent: `fdreader` exists in `.env`; pass as `PGUSER`/`PGPASSWORD` only to the agent process
- [ ] **manual, owner:** `docker exec -it flightdeck-odoo19 claude` → login with the subscription account; credentials land in the `odoo-data` volume under `.claude`
- [x] follower reads a runner's `transcript_root` first (`/var/lib/odoo/.claude/projects` for the container runner); zone A untested until login
- [x] cost: `runner_cost` on the session sums `total_cost_usd` from `result` events; `end_ts` follows

## Option 1 — session is a record

- [x] model `flightdeck.session`: `runner_id`, `runtime_state`, `cwd`, `runner_pid`, `runner_cost`; permission profile lives on the runner
- [x] runner outside Odoo (fd-runner): `--session-id` for a new session, `--resume` when the file exists, `--permission-prompts none`; idle 15 min → close stdin, then SIGTERM, then kill
- [x] `TranscriptBox` composer sends through `send_message`, which auto-starts a stopped session; state chip driven by bus `flightdeck.session/state`
- [x] `result` frame → cost + `end_ts`; turns arrive through the follower; the runner is the single writer
- [ ] hook: `PermissionRequest` `type: "command"` thin script in the image → `POST /flight_deck/decision` then `GET .../<id>` each second; model `flightdeck.decision` + `mail.activity`; approve/reject in the same form; default fail-closed, `auto_denied` on timeout
- [ ] `flightdeck.policy` records replace `~/.flightdeck/approve.conf` regex; hook reads them per call
- [x] behavioural test (Playwright): type in composer → reply drawn in 4.3 s
- [x] **approval loop closed (loop B).** `--permission-prompt-tool stdio` makes the runner the permission host: the CLI's `can_use_tool` control_request parks the turn, becomes a `flightdeck.decision`, and the card in the chat box answers it with a control_response. Measured: card on screen 2.8-3.3 s after Enter, gone 0.2 s after Allow, reply at 5.6 s. Deny reaches the model — it answered "The Bash tool denied the command — writing outside the project directory is restricted by the Odoo scope policy", the exact reason typed in Odoo, and the file was never created
- [x] **`blocked_path` is a second gate.** A call reaching outside the working directory carries `blocked_path`, and remembering only the rule keeps asking; the runner echoes `addRules` **and** `addDirectories` back, and drops the CLI's `setMode` suggestion so one answer never flips the whole session. Proven across processes: `.claude/settings.local.json` gained `Bash(touch /tmp/fd-always-2.txt)` plus `additionalDirectories: ["/tmp"]`, and a new session ran the same command with no prompt
- [x] **permission mode per turn**, the way shift+tab works in the terminal. The picker sits in the composer bar under the input, next to Send, the way a chat client carries its controls. Picking one writes the session and, while a process is running, sends `set_permission_mode` on the control channel; the next turn's `init` reports it. Proven default → acceptEdits: the Write that followed ran unasked
- [x] **one act, wherever it is picked.** The command rides `write()` on the session, so the composer picker and the field on the form behave the same; the runner's echo of a mode it already applied carries `fd_no_mode_cmd` so it does not bounce back as a new command. Both widgets follow each other live: the picker reloads the form's record, the write buses the mode to every open box (pill → form "Auto", form → pill `acceptEdits`, both without a page reload)
- [x] **model badge and picker** in the same bar, the shape the terminal uses: the badge shows the model and, when set, the effort; the popup lists Default / Opus 5 / Fable 5.1 / Sonnet 5 / Haiku 4.5 with one line each and a tick on the current one, plus the five effort levels. `set_model` on the control channel changes a running process (measured haiku → sonnet → null, and each turn's `init` reported the new one). Effort has no control subtype, so it goes as the CLI's own `/effort <level>` turn — which costs a turn, and the popup says so
- [x] **output drawn by pattern, not by one shape for all.** `flight_deck.tool_renderers` is an Odoo registry: an entry is `{match(line), Component}` with a sequence, the box asks it which component draws a call, and the raw output is the fallback. The first entry is images. Adding another is one `.add()` in any module, no change to the box
- [x] **images show without expanding.** A tool result carries them as base64 blocks in the transcript (measured: 396 blocks over 279 calls in one session, ~200 KB each), so the row stores only where they are — media type, block index, and the byte the result's line starts at — and `/flight_deck/tool_image/<line>/<index>` seeks to that byte and returns the bytes, `nosniff`, png/jpeg/gif/webp only, 404 on anything stale. A URL ending in an image extension in the output text is previewed straight from the source. Measured: two `Read` of a PNG drew their thumbnails, 200 `image/png`, a bad index 404
- [x] the approval card sits **on** the composer, 8 px above the input, so the request reads as part of the control it is blocking
- [ ] approvals only reach whoever has the form open or the Approvals list; no `mail.activity`, no notification
- [x] liveness guard in fd-runner: a `start` with `resume` asks `claude agents --json` and refuses when an interactive process (not its own) holds the session; a check that cannot run refuses too. Proven on this conversation's session: refused in 0.5 s, no turn written. `allow_live` on the session overrides it. The tmux-pane wrapper for live sessions is not built
- [x] resume of a real host session: `cwd` taken from the transcript's own first record, `--resume` keeps the original file whatever the cwd (measured), reply in ~5 s on session 2537
- [ ] `start` has no `expires_at`, and `_reconcile` skips a session with a queued command, so a session started while its runner is down sits in `starting` until the runner returns
- [ ] zone A credential isolation is still open: the env scrub is real, but PID 1's command line carries `--db_password` and `odoo shell` is in the image; needs the read-only role and a way to keep the password off the command line
- [ ] container stop signals PID 1 (odoo) only; fd-runner at PID 11 gets no SIGTERM, so `StopAll` never runs there
- [ ] zone B sessions land in `~/.claude/projects`, which FlightDeck ingest also reads; the "only Odoo reads Odoo-created sessions" rule holds for zone A only

## Option 2 — standing agent

- [ ] model `flightdeck.agent`: prompt, schedule, allowlist, `session_id`, read-only DB role, `--bare`
- [ ] `queue_job` (OCA mount + `server_wide_modules`) or, until then, `ir.cron` with a stated reason; one run per tick, `identity_key`
- [ ] outputs: `mail.activity` on the affected record, chatter note, cost row
- [ ] first job: failed crons, `queue_job` failures, Odoo log tail (`logfile` into the volume so the agent can read it)

## Not now

- addons mount `rw`, scratch DB, `--test-tags /flight_deck` gate (loop D)
- terminal in Odoo (`ttyd`) — dropped
- watching all sessions
