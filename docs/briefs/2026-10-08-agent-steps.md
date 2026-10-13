# Agent steps for Workflow Studio

Feature: Ask Agent From a Flow
Goal: a flow drawn in Workflow Studio asks a FlightDeck session with one step, Ask Agent, and goes on with its answer, or starts a session and goes on (Start Session).
Now: a flow calls `flightdeck.session.fd_chat_send` in a Code step and waits on the returned key with a separate Wait step (the Zalo chat flow).
Direction: a new module `flight_deck_workflow` whose two runners wrap `fd_chat_send`; Ask Agent opens the wait itself; Workflow Studio learns waiting and outside-reaching steps from a runner's attributes instead of fixed lists.
Constraints: the chat bridge stays as it is; chat sessions keep `dontAsk` and the runner's allowed tools; only a flow whose user is a FlightDeck manager may send.
Done when: the module's tests pass on a copy of the live database; the Workflow Studio suite passes on `wfs_port`; the live Zalo chat flow runs on Ask Agent.
Not now: choosing a session by project or task tags, sending to interactive sessions through the orchestrator (P3), agents editing flows (P2).
Refs: proposal in session f927a2b4 (2026-10-08, P1); brief 2026-10-02-zalo-chat-bridge; `odoo/flight_deck/models/chat_bridge.py`.

## Decisions

- Chose a separate module `flight_deck_workflow` (depends on `flight_deck` and `workflow_studio`) because neither module should require the other; a database without one of them keeps working.
- Chose to register the runners by updating `WorkflowExecutor.NODE_RUNNERS` at import because that is how Workflow Studio's own runners join; no registry API was added.
- Chose runner attributes (`parks_run`, `reaches_outside`) over fixed type lists in Workflow Studio because an add-on step must count as waiting (orchestration) and as outside-reaching (atomic flows refuse it) without editing core lists.
- Chose a default time limit of 10 minutes for Ask Agent because the bridge's runner reaps idle sessions after 15 minutes; the answer or the timeout arrives before the session is gone.
- Chose to resume with `{answer, session_id, chat_key, input}` because the step's input is lost once the run parks; the next step needs the original message (for example the chat id) to reply.
- Chose an empty conversation key to mean one session per run (`flow:<id>:run:<id>`) because two agent steps in one run usually continue one conversation.
- Chose to allow only a superuser or a FlightDeck manager as the flow's user because a session runs tools on the host with `dontAsk`; the check runs before anything is sent.
- Chose to send nothing in a preview without a run because a preview cannot park, and a send would start a real session.
- Chose to report a bridge `UserError` (no active runner) as a permanent `connection` error because a retry does not bring a runner back; the error socket handles it.
