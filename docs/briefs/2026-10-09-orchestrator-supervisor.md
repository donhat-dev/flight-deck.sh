# Orchestrator supervisor

```text
Feature: Supervise Sessions From the Orchestrator
Goal: one long-lived Orchestrator watches the main sessions, tells a session or a person when something they wait on happens, and keeps a ledger of everything it saw and did.
Now: the Orchestrator page shows live sessions and agents; its relay process (fd-orchestrator, Haiku) sends only messages a person typed and confirmed, answers questions from a snapshot, and runs only while someone starts it (used once, 2026-09-28).
Direction: watch rules and an append-only ledger in Odoo; the follow cron records what a rule matches and delivers it (ledger, push, or a notice to another session through the relay's exact-match gate); a supervise step keeps the relay running, restarts it with back-off and replaces it when its context grows.
Constraints: the model never writes an outgoing message, Flightdoo code does, and the gate still allows only exact text it queued; nothing changes for following, push or the chat bridge; only FlightDeck managers create watches.
Done when: module tests pass on a copy of the live database (watch → notice → gate allows the exact text and denies any other; ledger dedupe and append-only; keep-alive start and back-off; rotation); live upgraded; one real notice delivered between two sessions on this machine.
Not now: the model deciding what to send; interrupting or stopping sessions; watches on task or project tags; a work registry (task → sessions → state).
Refs: knowledges/ai-engineering/agent-team-harnesses (C16–C19); models/orchestrator_relay.py; models/turn_notify.py; brief 2026-10-08-agent-steps (P3).
```

## Decisions

- Chose deterministic watch rules in Odoo over letting the relay model decide what to send, because the gate's guarantee (only exact, Flightdoo-queued text leaves) holds only if code writes the text; the model stays a relay and a question answerer.
- Chose a request kind `notice` beside `send`, sharing one gate path (`RELAYED`), over a second gate, because the rule is the same: the SendMessage call must carry the queued text exactly; the kind only records who wrote it.
- Chose to run the supervisor pass at the end of the follow cron, rate-limited to every 20 s in process memory, over a cron of its own, because a turn is then seen in the pass that stored it, and a config-parameter cursor would clear the registry cache on every pass.
- Chose a 15-minute look-back with a unique key per observation and watch (`turn:<id>:w<id>`) over a stored cursor, because the key makes a second pass harmless and older turns are history, not news.
- Chose one ledger entry per observation per matching watch, because each watch delivers separately and the entry is what its delivery links to.
- Chose an append-only ledger (write raises; unlink only with `fd_ledger_prune` in context) with users reading and nobody writing through the UI, because the ledger is evidence of what the relay did.
- Chose to record every relay request state change and every start, stop and gate change as ledger entries through `create`/`write` hooks, because the request rows already change state at each step and hooks cannot be skipped by a new caller.
- Chose "every main session" to mean sessions in the CLI roster with kind `interactive`, minus the relay and chat sessions, because headless runs and the chat bridge are not the work the user follows.
- Chose to ship two ledger-only watches (main sessions' ended turns, failed agents) and no notice watch, because going live then starts no process and sends nothing; a notice needs a person to create a watch.
- Chose keep-alive when "keep running" is on or a notice watch exists, with a 10-minute back-off between starts, because a relay that dies at once must not be restarted every 20 s.
- Chose to retire a relay whose last call carried more than 80k tokens of context, only when no request is queued or in flight, and to start the replacement only after the old process has stopped (or 10 minutes passed), because two processes would both take the name fd-orchestrator.
- Chose to put the last 24 hours of the ledger into the question snapshot, because a replaced relay has no memory and the ledger is what it should answer from.
- Chose notice text with the watch name, the session, the turn title, at most 600 characters of the last reply, the watch note, and a line saying Flightdoo wrote it, because the receiving Claude has to know it is not the user speaking.
- Chose native list/form views for watches and the ledger and a small Supervisor section on the page (keep running, watches, latest ledger, "Watch this session"), and turned the Orchestrator menu into a group (Live, Watches, Ledger).
- Chose to count a user line with `turnOrigin: sdk` as a turn's prompt (`activity_reader.is_human_prompt`), because since CLI 2.1.283 every prompt a runner session or `claude -p` receives carries `sdk` and no turn was stored for them; the live probe found it. Peer messages, task notifications and scheduled wake-ups still do not start a turn.
- Chose to test on `fd_orch_test`, a pg_dump copy of the live database, with host runners deactivated, the existing relay unflagged and the roster patched inside each test, because a test must never write to the real spool.
