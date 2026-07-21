# CloudDeck Pulse — Version Scope and Implementation Roadmap

**Date:** 2026-07-14  
**Status:** Draft  
**Position:** A local human-attention companion for coding agents. The agent
harness owns execution; CloudDeck makes state, requests for attention, and
continuation context visible.

## Product rules

The core loop is:

    agent activity → attention request → human response → agent continues

- Kanban is a projection of session state and events, not the source of truth.
- A card cannot be dragged to mark an agent as finished.
- Polling provides correctness, hooks improve latency, and MCP captures
  human-meaningful checkpoints.
- Session is the execution unit. Outcome is optional grouping and memory.
- All versions are local-first and must degrade honestly when a connector fails.

## Version map

| Version | Core question | Integration | Deliverable |
|---|---|---|---|
| V0 | Is the interaction model understandable? | Mock events only | Clickable prototype |
| V1 | Does this reduce lost context in Claude Code? | Claude CLI, hooks, MCP | Local Session Radar |
| V1.1 | Can intervention be quick and safe? | Safe CLI actions | Personal supervisor |
| V2 | Can it support more than one harness? | Connector contract | Personal Agent Hub |

---

## V0 — Pulse Board Interaction Prototype

### Goal

Validate the attention-first Kanban model and the human-to-agent response loop
before building a daemon, connector, or database.

### Scope

- Browser-only prototype with mock session data and in-memory state.
- Four visible lanes: **Needs Me**, **In Flight**, **Review**, and **Parked**.
- A collapsed **Finished** history section.
- Session cards containing title, project, current state, activity summary,
  elapsed time, optional outcome, and child-agent count.
- A right-side detail drawer with event timeline, mock artifacts, and response
  form.
- A mock checklist request titled **Today outside Nakivo weather**:
  Temperature, Rain, Wind, and Commute advice.
- Simulated events: request_input, review_ready, child_started,
  child_completed, failed, resumed, and finished.
- Bright CloudDeck direction: cloud illustration, soft glow, afternoon/evening
  themes.

### Non-goals

- No Claude CLI, hooks, MCP, SQLite, server, authentication, or network calls.
- No real session IDs or message delivery.
- No drag-and-drop state changes, Todo system, graph view, or multi-harness UI.

### Interaction contract

    In Flight
      → simulated request_input
      → Needs Me gains emphasis
      → human completes form in drawer
      → simulated response_delivered
      → In Flight; timeline records response

Child agents stay as compact activity under the parent and never become
top-level Kanban cards.

### Implementation plan

1. Define 6–8 mock sessions, including one parent with three child activities
   and one weather checklist request.
2. Build the board shell, four lanes, Finished section, project filter, and
   lightweight live indicator.
3. Build reusable cards; keep the first scan line limited to title, state, and
   reason. Use only Explore, Produce, Verify, and Coordinate badges.
4. Build the detail drawer and structured response form.
5. Add a single reducer that accepts all simulated events and recomputes lane.
6. Expose low-emphasis demo controls or command-palette commands to emit those
   events.
7. Run a short usability review with the find-next-human-action-in-five-seconds
   test.

### Acceptance criteria

- Needs Me is unmistakable at a glance.
- Submitting a response moves its card to In Flight without page reload.
- The timeline answers why a card is in its current lane.
- Child activity gives context without creating board clutter.
- The UI never implies a human can manually change execution state.

### Deliverable

A self-contained interactive page; initially this can remain in
frontend/public/cloud-deck-prototype.html or become an isolated frontend route.

---

## V1 — Local Claude Companion

### Goal

Make the validated prototype a durable local companion for Claude Code: observe
live sessions, preserve continuation context, and surface only the moments that
need human attention.

### Scope

- Localhost-only daemon and web UI.
- One agent harness: Claude Code.
- Session reconciliation through the Claude CLI agent-listing JSON command.
- Lifecycle events from Claude hooks.
- Small MCP server for semantic checkpoints.
- SQLite storage for sessions, events, attention items, outcomes, and resume
  capsules.
- Server-Sent Events from daemon to browser.
- Read-oriented actions: copy attach and log commands.
- V0 board and drawer, now fed by live data.

### State model

| Source condition | Board state |
|---|---|
| Native working | In Flight |
| Native blocked, permission request, or open human decision | Needs Me |
| Review-ready checkpoint | Review |
| Native done | Finished |
| Native failed | Needs Me |
| Native stopped or confirmed stale session | Parked |

State precedence is: failure/stopped, open human attention, review ready,
working, complete, stale. The UI never writes a native execution state.

### Minimal persistent model

    sessions:        native IDs, project, snapshot, summary, timestamps
    outcomes:        optional title and project grouping
    events:          append-only meaningful lifecycle/checkpoint events
    attention_items: open/resolved requests requiring a human
    resume_capsules: reached, decisions, artifacts, unresolved work, next step

Events need idempotency keys. A one-off missed poll must not park a session;
use a defined stale threshold, such as three missed polls.

### MCP surface

    clouddeck.checkpoint(
      kind: decision | blocked | request_input | review_ready | artifact | resume,
      summary: string,
      outcome?: string,
      artifacts?: string[],
      next?: string[]
    )

Request-input creates an attention item. CloudDeck posts the human response back
to the intended Claude session and waits for connector reconciliation before
showing it as resumed.

### Non-goals

- No UI-driven start, stop, respawn, or dispatch of sessions.
- No multi-harness support, cloud sync, shared workspace, or RBAC.
- No full Todo CRUD, worktree/diff/PR panels, or repository mutation.
- No LLM state inference or ingestion of every raw tool call.

### Implementation plan

