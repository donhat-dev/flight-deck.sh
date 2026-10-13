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
| claude writes every transcript 0600 | a default ACL never reaches one (the mask follows the group bits): a directory with `setfacl -d -m u:100:r` gives a file created 0600 `mask::---` and a file created 0644 `mask::r--` |
| reading them instead by identity | the container runs `user: "1000:101"`; 0 of 238 agent transcripts unreadable, filestore and spool still writable, transcript mount refuses writes |

- [x] Turns in the transcript box. A turn is a prompt somebody wrote and everything the
      session did about it, split on the lines rather than stored: `models/session_turn.py`
      adds `fd_turns` and `fd_turn_steps` to `flightdeck.session`, both scoped to a session or
      to one agent. The rule for "a person's prompt" was measured, not guessed — a user line
      with no tool name, not starting with `<` (task notifications, editor state, slash
      commands, reminders), not `[Request interrupted`, `[Image:` or `Caveat:`. Session 580:
      13,653 assistant lines, 1,424 raw user lines, 965 turns. The box gained a picker that
      opens on the last turn and bounds what it reads to that turn's lines, and a Gantt tab.
      Because it lives in the box, the form, the agent dialog and every column of the wall get
      it at once. Measured: 61 options, default #966, picking #909 narrows the box to seq
      124864-124914, the chart lays 15 steps across 100% with 2 called out as where the time
      went and 1 marked as a repeat (`devtools_mcp_trace/turn-gantt.png`, `turn-picked.png`).
      Not doing: a stored turn model, analysis across turns, agent sub-rows inside a chart.
- [x] A bar is laid on the gap from one line to the next, which is not how long a tool ran: a
      result is written back onto the line that called it, so no line carries an end of its
      own. Said in the method docstring and in the chart's own header.
- [x] Two OWL traps, both silent. A `t-foreach` variable named `turn` resolved to the
      component's `turn` getter, so all 60 options carried the current turn's number; renaming
      the loop variable fixed it. And `.find((a) => a.id === x)` inside a `t-on-change`
      expression fails to compile (`v19 is not a function`) — arrow functions nested in a
      template expression belong in a method.
- [x] Transcripts side by side, as a client action (`flight_deck.transcript_wall`, menu
      **Side by side**). Rows of columns. A column takes an equal share of its row unless it
      has been dragged, so the count decides the width and nobody sets a size; a row holds at
      most three, because a fourth is narrower than a tool call. A row with fewer than two
      columns ends in a dashed square with a `+` in it, and that square is gone as soon as the
      row is a comparison — the third column comes from the button beside the row. One row
      fills the page; two or more are 80vh each, so the next one shows. The column is the same
      `TranscriptBox` the form uses, so nothing about a turn renders twice. The layout lives in
      `localStorage` (the flat list an older browser holds still opens, as one row), and the
      action reads `context.fd_panes` so a form can seed one later. Measured at 1600x1000: one
      column and the square split the row 697/713 px, the square disappears at two columns, the
      third is refused a fourth (rail button disabled, clone button gone), rows are 800 px
      against 80vh = 800, a drag clamps at the 320 px reading width, evening out restores the
      share, and a reload brings back 2 rows and 4 columns with 0 console errors
      (`devtools_mcp_trace/wall-rows.png`, `wall-rows-restored.png`).
      Not doing: dragging a column to reorder, tabs inside a column, scroll kept in step.
- [x] Dragging a divider moved one column, not the boundary. The left column gave up pixels
      and the right one kept its own, so the row came up short by exactly what was dragged and
      the shortfall showed as a gap at the end. A drag now takes from one side and gives to the
      other, and the last column of a row grows from its width rather than sitting at it, so a
      closed column or a resized window is absorbed as well. Measured at 1600x1000, two columns
      then three: every drag, every over-drag against the 320 px floor, closing the third
      column, and narrowing the window to 1200 and back all leave the row exactly full
      (`GAP 0` throughout, `devtools_mcp_trace/wall-resize-no-gap.png`).
- [x] A resized column rendered 28,906 px wide while its own style said 525. A flex item
      defaults to `min-width: auto`, which is its widest unbreakable content, and one long line
      in a tool call is wider than any screen. Both branches of the column style now state a
      min-width; the pinned one says 0 and the drag does the clamping.
- [x] One bus channel, many boxes. `deleteChannel` is unconditional in the bus worker, so the
      second box to leave a session took the channel away from the first — two columns on one
      session, or an agent dialog over a form. The boxes now count themselves onto a channel.
      Negative test, same script both ways: with the count disabled the surviving column
      missed the state message (`RECEIVED_BUS false`), with it restored it got it
      (`RECEIVED_BUS true`, `devtools_mcp_trace/wall-shared-channel.png`).

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
ExecStart=%h/.flightdeck/bin/fd-runner -spool %h/.flightdeck/spool/host -claude %h/.nvm/versions/node/v22.23.0/bin/claude -idle 15m
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
- [x] Agent map in Odoo — `flightdeck.session.agent`, built from the last compact boundary the
      way the CLI's own reader builds it. Measured: session 208f6493 gives 5 agents, the tree
      renders 5 rows with status dots, a click loads that agent's own JSONL (84 lines) into a
      dialog with no composer. Screenshots `devtools_mcp_trace/agentmap-01-tab.png`,
      `agentmap-02-popup.png`.
