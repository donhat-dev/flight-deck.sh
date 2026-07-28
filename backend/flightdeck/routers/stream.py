"""SSE stream: notifies the frontend when the cached snapshot is refreshed.

The generator awaits the module-level `_updated` event (set by the ingest /
poll loops). Module-level event = 1-worker fan-out, by design."""
import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from flightdeck.runtime import _updated

router = APIRouter(tags=["stream"])


@router.get("/api/stream")
async def stream():
    async def gen():
        # Flush the response immediately so EventSource.onopen reflects the
        # real connection state even when no ingest event has fired yet.
        yield ": connected\n\n"
        while True:
            try:
                await asyncio.wait_for(_updated.wait(), timeout=15)
            except TimeoutError:
                # Keep reverse proxies and browser connection-state indicators
                # alive during quiet periods without triggering a data reload.
                yield ": keep-alive\n\n"
                continue
            _updated.clear()
            yield f"event: summary-updated\ndata: {json.dumps({'ts': True})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
