"""The `odoo_*` domain without an Odoo server anywhere in sight.

Everything below is a property of the tools themselves: what the schemas promise, which
wire each tool picks, what the guard refuses, how a `/jsonrpc` envelope is read, and the
fact that no error path prints the password. The guard tests all assert the same second
thing — that the fake transport recorded **no call at all** — because "refused" is only
worth something if nothing left the machine.

Tests reach `flightdeck.odoo.mcp_server` directly and pass its TOOLS to
`registry.dispatch`, so they hold whether or not the domain has been wired into
`_DOMAINS` yet.
"""
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from flightdeck.agentsurface import registry
from flightdeck.odoo import config, guard, mcp_server, transport

BACKEND = Path(__file__).resolve().parents[1]

# Distinctive on purpose: the leak assertions look for this exact string, and a value
# like "admin" would false-positive on the login field, which is legitimately printed.
FAKE_PASSWORD = "s3kr3t-do-not-print-me"

_TOML = """
[instances.demo]
base_url = "http://127.0.0.1:8069"
db = "demo_db"
login = "admin"
password_env = "DEMO_PASSWORD"
write_models = ["res.partner"]
write_methods = ["res.partner.message_subscribe"]
note = "a throwaway instance for the tests"

[instances.locked]
base_url = "http://127.0.0.1:8069"
db = "locked_db"
login = "admin"
password_env = "DEMO_PASSWORD"
note = "no write_models key at all; a missing allowlist must read as empty"
"""


class FakeTransport:
    """Stands in for `transport`, recording what it was asked to do."""

    def __init__(self, result=None, uid=2):
        self.calls = []
        self.result = {"ok": True, "result": []} if result is None else result
        self.uid = uid

    def authenticate(self, inst, timeout=None):
        self.calls.append({"kind": "authenticate", "instance": inst.name})
        return self.uid, None

    def version(self, inst, timeout=None):
        self.calls.append({"kind": "version", "instance": inst.name})
        return {"server_version": "12.0", "server_version_info": [12, 0]}, None

    def execute(self, inst, uid, model, method, args=None, kwargs=None,
                transport="xmlrpc", timeout=None):
        self.calls.append({"kind": "execute", "instance": inst.name, "model": model,
                           "method": method, "args": args, "kwargs": kwargs or {},
                           "transport": transport})
        return self.result

    @property
    def executes(self):
        return [c for c in self.calls if c["kind"] == "execute"]


@pytest.fixture()
def registry_file(tmp_path, monkeypatch):
    """A registry file plus a clean environment: any real ODOO_<NAME>_URL exported in
    this shell would otherwise leak an instance into the tests."""
    for key in list(os.environ):
        if re.match(r"^ODOO_[A-Z0-9][A-Z0-9_]*_URL$", key):
            monkeypatch.delenv(key, raising=False)
    path = tmp_path / "odoo.toml"
    path.write_text(_TOML, encoding="utf-8")
    monkeypatch.setenv(config.CONFIG_ENV, str(path))
    monkeypatch.setenv("DEMO_PASSWORD", FAKE_PASSWORD)
    return path


@pytest.fixture()
def fake(monkeypatch):
    f = FakeTransport()
    monkeypatch.setattr(mcp_server, "transport", f)
    return f


def call(name, args):
    return registry.dispatch(name, args, tools=mcp_server.TOOLS)


# ------------------------------------------------------------------ the table

def test_seven_tools_all_wearing_the_prefix():
    assert set(mcp_server.TOOLS) == {
        "odoo_instances", "odoo_ping", "odoo_fields", "odoo_search",
        "odoo_create", "odoo_write", "odoo_call"}
    assert all(n.startswith("odoo_") for n in mcp_server.TOOLS)


def test_the_domain_is_on_the_surface_and_shadows_nothing():
    """`merged()` raises on a duplicate name, so reaching this assertion at all proves
    no other domain claims an `odoo_` name. The assertions cover the other half: every
    tool defined here actually arrives, and arrives as this module's own function."""
    merged = registry.merged()
    assert set(mcp_server.TOOLS) <= set(merged)
    assert all(merged[name][0] is entry[0]
               for name, entry in mcp_server.TOOLS.items())


