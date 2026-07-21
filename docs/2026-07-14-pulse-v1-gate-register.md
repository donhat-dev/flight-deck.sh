# Pulse V1 - Mechanism Gate Register (prep)

**Date:** 2026-07-14
**Status:** Draft, pre-build
**Companion to:** `2026-07-14-clouddeck-pulse-roadmap.md` (V1 = Local Claude Companion)
**Purpose:** For each V1 part that still rests on an unverified mechanism, list the
open points, the current hypothesis, and how to close the gate. This is the
"open + owner" register the working loop asks for before we commit the connector
design.

## Evidence tiers used here

- **[verified]** hands-on against the installed Claude Code `2.1.208` CLI (`claude --help`, `claude agents --help`). Operational, top of the ladder for the CLI surface.
- **[docs]** from a Claude Code docs research pass. Directionally reliable, specific names/fields NOT yet confirmed on our version.
- **[ours]** already proven by the FlightDeck (token-audit) codebase, which ingests `~/.claude/projects/**/*.jsonl` today.

Anything **[docs]** must be re-confirmed in Gate 0 before we build on the exact name/field.

---

## V1 DECISION (LOCKED 2026-07-14): manage background agents

V1 targets **background agents that FlightDeck owns**, kept separate from ordinary
interactive sessions, and aligned to the real `claude agents` contract. We do NOT
try to observe or inject into arbitrary interactive terminals. This resolves
flip-fact 1 and collapses the hardest gates onto a supported, state-bearing,
supervised surface.

### Why this is the right lock (verified on `claude 2.1.208`)

`claude agents` (the "agent view", research preview) is already a built-in
attention board for background sessions. Its documented state model maps almost
1:1 onto the Pulse lanes, so our lane design is validated by the product itself:

| Agent view state | Pulse lane |
|---|---|
| Working (animated) | In Flight |
| Needs input (yellow, question OR permission) | Needs Me |
| Ready for review | Review |
| Stopped (Ctrl+X / `claude stop`) | Parked |
| Failed | Needs Me (failed tag) |
| Completed | Finished |
| Idle (ready for next prompt) | decision: Needs Me vs a calm Idle |

### Confirmed background-agent contract (verified `2.1.208`)

- **Start:** `claude --bg "<prompt>"`, `-n/--name <name>`, `--agent <sub> --bg`, `/bg` from inside a session, or dispatch from the `claude agents` view. (`--exec` for a shell-only job is in the docs but NOT on our build.)
- **Enumerate:** `claude agents --json` returns every live session as a JSON array. **Verified schema:** `pid`, `cwd`, `kind` (`interactive` | background), `startedAt` (epoch ms), `sessionId` (uuid), `name`. `--all` adds completed; `--cwd <path>` scopes. Filter `kind != "interactive"` for owned agents. **No state field here** (see the remaining spike).
- **Inspect / attach:** `claude attach <id>` (supervisor resumes from where it left off + posts a recap), `claude logs <id>` (recent output). Both subcommands **exist** on 2.1.208 (hidden from top-level help).
- **Send input:** in the view, `Space` = peek + reply, `Enter`/`→` = full attach. Programmatic path is the Agent SDK (`/en/headless`). No discrete one-shot `send` subcommand.
- **Stop / remove:** `claude stop <id>`, `claude kill <id>`, `/stop` inside, `Ctrl+X` in view; `claude rm <id>` removes from the list.
- **Supervisor:** `claude daemon status`. A **transient supervisor daemon** owns bg workers: control socket `/tmp/cc-daemon-<uid>/<id>/control.sock`, `roster.json` (worker roster + leases), `~/.claude/daemon.log`. It self-starts on demand, pre-spawns a spare worker, adopts workers across upgrades, and **idle-exits after ~5s with no clients**. So "always-on" needs at least one client holding a lease.
- **State store:** `~/.claude/jobs/<id>/state.json` (per-session state) + `~/.claude/jobs/<id>/tmp/`. `~/.claude/jobs/` also holds `pins.json` and `.draft-*`.
- **Worktrees:** agent view moves each dispatched session into its **own git worktree** automatically. Pulse must show per-agent cwd/worktree accordingly.
- **Cost:** each bg agent multiplies token usage (shared 5h cap applies). Pulse should surface run cost (it already owns spend data).

