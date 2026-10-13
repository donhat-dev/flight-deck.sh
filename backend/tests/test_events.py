"""The event bus: fan-out, isolation between subscribers, and the two failure
modes that would take the stream down with them.

The old `_updated` flag had no tests because there was nothing to get wrong — a
bell either rings or it does not. A bus carrying payloads has three properties
worth pinning: every subscriber sees every event (the old shared flag did not),
a full queue drops history rather than blocking the producer (producers are
watcher threads), and a raising sink does not stop delivery to the others (Hub
triggers will run user config).
"""
import asyncio
import threading

import pytest

from flightdeck.events import QUEUE_MAXSIZE, Bus, Event


@pytest.fixture
def bus():
    b = Bus()
    yield b
    b.reset()


def test_event_wire_form_keeps_payload_nested():
    ev = Event(kind="decision.pending", source="hook", payload={"id": "abc"})
    d = ev.as_dict()
    assert d["kind"] == "decision.pending"
    assert d["source"] == "hook"
    assert d["payload"] == {"id": "abc"}
    # `at` is stamped for us and belongs to the envelope, not the payload.
    assert isinstance(d["at"], float)
    assert "at" not in d["payload"]


def test_sink_receives_matching_kinds_only(bus):
    seen = []
    bus.on("decision", seen.append)
    bus.emit("decision.pending", source="hook", id=1)
    bus.emit("summary.updated", source="watcher")
    bus.emit("decision.resolved", source="api", id=1)
    assert [e.kind for e in seen] == ["decision.pending", "decision.resolved"]


def test_empty_prefix_takes_everything(bus):
    seen = []
    bus.on("", seen.append)
    bus.emit("a.b")
    bus.emit("c.d")
    assert len(seen) == 2


def test_off_removes_the_sink(bus):
    seen = []
    off = bus.on("", seen.append)
    bus.emit("a.b")
    off()
    bus.emit("a.b")
    assert len(seen) == 1
    off()  # idempotent


def test_raising_sink_does_not_block_the_others(bus):
    seen = []

    def boom(_ev):
        raise RuntimeError("bad trigger config")

    bus.on("", boom)
    bus.on("", seen.append)
    bus.emit("a.b")
    assert len(seen) == 1, "a broken sink must not stop delivery"


def test_emit_without_a_loop_still_reaches_sinks(bus):
    """The CLI and the test suite run with no server loop bound. A sink must
    still fire there, because that is the only path a non-HTTP consumer has."""
    seen = []
    bus.on("", seen.append)
    ev = bus.emit("a.b", source="cli")
    assert seen == [ev]


def test_every_subscriber_gets_every_event(bus):
    """The property the old `_updated.clear()` fan-out did not have."""
    async def scenario():
        bus.bind(asyncio.get_running_loop())
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.emit("a.b", source="t", n=1)
        # `emit` hops through call_soon_threadsafe even on-loop, so yield once.
        await asyncio.sleep(0)
        return q1.get_nowait(), q2.get_nowait()

    e1, e2 = asyncio.run(scenario())
    assert e1.payload == e2.payload == {"n": 1}
    assert e1 is e2, "one Event object is fanned out, not re-created per queue"


def test_unsubscribe_stops_delivery_and_is_idempotent(bus):
    async def scenario():
        bus.bind(asyncio.get_running_loop())
        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.unsubscribe(q)          # a generator's finally may run twice
        bus.emit("a.b")
        await asyncio.sleep(0)
        return q.qsize(), bus.subscriber_count

    size, count = asyncio.run(scenario())
    assert size == 0
    assert count == 0


def test_full_queue_drops_oldest_and_counts_it(bus):
    """A parked tab loses history, never news, and the producer never blocks."""
    async def scenario():
        bus.bind(asyncio.get_running_loop())
        q = bus.subscribe()
        for i in range(QUEUE_MAXSIZE + 3):
            bus.emit("a.b", n=i)
        await asyncio.sleep(0)
        drained = []
        while not q.empty():
            drained.append(q.get_nowait().payload["n"])
        return drained, bus.dropped

    drained, dropped = asyncio.run(scenario())
    assert len(drained) == QUEUE_MAXSIZE
    assert dropped == 3
    # Newest survived; the three oldest are what went.
    assert drained[-1] == QUEUE_MAXSIZE + 2
    assert drained[0] == 3


def test_emit_from_another_thread_reaches_the_queue(bus):
    """Watcher, debouncers and poll loops all emit off-loop. That path must not
    touch `asyncio.Queue` directly, which is why `emit` hops onto the loop."""
    async def scenario():
        bus.bind(asyncio.get_running_loop())
        q = bus.subscribe()
        done = threading.Event()

        def producer():
            bus.emit("a.b", source="watcher", n=7)
            done.set()

        threading.Thread(target=producer).start()
        await asyncio.to_thread(done.wait, 2)
        return await asyncio.wait_for(q.get(), timeout=2)

    ev = asyncio.run(scenario())
    assert ev.source == "watcher"
    assert ev.payload == {"n": 7}