def test_every_tool_has_a_description_and_a_schema():
    for name, (fn, description, props, required) in mcp_server.TOOLS.items():
        assert callable(fn), name
        assert len(description) > 40, name
        assert isinstance(props, dict) and isinstance(required, list), name
        assert set(required) <= set(props), name


def test_every_schema_matches_its_function_signature():
    """The registry-wide contract test, replicated here so it holds before the domain
    is wired into `_DOMAINS` — which is when a schema and a signature drift apart."""
    for name, (fn, _d, _p, required) in mcp_server.TOOLS.items():
        for p in inspect.signature(fn).parameters.values():
            needs = p.default is inspect.Parameter.empty
            says = p.name in required
            assert needs == says, f"{name}.{p.name}: signature and schema disagree"


def test_instance_is_required_everywhere_except_the_one_tool_that_calls_nobody():
    for name, (_fn, _d, props, required) in mcp_server.TOOLS.items():
        if name == "odoo_instances":
            assert "instance" not in props
            continue
        assert required[0] == "instance", name


def test_confirm_defaults_to_false_and_stays_out_of_required():
    for name in ("odoo_create", "odoo_write", "odoo_call"):
        fn, _d, props, required = mcp_server.TOOLS[name]
        assert "confirm" in props and "confirm" not in required, name
        assert inspect.signature(fn).parameters["confirm"].default is False, name


# ------------------------------------------------------ the tool that must not exist

def test_there_is_no_unlink_tool():
    assert "odoo_unlink" not in mcp_server.TOOLS
    assert not any("unlink" in n for n in mcp_server.TOOLS)
    assert "error" in call("odoo_unlink", {"instance": "demo", "model": "res.partner",
                                           "ids": [1]})


def test_the_cli_exits_3_on_odoo_unlink(tmp_path):
    """The negative test run at the process boundary, where the exit code lives."""
    env = {**os.environ, "TOKEN_AUDIT_DB_PATH": str(tmp_path / "cli.db"),
           "TOKEN_AUDIT_DATABASE_URL": "", "TREASURES_STORE": str(tmp_path / "store")}
    proc = subprocess.run(
        [sys.executable, "-m", "flightdeck.cli", "odoo_unlink",
         "--instance", "staging-ce", "--model", "res.partner", "--ids", "[1]"],
        cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "unknown tool" in json.loads(proc.stdout)["error"]


# --------------------------------------------------------------- the registry file

def test_a_missing_registry_is_an_error_not_a_crash(tmp_path, monkeypatch):
    for key in list(os.environ):
        if re.match(r"^ODOO_[A-Z0-9][A-Z0-9_]*_URL$", key):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "nowhere.toml"))
    out = call("odoo_instances", {})
    assert out["layer"] == "config" and "no Odoo instances" in out["error"]
    out = call("odoo_ping", {"instance": "demo"})
    assert out["layer"] == "config"


def test_a_broken_registry_reports_the_file_it_could_not_read(tmp_path, monkeypatch):
    path = tmp_path / "odoo.toml"
    path.write_text("[instances.demo\nbroken = ", encoding="utf-8")
    monkeypatch.setenv(config.CONFIG_ENV, str(path))
    out = call("odoo_ping", {"instance": "demo"})
    assert out["layer"] == "config" and str(path) in out["config_path"]


def test_the_file_path_loads_and_a_missing_allowlist_reads_as_empty(registry_file):
    out = call("odoo_instances", {})
    by_name = {i["instance"]: i for i in out["instances"]}
    assert by_name["demo"]["write_models"] == ["res.partner"]
    assert by_name["demo"]["source"] == "file"
    assert by_name["locked"]["write_models"] == []
    assert by_name["locked"]["write_methods"] == []
    assert by_name["locked"]["read_only"] is True


