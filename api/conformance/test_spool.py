"""Validate the runner spool samples against api/schemas/spool/.

The files in spool-samples/ are written by go/runner's
TestSpoolContractSamples through the runner's own write paths (Emit,
SetState, DeltaWriter, the Command struct); `make contracts` runs that test
with FD_SPOOL_SAMPLES pointed here first. A missing file is a failure, never
a skip — the suite must say so rather than pass silently without samples.
"""
import json

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
