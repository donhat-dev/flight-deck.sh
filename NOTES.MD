# FlightDeck on Odoo 19 — experiment notes

A working experiment, not a port. It asks one question: can Odoo 19 CE hold FlightDeck's data
and screens well enough to be the base application? Scope was three tabs — logbook, treasure,
spend — and it grew a live transcript reader along the way.

Nothing here touches the FastAPI service or the React deck. The Odoo module reads, and (for
followed sessions) reads directly from the transcript files.

## Run it

```bash
cd docker/odoo19
cp .env.example .env            # then fill in the two role passwords
docker compose up -d
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
    -d flightdeck_odoo -i flight_deck --stop-after-init
docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
    -d flightdeck_odoo --no-http < ../../odoo/migration/import_ledger.py
```

http://flightdoo.localhost or http://localhost:8029, `admin` / `admin`. The host name is served
by `local-domains` and is not in this repository.

Go through `/entrypoint.sh`, never `odoo` directly: the credentials live in `.env`, and it is
the entrypoint that turns them into `--db_user` / `--db_password`.

## What is in here

| | |
|---|---|
| `odoo/flight_deck` | the module: 7 models, native views, a transcript chat box, a live reader |
| `odoo/migration/import_ledger.py` | one pass over the FlightDeck Postgres ledger, idempotent |
| `odoo/migration/transcript_to_chatter.py` | one session's transcript as `mail.message` |
| `odoo/migration/session_to_channel.py` | the same session as a `discuss.channel` |
| `odoo/migration/inject_turn.py` | append one turn, to watch it arrive live |
| `docker/odoo19` | its own compose, database and port; shares nothing with the NAKIVO tracks |
| `docs/2026-09-08-odoo-base-app-experiment.md` | the spec this was built to |
| `docs/2026-09-08-odoo-base-app-findings.md` | every measurement, and what each one settled |

## What it found

The long version is in the findings document. The short version:

- **Spend is the clear win.** Odoo's pivot and graph give, for free, the day-by-model cost
  breakdown and the stacked bars that FlightDeck builds by hand in React.
- **The ORM is not the bottleneck** anyone expects: 240,000 rows imported in about 21 seconds.
- **A transcript needs a component.** Native list and form views render rows, not a
  conversation. `TranscriptBox` is one OWL component behind both the field widget and the
  form's side pane.
- **Odoo can read the transcripts itself.** A thin watcher whose only act is
  `ir.cron._trigger()`, and a cron that does the reading. Byte offset says where to resume,
  `uuid` says what is already stored. Latency measured end to end: 0.17 s for a turn to reach
  disk, 4 ms from Odoo's commit to the turn painted.
- **Tool output exists only in the files.** FlightDeck's ingest drops `tool_result` on purpose,
  so the ledger has none.

## Traps worth keeping

- `mail.thread` owns the field name `message_ids`. A model of ours that defines its own
  silently shadows it, and the chatter then reads the wrong table. Renamed to `usage_ids`.
- `registerThreadAction` does not reach the chatter — that registry serves Discuss and chat
  windows. The chatter topbar is extended by inheriting the `mail.Chatter` template.
- `mail.message.subtype.name` is translated, so it is the wrong thing to branch on in the
  browser. The role travels as a class on the turn's own body.
- `seq` is the **absolute line number in a file**, not a count of stored rows. Starting a follow
  from the row count gives live turns a lower seq than imported ones, and they sort into the
  middle of the transcript.
- `odoo shell` runs as OdooBot. Anything created there that belongs to a person — a channel, a
  follower — lands in an account nobody opens.
- A crashed first install leaves Odoo's `auto_install` pass unfinished for good. The database
  then runs without `html_editor`, `web.assets_frontend` fails to compile, and every page
  outside the backend renders unstyled while the backend keeps working.
- Wrapping in a flex column needs `min-width: 0` on every ancestor. Without it a long path is
  pushed past the edge and clipped, which reads as a wrapping bug when wrapping never ran.

## Still open

- **Two readers over one source.** If Odoo follows a session that FlightDeck is also ingesting,
  each keeps its own byte gate and its own `seq`. They will disagree, silently. Decide who
  reads: the two are safe today only because Odoo follows a small set of sessions.
- **`session_send.py` has no counterpart here.** It calls `POST /api/session/<id>/send` on the
  FlightDeck backend, which lives in `backend/flightdeck/routers/send.py` — deliberately not in
  this commit, because that tree carries unrelated uncommitted work. Until that side is
  committed the model raises a clear error instead of sending, and no view exposes it.
- **Following is opt-in per session, and it has to stay that way.** The transcript tree is 1,927
  files and 873 MB, with a single session file reaching 380 MB. Watching all of them is not a
  configuration choice, it is a different program.
- **The host grants read access by ACL.** `setfacl -R -m u:100:rX ~/.claude/projects` plus the
  same with `-d`. The default ACL is what keeps new session files readable; without it this
  stops working tomorrow, silently.
- **The live-reload levers in `static/src/dev/live_reload.js` are for development.** They
  reload any open page as soon as a bundle changes and re-render a form whenever a view is
  written. That is right for one developer and wrong for anyone else on the instance.
- **Session totals do not follow edits.** They are written by the import; the form carries a
  Recompute totals button and that is the whole correction path.
- **Not attempted:** the React deck (pulse, missions, systems, radar, hub, the MCP surface),
  and the Treasures wrap / lint / render / publish pipeline. Odoo displays a rendered artifact;
  it does not make one.