- [ ] An agent stays `working` when nothing ever reports its end: the parent transcript carries
      no "the process died" line, which is what the extension gets from its own process. The
      hook path (`SubagentStop`, `Stop.background_tasks`) is what closes this.
- [x] Subagent transcripts are readable from the container. The ACL could not do it — its mask
      is empty on a 0600 file — so the container runs as the uid that owns them
      (`user: "${FD_UID}:101"`) and the runner's `-transcript-mode` chmod is gone. Measured:
      0 of 238 agent transcripts unreadable, 48 of them 0600; agent 228 loaded 186 rows from a
      0600 file through the map dialog (`devtools_mcp_trace/agent-transcript-0600.png`).
- [x] Nested agents on the map. An agent can spawn its own agent: measured 1 of 231 agent
      metadata files at `spawnDepth: 2`, its Agent call living in the parent agent's own JSONL
      and never in the session's. The map now adopts those from `parentAgentId` in the metadata.
      Session 395a72f8 → 114 rows, 1 nested block rendered (`agentmap-03-nested.png`).
- [x] Workflow agents on the map. The `Workflow` call is now scanned beside `Agent`: its
      answer names the directory it will write into, which becomes the row's `workflow_id`,
      and the `journal.jsonl` beside the transcripts names every agent the workflow started,
      with the phase and label the script gave it. The workflow itself is a card that opens
      nothing — it has no transcript of its own. A workflow launched before a resume writes
      under the session id current at the time, so the directory is searched across the
      project when the session's own tree does not hold it. Measured on session 395a72f8:
      journal `started` 8 / 7 / 10 → rows 8 / 7 / 10, two rebuilds keep 145 rows, and one
      fourth workflow whose session transcripts are gone reads `stopped` rather than running
      forever (`devtools_mcp_trace/workflow-map.png`, `workflow-child-transcript.png`).
- [x] Agent map drawn the way the terminal draws it: the session is the root card on the left,
      generations grow right with elbow connectors, each card carries a status dot and a
      `3m 8s · 62.1k tokens` line. The root opens nothing — its transcript is the page the map
      sits on; only an agent card opens a dialog. Usage comes from the `<usage>` block in the
      task notification. Measured on session 7d3a622b: 1 root, 5 agent cards, 2 generations,
      root is not a button, 0 console errors (`agentmap-04-tree.png`, `agentmap-05-page.png`).
- [x] A nested agent carries its own numbers. Two sources, because neither covers both cases:
      `_read_outcomes` now opens the parent's own transcript by its `transcript_path` and runs on
      every rebuild rather than only at adoption, and `_read_usage` sums the agent's own file from
      a stored offset for the agents nobody reports on at all — a workflow's agents, whose journal
      carries no usage, and a child that outlived the parent whose file would have named it.
      Cache reads are left out to match what a notification reports: counting them put a 39-turn
      agent at 2.6M against the 120k its notification gave, and what is left still runs 10 to 25
      per cent above that figure. Measured on session 395a72f8: 26 of 26 nested rows had no
      tokens, 26 of 26 have them now (118k-288k, the same order as the top-level cards), two
      rebuilds in a row drift by 0, rebuild takes 1.5 s, and a child adopted before its first
      write no longer stays `stopped` once its file appears
      (`devtools_mcp_trace/nested-agent-tokens.png`).
- [x] Three map bugs fixed. Rebuild was deleting and recreating every row, so the browser merged
      the new ids on top of the old ones it still held — 5 agents became 20 after four rebuilds.
      Rebuild now matches rows on the call that made them and prunes what the file no longer names;
      a boundary sitting at the offset the read starts from is no longer treated as a new one.
      Measured: three rebuilds in a row keep 5 rows with the same ids, and two rebuilds in the
      browser keep 5 cards.
- [x] Map redrawn down the page like Odoo's own org chart — it shares the form with the transcript
      pane and a left-to-right tree spent that narrow column on connectors.
- [x] A card whose transcript cannot be read is clickable again and says why: the click raises a
      UserError naming the file instead of the card sitting dead.
- [x] Session form: notebook moved under the session id, tabs Agents / General / Usage / Treasures
      / Settings, Tool calls and Tokens and cost merged into Usage, Following turned into a smart
      button. Measured: tab order as listed, Agents opens first, the button toggles
      Following -> Not following -> Following. `agentmap-08-vertical.png`.
- [x] Every node on the map is the same width. A generation gives back the inset it took
      (`margin-right: calc(-1.875rem - 1px)`, the two insets plus the rail), and the tree is pulled
      in by what its deepest branch sticks out, from a `--fd-org-depth` variable the component sets.
      Measured: all six cards 288px wide, nothing clipped.
