"""The host table, and the rule that keeps secrets out of the file that names them.

`~/.flightdeck/hosts.toml` (chmod 600) declares which machines exist, how to reach
them, and what may be run on each. It never holds a password: it holds the NAME of an
environment variable, and the value is read from the environment at call time. A config
file that leaks therefore leaks no credential.

`FLIGHTDECK_HOSTS` points the loader at a different file. That is how the tests aim at
a throwaway host without touching the operator's real config, and it is the only way
this module can be made to read anything else.

Nothing here ever reads `~/.ssh/config`: every ssh invocation this domain makes passes
`-F /dev/null`, so a host is reachable only because this file says how.
"""
import os
import tomllib
from pathlib import Path

DEFAULT_PORT = 22
MODES = ("allow", "escalate", "deny")
# Fail-closed: a host that declares no mode, or an unrecognised one, escalates.
DEFAULT_MODE = "escalate"


def config_path() -> Path:
    override = os.environ.get("FLIGHTDECK_HOSTS")
    return Path(override) if override else Path.home() / ".flightdeck" / "hosts.toml"


def control_dir() -> Path:
    """Where the ControlMaster sockets and the known-hosts file live, mode 700.

    Its own directory, not `~/.ssh`: the domain must never be able to modify the
    operator's ssh configuration, keys, or trusted hosts.
    """
    base = os.environ.get("FLIGHTDECK_SSH_DIR")
    d = Path(base) if base else Path.home() / ".flightdeck" / "cm"
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


def known_hosts_file() -> Path:
    return control_dir() / "known_hosts"


def load() -> dict:
    """`{name: config}` for every declared host. A missing file is an empty table."""
    path = config_path()
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    hosts = raw.get("hosts") or {}
    return {name: _normalise(name, cfg) for name, cfg in hosts.items()
            if isinstance(cfg, dict)}


def _normalise(name: str, cfg: dict) -> dict:
    mode = str(cfg.get("mode") or DEFAULT_MODE).strip().lower()
    return {
        "name": name,
        "hostname": cfg.get("hostname") or "",
        "user": cfg.get("user") or "",
        "port": int(cfg.get("port") or DEFAULT_PORT),
        # A key PATH is not a secret value, so it may sit in the file; the key itself
        # never does.
        "identity_file": cfg.get("identity_file") or "",
        "password_env": cfg.get("password_env") or "",
        "mode": mode if mode in MODES else DEFAULT_MODE,
        "declared_mode": mode,
        "allow": list(cfg.get("allow") or []),
        "deny": list(cfg.get("deny") or []),
        "tmux": bool(cfg.get("tmux", True)),
    }


def get(name: str) -> dict:
    """One host's config, or a KeyError naming what was declared instead."""
    table = load()
    if name not in table:
        known = ", ".join(sorted(table)) or "none"
        raise KeyError(f"no host named {name!r}; declared hosts: {known}")
    return table[name]


def credential(cfg: dict) -> dict:
    """What the config says about a password, WITHOUT the password.

    The value is looked up only to report whether it is set. It is never returned,
    never logged, and never placed on a command line.
    """
    env_name = cfg.get("password_env") or ""
    if not env_name:
        return {"password_env": None, "password_available": False}
    return {"password_env": env_name,
            "password_available": bool(os.environ.get(env_name))}


def public(cfg: dict) -> dict:
    """The view `ssh_hosts` may show: how to reach it, what it permits, no secrets."""
    out = {
        "name": cfg["name"],
        "hostname": cfg["hostname"],
        "user": cfg["user"],
        "port": cfg["port"],
        "mode": cfg["mode"],
        "auth": ("key" if cfg["identity_file"]
                 else "password" if cfg["password_env"] else "agent"),
        "allow_rules": len(cfg["allow"]),
        "deny_rules": len(cfg["deny"]),
        "tmux": cfg["tmux"],
    }
    out.update(credential(cfg))
    if cfg["declared_mode"] != cfg["mode"]:
        out["note"] = (f"mode {cfg['declared_mode']!r} is not one of "
                       f"{', '.join(MODES)}; treated as {cfg['mode']}")
    return out
