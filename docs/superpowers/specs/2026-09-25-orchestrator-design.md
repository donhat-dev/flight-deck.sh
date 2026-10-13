# Orchestrator page, v1 (watching only)

Date: 2026-09-25. Module `flight_deck` (Flightdoo), menu FlightDeck > Orchestrator.
Mock: `docs/diagrams/orchestrator-canvas.lineframe.json`.

## What it shows

One tree, left to right, redrawn every 5 s:

- **Orchestrator**: the root. It stands for this machine. Later it becomes a real process
  you can ask about the current state and use to send messages to the sessions below it.
- **Sessions**: every entry in the CLI roster that has a logbook, busy or idle, newest
  first. An entry with no logbook is a window opened and never sent a message (the CLI
  writes the transcript at the first message), or one left behind, so it is left out.
- **Agents**: the agents each session started, finished ones included, nested under the
  agent that started them.

Wires leave a node's right side and enter its child's left side at right angles, with
rounded corners as in Workflow Studio. They turn in the middle of the gap, so the wires from
one parent share one vertical line; live wires are drawn over quiet ones. The wire and the
node's left edge take the child's status: busy in azure, running in green,
waiting in amber, failed in red. Finished agents have a dashed wire and a dashed border.
Scroll to pan, Ctrl or Cmd + scroll to zoom, drag the background to move, Fit to see the
whole tree. The tree opens against the left edge, centred top to bottom.

Clicking a node opens a side panel beside the canvas (click again or Esc to close), refreshed
with the tree. A session shows its folder, branch, the last 24 hours (turns, cost), the latest
turn's prompt and reply, and its recent turns, with an Open logbook button. An agent shows its
kind, model, start and end, tokens, what it was asked to do and what it reported, with an Open
transcript button. One call: `fd_orchestrator_detail(kind, id)`.

## Where the data comes from

| Level | Source | Read by |
|---|---|---|
| Sessions | `~/.claude/sessions/<pid>.json`, mounted at `/mnt/claude-sessions` | `read_roster()` on every refresh |
| Link to the logbook | `sessionId` in the roster = `flightdeck.session.session_id` | same call |
| Agents | `flightdeck.session.agent`, built by the follow tick | `search_read`, ordered by start |
| Model | the main agent's latest billed call | one `DISTINCT ON` query |

The roster gives the same facts as `/list-agents` (name, busy or idle, folder, start time)
plus the session id, which `/list-agents` leaves out. Reading it needs no fd-runner call and
no CLI process. Only the `.json` files are read; the `.key` files beside them are the
sessions' messaging keys and stay unread.

One call serves the page: `flightdeck.session.fd_orchestrator_tree()` in
`models/orchestrator.py`. The layout is computed in the browser
(`static/src/components/orchestrator/orchestrator_action.js`).

## Known limits

- The container cannot see host processes. An entry left by a killed session shows as idle
  until the CLI removes its file.
- A background agent that ended without a task notification is marked finished once its
  file has been quiet for 2 minutes, with no end time. It shows no duration.
- Sessions the follow tick does not follow have no agent rows yet, so they show no agents.

## Messaging probe (2026-09-28)

Run against two throwaway sessions started for the probe (`claude --bg` in the scratchpad);
no working session was messaged. The sender's `SendMessage` went through a PreToolUse hook
that allowed only the probe names; a send to another name was blocked.

| Question | Observed |
|---|---|
| Can a headless process list and message sessions? | Yes: `claude -p`, and a long-lived `claude -p --input-format stream-json` |
| Idle target | The message starts a turn within about 4 s |
| Target inside a 45 s tool call | Waits until the call ends (33 s), then is read; nothing is interrupted |
| Replies to a one-shot `-p` sender | Lost once it exits (`ENOENT` on its socket) |
| Replies to the long-lived sender | Arrive as new turns on its output; a second instruction on stdin works |
| Does the target obey? | It treats the message as another session's, not its user's: it acknowledged a request to run a command but did not run it |
| Subagent of another session | Not listed; `SendMessage` answers "No agent named 'probe-sub' is reachable" |
| Cost per send (Haiku, no user settings or MCP) | $0.025–0.035 and 3–8 s; Opus with the full setup $0.31 and 31 s |

So the Orchestrator is a long-lived `stream-json` process on the host; a message is a
request the receiving session may decline unless its user allowed it; a subagent is reached
through its session. `claude agents --json` also lists every session with its status and id.

## The relay (2026-09-28)

The Orchestrator's own process is a `flightdeck.session` on the host runner
(`is_orchestrator`), working in `~/.flightdeck/spool/orchestrator` (`/mnt/spool/orchestrator`
here) on Haiku in `default` mode, renamed `fd-orchestrator` at each start. Code:
`models/orchestrator_relay.py`.

- **Gate.** Flightdoo writes the folder's `.claude/settings.json` before each start:
  `permissions.ask: [SendMessage]` and a deny list for Bash, Edit, Write and the like. Every
  send then parks as a permission request (`flightdeck.decision`), and
  `_fd_orchestrator_gate` allows it only when `to` and `message` equal a request a person
  confirmed on the page. Everything else is denied.
- **Live test at every start.** The process is told to message `fd-orchestrator-selftest`.
  That call has to arrive as a permission request, which is denied; only then is the gate
  "working" and queued requests are forwarded. No request within 2 minutes = "broken".
- **Page.** Root panel: relay state, cost, Start/Stop, a question box (answered from a
  snapshot of the tree), recent requests. Session panel: a send box behind a confirm dialog,
  and that session's sends with their replies. A session that goes busy → idle while the page
  is open gets a toast and a dot until it is looked at.

| Probe, phase 2 | Observed |
|---|---|
| Project settings under the runner | Load from the cwd: a PreToolUse hook there fired |
| `SendMessage` in `default` mode | Does not ask; with `permissions.ask: [SendMessage]` it parks as a request in Approvals |
| `/rename` sent as a stream-json turn | Works; the name resets at the next start |
| `default` sender → `auto` target | Delivered, not held |
| `notify_when_idle` | One idle notice with the target's last message, as a new turn |
| Gate test at start | Denied as expected; gate "working" 40 s after Start |
| Unconfirmed `FORWARD` to a live probe target | Denied; the target got nothing |
| Question through the relay | Answered from the snapshot |
| Cost | About $0.07 per relay turn (the runner loads the user's settings) |
| Decision latency | About 25 s from the tool call to the answer |

Not verified: an allowed send end to end through the page. The harness's auto-mode
classifier blocked this session from confirming a send as the person, and from starting a
worker session told to carry out tasks sent by another session.

## Not in v1

Sending anything to a session or an agent. How the Orchestrator process asks and sends
will be decided separately.
