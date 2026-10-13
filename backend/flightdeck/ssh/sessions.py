"""tmux sessions on the far side: written once, read from then on.

A session is created with its command already decided. After that the agent can
only look at it: `capture-pane` and nothing else. There is deliberately no way to
type into a running session from here, because typing into one is how every rule in
`policy` gets skipped: the policy would see a command starting with `tmux`, not the
command actually being run.

That gives the escalation path its shape. A command the policy will not let the agent
run is not refused and forgotten — it is typed into a new session and **left at the
prompt, unsent**. The reply says which session, and the exact ssh line to attach to it.
A person attaches, reads what is sitting there, and presses Enter, or does not. Anything
that needs a password typed — sudo, a key passphrase, a y/n prompt — belongs on this
path for the same reason: the agent has no business typing a password, and a person at
a terminal can.

The session also outlives the connection, which is the second reason to use tmux: a
long build keeps running when the laptop sleeps or the VPN drops.
"""
import time

from flightdeck.ssh import master
from flightdeck.ssh import policy

PREFIX = "fd-"


def make_name(kind: str = "run") -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{PREFIX}{kind}-{stamp}-{int(time.time() * 1000) % 1000:03d}"


def valid_name(name: str) -> bool:
    return bool(policy.SESSION_NAME.match(name or ""))


def has_tmux(cfg: dict) -> bool:
    return master.run(cfg, "command -v tmux", timeout=15)["rc"] == 0


def create(cfg: dict, name: str, command: str, *, enter: bool) -> dict:
    """Open a detached session and put `command` into it.

    `enter=True` runs it. `enter=False` leaves it typed at the prompt, which is the
    whole escalation mechanism: the text is there, visible, and waiting for a person.
    `send-keys -l` sends the string literally, so a command containing something like
    `Enter` or `C-c` cannot be read as a key name.
    """
    if not valid_name(name):
        return {"error": f"session name {name!r} must look like fd-<name>"}

    # The session opens on a plain shell and the command is typed into it, rather than
    # the session being the command. Two reasons: a staged command has to sit at a
    # prompt for a person to see, and a session whose command has finished would take
    # its output away with it.
    out = master.run(cfg, master.remote_argv(
        ["tmux", "new-session", "-d", "-s", name]), timeout=30)
    if out["rc"] != 0:
        return {"error": "could not create the session: "
                         f"{(out['stderr'] or out['stdout']).strip()}"}

    typed = master.run(cfg, master.remote_argv(
        ["tmux", "send-keys", "-t", name, "-l", command]), timeout=30)
    if typed["rc"] != 0:
        return {"error": "the session was created but the command could not be typed "
                         f"into it: {(typed['stderr'] or '').strip()}",
                "session": name}

    if enter:
        sent = master.run(cfg, master.remote_argv(
            ["tmux", "send-keys", "-t", name, "Enter"]), timeout=30)
        if sent["rc"] != 0:
            return {"error": "the command was typed but not started: "
                             f"{(sent['stderr'] or '').strip()}", "session": name}

    return {"session": name, "started": bool(enter),
            "attach": master.attach_command(cfg, name)}


# Colon, not tab: tmux 3.4 rewrites a tab inside `-F` output to an underscore, so a
# tab-separated format comes back as one unsplittable field. A colon is safe because
# tmux itself forbids one in a session name.
_LIST_FORMAT = ("#{session_name}:#{session_created}:#{session_attached}"
                ":#{session_windows}")


def listing(cfg: dict) -> dict:
    out = master.run(cfg, master.remote_argv(
        ["tmux", "list-sessions", "-F", _LIST_FORMAT]), timeout=30)
    if out["rc"] != 0:
        text = (out["stderr"] or "") + (out["stdout"] or "")
        if "no server running" in text.lower():
            return {"sessions": [], "note": "no tmux server is running on the host"}
        return {"error": f"could not list sessions: {text.strip()}"}

    sessions = []
    for line in out["stdout"].splitlines():
        parts = line.rsplit(":", 3)
        if len(parts) < 4:
            continue
        name, created, attached, windows = parts
        sessions.append({
            "name": name,
            "created": _as_int(created),
            "attached": attached not in ("0", ""),
            "windows": _as_int(windows),
            "ours": name.startswith(PREFIX),
        })
    return {"sessions": sessions}


def capture(cfg: dict, name: str, lines: int = 200) -> dict:
    """A picture of the terminal, which is how the agent watches without taking over."""
    if not name:
        return {"error": "give a session name"}
    lines = max(1, min(int(lines or 200), 2000))
    out = master.run(cfg, master.remote_argv(
        ["tmux", "capture-pane", "-p", "-t", name, "-S", f"-{lines}"]), timeout=30)
    if out["rc"] != 0:
        text = ((out["stderr"] or "") + (out["stdout"] or "")).strip()
        return {"error": f"could not read session {name!r}: {text}"}
    screen = out["stdout"].rstrip("\n")
    return {"session": name, "lines": len(screen.splitlines()), "screen": screen}


def kill(cfg: dict, name: str) -> dict:
    """Only sessions this domain created. A person's own shell is not ours to end."""
    if not valid_name(name):
        return {"error": f"refusing to kill {name!r}: this tool only kills sessions it "
                         f"created, whose names start with {PREFIX!r}"}
    out = master.run(cfg, master.remote_argv(
        ["tmux", "kill-session", "-t", name]), timeout=30)
    if out["rc"] != 0:
        text = ((out["stderr"] or "") + (out["stdout"] or "")).strip()
        return {"error": f"could not kill session {name!r}: {text}"}
    return {"session": name, "killed": True}


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
