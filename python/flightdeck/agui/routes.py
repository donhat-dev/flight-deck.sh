"""AG-UI HTTP surface for FlightDeck's Relay view.

- POST /api/agui/run      -> text/event-stream of AG-UI events (replay | demo)
- POST /api/agui/resume   -> continue an interrupted demo run after a decision
- GET  /api/agui/sessions -> a few recent sessions to pick for replay

The run/resume responses are Server-Sent Events, the AG-UI-recommended default
transport. Events are paced with a small sleep so text streams like tokens; the
`speed` prop scales it (0 = as fast as possible).
"""
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from flightdeck import db, metrics, transcript
from flightdeck.agui import adapter
from flightdeck.agui import events as E

router = APIRouter(prefix="/api/agui", tags=["agui"])

# Interrupted demo runs awaiting a decision: runId -> {threadId, ts}. Bounded
# so a long-lived server can't accumulate them.
_PENDING: dict = {}
_PENDING_MAX = 64

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",   # let nginx pass the stream through unbuffered
}


class RunInput(BaseModel):
    threadId: str | None = None
    runId: str | None = None
    # AG-UI carries app-specific inputs under forwardedProps.
    forwardedProps: dict = {}


class ResumeInput(BaseModel):
    threadId: str
    runId: str
    decision: str                    # approve | reject | edit
    command: str | None = None       # edited command when decision == "edit"


def _paced(gen, speed: float):
    """Frame each event as SSE and sleep a little between them so the stream
    feels live. speed scales the delay; <=0 disables it."""
    base = 0.02
    delay = 0 if speed <= 0 else min(0.2, base / speed)
    for evt in gen:
        yield E.sse(evt)
        if delay:
            time.sleep(delay)


def _remember(run_id, thread_id):
    if len(_PENDING) >= _PENDING_MAX:
        # drop the oldest
        oldest = min(_PENDING, key=lambda k: _PENDING[k]["ts"])
        _PENDING.pop(oldest, None)
    _PENDING[run_id] = {"threadId": thread_id, "ts": time.time()}


@router.post("/run")
def agui_run(inp: RunInput, request: Request):
    thread_id = inp.threadId or f"thr_{uuid.uuid4().hex[:8]}"
    run_id = inp.runId or f"run_{uuid.uuid4().hex[:8]}"
    props = inp.forwardedProps or {}
    mode = props.get("mode", "replay")
    speed = float(props.get("speed", 1.0) or 0)

    if mode == "demo":
        def demo_gen():
            yield from adapter.stream_demo_intro(thread_id, run_id)
            # Pause: mark the run interrupted so the UI shows the approval card.
            _remember(run_id, thread_id)
            yield E.run_finished(thread_id, run_id,
                                 result={"reason": "interrupted",
                                         "resume": f"/api/agui/resume"})
        return StreamingResponse(_paced(demo_gen(), speed),
                                 media_type="text/event-stream",
                                 headers=_SSE_HEADERS)

    # replay mode
    session_id = props.get("sessionId")

    def replay_gen():
        if not session_id:
            yield E.run_error("no sessionId supplied for replay", code="no_session")
            return
        path = _session_path(request, session_id)
        if not path:
            yield E.run_error(f"session {session_id} not found", code="not_found")
            return
        tx = transcript.build_transcript(path, offset=0, limit=40)
        yield from adapter.stream_replay(tx, thread_id, run_id)

    return StreamingResponse(_paced(replay_gen(), speed),
                             media_type="text/event-stream",
                             headers=_SSE_HEADERS)


@router.post("/resume")
def agui_resume(inp: ResumeInput, request: Request):
    speed = 1.0
    thread_id, run_id = inp.threadId, inp.runId
    known = _PENDING.pop(run_id, None)

    def gen():
        if known is None:
            yield E.run_error("unknown or already-resumed run", code="no_pending")
            return
        decision = inp.decision if inp.decision in ("approve", "reject", "edit") \
            else "approve"
        yield from adapter.stream_demo_resume(
            thread_id, run_id, decision, edited_command=inp.command)

    return StreamingResponse(_paced(gen(), speed),
                             media_type="text/event-stream",
                             headers=_SSE_HEADERS)


@router.get("/sessions")
def agui_sessions(request: Request, limit: int = 12):
    """A few recent sessions to offer as replay sources."""
    cfg = request.app.state.cfg
    conn = db.open_read(cfg["db_path"])
    try:
        rows = metrics.sessions(conn, limit=limit, offset=0, since=None,
                                projects_dir=cfg["projects_dir"])
    except Exception as e:  # never 500 the picker
        return {"sessions": [], "warning": str(e)}
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r) if not isinstance(r, dict) else r
        out.append({
            "id": d.get("session_id") or d.get("id"),
            "title": d.get("title") or d.get("session_id"),
            "turns": d.get("turns"),
            "last_ts": d.get("last_ts"),
        })
    return {"sessions": out}


def _session_path(request: Request, session_id: str):
    cfg = request.app.state.cfg
    return transcript.find_session_file(cfg["projects_dir"], session_id)
