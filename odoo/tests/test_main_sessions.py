"""Which roster entries count as main sessions, read from real roster files."""
import json
import os
import tempfile

import pytest

try:
    from conftest import load
    orchestrator = load("orchestrator")
except ImportError:  # the module imports Odoo; only the roster reader is needed here
    orchestrator = None


def write_roster(entries):
    root = tempfile.mkdtemp(prefix="fd-roster-")
    for n, entry in enumerate(entries):
        with open(os.path.join(root, "%d.json" % n), "w", encoding="utf-8") as fh:
            json.dump(entry, fh)
    return root


@pytest.mark.skipif(orchestrator is None, reason="orchestrator.py needs Odoo")
def test_read_roster_keeps_the_entrypoint():
    root = write_roster([{"sessionId": "a", "kind": "interactive", "entrypoint": "sdk-cli"}])
    assert orchestrator.read_roster(root)[0]["entrypoint"] == "sdk-cli"
