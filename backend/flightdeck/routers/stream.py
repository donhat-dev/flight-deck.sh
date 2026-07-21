"""SSE stream: notifies the frontend when the cached snapshot is refreshed.

The generator awaits the module-level `_updated` event (set by the ingest /
poll loops). Module-level event = 1-worker fan-out, by design."""
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from flightdeck.runtime import _updated

router = APIRouter(tags=["stream"])


@router.get("/api/stream")
async def stream():
    async def gen():
        while True:
            await _updated.wait()
            _updated.clear()
            yield f"event: summary-updated\ndata: {json.dumps({'ts': True})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
