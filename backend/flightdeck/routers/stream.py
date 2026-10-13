"""SSE stream: every event on the bus, delivered per connection.

This used to await one module-level `asyncio.Event` and translate it into a
single `summary-updated` frame. It now drains a per-connection queue from
`events.BUS`, which changes two things and deliberately preserves a third:

- **A slow or parked tab no longer eats another tab's event.** The old
  generator called `_updated.clear()`, so with two connections the wake was
  shared; fine for a bell, wrong once frames carry data.
- **Typed events reach the browser** as `fd-event`, whose data is the full
  envelope (`kind`, `at`, `source`, `payload`).
- **`summary-updated` is byte-identical to before** — `data: {"ts": true}`.
  `frontend/src/api.js` `subscribe()` listens for exactly that name, and a
  browser tab left open across a deploy must keep working.
"""
import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from flightdeck.events import BUS

router = APIRouter(tags=["stream"])

# Seconds between comment frames when nothing is happening. Reverse proxies and
# the browser's own connection-state indicator both go stale without traffic;
# a comment frame is not an event, so it never triggers a client reload.
KEEPALIVE_SECONDS = 15


def _frame(ev) -> str:
    """One event as SSE wire text.

    `summary.updated` keeps its legacy name and body. Everything else rides a
    single generic `fd-event` frame rather than one SSE event name per kind:
    the client filters on `kind` from the payload, so adding a kind on the
    server needs no `addEventListener` on the client.
    """
    if ev.kind == "summary.updated":
        return f"event: summary-updated\ndata: {json.dumps({'ts': True})}\n\n"
    return f"event: fd-event\ndata: {json.dumps(ev.as_dict())}\n\n"


@router.get("/api/stream")
async def stream():
    async def gen():
        q = BUS.subscribe()
        try:
            # Flush immediately so EventSource.onopen reflects the real
            # connection state even when no event has fired yet.
            yield ": connected\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _frame(ev)
        finally:
            BUS.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream")
