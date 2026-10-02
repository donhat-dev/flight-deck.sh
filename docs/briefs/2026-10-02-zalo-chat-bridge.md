# Zalo chat bridge (phase 1)

Feature: Zalo chat bridge
Goal: a message to the Zalo bot starts a Workflow Studio flow that asks a Flightdoo session and sends the session's answer back to the same Zalo chat.
Now: the Zalo Bot connector receives messages and sends replies (OWS); Flightdoo starts claude sessions on a runner and reads their transcripts, but nothing outside Flightdoo can ask a session and wait for its answer.
Direction: `flightdeck.session.fd_chat_send(chat_key, text)` finds or makes one session per chat on the container runner and returns a key; when the turn ends, the follow cron resumes the flow's Wait step on that key with the answer; the flow sends it with Zalo Send Message.
Constraints: the chat session runs on the container runner in `dontAsk` with no allowed tools and a system prompt for chat replies; only listed Zalo senders reach it; nothing in Flightdoo's existing follow, runner or transcript behaviour changes for other sessions.
Done when: a delivery posted to the Zalo inbound URL produces a flow run that waits, a session answer resumes it, and the reply step calls Zalo's sendMessage with that answer (tested with HTTP mocked); the flow and runner settings exist on live; the manual steps (bot token, public URL, allowed sender) are written down.
Not now: streaming partial answers; per-chat memory limits; a palette step for the FlightDeck call (the flow uses a code step).
Refs: OWS ADR-019 (waits), ADR-021 (connectors); OWS briefs 2026-10-02-connector-operations and -connector-accounts; session 0bfc269a-d2eb-433c-a97c-1fc87bd31ce3.

## Decisions

- Chose one flow that waits (Zalo trigger → code step `fd_chat_send` → Wait on a key → Zalo Send Message) over two flows joined by an event, because the Wait step already resumes by key (OWS ADR-019) and the whole exchange then shows as one run.
- Chose to read the answer from the transcript after the runner's `result` event, over adding the text to the event in the Go runner, because the follow cron already turns the transcript into rows and no runner rebuild or container recreate is needed; the event only marks the turn ended.
- Chose a reply row per prompt (`flightdeck.session.reply`) with the transcript line before the prompt, over "the last assistant text", because a second message can arrive before the first answer; turns end in order, so a `result` ends the oldest waiting row.
- Chose one session per chat (`chat_key`) on the container runner, in `dontAsk` with no allowed tools, a chat system prompt (`--append-system-prompt`, already supported by the runner), and the runner's sandbox directory as cwd, because a chat sender must not be able to approve or run a tool on this machine.
- Chose an allowed-sender list inside the flow's If step, empty at first, with strangers told their Zalo id, because the person's id is only known once they write to the bot.
- Chose a code step for the FlightDeck call over a palette step, because one flow uses it; a palette step comes when a second flow needs it.
- Chose to test on a copy of the live database (`fd_chat_copy`), because a fresh install of flight_deck fails (`flight_deck.menu_root` is used before `menus.xml` loads), which is outside this brief.
- Chose a Caddy listener on 127.0.0.1:8031 that passes only `/workflow_studio/connector/*` to Flightdoo, as the tunnel's origin, so a public URL never reaches the rest of Odoo.
- Chose to leave starting the tunnel, the bot token and the allowed id to the user: each is a secret or opens the machine to the internet.