### How the gates change under this lock

- **G1 discovery -> CLOSED.** `claude agents --json` is the live registry.
- **G2 live state -> mostly CLOSED for bg agents.** The agent-view state set (Working / Needs input / Idle / Completed / Failed / Stopped) is real and supervised; source is `~/.claude/jobs/<id>/state.json` + the daemon roster. Only remaining unknown = the `state.json` field schema (spike).
- **G4 correlation -> CLOSED.** We launch each agent with `--session-id`/`--name` and own it; the jobs store is keyed by id.
- **G5 response delivery -> SUPPORTED path exists.** Peek-reply / attach (built-in) for the human loop; Agent SDK for programmatic send. Channels remains the cleaner future push path once GA.
- **G6 subagents -> unchanged** (still hooks + transcript scan).

### What Pulse adds over the built-in agent view (so we do not rebuild it)

Agent view is a **transient terminal TUI**. Pulse is the **durable web deck**:
persistence + history (SQLite logbook, outcomes, resume capsules), the FlightDeck
Night web UI, attention that reaches a browser/phone (not only the terminal),
and integration with the existing spend / quota / charts / Clearance transcript
views. Pulse reads the same supervisor data; it does not reimplement dispatch.

### Spike RESOLVED 2026-07-14 - the V1 data contract is verified

Ran one tiny bg agent (`claude --bg -n pulse-spike "ask one question and wait"`,
left running per request). Everything Pulse needs is on disk, no TUI, no SDK.

Per-session store is `~/.claude/jobs/<SHORT_ID>/` (the 8-char short id, NOT the
full uuid - my first path miss was using the uuid). Contents: `state.json`,
`timeline.jsonl`, `tmp/`.

**`state.json`** (the live snapshot Pulse reads) - verified fields:

    state         lifecycle: working | blocked | (completed|failed|stopped|idle, per docs)
    detail/needs  human-facing text; the EXACT question when blocked   -> Needs Me copy
    intent        the original task/prompt                             -> card title/task
    tempo         coarse tempo mirror of state
    inFlight      { tasks, queued, kinds[] } in-flight tool/subagent    -> In Flight summary
    children      child agents (null when none)                        -> child roster
    tokens        token count                                          -> per-run cost
    output        final result text (null until terminal)              -> Finished summary
    name, nameSource
    sessionId, resumeSessionId, daemonShort
    respawnFlags  e.g. ["-n","pulse-spike","--model","sonnet"]          -> how to respawn
    cwd, linkScanPath (transcript jsonl), backend="daemon", cliVersion
    createdAt, updatedAt, firstTerminalAt (set at terminal state)       -> timestamps

**`timeline.jsonl`** - append-only event log, one line per transition:
`{"at","state","detail","text"}`. This is the durable per-session timeline AND
the idempotency/ordering source (key on `at` + `state`).

**`agents --json` for a bg agent** also carries live `status` (idle|busy) +
`state` (working|blocked|...) + the short `id`, so a single poll already yields
the lane for every owned agent without opening any file.

Observed lifecycle: `working/idle` (spawn) -> `working/busy` (generating) ->
`blocked/idle` (Needs input, stable). Only working + blocked seen live;
completed/failed/stopped/idle are from the agent-view docs.

Other artifacts found (for reference, not needed by the poller): transcript
`~/.claude/projects/<enc-cwd>/<uuid>.jsonl`; env `~/.claude/session-env/<uuid>`;
daemon sockets `/tmp/cc-daemon-<uid>/<daemon>/{pty,rv}/<short>.sock`.
`claude logs <id>` returns a raw ANSI TUI replay (not a clean text source) - use
`state.json`/`timeline.jsonl` instead.

**DECIDED data-access method:** poll `claude agents --json` (roster + live state
every 3-5s) + read `~/.claude/jobs/<short>/state.json` and tail `timeline.jsonl`
for detail. No `control.sock`, no Agent SDK. This drops straight onto
token-audit's existing poll -> SQLite -> SSE pipeline.

