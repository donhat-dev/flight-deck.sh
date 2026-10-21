"""Conformance fixtures for the machine-read contracts in api/.

Self-contained: runs from the worktree root with the venv interpreter —
    .venv/bin/python -m pytest api/conformance -q -p no:cacheprovider
and through `make contracts`. No imports from python/tests; the suite adds
python/ to sys.path itself and never reads a live FlightDeck path: every fd
invocation gets a temp --db/--projects and a scrubbed environment, and the
FastAPI app runs against a temp config with all background loops off.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import yaml
import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

FD = ROOT / "go" / "bin" / "fd"
SPEND_YAML = ROOT / "api" / "openapi" / "spend.yaml"
PYTHON_JSON = ROOT / "api" / "openapi" / "python.json"
GEN_OPENAPI = ROOT / "tools" / "contracts" / "gen_python_openapi.py"
CLI_SCHEMAS = ROOT / "api" / "schemas" / "cli"
SPOOL_SCHEMAS = ROOT / "api" / "schemas" / "spool"
SPOOL_SAMPLES = ROOT / "api" / "conformance" / "spool-samples"

BASE_URI = "https://contracts.flightdeck/schemas/"
_METHODS = {"get", "put", "post", "delete", "patch"}


# --- schemas -----------------------------------------------------------------

@pytest.fixture(scope="session")
def spend_spec():
    return yaml.safe_load(SPEND_YAML.read_text())


@pytest.fixture(scope="session")
def python_spec():
    return json.loads(PYTHON_JSON.read_text())


@pytest.fixture(scope="session")
def registry(spend_spec):
    reg = Registry().with_resource(
        BASE_URI + "openapi/spend.yaml",
        Resource.from_contents(spend_spec, default_specification=DRAFT202012))
    for p in sorted(CLI_SCHEMAS.glob("*.json")) + sorted(SPOOL_SCHEMAS.glob("*.json")):
        reg = reg.with_resource(
            BASE_URI + f"{p.parent.name}/{p.name}",
            Resource.from_contents(json.loads(p.read_text()),
                                   default_specification=DRAFT202012))
    return reg


def validate(registry, schema, instance):
    jsonschema.Draft202012Validator(schema, registry=registry).validate(instance)
    return instance


def _absolute_refs(node):
    """Rewrite local `#/...` refs to absolute spend.yaml URIs, so an extracted
    subschema still resolves them against the full document in the registry."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/"):
                out[k] = BASE_URI + "openapi/spend.yaml" + v
            else:
                out[k] = _absolute_refs(v)
        return out
    if isinstance(node, list):
        return [_absolute_refs(v) for v in node]
    return node


def operation_schema(spend_spec, path):
    """The 200-response schema of a GET operation in spend.yaml, with its
    `#/components/...` refs made absolute into the spend.yaml resource."""
    item = spend_spec["paths"][path]
    op = item["get"]
    schema = _absolute_refs(
        op["responses"]["200"]["content"]["application/json"]["schema"])
    return schema, op


def cli_schema(name):
    return json.loads((CLI_SCHEMAS / f"{name}.json").read_text())


def spool_schema(name):
    return json.loads((SPOOL_SCHEMAS / f"{name}.json").read_text())


def shared_paths(spend_spec, server):
    """Paths in spend.yaml whose operation is marked for `server`."""
    out = []
    for path, item in spend_spec["paths"].items():
        for method, op in item.items():
            if method in _METHODS and server in (op.get("x-servers") or []):
                out.append(path)
    return sorted(out)


# --- fd fixture -------------------------------------------------------------

def clean_env():
    """Environment for fd child processes: no inherited FD_* or Claude paths."""
    drop = ("FD_", "CLAUDE_CONFIG_DIR", "FLIGHTDECK_WEB_DIST")
    env = {k: v for k, v in os.environ.items()
           if not any(k == d or k.startswith(d) for d in drop)}
    return env


def _asst(uuid, session, msg_id, out_tokens, model="claude-opus-5-5",
          ts="2026-09-25T07:00:00.000Z"):
    # Same line shape as go/core/ingest/ingest_test.go's asst().
    return json.dumps({
        "type": "assistant", "uuid": uuid, "sessionId": session, "cwd": "/p",
        "timestamp": ts, "requestId": f"req-{msg_id}-{uuid}",
        "message": {"id": msg_id, "model": model, "usage": {
            "input_tokens": 2, "cache_read_input_tokens": 1000,
            "output_tokens": out_tokens,
            "cache_creation": {"ephemeral_5m_input_tokens": 0,
                               "ephemeral_1h_input_tokens": 0}}}})


def write_transcript_fixture(projects: Path):
    """Two sessions under <projects>/-p/, copied from the ingest tests' shapes."""
    d = projects / "-p"
    d.mkdir(parents=True, exist_ok=True)
    (d / "s1.jsonl").write_text("\n".join([
        _asst("u1", "s1", "M1", 10), _asst("u2", "s1", "M1", 10),
        _asst("u3", "s1", "M1", 300),          # same (id, requestId): one response
        _asst("u4", "s1", "M2", 40),
        json.dumps({"type": "custom-title", "sessionId": "s1",
                    "customTitle": "Fixture session"}),
    ]) + "\n")
    (d / "s2.jsonl").write_text(
        _asst("u5", "s2", "M3", 25, model="claude-sonnet-4-5") + "\n")


def run_fd(*args, env=None, check=True, **kw):
    proc = subprocess.run(
        [str(FD), *args], env=env or clean_env(),
        capture_output=True, text=True, timeout=60, **kw)
    if check and proc.returncode != 0:
        raise AssertionError(
            f"fd {' '.join(args)}: exit {proc.returncode}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
    return proc


@pytest.fixture(scope="session")
def go_ledger(tmp_path_factory):
    """A seeded temp ledger + projects dir for every fd invocation."""
    tmp = tmp_path_factory.mktemp("go-ledger")
    projects = tmp / "projects"
    projects.mkdir()
    write_transcript_fixture(projects)
    db = tmp / "ledger.db"
    run_fd("ingest", "--db", str(db), "--projects", str(projects), "--json")
    return {"db": db, "projects": projects}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_health(url, proc, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"fd serve exited {proc.returncode}:\n{proc.stderr.read()}")
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"fd serve did not answer {url}/api/health in {timeout}s")