def test_the_environment_path_declares_a_whole_instance(tmp_path, monkeypatch):
    for key in list(os.environ):
        if re.match(r"^ODOO_[A-Z0-9][A-Z0-9_]*_URL$", key):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "nowhere.toml"))
    monkeypatch.setenv("ODOO_STAGING_CE_URL", "http://10.0.0.1:8269/")
    monkeypatch.setenv("ODOO_STAGING_CE_DB", "<staging-db>")
    monkeypatch.setenv("ODOO_STAGING_CE_USER", "admin")
    monkeypatch.setenv("ODOO_STAGING_CE_PASSWORD", FAKE_PASSWORD)
    monkeypatch.setenv("ODOO_STAGING_CE_WRITE_MODELS", "res.partner, res.users")
    out = call("odoo_instances", {})
    got = out["instances"][0]
    assert got["instance"] == "staging-ce"          # ODOO_STAGING_CE_ -> staging-ce
    assert got["base_url"] == "http://10.0.0.1:8269"
    assert got["write_models"] == ["res.partner", "res.users"]
    assert got["source"] == "env" and got["password_available"] is True


def test_the_environment_shadows_the_file_entry_whole(registry_file, monkeypatch):
    monkeypatch.setenv("ODOO_DEMO_URL", "http://192.168.0.9:8069")
    monkeypatch.setenv("ODOO_DEMO_DB", "from_env")
    monkeypatch.setenv("ODOO_DEMO_USER", "someone-else")
    monkeypatch.setenv("ODOO_DEMO_PASSWORD", FAKE_PASSWORD)
    inst, err = config.resolve("demo")
    assert err is None
    assert (inst.source, inst.db, inst.login) == ("env", "from_env", "someone-else")
    # Whole-instance, not field-by-field: the file's write_models does NOT survive.
    assert inst.write_models == []


def test_an_unknown_instance_lists_the_known_ones_and_touches_nothing(registry_file,
                                                                      fake):
    out = call("odoo_write", {"instance": "demoo", "model": "res.partner",
                              "ids": [1], "values": {"comment": "x"},
                              "confirm": True})
    assert out["layer"] == "config"
    assert out["known"] == ["demo", "locked"]
    assert fake.calls == []


def test_a_missing_password_variable_is_named_but_never_read(registry_file,
                                                             monkeypatch):
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    inst, err = config.resolve("demo")
    assert err is None
    _pw, perr = inst.password()
    assert perr["layer"] == "auth" and perr["password_env"] == "DEMO_PASSWORD"
    assert FAKE_PASSWORD not in json.dumps(perr)


# ------------------------------------------------------------------- the guard

def test_a_model_outside_write_models_is_refused_before_the_wire(registry_file, fake):
    out = call("odoo_write", {"instance": "locked", "model": "res.partner",
                              "ids": [1], "values": {"comment": "x"},
                              "confirm": True})
    assert out["layer"] == "guard" and out["confirmed"] is False
    assert out["write_models"] == []
    assert "not allowed" in out["error"]
    assert fake.calls == []


def test_create_is_refused_the_same_way(registry_file, fake):
    out = call("odoo_create", {"instance": "locked", "model": "res.partner",
                               "values": {"name": "x"}, "confirm": True})
    assert out["layer"] == "guard" and fake.calls == []


def test_without_confirm_the_refusal_spells_out_the_change(registry_file, fake):
    out = call("odoo_write", {"instance": "demo", "model": "res.partner",
                              "ids": [12, 15], "values": {"comment": "hello"}})
    assert out["layer"] == "guard" and out["confirmed"] is False
    assert "confirm=true" in out["error"]
    assert "res.partner" in out["error"] and "2" in out["error"]
    assert out["values"] == {"comment": "hello"}
    assert fake.calls == []


def test_confirm_must_be_the_boolean_not_the_word(registry_file, fake):
    out = call("odoo_write", {"instance": "demo", "model": "res.partner", "ids": [1],
                              "values": {"comment": "x"}, "confirm": "true"})
    assert out["layer"] == "guard" and fake.calls == []


