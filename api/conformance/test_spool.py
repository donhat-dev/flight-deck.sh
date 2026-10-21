"""Validate the runner spool samples against api/schemas/spool/.

The files in spool-samples/ are written by go/runner's
TestSpoolContractSamples through the runner's own write paths (Emit,
SetState, DeltaWriter, the Command struct); `make contracts` runs that test
with FD_SPOOL_SAMPLES pointed here first. A missing file is a failure, never
a skip — the suite must say so rather than pass silently without samples.
"""
import json

import jsonschema
import pytest

from conftest import SPOOL_SAMPLES, spool_schema, validate

EXPECTED = ["command.json", "state.json", "events.jsonl", "deltas.jsonl"]


def test_spool_samples_exist():
    missing = [n for n in EXPECTED if not (SPOOL_SAMPLES / n).is_file()]
    assert not missing, (
        f"spool samples missing in {SPOOL_SAMPLES}: {missing} — "
        "run `make contracts` (it runs go/runner's TestSpoolContractSamples "
        "with FD_SPOOL_SAMPLES first)")


def test_command_sample(registry):
    validate(registry, spool_schema("command"),
             json.loads((SPOOL_SAMPLES / "command.json").read_text()))


def test_state_sample(registry):
    validate(registry, spool_schema("state"),
             json.loads((SPOOL_SAMPLES / "state.json").read_text()))


def test_events_sample(registry):
    lines = (SPOOL_SAMPLES / "events.jsonl").read_text().splitlines()
    assert lines, "events.jsonl is empty"
    schema = spool_schema("event")
    for i, line in enumerate(lines):
        validate(registry, schema, json.loads(line))


def test_deltas_sample(registry):
    lines = (SPOOL_SAMPLES / "deltas.jsonl").read_text().splitlines()
    assert lines, "deltas.jsonl is empty"
    schema = spool_schema("delta")
    for i, line in enumerate(lines):
        validate(registry, schema, json.loads(line))


# --- negative cases: malformed events must fail per-kind validation ----------

def _invalid(registry, schema, doc):
    with pytest.raises(jsonschema.ValidationError):
        validate(registry, schema, doc)


@pytest.mark.parametrize("doc", [
    # permission event without session_id or payload
    {"ts": "2026-10-21T00:00:00Z", "kind": "permission"},
    # result with a string cost
    {"ts": "t", "kind": "result", "session_id": "s",
     "payload": {"result": "ok", "total_cost_usd": "0.01", "is_error": False,
                 "num_turns": 1, "duration_ms": 10, "subtype": "success",
                 "first_content_frame_ms": 5}},
    # decision with numeric behavior
    {"ts": "t", "kind": "decision", "session_id": "s",
     "payload": {"request_id": "r", "behavior": 1, "scope": "once",
                 "remembered": False, "waited_ms": 3}},
    # state event missing the pid payload
    {"ts": "t", "kind": "state", "session_id": "s", "state": "busy"},
    # usage event carrying a session_id it never sets
    {"ts": "t", "kind": "usage", "session_id": "s",
     "payload": {"text": "5h: 12%", "captured_at": "t"}},
    # unknown kind
    {"ts": "t", "kind": "explode", "session_id": "s"},
])
def test_malformed_events_fail(registry, doc):
    _invalid(registry, spool_schema("event"), doc)


@pytest.mark.parametrize("schema_name,doc", [
    # command missing args
    ("command", {"cmd_id": "c", "seq": 1, "op": "start", "session_id": "s"}),
    # command with an unknown op
    ("command", {"cmd_id": "c", "seq": 1, "op": "explode", "session_id": "s",
                 "args": {}}),
    # command with an arg the runner ignores
    ("command", {"cmd_id": "c", "seq": 1, "op": "send", "session_id": "s",
                 "args": {"surprise": True}}),
    # state with a state it never writes
    ("state", {"session_id": "s", "pid": 1, "state": "stopped", "cwd": "/p",
               "since": "t", "last": "t"}),
    # delta missing the snapshot text
    ("delta", {"ts": "t", "session_id": "s", "stream": "f", "seq": 1}),
])
def test_malformed_spool_files_fail(registry, schema_name, doc):
    _invalid(registry, spool_schema(schema_name), doc)
