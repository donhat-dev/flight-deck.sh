"""The consolidated agent surface: one registry, a CLI base, an MCP wrapper over it.

The CLI tests run the REAL entrypoint in a subprocess, because the properties that
matter — exit codes, stdout discipline, stdin-JSON surviving Vietnamese markdown with
backticked XML — live at the process boundary and cannot be seen from an imported
function. The wrapper test speaks actual newline-delimited JSON-RPC to the actual
server process for the same reason.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from flightdeck.agentsurface import registry

BACKEND = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------- registry

def test_the_registry_merges_both_domains_without_collisions():
    tools = registry.merged()
    assert len(tools) == 30
    assert "radar_move" in tools and "treasure_wrap" in tools
    # Every name wears its domain prefix, which is what makes collisions structurally
    # unlikely rather than merely untested.
    assert all(n.startswith(("radar_", "treasure_")) for n in tools)


def test_every_merged_schema_matches_its_function_signature():
    """The cross-domain version of the radar suite's contract test: the consolidation
    would be the easiest place for a schema and a signature to drift apart."""
    import inspect
    for name, (fn, _d, _p, required) in registry.merged().items():
        for p in inspect.signature(fn).parameters.values():
            needs = p.default is inspect.Parameter.empty
            says = p.name in required
            assert needs == says, f"{name}.{p.name}: signature and schema disagree"


# ------------------------------------------------------------------------ CLI

def cli(args, stdin=None, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable, "-m", "flightdeck.cli", *args],
                          cwd=str(BACKEND), input=stdin, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.fixture()
def scratch(tmp_path):
    """Point every CLI subprocess at a throwaway SQLite file.

    TOKEN_AUDIT_DATABASE_URL must be EMPTIED, not merely left alone: the repo .env
    points it at the real Postgres ledger, and runtime._load_dotenv only setdefaults —
    an empty value set here wins and config falls back to SQLite.
    """
    return {"TOKEN_AUDIT_DB_PATH": str(tmp_path / "cli.db"),
            "TOKEN_AUDIT_DATABASE_URL": "",
            "TREASURES_STORE": str(tmp_path / "store")}


def test_list_names_all_tools(scratch):
    proc = cli(["--list"], env_extra=scratch)
    names = proc.stdout.split()
    assert proc.returncode == 0 and len(names) == 30


def test_schema_output_is_what_the_mcp_advertises(scratch):
    proc = cli(["--schema", "radar_move"], env_extra=scratch)
    schema = json.loads(proc.stdout)
    assert schema["name"] == "radar_move"
    assert schema["inputSchema"]["required"] == ["slug", "num", "ring", "period", "why"]


def test_stdin_json_survives_vietnamese_markdown_with_backticked_xml(scratch):
    """The case the stdin channel exists for: shell quoting has no chance here."""
    cli(["radar_create", "--slug", "r", "--title", "T"], env_extra=scratch)
    body = {"slug": "r", "name": "Odoo view fix", "quadrant": "techniques",
            "ring": "trial", "period": "Q3 2026",
            "why": "Đo **hai lần**: bản nâng cấp thêm lại `<field name=\"tz\"/>`.",
            "session_id": "test"}
    proc = cli(["radar_blip_add", "--json", "-"], stdin=json.dumps(body),
               env_extra=scratch)
    out = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert out["ring"] == "trial"
    assert '`<field name="tz"/>`' in out["why"]
    assert out["moves"][0]["session_id"] == "test"


def test_flag_values_coerce_and_bare_words_stay_strings(scratch):
    cli(["radar_create", "--slug", "r", "--title", "T"], env_extra=scratch)
    cli(["radar_blip_add", "--slug", "r", "--name", "A", "--quadrant", "tools",
         "--why", "w", "--period", "Q3", "--ring", "null"], env_extra=scratch)
    # --num 1 must arrive as an int (the tool int()s defensively, but the point of
    # coercion is that JSON-typed tools can trust their inputs), --slug as a string.
    proc = cli(["radar_blip", "--slug", "r", "--num", "1"], env_extra=scratch)
    assert json.loads(proc.stdout)["num"] == 1


def test_a_refusal_exits_2_with_the_error_as_data(scratch):
    cli(["radar_create", "--slug", "r", "--title", "T"], env_extra=scratch)
    proc = cli(["radar_blip_add", "--slug", "r", "--name", "X", "--quadrant", "tools",
                "--why", "see <b>html</b>", "--period", "Q3",
                "--description", "plain"], env_extra=scratch)
    # why passes (no HTML)… wait, it has HTML: refused by the markdown guard.
    out = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert "markdown, not HTML" in out["error"]


def test_an_unknown_tool_exits_3(scratch):
    proc = cli(["radar_nope"], env_extra=scratch)
    assert proc.returncode == 3
    assert "unknown tool" in json.loads(proc.stdout)["error"]


def test_stdout_carries_exactly_one_json_document(scratch):
    """The contract every wrapper depends on: parse stdout, whole, as one document."""
    proc = cli(["radar_get", "--slug", "missing"], env_extra=scratch)
    json.loads(proc.stdout)             # would raise on any stray print
    assert proc.returncode == 2


def test_flags_override_the_json_body(scratch):
    cli(["radar_create", "--slug", "r", "--title", "T"], env_extra=scratch)
    body = {"slug": "r", "name": "Template name", "quadrant": "tools",
            "why": "w", "period": "Q3", "ring": None}
    proc = cli(["radar_blip_add", "--json", "-", "--name", "Overridden"],
               stdin=json.dumps(body), env_extra=scratch)
    assert json.loads(proc.stdout)["name"] == "Overridden"


# -------------------------------------------------------------- MCP wrapper

def test_the_wrapper_serves_fresh_tools_and_results_over_real_stdio(scratch, tmp_path):
    """Spawn the actual flightdeck MCP server and speak JSON-RPC to it.

    What this proves that no unit test can: the wrapper finds the CLI, the CLI finds its
    config, and a tool result travels CLI stdout → MCP content verbatim. The wrapper has
    no tool logic of its own to test — that absence is its design.
    """
    env = {**os.environ, **scratch}
    proc = subprocess.Popen(
        [sys.executable, str(BACKEND / "flightdeck" / "mcp_server.py")],
        cwd=str(BACKEND), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1)

    def rpc(method, params=None, mid=1):
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert line, f"server died: {proc.stderr.read()[:400]}"
        return json.loads(line)

    try:
        init = rpc("initialize")
        assert init["result"]["serverInfo"]["name"] == "flightdeck"

        tools = rpc("tools/list")["result"]["tools"]
        assert len(tools) == 30

        out = rpc("tools/call", {"name": "radar_create",
                                 "arguments": {"slug": "wrap", "title": "Via wrapper"}})
        payload = json.loads(out["result"]["content"][0]["text"])
        assert payload["slug"] == "wrap" and payload["blipCount"] == 0

        # An error is data through every layer, never a dead server.
        out = rpc("tools/call", {"name": "radar_get", "arguments": {"slug": "nope"}})
        payload = json.loads(out["result"]["content"][0]["text"])
        assert "LookupError" in payload["error"]
    finally:
        proc.stdin.close()
        proc.wait(timeout=15)