**Final lane mapping:** working -> In Flight; blocked -> Needs Me (show `needs`);
review-ready -> Review; failed -> Needs Me; stopped -> Parked; completed ->
Finished (show `output`); idle -> TBD (Needs Me vs a calm Idle).

Nothing now blocks a V1 skeleton. Owner: connector build.

### Agent SDK - expand vs limit (evaluated 2026-07-14)

Evaluated whether to build on the **Claude Agent SDK** (Python/TS) instead of, or
beside, the CLI+files approach. Verdict: **CLI+files for OBSERVING, SDK only for
agents Pulse itself launches and drives** (V1.1/V2 territory).

What the SDK ADDS (only for Pulse-owned, in-process agents): programmatic
create/`resume`/`fork_session`; **streaming input** (`ClaudeSDKClient.query`
continuation / `streamInput` - a clean G5, send a turn into a running session);
**in-process hook callbacks** (no hook script files); **`canUseTool` permission
callback** (approve/deny tools in code - a real supervision gate without a TUI);
structured message/event stream (`init`/`ResultMessage`) instead of parsing
jsonl; per-run cost/usage in `ResultMessage.total_cost_usd`; inline subagent
defs + `parent_tool_use_id` (G6); in-process MCP; **session-store adapters**
(mirror transcripts to Postgres/Redis/S3, cross-host durability); OTEL export.
It also loads `.claude/`+`~/.claude/` config.

HARD LIMITS (why it does NOT replace our observer):
1. **Separate world from `claude --bg`.** The SDK reads `~/.claude/projects/*.jsonl` (sessions it created); it has NO view of the background supervisor's `~/.claude/jobs/` store or `claude agents --json`. `listSessions()` sees only SDK sessions. So the SDK cannot observe the user's own `claude --bg` agents - our whole V1 use case.
2. **We own the process lifecycle.** SDK spawns a subprocess tied to our host process; host dies -> agent dies. No detached `--bg`, no supervisor, no agent-view, no jobs-store, no auto-worktree. Always-on = we self-host and keep the process alive.
3. **Auth/billing is a separate Console API key, NOT Nakivo's claude.ai/Team plan.** Docs are explicit: "Anthropic does not allow third party developers to offer claude.ai login ... use the API key authentication methods instead." (or Bedrock/Vertex/Foundry). Going SDK opens a new metered bill; CLI (`claude --bg`) runs on the existing subscription. **[docs, high]**
4. No total-runtime timeout (set `maxTurns`); memory grows on long sessions (recycle); big subagent fanouts hit rate limits. Python SDK fits token-audit (FastAPI/Python).

Net: keep V1 observation on CLI+files. If Pulse later needs to launch+drive its
own agents, either accept SDK's API billing + self-hosting for the richer control
(streaming input, canUseTool, structured stream), OR drive Pulse-owned agents via
the CLI itself (`claude --bg` + `attach`/`--resume -p`) on the existing
subscription, trading away those SDK-only primitives. Managed Agents (hosted REST)
is the separate branch if async cloud hosting is ever wanted.

---

## Gate 0 - Capability verification spike (superseded by the V1 decision above)

Not a product feature, the pre-req that de-risks every gate below. One instrumented
real session, one background agent, one Task subagent, capturing ground truth.

