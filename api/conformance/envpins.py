"""The env vars that can redirect the FlightDeck app's filesystem paths, and
the temp values the contract fixtures pin them to.

Shared by api/conformance/test_python_server.py's client fixture and
tools/contracts/gen_python_openapi.py. Why: `config.load()` lets
TOKEN_AUDIT_DB_PATH / TOKEN_AUDIT_PROJECTS_DIR (and the other
TOKEN_AUDIT_* / FLIGHTDECK_* / TREASURES_* reads in python/flightdeck) beat
the temporary config.toml, so an inherited override would make a test open
or ingest real paths. Every path override is pinned under the fixture's
temp dir; non-path overrides are deleted so their defaults apply.

`discovered()` sweeps the package source for env names, and the coverage
test asserts every name is classified here — a new override fails until it
is pinned or cleared, which is what keeps this list honest.
"""
import os
import re
from pathlib import Path

# name -> "dir" (created under the fixture temp dir) | "path" (a path under
# it that is never created, so reads find nothing and no mount happens).
PIN = {
    # config.py overrides (config.py:27-37) — pin to the same temp paths the
    # temporary TOML carries.
    "TOKEN_AUDIT_DB_PATH": "path",
    "TOKEN_AUDIT_PROJECTS_DIR": "dir",
    # server.py (server.py:32,61)
    "TOKEN_AUDIT_FLOWS_DIR": "dir",
    "FLIGHTDECK_WEB_DIST": "path",        # nonexistent -> no StaticFiles mount
    # runtime.py write target (runtime.py:246)
    "TOKEN_AUDIT_LOCAL_REPORT_FILE": "path",
    # paths request handlers / lifespan read; pinning keeps them off HOME
    "TREASURES_STORE": "dir",
    "TREASURES_READ_ROOTS": "dir",
    "FLIGHTDECK_WORKSPACE": "dir",
    "FLIGHTDECK_MEMORY_DIR": "dir",
    "FLIGHTDECK_SSH_DIR": "dir",
    "TOKEN_AUDIT_JOBS_DIR": "dir",
    "TOKEN_AUDIT_DAEMON_DIR": "dir",
    "TOKEN_AUDIT_SKILLS_DIR": "dir",
    "TOKEN_AUDIT_PLUGINS_DIR": "dir",
    "TOKEN_AUDIT_COMPOSE_ROOTS": "dir",
    "TOKEN_AUDIT_GRAPH_FILE": "path",
    "TOKEN_AUDIT_QUOTA_FILE": "path",
    "TOKEN_AUDIT_REPORT_FILE": "path",
    "TOKEN_AUDIT_DOCKER_SOCK": "path",
    "TOKEN_AUDIT_CLAUDE_JSON": "path",
    "FLIGHTDECK_HOSTS": "path",
    "FLIGHTDECK_APPROVE_CONF": "path",
    "FLIGHTDECK_ODOO_CONFIG": "path",
    "TREASURES_PANDOC": "path",
}

# Non-path overrides, deleted outright: DSNs, USD amounts, intervals, TTLs,
# flags, command and host names. Their defaults are inert in tests.
CLEAR = (
    "TOKEN_AUDIT_DATABASE_URL",
    "TOKEN_AUDIT_SUBSCRIPTION_USD",
    "TOKEN_AUDIT_POLL_INTERVAL",
    "TOKEN_AUDIT_POLL_ACTIVE_INTERVAL",
    "TOKEN_AUDIT_TASK_LABEL_INTERVAL",
    "TOKEN_AUDIT_AGENT_IDLE_CAP_MIN",
    "TOKEN_AUDIT_THREADPOOL",
    "TOKEN_AUDIT_CCUSAGE_CMD",
    "TOKEN_AUDIT_CCUSAGE_TTL",
    "TOKEN_AUDIT_CLAUDE_BIN",
    "TOKEN_AUDIT_CODEX_BIN",
    "TOKEN_AUDIT_HUB_HOST",
    "FLIGHTDECK_TRIGGERS",
    "FLIGHTDECK_CONN_IDLE_TTL",
    "TREASURES_CONN_IDLE_TTL",
    "TREASURES_DEBOUNCE_SECONDS",
)

# Set deliberately by the fixtures themselves: TOKEN_AUDIT_CONFIG points at
# the temporary TOML; the two loop envs keep the lifespan's background work
# off (conftest/test_python_server.py documents the rest).
FIXTURE_VARS = (
    "TOKEN_AUDIT_CONFIG",
    "TOKEN_AUDIT_WATCH",
    "TOKEN_AUDIT_REINGEST_INTERVAL",
)

_ENV_NAME = re.compile(r"(?:TOKEN_AUDIT|FLIGHTDECK|TREASURES)_[A-Z_]+")


def discovered(root):
    """Every TOKEN_AUDIT_*/FLIGHTDECK_*/TREASURES_* name the package mentions."""
    names = set()
    for p in sorted((root / "python" / "flightdeck").rglob("*.py")):
        names.update(_ENV_NAME.findall(p.read_text()))
    return names


def env_map(tmp):
    """name -> pinned value for every PATH override, under `tmp`.

    TOKEN_AUDIT_DB_PATH / TOKEN_AUDIT_PROJECTS_DIR pin to the same paths the
    temporary TOML carries (audit.db / projects): the env override wins over
    the file, so pointing it at a different temp path would silently open a
    different ledger than the seeds wrote.
    """
    tmp = Path(tmp)
    out = {}
    for name, kind in PIN.items():
        p = tmp / name.lower()
        if kind == "dir":
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
        out[name] = str(p)
    out["TOKEN_AUDIT_DB_PATH"] = str(tmp / "audit.db")
    out["TOKEN_AUDIT_PROJECTS_DIR"] = str(tmp / "projects")
    return out


def apply_env(tmp):
    """Pin the path overrides under `tmp` and clear the non-path ones.

    Operates on os.environ — for standalone tools; the pytest fixture uses
    env_map/CLEAR through monkeypatch instead.
    """
    for name in CLEAR:
        os.environ.pop(name, None)
    os.environ.update(env_map(tmp))
