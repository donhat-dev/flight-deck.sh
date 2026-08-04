"""The watcher must not be driven by its own reads, and must not cost a thread
per event.

Both defects were measured on the live process, not theorised:

  * `on_any_event` filtered by FILENAME only, so `FileOpenedEvent` +
    `FileClosedNoWriteEvent` — what a READ emits — drove re-ingest. Since
    `build_snapshot` reads the very tree it watches (metrics.sessions ->
    transcript.subagent_usage -> open()), the watcher fed itself: 167-423
    events/s with zero HTTP requests and zero modified events. Stopping the
    service dropped the tree to 0.15 events/s, a ~2800x difference.

  * each event built a `threading.Timer` and cancelled the previous one.
    `Timer.__init__` constructs a Thread and `.start()` spawns a real OS
    thread, so cancelling never undid the spawn. The logged thread numbers
    went 1,427,661 -> 8,283,284 in 25.4 hours (~78/s average).

The real watchdog event classes are used below rather than hand-made stubs with
an `event_type` string: the point is that the types watchdog actually emits for
a read are rejected, which a stub cannot prove.
"""
import threading
import time

import pytest
from watchdog.events import (
    DirModifiedEvent, FileClosedEvent, FileClosedNoWriteEvent, FileCreatedEvent,
    FileDeletedEvent, FileModifiedEvent, FileMovedEvent, FileOpenedEvent,
)

from flightdeck.runtime import Debouncer, is_content_event


# ------------------------------------------------------------ event filtering

@pytest.mark.parametrize("event", [
    FileOpenedEvent("/t/a.jsonl"),
    FileClosedNoWriteEvent("/t/a.jsonl"),
    FileClosedEvent("/t/a.jsonl"),
])
def test_a_read_never_counts_as_a_change(event):
    """These three are the whole bug: 99.99% of the events on the tree.

    `FileClosedEvent` is close-after-write, so it does follow a real write — it
    is still rejected because `FileModifiedEvent` already covers that write, and
    accepting both would fire the debounce twice per append.
    """
    assert is_content_event(event) is False


@pytest.mark.parametrize("event", [
    FileModifiedEvent("/t/a.jsonl"),
    FileCreatedEvent("/t/a.jsonl"),
    FileMovedEvent("/t/a.jsonl", "/t/b.jsonl"),
    FileDeletedEvent("/t/a.jsonl"),
    DirModifiedEvent("/t"),
])
def test_a_real_change_still_counts(event):
    assert is_content_event(event) is True


def test_the_filter_is_an_allowlist_not_a_denylist():
    """A future watchdog event type must default to "not a change".

    Denying the read-ish types by name would mean the next type watchdog adds
    starts driving re-ingest silently — which is exactly how `opened` /
    `closed_no_write` got in.
    """
    class FutureEvent:
        event_type = "peeked"
        src_path = "/t/a.jsonl"

    assert is_content_event(FutureEvent()) is False
    # And something with no event_type at all must not slip through.
    assert is_content_event(object()) is False


# ------------------------------------------------------------------- debounce

def live_workers(name):
    return [t for t in threading.enumerate() if t.name.startswith(name)]


def test_one_worker_thread_however_many_events_arrive():
    """The regression that mattered: threads must not scale with events."""
    calls = []
    d = Debouncer(0.05, lambda: calls.append(1), name="test-flood")
    try:
        assert len(live_workers("test-flood")) == 1

        # Count Thread CONSTRUCTIONS, not live threads: the old code's threads
        # were cancelled and died quickly, so a live-count check would have
        # passed while 78 threads/second were still being spawned.
        created = []
        real_init = threading.Thread.__init__

        def counting_init(self, *a, **kw):
            created.append(1)
            return real_init(self, *a, **kw)

        threading.Thread.__init__ = counting_init
        try:
            for _ in range(500):
                d.poke()
        finally:
            threading.Thread.__init__ = real_init

        assert created == [], f"{len(created)} threads spawned for 500 events"
        assert len(live_workers("test-flood")) == 1
    finally:
        d.stop()


def test_a_burst_collapses_to_one_call():
    calls = []
    d = Debouncer(0.08, lambda: calls.append(time.monotonic()), name="test-burst")
    try:
        for _ in range(50):
            d.poke()
            time.sleep(0.001)
        time.sleep(0.4)
        assert len(calls) == 1, calls
    finally:
        d.stop()


def test_the_quiet_period_is_measured_from_the_LAST_event():
    """Trailing edge: a steady drip must postpone the call, not fire mid-burst."""
    calls = []
    d = Debouncer(0.15, lambda: calls.append(1), name="test-trailing")
    try:
        for _ in range(8):          # 8 x 40ms = 320ms of drip, delay is 150ms
            d.poke()
            time.sleep(0.04)
        assert calls == [], "fired while events were still arriving"
        time.sleep(0.4)
        assert len(calls) == 1
    finally:
        d.stop()


def test_it_keeps_working_after_a_quiet_stretch():
    # A one-shot worker would fire once and then sit dead, freezing the snapshot
    # with no error anywhere — the same silent-failure shape as the write
    # connection that never reconnected.
    calls = []
    d = Debouncer(0.05, lambda: calls.append(1), name="test-again")
    try:
        d.poke(); time.sleep(0.3)
        d.poke(); time.sleep(0.3)
        d.poke(); time.sleep(0.3)
        assert len(calls) == 3, calls
    finally:
        d.stop()


def test_a_raising_callback_does_not_kill_the_worker():
    # This is how the outage would have compounded: ingest throwing must not
    # also take out the debounce thread, or recovery needs a restart.
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")

    d = Debouncer(0.05, fn, name="test-raise")
    try:
        d.poke(); time.sleep(0.25)
        d.poke(); time.sleep(0.25)
        assert state["n"] == 2
        assert len(live_workers("test-raise")) == 1
    finally:
        d.stop()


def test_stop_ends_the_worker():
    d = Debouncer(0.05, lambda: None, name="test-stop")
    d.stop()
    for _ in range(50):
        if not live_workers("test-stop"):
            break
        time.sleep(0.02)
    assert live_workers("test-stop") == []


def test_stop_is_idempotent_and_a_late_poke_is_harmless():
    calls = []
    d = Debouncer(0.05, lambda: calls.append(1), name="test-late")
    d.stop()
    d.stop()
    d.poke()
    time.sleep(0.2)
    assert calls == []