def test_unlink_is_refused_even_when_the_allowlist_names_it(tmp_path, monkeypatch,
                                                            fake):
    for key in list(os.environ):
        if re.match(r"^ODOO_[A-Z0-9][A-Z0-9_]*_URL$", key):
            monkeypatch.delenv(key, raising=False)
    path = tmp_path / "odoo.toml"
    path.write_text(_TOML + '\nwrite_methods = ["res.partner.unlink"]\n',
                    encoding="utf-8")
    monkeypatch.setenv(config.CONFIG_ENV, str(path))
    monkeypatch.setenv("DEMO_PASSWORD", FAKE_PASSWORD)
    inst, _ = config.resolve("locked")
    assert "res.partner.unlink" in inst.write_methods      # the allowlist really says so
    out = call("odoo_call", {"instance": "locked", "model": "res.partner",
                             "method": "unlink", "ids": [1], "confirm": True})
    assert out["layer"] == "guard" and "denied outright" in out["error"]
    assert fake.calls == []
    assert "unlink" in guard.HARD_DENIED_METHODS


def test_a_method_outside_write_methods_is_refused(registry_file, fake):
    out = call("odoo_call", {"instance": "demo", "model": "sale.order",
                             "method": "action_confirm", "ids": [1], "confirm": True})
    assert out["layer"] == "guard" and "sale.order.action_confirm" in out["error"]
    assert fake.calls == []


def test_an_allowed_method_with_confirm_reaches_the_wire(registry_file, fake):
    out = call("odoo_call", {"instance": "demo", "model": "res.partner",
                             "method": "message_subscribe", "ids": [7],
                             "confirm": True})
    assert out["ok"] is True
    assert fake.executes[0]["method"] == "message_subscribe"


# ------------------------------------------------------------ choosing the wire

def test_reads_go_over_xmlrpc(registry_file, fake):
    call("odoo_search", {"instance": "demo", "model": "res.partner"})
    call("odoo_fields", {"instance": "demo", "model": "res.partner"})
    assert [c["transport"] for c in fake.executes] == ["xmlrpc", "xmlrpc"]


def test_everything_that_can_write_goes_over_jsonrpc(registry_file, fake):
    """The invariant §2 exists for: XML-RPC reports a failure on a call that already
    committed, whenever the method returns None."""
    call("odoo_create", {"instance": "demo", "model": "res.partner",
                         "values": {"name": "x"}, "confirm": True})
    call("odoo_write", {"instance": "demo", "model": "res.partner", "ids": [1],
                        "values": {"comment": "x"}, "confirm": True})
    call("odoo_call", {"instance": "demo", "model": "res.partner",
                       "method": "message_subscribe", "ids": [1], "confirm": True})
    assert [c["transport"] for c in fake.executes] == ["jsonrpc"] * 3


# ------------------------------------------------------------------ the limit rule

def test_limit_zero_counts_and_never_reaches_search_read(registry_file, monkeypatch):
    """Zero is falsy in Odoo, so `search_read(limit=0)` returns the whole table."""
    fake = FakeTransport(result={"ok": True, "result": 4213})
    monkeypatch.setattr(mcp_server, "transport", fake)
    out = call("odoo_search", {"instance": "demo", "model": "res.partner", "limit": 0})
    assert out == {"instance": "demo", "model": "res.partner", "domain": [],
                   "count": 4213}
    assert [c["method"] for c in fake.executes] == ["search_count"]
    assert all(c["kwargs"].get("limit") is None for c in fake.executes)


def test_an_omitted_limit_becomes_a_number_not_nothing(registry_file, fake):
    call("odoo_search", {"instance": "demo", "model": "res.partner"})
    call("odoo_search", {"instance": "demo", "model": "res.partner", "limit": None})
    for c in fake.executes:
        assert c["method"] == "search_read"
        assert c["kwargs"]["limit"] == mcp_server.DEFAULT_LIMIT


def test_a_negative_limit_is_refused(registry_file, fake):
    out = call("odoo_search", {"instance": "demo", "model": "res.partner",
                               "limit": -1})
    assert out["layer"] == "guard" and fake.executes == []