Open points to close, in order:
1. Full **hook event list + exact stdin payload fields** on `2.1.208` (dump every hook's JSON to a file via a logging hook). The docs list read as over-broad, confirm what actually fires.
2. Which hook (if any) fires when Claude is **blocked on a permission decision** vs merely idle.
3. Whether a **blocking MCP tool call survives** a multi-minute / multi-hour wait (find the real timeout, if any).
4. Whether a custom MCP server can learn its **calling session id** (env, argv, or a correlation token joined via a hook).
5. `claude agents --json` output **schema** and whether it covers only background agents or also interactive ones.
6. `-p --input-format stream-json` round trip: can we **feed a user message** to a live headless session and have it continue.

Owner: connector build. Blocks: all of V1. Evidence target: operational.

---

## Channels (research preview) - the near-exact primitive for G2/G4/G5

Claude Code docs describe **Channels** (`/en/channels`, `/en/channels-reference`),
which is almost precisely the mechanism the crux gates were missing. Assessed
2026-07-14.

**What it is:** a channel is an MCP server (stdio, spawned per-session as a
subprocess) that **pushes events INTO a running session** via a
`notifications/claude/channel` notification. The event lands in Claude's context
as `<channel source="..." meta...>`. Two-way channels also expose a standard MCP
**reply tool** so Claude sends messages back out. A channel can also declare
`claude/channel/permission` to **relay permission prompts**: Claude Code sends
`notifications/claude/channel/permission_request` (fields `request_id`,
`tool_name`, `description`, `input_preview`); the channel replies
`notifications/claude/channel/permission` with `request_id` + `behavior:
allow|deny`, applied in parallel with the local terminal dialog (first answer
wins). Enabled per session with `claude --channels plugin:<name>@<marketplace>`;
custom/unlisted servers need `--dangerously-load-development-channels server:<name>`.

**How it maps to our gates:**
- **G5 (push into a running session)** - this is the supported primitive I had marked ungated. Pulse becomes a channel: request lands in the session, human answers in the Pulse UI, answer is pushed back as a channel notification. No `--resume` hack.
- **G4 (session correlation)** - inherent: the channel is a stdio subprocess of exactly one session, so "which session" is the process itself. Watching N sessions = N channel subprocesses reporting to the central daemon.
- **G2 (needs-me on permission)** - permission relay gives a first-class pushed "blocked on a tool approval" signal WITH tool/description/args, and lets us answer allow/deny remotely. Far better than transcript parsing.
- **G3 (hooks)** - channels reduce reliance on hooks for the attention loop (push + reply + permission are native).

**Degree of fulfillment - the hard caveats (why this is not a V1 dependency yet):**
1. **NOT in our installed build.** `claude 2.1.208` help has NO `--channels` flag (checked, zero "channel" matches in 226 lines). Research preview, rolling out gradually; we cannot use it today. **[verified]**
2. **Research preview** - the docs say the `--channels` flag syntax and protocol contract MAY CHANGE on feedback. Building V1 hard against it is premature.
3. **Custom channel needs the danger flag** during preview (`--dangerously-load-development-channels`), since Pulse would not be on Anthropic's curated allowlist. Official listing needs an Anthropic partner contact; OR, on Team/Enterprise, an admin adds it to `allowedChannelPlugins` in managed settings (with `channelsEnabled: true`).
4. **Org gate** - claude.ai Team/Enterprise blocks channels until an Owner sets `channelsEnabled`. Confirm NAKIVO's plan/policy. Pro/Max individuals are ungated.
5. **Auth constraint** - claude.ai or Console API key only; NOT Bedrock/Vertex/Foundry.
6. **Session must be open with `--channels`** - events arrive only while the session runs; always-on needs a background process / persistent terminal. This *confirms* flip-fact 1 (owned/background sessions), it does not help observe arbitrary interactive sessions the user did not start as channels.
7. **Fire-and-forget** - notifications are not acknowledged, dropped silently if the session is closed or org policy blocks; queued and delivered in-order in a batch on the next turn. Track state on our side.
8. **Permission relay scope** - tool-use approvals only (Bash/Write/Edit). NOT project-trust or MCP-consent dialogs, and NOT a prose "open question" (that still has no passive signal, the agent must call a reply/ask tool).

**Recommendation:** treat Channels as the **intended V1+ target mechanism** for the
attention/response loop, but gate it behind "available in our build + out of
research preview + org-enabled". Until then, V1 ships on the fallback stack
(hooks + poll + `--resume`/stream-json for owned background agents), designed so
the loop can swap to Channels with minimal churn (same request/verdict shapes).
Reference implementation to study now: the official **fakechat** channel (a
localhost web-UI channel) - it is structurally what Pulse would become.

Owner: connector build + product (adopt-when-GA decision). Blocks: nothing today (fallback exists); becomes the preferred path once GA.

---

## G1 - Session discovery and reconciliation

**Needs:** enumerate sessions (id, project, snapshot, timestamps) and keep them reconciled.

- **[verified]** `claude agents --json` prints active sessions as a JSON array (no TTY needed); `--all` adds completed; `--cwd <path>` filters. `claude agents` explicitly says "**Manage background agents**".
- **[ours]** reading `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl` works today (our ingester already does it).

Open points (mechanism):
- Does `agents --json` enumerate **only background agents** (started with `--bg`) or also foreground interactive sessions? The subcommand name says background. If background-only, interactive sessions are discoverable **only** by fs-scan.
- The **encoded-cwd -> project** mapping rule (non-alphanumerics to `-`), and collisions when two projects encode the same.
- Multiple **concurrent sessions in one cwd** (many jsonl files), which is "current".
- Reconciling the two sources (JSON list vs fs-scan) without double-counting.

Hypothesis: fs-scan is the correctness floor (we own it); treat `agents --json` as the richer, live view for background agents. Poll every 3-5s (roadmap).

Close via: Gate 0 item 5 + diff the JSON list against the jsonl files for the same cwd.

Owner: connector build. Blocks: everything downstream.

---

## G2 - Live state derivation (the hardest sensing gate)

**Needs:** map each session to In Flight / Needs Me / Review / Finished / Parked.

- **[docs]** there is **no external "is this session working vs blocked vs idle" API**. State must be inferred from hook transitions + the transcript tail + file mtime.

Open points (mechanism):
- Ground-truth signal for each lane. Likely: `SessionEnd`/last-event = Finished; a permission hook = Needs Me; a stop/failure signal = Needs Me / Parked; recent mtime + no terminal event = In Flight.
- Is there a distinct **"waiting on a human permission decision"** signal, or must we infer it from a permission hook firing with no subsequent event?
- **"Open human decision" that is NOT a tool permission** (the agent asks a question in prose). There is probably **no passive signal** for this, it may only be observable if the agent raises it through our MCP checkpoint (see G4). This is a real limit on what the board can catch passively.
- "Done" detection: is `SessionEnd` written into the jsonl, or only a process-exit the daemon cannot see for a detached session?
- Stale vs stopped: distinguishing a crashed/killed session from a slow one.

Hypothesis: hooks give low-latency transitions, transcript-tail + mtime give the correctness backstop, precedence per roadmap (failure/stopped > open attention > review > working > complete > stale), stale = 3 missed polls with no mtime change and no terminal event.

Close via: Gate 0 items 1-2, then build the state truth table from observed payloads (operational tier).

Owner: connector build. Blocks: board correctness (the whole product's trust).

---

## G3 - Hooks ingestion

**Needs:** fast, best-effort lifecycle signals pushed to the daemon.

- **[docs]** hooks are shell commands, receive JSON on stdin (incl. `session_id`, `transcript_path`, `cwd`, `hook_event_name`), and can run `curl http://localhost:...`. Some are blocking (pre-tool / permission), most are best-effort.
- **[verified]** hooks exist as a first-class subsystem (`--bare` documents "skip hooks"; `--debug hooks` category exists).

Open points (mechanism):
- **Exact event list and payload fields on 2.1.208** (Gate 0 item 1). Do not hard-code names from docs.
- Hook **timeout / blocking policy**: we must guarantee our hook never blocks Claude. A localhost POST must be fire-and-forget with a hard local timeout.
- **Registration scope and install UX**: user `settings.json` vs project `.claude/settings.json` vs local; how a `pulse install` writes them safely without clobbering the user's existing hooks.
- **Double-fire / at-least-once**: hooks may fire more than once, need dedupe (see G7).
- **Guarantee level**: hooks are best-effort, so polling stays authoritative (roadmap rule: polling = correctness, hooks = latency).

Hypothesis: hooks are latency improvers only, never authoritative, never blocking.

Owner: connector build. Blocks: attention latency (not correctness).

---

## G4 - Attention request via MCP checkpoint + session correlation

**Needs:** the agent raises `request_input` / `review_ready` tied to the correct session.

- **[docs]** we can run a local stdio MCP server and expose a `pulse.checkpoint(...)` tool; Claude calls it. `claude mcp` + `--mcp-config` manage registration.
- **[docs]** the MCP tool call carries **no session id / correlation id** by default.
- **[verified]** `--session-id <uuid>` lets us **launch a session with a known id**. `--mcp-config` can attach a server per dispatched session.

Open points (mechanism):
- How the checkpoint learns **which session** called it. Candidates:
  - (a) daemon **launches the session with `--session-id <uuid>` and injects that id into the MCP server env** (viable only for daemon-owned / background sessions);
  - (b) the agent passes a **correlation token** in the checkpoint payload that we also observe via a hook, then join;
  - (c) match by cwd + timing (fragile, reject unless nothing better).
- Whether `.mcp.json` per-project registration can carry **per-session env** at all, or only static config.
- How the agent is **told to call** the checkpoint tool (instructions / skill / CLAUDE.md snippet), and how reliably it does.

Hypothesis: option (a) for daemon-owned background agents (clean, uses `--session-id`); option (b) as the fallback for foreground sessions.

Close via: Gate 0 items 4-5.

Owner: connector build. Blocks: the attention half of the loop.

---

## G5 - Response delivery back into the session (the crux gate)

**Needs:** the human answer reaches the agent so it continues.

- **[docs]** you **cannot inject a message into a currently-running interactive session** from outside.
- **[verified]** `-r/--resume [id]` continues the **same** session id (appends to the same transcript); `--fork-session` forks instead; resume works once the session has **stopped**.
- **[verified]** `-p --input-format stream-json` + `--output-format stream-json` is a **bidirectional headless channel**, a candidate for feeding messages to a live headless/background session.

Open points (mechanism), two rival models:
- **M1 - Blocking MCP tool (pause in place).** Agent calls `pulse.request_input()`; our MCP server **blocks**, polls the daemon for the human answer, returns it as the tool result. The session never exits.
  - Open: does a **multi-minute / multi-hour blocking tool call survive** (timeout)? Requires G4 correlation. Concurrency of several pending answers.
- **M2 - Stream-json headless (feed a live background agent).** Run the session as a background agent with stream-json input; the daemon writes the human message into that input stream.
  - Open: does stream-json input **accept new user turns mid-run**, and does it target the right session? Only works for daemon-launched headless/background sessions, not the user's own interactive terminal.
- **M3 - Resume-after-stop (fallback).** Agent stops with an open request; human answers; daemon runs `claude --resume <id> -p "<answer>"`.
  - Open: reliable headless continuation with correct **cwd / project / permission mode**; latency; it changes the UX from "pause" to "stop and restart".

Hypothesis: prefer M1 for the clean "pause in place" UX; keep M3 as the guaranteed-supported fallback; evaluate M2 for background agents.

**This is the V1 flip-fact.** If long-blocking tool calls are not viable (Gate 0 item 3), V1's loop becomes stop-then-resume (M3), which changes the acceptance criteria and the drawer UX. Decide before committing.

Owner: connector build + product (which model). Blocks: the response half of the loop.

---

## G6 - Subagent / child attribution

**Needs:** show child agents as activity under their parent (never as top-level cards).

- **[docs]** subagents get their own session id + their own jsonl; the subagent-stop hook carries the **parent `session_id`** plus `agent_id` / `agent_transcript_path` (`agent_id` from ~v2.1.199, our 2.1.208 qualifies).

Open points (mechanism):
- Confirm the **parent-linkage field** actually present in the payload on 2.1.208 (name may not be `parent_session_id`).
- A **poll backstop** for when the hook is missed: reconstruct parentage by scanning the parent transcript for Task tool_use ids / `parent_tool_use_id`.
- Nesting depth (docs say up to 5) and how deep the board should render.

Hypothesis: hook for live linkage, transcript-scan as the backstop, render only depth-1 children as activity, deeper nesting collapses to a count.

Close via: Gate 0 (run one Task, capture both jsonl files + the hook payloads).

Owner: connector build. Blocks: the child-activity rendering (already in the V0 board).

---

## G7 - Idempotency, ordering, staleness

**Needs:** dedupe events, order them, never park on one missed poll.

Open points (mechanism):
- **Idempotency key** derivation. Candidate: `(session_id, hook_event_name, prompt_id, tool_use_id, ts)`. Confirm which of these fields actually exist (Gate 0).
- **Poll vs hook race**: the same transition arriving from both a hook and a poll, dedupe must span both sources.
- **Stale threshold**: pick a concrete poll interval (roadmap 3-5s) and N missed polls (roadmap ~3) with no mtime change and no terminal event.
- **Clock**: hook/transcript timestamps vs daemon receipt time (we hit a UTC-vs-local issue already in the Logbook "today" bug, apply the same care).

Hypothesis: append-only events with an idempotency key; last-writer-wins by monotonic ts; stale = 3x poll interval.

Owner: persistence build. Lower gate (design-solvable once field names are known).

---

## G8 - Read-oriented actions (attach / resume / logs)

**Needs:** V1 only copies safe commands; active control is V1.1.

- **[verified]** `claude --resume <id>`, `--session-id <uuid>`, `claude attach <id>`, `claude logs <id>` all exist on 2.1.208 (attach/logs are hidden from top-level help but real). Transcript path is stable and documented.
- For the owned-background-agent model, the natural safe actions are `claude attach <id>` (open the session), `claude logs <id>` (recent output), and `claude stop <id>` (V1.1, confirmation-gated).

Open points (mechanism):
- Which of these we surface as copy-commands in V1 (read-only) vs one-click actions in V1.1 (attach/logs read-only; stop/kill/rm gated).
- Whether to offer "open transcript" pointing at the jsonl / `~/.claude/jobs/<id>` path.

Owner: UI. Low gate.

---

## G9 - Daemon to browser transport (SSE)

**Needs:** push live board updates to the browser.

- **[ours]** the FlightDeck app already runs SSE `/api/stream` + a per-range snapshot cache. Reuse it.

Open points (mechanism):
- Per-attention-item fan-out and reconnect/backfill on the SSE stream.
- The SSE-connection-accumulation behavior we already characterized (long-lived tabs), carry the same mitigation.

Owner: reuse existing. Low gate.

---

## G10 - MCP install / config path

**Needs:** the checkpoint MCP server is registered so Claude calls it.

- **[verified]** `claude mcp` manages servers; `--mcp-config` / `--strict-mcp-config` attach config to dispatched sessions.

Open points (mechanism):
- Registration **scope**: project `.mcp.json` vs user settings, and whether per-session env can be injected (ties to G4 option a).
- Install UX: a `pulse install` that writes the MCP entry + hooks without clobbering existing user config, and an uninstall that cleanly reverses it.

Owner: connector build + DX. Medium gate.

---

## Flip-facts that could reshape V1

1. **Owned vs observed sessions. -> DECIDED 2026-07-14: owned background agents** (see "V1 DECISION" at the top). The supported primitives (`claude agents --json`, `--bg`, `--session-id`, the jobs store + supervisor) all favor sessions FlightDeck launches and owns. This collapsed G1/G4 to CLOSED and G2/G5 to mostly-supported.

2. **Response-delivery model (G5).** M1 (blocking MCP tool) gives the nicest UX but hinges on long blocking calls being viable; M3 (resume-after-stop) is guaranteed but changes the loop. This single spike result sets the V1 acceptance criteria and the drawer behavior. Owner: connector + product.

## Verified CLI surface (Claude Code 2.1.208, for reference)

- `claude agents --json [--all] [--cwd <path>]` - active (and with `--all`, completed) sessions as JSON. Scope: background agents.
- `claude --bg / --background` - start a session as a background agent, return immediately.
- `claude --session-id <uuid>` - start with a chosen session id.
- `claude -r/--resume [id]`, `-c/--continue`, `--fork-session` - resume same id / most recent / fork.
- `claude -p/--print` with `--input-format stream-json` and `--output-format stream-json` - bidirectional headless I/O.
- `claude mcp`, `--mcp-config`, `--strict-mcp-config` - MCP registration.
- No `logs` and no `attach` subcommand exist. Top-level commands: agents, auth, auto-mode, doctor, gateway, install, mcp, plugin, project, setup-token, ultrareview, update.
