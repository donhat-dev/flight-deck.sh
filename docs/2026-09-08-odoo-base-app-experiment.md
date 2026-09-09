# Odoo 19 as FlightDeck's base application — experiment spec

**Question.** Can Odoo 19 CE host FlightDeck's operational data and screens well enough
to be the base application, instead of the FastAPI + React stack?

**Scope.** Three tabs only: logbook, treasure, spend. One module, `flight_deck`.
Data comes once from the live ledger; live JSONL ingest is out of scope.

## Stack

Its own compose in `docker/odoo19/`, untouched by `platform/odoo-dev`, because this is a
from-zero install with no NAKIVO data in it.

| | |
|---|---|
| image | `odoo:19.0` |
| container / port | `flightdeck-odoo19`, `8029:8069` |
| database | `flightdeck_odoo` on `pgcore-17` |
| network | `pgnet`, so the import reads the `flightdeck` ledger directly, read only |
| mounts | `../../odoo:/mnt/flightdeck-addons:ro`, `~/.flightdeck/treasures:/mnt/treasures:ro` |

The ledger database is the live one. The import opens it read only and writes nothing back.

## Models

| model | source table | role |
|---|---|---|
| `flightdeck.project` | `messages.project` | grouping key with context, which a bare char field does not give a pivot |
| `flightdeck.session` | `session_meta` | logbook row; totals stored |
| `flightdeck.message` | `messages` | the grain spend is measured on; `cost` is a stored float |
| `flightdeck.message.text` | `message_text` | transcript lines, ordered by `seq` |
| `flightdeck.tool.call` | `tool_calls` | tool tab of a session |
| `flightdeck.treasure`, `.tag` | `treasures`, `treasure_tags` | library; `origin_id` becomes a link to the session |
| `flightdeck.model.price` | `backend/flightdeck/pricing.py` | rates as records: longest prefix wins, date window, cache multipliers |

Cost is computed once at import time from the message's own timestamp and stored.
A non-stored computed field cannot be a pivot measure, and the promotional rate that
ended 2026-09-01 means the rate depends on when a message was sent, not on today.

Session totals are written by the import rather than recomputed from `@api.depends`.
With 103k child rows, recompute during load is the difference between minutes and hours.
The cost of that choice: editing a message by hand leaves the session total stale.

## Screens

| tab | model | views |
|---|---|---|
| Spend | `flightdeck.message` | pivot (`ts:day` by `model`, measure `cost`), stacked bar graph, list |
| Logbook | `flightdeck.session` | list, then form with a transcript tab, a tool call tab and a token tab |
| Treasure | `flightdeck.treasure` | kanban by status, then form with the rendered artifact |
| Configuration | `flightdeck.model.price` | editable list |

Two OWL components, and only two:

1. **Transcript viewer** — a session is interleaved user, assistant and tool turns with
   foldable tool input. A one2many list is readable but is not a transcript.
2. **Artifact preview** — an `Html` field sanitizes its content, and an artifact is a whole
   page with its own `<style>` and embedded fonts. It is served from `ir.attachment` into a
   sandboxed iframe.

Spend stays on the native pivot. If the pivot cannot stack cost by model over time, or is
too slow over 103k rows, that is when a third component is written, with the measurement
that justified it.

## Import

`odoo/migration/import_ledger.py`, run under `odoo shell`, which does not autocommit.

Order: projects, sessions, messages, message text, tool calls, treasures and tags, then the
rendered artifacts as attachments. Batches of 1000, one commit per batch, idempotent on the
natural key so a re-run adds nothing. Each phase prints its row count and elapsed seconds.

## Done when

1. `flight_deck` installs on an empty database.
2. Row counts match the ledger, counted with SQL on both sides at import time. The
   numbers themselves live in the findings document, because the ledger keeps ingesting
   and a count is only true of the moment it was taken. Count rows: `pg_stat_user_tables`
   is an estimate and was wrong by a factor of twenty on the treasure table.
3. `SUM(cost)` in Odoo matches FlightDeck's own cost math — `backend/flightdeck/pricing.py`
   run over the same ledger rows — to rounding.
4. Three flows drive in a browser, screenshots in `devtools_mcp_trace/`: the spend pivot by
   day and model, a session opened to its transcript, a treasure opened to its artifact.
5. A findings register: what the native views covered, what was lost, and one line per OWL
   component naming what native could not do.