# ------------------------------------------------------- reading the JSON envelope

def test_an_empty_envelope_is_success_with_a_null_result():
    """`{"jsonrpc": "2.0", "id": 1}` — no result key, no error key. This is the exact
    call that raises over XML-RPC after the database has already changed."""
    out = transport.parse_jsonrpc({"jsonrpc": "2.0", "id": 1},
                                  {"instance": "demo", "model": "res.partner"})
    assert out == {"ok": True, "result": None}


def test_an_access_error_lands_in_the_acl_layer():
    out = transport.parse_jsonrpc(
        {"error": {"data": {"name": "odoo.exceptions.AccessError",
                            "message": "Sorry, you are not allowed to modify this."}}},
        {"instance": "demo", "model": "account.move", "method": "write"})
    assert out["layer"] == "odoo_acl"
    assert out["odoo_exception"] == "odoo.exceptions.AccessError"
    assert "account.move" in out["error"]


def test_a_user_error_lands_in_the_orm_layer_with_a_short_traceback():
    out = transport.parse_jsonrpc(
        {"error": {"data": {"name": "odoo.exceptions.UserError",
                            "message": "Cannot confirm a cancelled order.",
                            "debug": "line1\nline2\nline3\nline4\nline5"}}},
        {"instance": "demo", "model": "sale.order", "method": "action_confirm"})
    assert out["layer"] == "orm"
    assert out["traceback_tail"] == ["line3", "line4", "line5"]


def test_a_bad_credential_lands_in_the_auth_layer():
    out = transport.parse_jsonrpc(
        {"error": {"data": {"name": "odoo.exceptions.AccessDenied",
                            "message": "Access denied"}}},
        {"instance": "demo", "model": "res.partner", "method": "read"})
    assert out["layer"] == "auth"


def test_a_dead_host_is_a_transport_error_not_a_crash(registry_file, monkeypatch):
    monkeypatch.setenv("ODOO_DEAD_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ODOO_DEAD_DB", "nothing")
    monkeypatch.setenv("ODOO_DEAD_USER", "admin")
    monkeypatch.setenv("ODOO_DEAD_PASSWORD", FAKE_PASSWORD)
    out = call("odoo_ping", {"instance": "dead"})
    assert out["layer"] == "transport" and out["instance"] == "dead"
    assert FAKE_PASSWORD not in json.dumps(out)


# --------------------------------------------------------------- no leaked secret

def test_no_layer_prints_the_password(registry_file, monkeypatch):
    """Assert on the JSON string, since that is what actually reaches the agent."""
    payloads = [
        call("odoo_instances", {}),
        call("odoo_write", {"instance": "locked", "model": "res.partner", "ids": [1],
                            "values": {"comment": "x"}, "confirm": True}),
        call("odoo_write", {"instance": "demo", "model": "res.partner", "ids": [1],
                            "values": {"comment": "x"}}),
        call("odoo_call", {"instance": "demo", "model": "res.partner",
                           "method": "unlink", "ids": [1], "confirm": True}),
        transport.parse_jsonrpc(
            {"error": {"data": {"name": "odoo.exceptions.UserError",
                                "message": f"password was {FAKE_PASSWORD}"}}},
            {"instance": "demo"}),
    ]
    for p in payloads[:-1]:
        assert FAKE_PASSWORD not in json.dumps(p, default=str)
    # The last one is the case scrubbing exists for: an Odoo message echoing the value.
    assert FAKE_PASSWORD not in json.dumps(
        transport.scrub(payloads[-1], FAKE_PASSWORD))


def test_execute_scrubs_whatever_the_wire_hands_back(registry_file, monkeypatch):
    monkeypatch.setattr(transport, "_execute_jsonrpc",
                        lambda *a, **k: {"error": f"leak {FAKE_PASSWORD}",
                                         "layer": "orm"})
    inst, _ = config.resolve("demo")
    out = transport.execute(inst, 2, "res.partner", "write", [[1], {}],
                            transport="jsonrpc")
    assert FAKE_PASSWORD not in json.dumps(out) and "***" in out["error"]
