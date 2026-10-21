"""Every `fd` command's --json stdout validates against its schema in
api/schemas/cli/. Commands run against the session's seeded temp ledger with
a scrubbed environment; mutating commands run against a private copy of the
ledger file. `help` is text by contract (api/cli.md) and exempt from schema
validation. `fd serve`'s stdout contract is validated in test_go_server.py
after the fixture server stops. One refusal case validates against the error
envelope and exits 2; an unknown command exits 3 with the same envelope.
"""
import json
import shutil

import pytest

from conftest import cli_schema, run_fd, validate

# pg_restore -f - --data-only --table=messages output: header, rows, `\.`.
# Same column list as go/core/importer/pgcopy_test.go's `header`.
_COPY_HEADER = (
    "COPY public.messages (uuid, session_id, project, model, ts, input_tokens, "
    "cache_read, cache_create_5m, cache_create_1h, output_tokens, service_tier, "
    "message_id, request_id, speed, inference_geo, dup_of, dedupe_method, "
    "prompt_id, agent_id) FROM stdin;")
_COPY_ROW = "\t".join([
    "pg1", "pg-sess", "/p", "claude-opus-5-5", "2026-08-01T00:00:00.000Z",
    "3", "4", "0", "0", "7", "standard", "PGM1", "PGR1",
    r"\N", r"\N", r"\N", "exact", r"\N", r"\N"])


def _out(proc):
    return json.loads(proc.stdout)


def _db_copy(tmp_path, go_ledger):
    db = tmp_path / "ledger.db"
    shutil.copy(go_ledger["db"], db)
    return db


def _paths(go_ledger):
    return ("--db", str(go_ledger["db"]), "--projects", str(go_ledger["projects"]))


def test_ingest_json(go_ledger, registry):
    p = run_fd("ingest", *_paths(go_ledger), "--json")
    validate(registry, cli_schema("ingest"), _out(p))


def test_spend_json(go_ledger, registry):
    p = run_fd("spend", *_paths(go_ledger), "--no-ingest", "--json")
    validate(registry, cli_schema("spend"), _out(p))


def test_sessions_json(go_ledger, registry):
    p = run_fd("sessions", *_paths(go_ledger), "--no-ingest", "--json")
    validate(registry, cli_schema("sessions"), _out(p))


def test_thinking_json(go_ledger, registry):
    p = run_fd("thinking", *_paths(go_ledger), "--no-ingest", "--days", "30",
               "--json")
    validate(registry, cli_schema("thinking"), _out(p))


def test_import_json(tmp_path, go_ledger, registry):
    dump = tmp_path / "dump.txt"
    dump.write_text(f"{_COPY_HEADER}\n{_COPY_ROW}\n\\.\n")
    db = _db_copy(tmp_path, go_ledger)
    p = run_fd("import", "--db", str(db), "--projects", str(go_ledger["projects"]),
               "--pg-copy", str(dump), "--no-ingest", "--json")
    validate(registry, cli_schema("import"), _out(p))


def test_pricing_list_json(go_ledger, registry):
    p = run_fd("pricing", "list", *_paths(go_ledger), "--json")
    validate(registry, cli_schema("pricing-list"), _out(p))


def test_pricing_defaults_json(registry):
    p = run_fd("pricing", "defaults", "--json")
    validate(registry, cli_schema("pricing-defaults"), _out(p))


def test_pricing_set_json(tmp_path, go_ledger, registry):
    db = _db_copy(tmp_path, go_ledger)
    p = run_fd("pricing", "set", "test-model", "--db", str(db),
               "--projects", str(go_ledger["projects"]),
               "--input", "1", "--output", "2", "--json")
    validate(registry, cli_schema("pricing-set"), _out(p))


def test_pricing_import_json(tmp_path, go_ledger, registry):
    cfg = tmp_path / "prices.json"
    cfg.write_text(json.dumps({"models": [
        {"prefix": "test-model", "input": 1, "output": 2}], "geo": {}}))
    db = _db_copy(tmp_path, go_ledger)
    p = run_fd("pricing", "import", str(cfg), "--db", str(db),
               "--projects", str(go_ledger["projects"]), "--json")
    validate(registry, cli_schema("pricing-import"), _out(p))


def test_version_json(registry):
    p = run_fd("version", "--json")
    validate(registry, cli_schema("version"), _out(p))


def test_help_is_text_and_exempt(go_ledger):
    # Text by contract, exit 0; no JSON schema applies.
    p = run_fd("help")
    assert "usage:" in p.stdout


def test_refusal_is_error_envelope_exit_2(go_ledger, registry):
    p = run_fd("spend", *_paths(go_ledger), "--range", "bogus", "--json",
               check=False)
    assert p.returncode == 2, f"{p.returncode}: {p.stdout} {p.stderr}"
    validate(registry, cli_schema("envelope-error"), _out(p))


def test_unknown_command_is_error_envelope_exit_3(registry):
    p = run_fd("frobnicate", "--json", check=False)
    assert p.returncode == 3, f"{p.returncode}: {p.stdout} {p.stderr}"
    validate(registry, cli_schema("envelope-error"), _out(p))
