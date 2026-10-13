# Session messages

```text
Feature: Session messages
Goal: See the messages Claude sessions send each other, in the transcript and as conversations between two sessions, so the exchanges can be measured later.
Now: An outgoing SendMessage shows as a tool call; an incoming message that started a turn shows as a user line with the wrapper text; one that arrived mid-turn is not imported at all.
Direction: Read both sides from every transcript into one row per msg_id, group rows by the pair of sessions, and add a Sessions filter to the transcript box.
Constraints: Transcript import, spend and the turn ledger keep working as they do; no writes to flightdeck.session from the new scan.
Done when: the box shows incoming messages with the sender's name and a Sessions filter; the menu lists conversations with their messages, reply links and reply times; fork copies give one row per msg_id.
Not now: sends made from a subagent's own file; delivery notices (none in the transcripts yet); charts.
Refs: odoo/flight_deck/models/session_message.py, transcript_reader.py (parse_line, scan_peer_messages)
```

## What the transcripts hold (2026-10-02, 215 files)

- Outgoing: an assistant `tool_use` named `SendMessage` (`to`, `summary`, `message`) and, on a later line, its result with `toolUseResult.msg_id`.
- Incoming mid-turn: `type: attachment`, `attachment.type: queued_command`, `attachment.origin` with `kind: peer`, `msg_id`, `from` (socket), `name`, `body`.
- Incoming between turns: `type: user`, `isMeta`, top-level `origin` with the same fields.
- 57 sent and 59 received msg_ids; 56 match.
- `kind: peer` without `msg_id` is a subagent hand-back (533 lines), not a message.
- A forked session copies the lines it inherited with new uuids, so one msg_id sits in up to three files on each side.

## Decisions

- A cross-session message is `origin.kind == "peer"` with a `msg_id`; this drops hand-backs and idle notices.
- One row per msg_id. The sender side is created at the call line (msg_id empty) and gets its msg_id from the result line, then merges into a receiver-first row with the same id.
- Fork copies: the side kept is the session whose file starts first, then the lowest session id; the original starts before the forks made from it.
- A conversation is the pair of resolved session records, never names or sockets: one socket arrived under two different names in the same session.
- The roster (`~/.claude/sessions/<pid>.json`) resolves a sender socket only when the other side was never read; it covers live processes only.
- The scan keeps its cursor on its own model, so it never writes `flightdeck.session` while the follow cron does.
- Incoming lines are stored with role `peer`. The turn picker keeps `role = 'user'`, which matches the turn ledger: `activity_reader` skips `isMeta` lines, and peer lines are `isMeta`.
- Reply: the latest earlier message in the same conversation sent the other way; reply time is from its arrival to this send.
- The Sessions filter in the box works on the server (it ignores the turn range); the box only holds lines read while a session was followed, so the menu is the full history.
- A SendMessage answered without a msg_id went to the session's own subagent (106 of 208 calls on 2026-10-02) and is dropped; one answered with an error stays as Refused, with the answer.
- The box's Sessions filter still shows SendMessage calls to subagents: telling them apart needs the answer, which a live line gets later than the call.
- The CLI appends its own note to the model after `</cross-session-message>`; it is cut from the stored text (the 60 rows already imported were corrected on live).
- 1 GB per cron run: the full corpus read in about 3 s warm, so the cap only matters on a cold cache.
