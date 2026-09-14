# What the transcript view still needs from the backend

Written after building the reading view (`session-detail-redesign.md`). Each
item below is something the UI currently fakes, degrades, or cannot do at all,
with the reason it belongs on the server rather than in the browser.

## Where things stand

Already there and used:

| Endpoint | Used for |
|---|---|
| `GET /api/session/{id}?anchor=tail&limit=` | the windowed transcript |
| `GET /api/session/{id}/subagent/{agent}` | a nested thread, on expand |
| `GET /api/session/{id}/usage` | tokens and cost in the instruments rail |
| `GET /api/screenshot?path=` | the image renderer |

Already in the ledger and NOT exposed over HTTP:

- `message_text` - the full-text corpus, with `flightdeck/sessions/service.py`
  providing `search()` and `read()`. Reachable today only through MCP.
- `tool_calls` - one row per tool_use: `id, session_id, project, ts, tool,
  server, detail`. No duration, no outcome, no result size.

## 1. Search inside a session (unblocks the `/` key)

`GET /api/session/{id}/search?q=&role=&limit=`

Wrap the existing `sessions.service.search()` with a `session_id` filter and
return `[{uuid, role, ts, turn_offset, excerpt}]`. `turn_offset` is what makes
it useful: the client can fetch the window containing the hit instead of paging
back to it.

The client cannot do this itself. It holds 150 turns; the answer is usually in
the other 38,000.

## 2. Prompt index (the rail currently sees only the loaded window)

`GET /api/session/{id}/prompts`

Every user turn: `[{turn_offset, ts, text (first 200 chars), tool_count,
error_count}]`. Streams from the same byte index `build_transcript` already
builds, so it costs one pass and then nothing.

Turns the prompts rail from "the five prompts you happen to have loaded" into
the table of contents for the whole session, where clicking one loads its
window.

## 3. Tool-call facts (durations are currently guessed)

The card shows a duration derived from the gap between the assistant turn and
the turn its result arrived in. That is the right order of magnitude and the
wrong number whenever anything else happened in between.

Extend `tool_calls` during ingest with what the JSONL already contains:

```sql
ALTER TABLE tool_calls ADD COLUMN result_ts TEXT;      -- when the result landed
ALTER TABLE tool_calls ADD COLUMN duration_ms INTEGER; -- computed at ingest
ALTER TABLE tool_calls ADD COLUMN is_error INTEGER;    -- 0/1
ALTER TABLE tool_calls ADD COLUMN result_bytes INTEGER;
```

Then `GET /api/session/{id}/tools?kind=&errors_only=` returns the activity rail
from mock S3 for the WHOLE session: what ran, in order, how long it took, which
failed. Today that rail can only describe the loaded window.

This also gives the Logbook a "slowest tool calls" and "most failed tool" view
for free, which is the kind of thing the deck exists for.

## 4. The `fd-widget` envelope, from our own MCP

The renderer and its tests are built; nothing emits the envelope yet. Fit it to
the three tools where a table beats prose:

- `session_search` - metrics (matches, sessions, span) plus a hit table.
- `radar_list` - blips by ring.
- `treasure_list` - title, tags, updated.

Rule to keep: emit the fence AND the plain text. A client that does not know the
envelope must still read something sensible, and `pickRenderer` already falls
back to `json` for an unknown `type`.

## 5. Full block on demand

`_MAX_BLOCK_CHARS = 24000` truncates a block server-side. That is right for the
window payload and wrong when the reader clicks "show the rest" on the one block
they care about.

`GET /api/session/{id}/block/{turn_offset}/{block_index}?full=1` returning the
untruncated text closes the loop. Small, and it removes the only place where the
UI says "show the rest" and cannot deliver it.

## 6. Cheap wins already visible from here

- **Errors index**: `GET /api/session/{id}/errors` (offset + one-line reason) so
  `E` can jump across the whole session, not the window.
- **`anchor=offset` around a turn**: `?around=<offset>&limit=` for deep links
  from search and from the prompt index; today only head and tail exist.
- **ETag on the window**: the live tail refetches the same page whenever the
  file changes anywhere. An ETag over `(mtime, size, offset, limit)` turns most
  of those into 304s.

## 7. One frontend item that belongs on this list

Row height variance is what makes upward scrolling jump: the virtualizer
estimates an unmeasured row at the median (52px) and a long prose block is
~590px, so drawing one above the viewport moves the total by half a screen with
no compensation. Measured: 3 events per 60 wheel notches scrolling up, worst
473px (was 1069px before the block clamp came down to 460px).

The structural fix is to make a long prose block several rows instead of one -
split markdown at top-level block boundaries, keeping lists, tables and code
fences whole so numbering does not restart. That puts every row near the median
and removes the class of jump rather than bounding it.

## Order

1. Search + `around=` (they ship together; search is useless without the jump).
2. Prompt index.
3. `tool_calls` columns + the tools endpoint.
4. Widget envelope in the MCP.
5. Full block, errors index, ETag.

1 to 3 are what the built UI is currently faking. 4 is new capability. 5 is
polish that only matters once the first three are in.
