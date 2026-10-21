"""`fd serve` on a temp ledger answers every route spend.yaml marks for it,
plus the JSON 404 the SPA fallback needs.

The fixture seeds a ledger by `fd ingest` of a small transcript fixture
(copied from go/core/ingest test shapes) under the test's temp dir, starts
`fd serve --addr 127.0.0.1:<free port>` with a scrubbed environment, and
polls /api/health. Teardown stops the process (SIGINT, then SIGKILL as a
backstop) and validates its final stdout — `{"stopped": "<addr>"}` — against
schemas/cli/serve.json (the `fd serve` --json output contract; api/cli.md).
Loopback test servers are allowed; live services are untouched.
"""
import json
import signal
import subprocess
import urllib.error
import urllib.request

import pytest
import yaml

from conftest import (FD, SPEND_YAML, clean_env, cli_schema, free_port,
                      operation_schema, shared_paths, validate, wait_health)

_SPEND = yaml.safe_load(SPEND_YAML.read_text())


def _get(url, path):
    with urllib.request.urlopen(url + path, timeout=5) as r:
        return r.status, json.loads(r.read())


@pytest.fixture(scope="module")
def go_server(go_ledger, registry):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [str(FD), "serve", "--db", str(go_ledger["db"]),
         "--projects", str(go_ledger["projects"]), "--addr", f"127.0.0.1:{port}"],
        env=clean_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    try:
        wait_health(url, proc)
        yield url
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        # `fd serve` is a daemon whose only stdout document is the stop line
        # (api/cli.md: {"stopped": "<addr>"}); validated here, post-shutdown.
        out, err = proc.stdout.read(), proc.stderr.read()
        assert proc.returncode == 0, f"fd serve exit {proc.returncode}: {err[-500:]}"
        doc = json.loads(out)
        validate(registry, cli_schema("serve"), doc)


@pytest.mark.parametrize("path", shared_paths(_SPEND, "go"))
def test_route_matches_spend_yaml(go_server, registry, spend_spec, path):
    schema, _ = operation_schema(spend_spec, path)
    status, body = _get(go_server, path)
    assert status == 200, f"{path}: {status} {body}"
    validate(registry, schema, body)


def test_json_404_for_unserved_api_path(go_server, registry, spend_spec):
    try:
        urllib.request.urlopen(go_server + "/api/not-a-route", timeout=5)
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404
        body = json.loads(e.read())
    validate(registry, spend_spec["components"]["schemas"]["Error"], body)
