"""The instance registry: which Odoo servers `odoo_*` may talk to, and how far.

Two ways to declare an instance, and both end at the same object:

1. **A config file**, `~/.flightdeck/odoo.toml`, chmod 600. It holds the *name* of the
   environment variable carrying the password, never the password. A leaked config file
   leaks no credential.
2. **Environment variables alone**, `ODOO_<NAME>_URL` / `_DB` / `_USER` / `_PASSWORD` /
   `_WRITE_MODELS` / `_WRITE_METHODS` / `_NOTE`. For a shell session or a CI job that
   should not leave a file behind.

Precedence is whole-instance, not per-field: if `ODOO_<NAME>_URL` is set, that instance
comes entirely from the environment and any file entry of the same name is shadowed.
Merging the two field by field would make "where did this password come from" a question
with no short answer, which is the wrong property for a credential path.

There is deliberately no `default` key. A tool call that names no instance is a schema
error, never a call that lands somewhere plausible: two instances here hold records with
the same ids, and a mis-aimed deeplink has already opened an unrelated real record with
no error in this workspace.

Odoo 12 has no API keys (`res.users.apikeys` arrives in Odoo 14), so `odoo12-local` and
`staging-ce` can only authenticate with a login and a password. Odoo 19 can use an API
key, and it goes in the same slot: the third argument of `execute_kw` takes either.
"""
from __future__ import annotations

import os
import re
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_ENV = "FLIGHTDECK_ODOO_CONFIG"
DEFAULT_CONFIG = Path.home() / ".flightdeck" / "odoo.toml"

# ODOO_STAGING_CE_URL -> staging-ce. Instance names therefore use dashes, never
# underscores: an underscore in a name cannot survive the round trip.
_ENV_INSTANCE = re.compile(r"^ODOO_([A-Z0-9][A-Z0-9_]*)_URL$")


@dataclass
class Instance:
    """One declared server plus the ceiling on what may be written to it."""
    name: str
    base_url: str
    db: str
    login: str
    password_env: str = ""
    write_models: list = field(default_factory=list)
    write_methods: list = field(default_factory=list)
    note: str = ""
    source: str = "file"

    @property
    def read_only(self) -> bool:
        return not self.write_models and not self.write_methods

    def public(self) -> dict:
        """Everything about this instance that is safe to print."""
        return {"instance": self.name, "base_url": self.base_url, "db": self.db,
                "login": self.login, "password_env": self.password_env,
                "write_models": list(self.write_models),
                "write_methods": list(self.write_methods),
                "read_only": self.read_only, "source": self.source,
                "note": self.note}

    def password(self) -> tuple[str, dict | None]:
        """The password from the environment, or an auth-layer error naming the
        variable that is missing. An empty password is never tried in its place."""
        var = self.password_env
        value = os.environ.get(var, "") if var else ""
        if not value:
            return "", {
                "error": f"no password for instance {self.name!r}",
                "layer": "auth", "instance": self.name, "password_env": var,
                "detail": f"environment variable {var!r} is unset or empty"
                          if var else "this instance declares no password_env",
                "hint": f"export {var}=... before the call"
                        if var else "add password_env to the config entry"}
        return value, None


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    return Path(override) if override else DEFAULT_CONFIG


def env_prefix(name: str) -> str:
    return "ODOO_" + name.upper().replace("-", "_") + "_"


def _as_list(value) -> list:
    """A missing or malformed allowlist reads as empty, never as 'everything'."""
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, (list, tuple)):
        return [str(p).strip() for p in value if str(p).strip()]
    return []


def _from_env(name: str) -> Instance:
    p = env_prefix(name)
    return Instance(
        name=name,
        base_url=os.environ.get(p + "URL", "").rstrip("/"),
        db=os.environ.get(p + "DB", ""),
        login=os.environ.get(p + "USER", ""),
        password_env=p + "PASSWORD",
        write_models=_as_list(os.environ.get(p + "WRITE_MODELS")),
        write_methods=_as_list(os.environ.get(p + "WRITE_METHODS")),
        note=os.environ.get(p + "NOTE", ""),
        source="env")


def instances_from_env() -> dict[str, Instance]:
    out = {}
    for key in os.environ:
        m = _ENV_INSTANCE.match(key)
        if not m or not os.environ.get(key):
            continue
        name = m.group(1).lower().replace("_", "-")
        out[name] = _from_env(name)
    return out


def _from_file(name: str, entry: dict) -> Instance:
    return Instance(
        name=name,
        base_url=str(entry.get("base_url", "")).rstrip("/"),
        db=str(entry.get("db", "")),
        login=str(entry.get("login", "")),
        password_env=str(entry.get("password_env", "")),
        # A missing allowlist key is an empty allowlist. Fail-closed covers the
        # incomplete config as well as the explicit one.
        write_models=_as_list(entry.get("write_models")),
        write_methods=_as_list(entry.get("write_methods")),
        note=str(entry.get("note", "")),
        source="file")


def load() -> tuple[dict[str, Instance], dict | None]:
    """Every declared instance, environment shadowing file, plus a read error if the
    file exists but cannot be understood. A missing file is not an error on its own —
    the environment path may still have declared everything."""
    out: dict[str, Instance] = {}
    problem = None
    path = config_path()
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            problem = {"error": f"cannot read the instance registry at {path}",
                       "layer": "config", "config_path": str(path),
                       "detail": f"{type(e).__name__}: {e}"}
            data = {}
        for name, entry in (data.get("instances") or {}).items():
            if isinstance(entry, dict):
                out[name] = _from_file(name, entry)
    out.update(instances_from_env())
    return out, problem


def file_mode_warning() -> str | None:
    """The registry names environment variables rather than holding secrets, so a loose
    mode is a warning and not a refusal — but it is still worth saying out loud."""
    path = config_path()
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None
    if mode & 0o077:
        return f"{path} is mode {mode:o}; chmod 600 keeps it to this user"
    return None


def nothing_declared() -> dict:
    """A registry with no instances in it, said in a way a caller can act on. Reached
    when the file is absent and no environment instance was exported either."""
    path = config_path()
    return {"error": "no Odoo instances are declared",
            "layer": "config", "config_path": str(path),
            "detail": f"{path} does not exist" if not path.exists()
                      else f"{path} declares no [instances.<name>] entry",
            "hint": "write ~/.flightdeck/odoo.toml (chmod 600), or export "
                    "ODOO_<NAME>_URL / _DB / _USER / _PASSWORD"}


def resolve(name) -> tuple[Instance | None, dict | None]:
    """One instance by name, or an error that lists the names that do exist.

    An unknown name never falls back to another instance. That is the whole reason this
    function returns an error instead of a best guess.
    """
    known, problem = load()
    if problem and not known:
        return None, problem
    if not known:
        return None, nothing_declared()
    if not isinstance(name, str) or name not in known:
        return None, {"error": f"unknown instance {name!r}", "layer": "config",
                      "known": sorted(known),
                      "hint": "call odoo_instances to see what is declared"}
    inst = known[name]
    missing = [f for f in ("base_url", "db", "login") if not getattr(inst, f)]
    if missing:
        return None, {"error": f"instance {name!r} is incomplete", "layer": "config",
                      "instance": name, "missing": missing, "source": inst.source}
    return inst, None