1. **Service foundation**
   - Create Python 3.12+ FastAPI local service with SQLite and asyncio
     subprocess calls.
   - Bind to localhost only; expose health, version, and connector capability.

2. **Claude connector**
   - Poll CLI snapshots every 3–5 seconds.
   - Normalize external records to an internal session model.
   - Retain raw payload only for diagnostics; make the UI depend on normalized
     fields.
   - Tolerate unavailable CLI, invalid JSON, unknown fields, and disappearing
     sessions.

3. **Persistence and projection**
   - Create tables for the minimal model.
   - Upsert snapshot state idempotently and derive board state deterministically.
   - Create and resolve attention items from source conditions.

4. **Hooks ingestion**
   - Receive permission, notification, subagent, session-end, and stop-failure
     events as fast, best-effort signals.
   - Keep hook failures non-blocking for Claude Code.
   - Do not ingest pre/post tool calls in V1.

5. **Checkpoint MCP**
   - Validate checkpoint payloads and attach them to the correct session.
   - Add a local plugin/configuration installation path.
   - Make request-input and review-ready visible through the same event stream.

6. **Live UI**
   - Replace V0 mock reducer inputs with REST snapshot plus SSE events.
   - Add timeline, capsule, and copy-command actions.
   - Show child activity only inside its parent card/detail.

7. **Recovery tests**
   - Test restart, duplicate hooks, stale polling, unknown fields, and connector
     outage.
   - Confirm Claude works normally while CloudDeck is unavailable.

### Acceptance criteria

- A live Claude session appears or changes within five seconds.
- One input request produces exactly one Needs Me item.
- A response is stored, delivered to the intended session, and shown in its
  timeline.
- Restart does not lose outcome, decision, or resume capsule.
- CloudDeck never writes directly to Claude session-history files.

### Deliverable

A personal localhost Session Radar for Claude Code, with durable context and
attention routing.

---

## V1.1 — Safe Active Supervision

### Goal

Shorten the time from noticing a session to taking a safe, reversible action,
without turning CloudDeck into an agent orchestrator.

### Scope

- Confirmation-gated actions: attach/open command, logs, stop background
  session, and respawn an eligible stopped/failed session.
- Structured response templates: decision, checklist, approval, clarification,
  and retry instruction.
- Outcome recap generated from stored events and capsules.
- Quiet hours and personal notification filtering.
- Audit trail for every human-initiated action.

### Non-goals

- No new-session spawn from Kanban.
- No arbitrary shell execution from browser.
- No worktree, code edit, Git, PR, or team-control experience.

### Implementation plan

1. Define allowed actions and their state preconditions; require confirmation
   for stop and respawn.
2. Add a local action adapter using argument arrays, never interpolated shell
   strings.
3. Persist actor, request, result, and timestamp as audit events.
4. Reconcile every action through the ordinary connector poller; never mutate
   board state optimistically from action success alone.
5. Add typed response templates and validate required fields before delivery.
6. Add recap and quiet-hour settings using structured stored data, not LLM
   reconstruction.
7. Test stale IDs, duplicate requests, failed commands, and recovery.

### Acceptance criteria

- Every active action has explicit confirmation, preconditions, and audit
  record.
- Board state changes only after connector confirmation.
- Common human requests can be resolved without manually composing a prompt.
- No endpoint accepts arbitrary local command execution.

### Deliverable

A safe personal supervisor layer on top of V1.

---

## V2 — Personal Agent Hub

### Goal

Extend the proven session/attention model to multiple agent harnesses without
pretending their native capabilities are identical.

### Scope

- Connector contract with Claude retained as reference implementation.
- One additional connector selected from real personal usage, such as Codex CLI.
- Harness-neutral session timeline, attention queue, outcomes, and resume
  handoff.
- Connector health and capability display.
- An outcome can aggregate sessions from different harnesses.

### Connector contract

    list_sessions() -> normalized snapshots
    subscribe_events() -> lifecycle/attention events, when available
    send_message(session_id, content) -> delivery result
    get_logs(session_id) -> optional log reference
    perform(action, session_id) -> optional policy-controlled action
    capabilities() -> supported operations

UI must hide an action that a connector does not support.

### Non-goals

- No universal model scheduler or autonomous dispatcher.
- No shared organization workspace, RBAC, billing, marketplace, or cloud sync.
- No assumption that raw logs from different harnesses mean the same thing.

### Implementation plan

1. Extract all Claude-specific code behind the connector contract while
   preserving V1 API responses.
2. Introduce normalized identity fields: harness, connector session ID, run ID,
   parent run ID, and stable CloudDeck ID.
3. Model resume/respawn as linked runs, never overwritten history.
4. Add capability-aware cards and session details; show harness and connector
   health clearly.
5. Implement one second connector with session discovery, message delivery, and
   one lifecycle path before optional controls.
6. Add fixture-based connector conformance tests.
7. Support cross-harness outcomes while keeping session ownership independent.
8. Test partial capability, offline connector, duplicate identity, and degraded
   state behavior.

### Acceptance criteria

- Claude V1 behavior is unchanged after connector extraction.
- A second harness fits the same board without special lanes.
- Message delivery targets the correct harness/session where supported.
- An offline connector produces an understandable degraded state, not false
  completion or activity.

### Deliverable

A local Personal Agent Hub with at least two capability-aware harness
integrations.

---

## Release gates

| Gate | Required evidence |
|---|---|
| V0 → V1 | Viewers identify next human action and complete the response loop without explanation. |
| V1 → V1.1 | State remains reliable through daemon restart, duplicate events, and temporary CLI failure. |
| V1.1 → V2 | Active controls are auditable and safe; connector boundary is stable in everyday use. |
