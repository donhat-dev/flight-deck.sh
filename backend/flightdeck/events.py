"""Typed in-process event bus — the substrate three surfaces share.

Before this module there was one `asyncio.Event` (`runtime._updated`), set from
five places and awaited by `/api/stream`. It carried no payload, so the only
thing a client could learn was "something changed": the SSE endpoint translated
every one of those five causes into the same `summary-updated` frame. That is
enough for a dashboard that refetches, and not enough for anything that must
know WHICH thing happened — an approval waiting on a human, a cue to play, a
flow to trigger.

So this is that same fan-out with three changes:

  1. **Events carry a kind and a payload.** `Event(kind, at, source, payload)`.
  2. **Every subscriber gets every event.** The old shared flag was raced by
     design (`_updated.clear()` in the generator), which is correct for a bell
     and wrong for a message: two open tabs would both wake, but a payload
     delivered that way belongs to whichever generator ran first. Each SSE
     connection now owns a bounded queue.
  3. **In-process sinks exist alongside SSE.** `on(prefix, cb)` lets backend
     code react without a browser in the loop — the Hub trigger matcher is the
     first consumer, and a notifier is the second.

Single-process, 1-worker by design, exactly like the `_updated` it replaces
(see `runtime.py` module docstring and docs/host-stack-migration.md). The bus is
module-level state for the same reason.

**Kinds are plain dotted strings, `<domain>.<verb>`, deliberately not a registry
enum.** A central enum would make every feature that emits a new kind edit one
shared file, which is precisely the coupling this module exists to avoid — three
tracks build on the bus at once. The convention is enforced by review, not by
import. Known kinds today:

    summary.updated     the snapshot cache was refreshed (the old bell)

Emitting is thread-safe: the watcher, the debouncers and the poll loops all run
off-loop and call `emit()` directly.
"""
import asyncio
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

# Per-connection queue depth. A parked browser tab must not grow memory without
# bound, so the queue drops its OLDEST event when full rather than blocking the
# emitter (an emitter is often a watcher thread; blocking it would stall ingest).
# 256 is far above any real burst: the busiest observed source is the transcript
# watcher, and its Debouncer already coalesces a write burst into ~one fire.
QUEUE_MAXSIZE = 256


@dataclass(frozen=True)
class Event:
    """One thing that happened, addressed by `kind`.

    `source` names the producer ("watcher", "poll", "api", "hook", ...) and is
    for humans reading a log or a trace, never for routing — routing is by
    `kind` alone, so a second producer of an existing kind needs no consumer
    change.
    """
    kind: str
    source: str = "unknown"
    payload: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        """Wire form for SSE / logs. Flat on purpose: `payload` stays nested so
        a consumer can tell an envelope field from event data."""
        return {"kind": self.kind, "at": self.at,
                "source": self.source, "payload": self.payload}


class Bus:
    """Fan-out to N async queues plus N synchronous callbacks.

    Two delivery paths because the two consumers differ in kind, not just in
    transport: an SSE connection is async and may be slow (hence a bounded
    queue that drops), while an in-process sink is a plain function that must
    run for every event (hence a direct call, and hence the rule that a sink
    does its own I/O elsewhere — see `on`).
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []
        self._listeners: list[tuple[str, Callable[[Event], None]]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        # Counted, not logged per occurrence: a dropping tab would otherwise
        # print once per event and bury the log it is trying to explain.
        self.dropped = 0

    # ---- lifecycle -------------------------------------------------------

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the loop that owns the subscriber queues.

        Called once from `lifespan`. Until this happens, `emit()` still reaches
        in-process listeners synchronously — which is what makes the bus usable
        in tests and in the CLI, neither of which runs a server loop.
        """
        self._loop = loop

    def reset(self) -> None:
        """Drop every subscriber and listener. For tests only."""
        self._queues.clear()
        self._listeners.clear()
        self._loop = None
        self.dropped = 0

    # ---- producing -------------------------------------------------------

    def emit(self, kind: str, source: str = "unknown", **payload: Any) -> Event:
        """Publish an event. Safe to call from any thread.

        Dispatch always hops onto the bound loop, even when the caller is
        already on it: `asyncio.Queue.put_nowait` is not thread-safe, and doing
        it uniformly also keeps a listener from re-entering `emit` inside its
        own dispatch. The returned Event is the one that was published, so a
        caller can log or store the exact `at` that consumers saw.
        """
        ev = Event(kind=kind, source=source, payload=dict(payload))
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._dispatch, ev)
        else:
            # No loop: SSE cannot exist yet, but sinks and tests can.
            self._dispatch(ev)
        return ev

    def _dispatch(self, ev: Event) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # Drop the oldest so a slow consumer loses history, not news.
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except Exception:
                    pass
                self.dropped += 1
        for prefix, cb in list(self._listeners):
            if not ev.kind.startswith(prefix):
                continue
            try:
                cb(ev)
            except Exception:
                # A broken sink must never stop delivery to the others. This is
                # the whole reason sinks are wrapped: a Hub trigger runs user
                # config, and bad config is expected, not exceptional.
                traceback.print_exc()

    # ---- consuming -------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        """A fresh bounded queue receiving every subsequent event."""
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Detach a queue. Idempotent: an SSE generator's `finally` may run
        after the connection already errored out."""
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    def on(self, prefix: str, cb: Callable[[Event], None]) -> Callable[[], None]:
        """Register a synchronous sink for kinds starting with `prefix`.

        `prefix` is a plain string prefix, so `""` takes everything, `"decision"`
        takes `decision.pending` and `decision.resolved`, and an exact kind
        works too. The callback runs ON THE EVENT LOOP and must not block —
        hand slow work to a thread or a queue.

        Returns a function that removes the registration.
        """
        entry = (prefix, cb)
        self._listeners.append(entry)

        def off() -> None:
            try:
                self._listeners.remove(entry)
            except ValueError:
                pass

        return off

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)


# The single bus. Module-level for the same 1-worker reason `_updated` was.
BUS = Bus()
