"""Running `ssh`, and keeping one authenticated connection alive between processes.

Every tool call is a fresh process, so a connection pool held in memory would be built
and thrown away on each call. `ControlMaster=auto` puts the pool on disk instead: the
first ssh opens a unix socket and forks a master that holds the authenticated TCP
connection; later processes multiplex onto it and skip the handshake entirely.
`ControlPersist=60s` keeps the master alive for a minute after the last client,
which is what carries it across spawns.

The line that matters: **the connection may live between calls, the decision may not.**
The master process holds a socket and none of this code. Policy is still read from disk
and evaluated fresh on every single call.

Every invocation passes `-F /dev/null`, so the operator's `~/.ssh/config` is neither
read nor needed, and `-o BatchMode=yes`, so ssh can never stop and ask for a
password — if a host needs one typed, that is a reason to hand the work to a person,
not something for this code to answer.
"""
import shlex
import subprocess
import time

from flightdeck.ssh import hosts as hosts_mod

# §1.2 caps a tool call at 120 seconds. Stay under it so the timeout that fires is
# ours, with a readable message, rather than the harness killing the process.
MAX_TIMEOUT = 110
DEFAULT_TIMEOUT = 60
CONTROL_PERSIST = "60s"


def control_path() -> str:
    """`%C` is ssh's own hash of host+port+user+proxy: one socket per endpoint."""
    return str(hosts_mod.control_dir() / "%C")


# A unix socket path is capped at 108 bytes by the kernel, and `%C` expands to a
# 40-character hash. `~/.flightdeck/cm/` leaves plenty of room; a deep directory does
# not, and ssh answers that with "ControlPath too long" on every call. Sharing is an
# optimisation, so a path that will not fit turns it off with a reason attached rather
# than making the host unreachable.
_SUN_PATH_MAX = 108
_HASH_LENGTH = 40


def multiplexing() -> tuple:
    """`(available, reason)` for the shared connection on this machine."""
    length = len(control_path()) - len("%C") + _HASH_LENGTH
    if length >= _SUN_PATH_MAX:
        return False, (f"the control socket path would be {length} bytes and a unix "
                       f"socket path stops at {_SUN_PATH_MAX}; point "
                       f"FLIGHTDECK_SSH_DIR at a shorter directory to share "
                       f"connections")
    return True, ""


def base_options(cfg: dict, *, control: bool = True) -> list:
    if control and not multiplexing()[0]:
        control = False
    opts = [
        "-F", "/dev/null",
        "-o", f"UserKnownHostsFile={hosts_mod.known_hosts_file()}",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "LogLevel=ERROR",
    ]
    if control:
        opts += ["-o", "ControlMaster=auto",
                 "-o", f"ControlPath={control_path()}",
                 "-o", f"ControlPersist={CONTROL_PERSIST}"]
    else:
        # A cold connection on purpose: no socket to join, none left behind.
        opts += ["-o", "ControlMaster=no", "-o", "ControlPath=none"]
    if cfg.get("port"):
        opts += ["-p", str(cfg["port"])]
    if cfg.get("identity_file"):
        opts += ["-i", str(cfg["identity_file"]), "-o", "IdentitiesOnly=yes"]
    return opts


def target(cfg: dict) -> str:
    user = cfg.get("user")
    return f"{user}@{cfg['hostname']}" if user else cfg["hostname"]


def attach_command(cfg: dict, session: str) -> str:
    """The exact line a person pastes to take over a session. For humans, so it is a
    plain ssh command with no FlightDeck anywhere in it."""
    parts = ["ssh"]
    if cfg.get("port") and int(cfg["port"]) != hosts_mod.DEFAULT_PORT:
        parts += ["-p", str(cfg["port"])]
    if cfg.get("identity_file"):
        parts += ["-i", str(cfg["identity_file"])]
    parts += ["-t", target(cfg), "tmux", "attach", "-t", session]
    return " ".join(shlex.quote(p) for p in parts)


def _spawn(argv, timeout):
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"rc": None, "stdout": "", "stderr": "",
                "timed_out": True, "duration_ms": round(timeout * 1000, 1)}
    return {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr,
            "timed_out": False,
            "duration_ms": round((time.monotonic() - started) * 1000, 1)}


def master_state(cfg: dict) -> str:
    """`alive` or `absent`, from ssh's own answer rather than from a socket file."""
    out = _spawn(["ssh", *base_options(cfg), "-O", "check", target(cfg)], 15)
    return "alive" if out["rc"] == 0 else "absent"


def drop_master(cfg: dict) -> None:
    _spawn(["ssh", *base_options(cfg), "-O", "exit", target(cfg)], 15)


def ensure_master(cfg: dict) -> dict:
    """Reuse the master if it answers, otherwise clear it away and open a new one.

    A socket file can outlive the process that owned it — a killed master, a rebooted
    host — and joining a dead socket fails in a way that reads like an auth error. So
    the check is `-O check`, and a failure means `-O exit` before trying again.
    """
    shared, why = multiplexing()
    if not shared:
        out = _spawn(
            ["ssh", *base_options(cfg, control=False), target(cfg), "true"], 20)
        if out["rc"] != 0:
            return {"master": "unavailable",
                    "detail": (out["stderr"] or "").strip() or "connection failed"}
        return {"master": "unshared", "detail": why}
    if master_state(cfg) == "alive":
        return {"master": "reused"}
    drop_master(cfg)
    out = _spawn(["ssh", *base_options(cfg), target(cfg), "true"], 20)
    if out["rc"] != 0:
        return {"master": "unavailable",
                "detail": (out["stderr"] or "").strip() or "connection failed"}
    return {"master": "opened", "open_ms": out["duration_ms"]}


def run(cfg: dict, remote_command: str, *, timeout: int = DEFAULT_TIMEOUT,
        control: bool = True) -> dict:
    """One remote command. A non-zero remote exit code is data, not an error here."""
    timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    argv = ["ssh", *base_options(cfg, control=control), target(cfg), remote_command]
    out = _spawn(argv, timeout)
    if out["timed_out"]:
        out["error"] = f"the command did not finish within {timeout}s"
    return out


def remote_argv(parts) -> str:
    """A remote command built from parts, quoted once for the remote shell.

    ssh joins its trailing arguments with spaces and hands the result to a shell on the
    far side, so quoting has to happen here — this is what stops a session name or a
    staged command from being re-parsed as shell.
    """
    return " ".join(shlex.quote(str(p)) for p in parts)
