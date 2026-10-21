"""The `odoo_*` tools against a real Odoo, plus the two negatives that have to be run.

Target: `staging-ce`, the shared the staging database server on 12.0. It is **shared with
colleagues**, so its `write_models` is empty and everything here is a read — the write
tests are the ones that prove a write is refused.

The whole module skips when the host is unreachable, decided by one short HTTP probe at
import time. No pytest marker is registered for this: that would mean editing the shared
`conftest.py`, and a laptop off the VPN should not turn the suite red either way.

The target is named entirely by the environment — host, database, login and password —
and the module skips when any of the four is unset. This repository has a remote, so an
internal hostname or a working login committed here would be published along with it;
none of the four has a default for that reason.

    export ODOO_STAGING_CE_URL=http://<host>:<port>
    export ODOO_STAGING_CE_DB=<database>
    export ODOO_STAGING_CE_USER=<login>
    export STAGING_CE_PASSWORD=<password>
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import xmlrpc.client
from pathlib import Path

import pytest

from flightdeck.odoo import config, mcp_server, transport

BACKEND = Path(__file__).resolve().parents[1]

_REQUIRED = ("ODOO_STAGING_CE_URL", "ODOO_STAGING_CE_DB",
             "ODOO_STAGING_CE_USER", "STAGING_CE_PASSWORD")
_MISSING = [name for name in _REQUIRED if not os.environ.get(name)]
if _MISSING:
    pytest.skip("set " + ", ".join(_MISSING) + " to run the live Odoo tests",
                allow_module_level=True)

BASE_URL = os.environ["ODOO_STAGING_CE_URL"]
DB = os.environ["ODOO_STAGING_CE_DB"]
LOGIN = os.environ["ODOO_STAGING_CE_USER"]
PASSWORD = os.environ["STAGING_CE_PASSWORD"]

_REGISTRY = f"""
[instances.staging-ce]
base_url = "{BASE_URL}"
db = "{DB}"
login = "{LOGIN}"
password_env = "STAGING_CE_PASSWORD"
write_models = []
write_methods = []
note = "shared staging; read-only"
"""


def _reachable() -> bool:
    body = json.dumps({"jsonrpc": "2.0", "method": "call", "id": 1,
                       "params": {"service": "common", "method": "version",
                                  "args": []}}).encode()
    req = urllib.request.Request(BASE_URL + "/jsonrpc", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as fh:
            return "result" in json.loads(fh.read().decode())
    except (OSError, urllib.error.URLError, ValueError):
        return False


if not _reachable():
    pytest.skip(f"staging-ce is not reachable at {BASE_URL}",
                allow_module_level=True)


@pytest.fixture()
def staging(tmp_path, monkeypatch):
    """Point the registry at a throwaway file holding only staging-ce."""
    path = tmp_path / "odoo.toml"
    path.write_text(_REGISTRY, encoding="utf-8")
    monkeypatch.setenv(config.CONFIG_ENV, str(path))
    monkeypatch.setenv("STAGING_CE_PASSWORD", PASSWORD)
    monkeypatch.delenv("ODOO_STAGING_CE_URL", raising=False)
    return path


def _raw_read(field, ids):
    """Read straight off XML-RPC, bypassing the tools, so a verification cannot be
    fooled by the same code path it is checking."""
    common = xmlrpc.client.ServerProxy(BASE_URL + "/xmlrpc/2/common")
    uid = common.authenticate(DB, LOGIN, PASSWORD, {})
    obj = xmlrpc.client.ServerProxy(BASE_URL + "/xmlrpc/2/object")
    return obj.execute_kw(DB, uid, PASSWORD, "res.partner", "read", [ids], {
        "fields": [field]})


# ------------------------------------------------------------------------ reads

def test_ping_says_who_this_server_is(staging):
    out = mcp_server.odoo_ping("staging-ce")
    assert out["ok"] is True
    assert out["db"] == DB
    assert out["version"].startswith("12.0")
    assert isinstance(out["uid"], int) and out["uid"] > 0
    assert out["read_only"] is True and out["write_models"] == []


def test_search_reads_real_records(staging):
    out = mcp_server.odoo_search("staging-ce", "res.partner",
                                 fields=["name", "email"], limit=5, order="id asc")
    assert out["count"] <= 5
    assert all("id" in r for r in out["records"])


def test_limit_zero_counts_instead_of_reading_the_table(staging):
    out = mcp_server.odoo_search("staging-ce", "res.users", limit=0)
    assert isinstance(out["count"], int) and out["count"] > 0
    assert "records" not in out


def test_fields_come_from_this_server_not_from_the_repository(staging):
    out = mcp_server.odoo_fields("staging-ce", "res.partner",
                                 fields=["name", "email"])
    assert out["fields"]["name"]["type"] == "char"
    assert "email" in out["fields"]


def test_a_bad_domain_is_an_orm_error_not_a_crash(staging):
    out = mcp_server.odoo_search("staging-ce", "res.partner",
                                 domain=[["no_such_field", "=", 1]])
    assert out["layer"] in ("orm", "odoo_acl")
    assert "no_such_field" in json.dumps(out)


def test_the_two_wires_disagree_on_a_method_that_returns_none(staging):
    """Why writes go over JSON-RPC, demonstrated on this server rather than argued.

    `check_access_rule` returns `None` and changes nothing, so it shows the split
    without touching a shared database. Over JSON-RPC the answer is a clean null; over
    XML-RPC the same call comes back as a marshalling TypeError — which, for a method
    that *does* write, arrives after the write has committed.

    If this test ever stops failing on the XML-RPC side, the split can be dropped.
    """
    inst, err = config.resolve("staging-ce")
    assert err is None
    uid, err = transport.authenticate(inst)
    assert err is None
    pid = mcp_server.odoo_search("staging-ce", "res.partner", fields=["id"],
                                 limit=1)["records"][0]["id"]

    over_json = transport.execute(inst, uid, "res.partner", "check_access_rule",
                                  [[pid], "read"], transport="jsonrpc")
    assert over_json == {"ok": True, "result": None}

    over_xml = transport.execute(inst, uid, "res.partner", "check_access_rule",
                                 [[pid], "read"], transport="xmlrpc")
    assert "cannot marshal None" in over_xml["odoo_message"]


# -------------------------------------------------- the negatives, run for real

def test_a_write_to_a_model_outside_write_models_is_refused_and_changes_nothing(
        staging):
    """Both halves matter: the refusal, and the record proving it was only a refusal."""
    first = mcp_server.odoo_search("staging-ce", "res.partner", fields=["id"],
                                   limit=1, order="id asc")
    pid = first["records"][0]["id"]
    before = _raw_read("write_date", [pid])

    out = mcp_server.odoo_write("staging-ce", "res.partner", [pid],
                                {"comment": "flightdeck guard probe"}, confirm=True)

    assert out["layer"] == "guard" and out["confirmed"] is False
    assert out["write_models"] == []
    assert "not allowed" in out["error"]
    assert _raw_read("write_date", [pid]) == before


def test_a_mistyped_instance_name_lands_nowhere(staging):
    out = mcp_server.odoo_write("staging-cee", "res.partner", [1], {"comment": "x"},
                                confirm=True)
    assert out["layer"] == "config"
    assert out["known"] == ["staging-ce"]


def test_odoo_unlink_exits_3_against_a_live_registry(staging, tmp_path):
    """Not a disabled tool — an absent one. Exit 3 is 'unknown tool'."""
    env = {**os.environ, "FLIGHTDECK_ODOO_CONFIG": str(staging),
           "STAGING_CE_PASSWORD": PASSWORD,
           "TOKEN_AUDIT_DB_PATH": str(tmp_path / "cli.db"),
           "TOKEN_AUDIT_DATABASE_URL": "",
           "TREASURES_STORE": str(tmp_path / "store")}
    proc = subprocess.run(
        [sys.executable, "-m", "flightdeck.cli", "odoo_unlink",
         "--instance", "staging-ce", "--model", "res.partner", "--ids", "[1]",
         "--confirm", "true"],
        cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "unknown tool" in json.loads(proc.stdout)["error"]
