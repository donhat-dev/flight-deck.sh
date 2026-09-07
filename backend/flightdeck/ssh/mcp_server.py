"""The `ssh_*` agent surface: run a command on another machine, under a policy.

Seven tools. One of them can change something on a remote host, and it is the one with
the whole design behind it.

**What happens to a command.** `ssh_run` reads the host's policy from disk,
normalises the command, and gets back one of four answers. `allow` runs it. `deny`
refuses, and there is no argument to pass that changes that. Anything unmatched is
`escalate`, and escalate does not mean "ask and then run it anyway": the command is
**typed into a new tmux session on the host and left at the prompt, unsent**, and the
reply carries the session name and the ssh line to attach to it. A person reads what
is sitting there and decides. That is also the answer for anything needing a password
typed: the agent never types one.

**Why the agent cannot un-do that.** A staged session is read-only to the agent from
then on — `ssh_session_read` takes a picture of the screen and there is no tool that
types into it. `tmux` and `send-keys` are blocked in code inside `ssh_run`, because
otherwise the agent could press its own Enter and the whole gate would be one string
away from useless.

**Refusals come back as data.** A blocked command is a JSON object saying which rule
blocked it and what the way forward is, on exit code 2 — never an exception. The
remote command's own exit code is a different thing entirely: a command that ran and
returned 1 is a successful tool call reporting `rc: 1`.
"""
from flightdeck.ssh import hosts as hosts_mod
from flightdeck.ssh import master
from flightdeck.ssh import policy
from flightdeck.ssh import probe as probe_mod
from flightdeck.ssh import sessions as sessions_mod


def _host(name):
    """`(cfg, None)` or `(None, error)`: a bad host name is data, not a raise."""
    if not name:
        declared = ", ".join(sorted(hosts_mod.load())) or "none"
        return None, {"error": f"give a host name; declared hosts: {declared}"}
    try:
        return hosts_mod.get(name), None
    except KeyError as e:
        return None, {"error": str(e).strip("'\"")}


def _stage(cfg, command, verdict, kind):
    """Put a command into a waiting session and hand the session to a person."""
    if not cfg.get("tmux"):
        return {"error": f"{command!r} needs a person, but the host is declared "
                         f"without tmux, so there is nowhere to leave it waiting",
                "decision": "escalate", "reason": verdict["reason"]}
    name = sessions_mod.make_name(kind)
    made = sessions_mod.create(cfg, name, command, enter=False)
    if "error" in made:
        return {"error": f"{command!r} needs a person, and the waiting session could "
                         f"not be created: {made['error']}",
                "decision": "escalate", "reason": verdict["reason"]}
    return {
        # An "error" key is what makes the CLI exit 2. The command did not run, and
        # the caller has to see that as a refusal, not as a result.
        "error": f"{command!r} was not run: {verdict['reason']}. It is typed into "
                 f"session {name} on {cfg['name']}, waiting at the prompt for a "
                 f"person to press Enter.",
        "decision": "escalate",
        "reason": verdict["reason"],
        "host": cfg["name"],
        "session": name,
        "command": verdict["command"],
        "attach": made["attach"],
        "next": "attach, read what is waiting, and run it or delete the session. "
                "The agent can watch with ssh_session_read but cannot type into it.",
    }


def ssh_hosts():
    table = hosts_mod.load()
    if not table:
        return {"hosts": [], "config": str(hosts_mod.config_path()),
                "note": "no hosts declared; the config file is missing or empty"}
    return {"config": str(hosts_mod.config_path()),
            "hosts": [hosts_mod.public(cfg) for _n, cfg in sorted(table.items())]}


def ssh_run(host, command, timeout=master.DEFAULT_TIMEOUT):
    cfg, err = _host(host)
    if err:
        return err
    if not command or not str(command).strip():
        return {"error": "give a command to run"}

    verdict = policy.evaluate(cfg, str(command))
    if verdict["decision"] == "deny":
        return {"error": f"{verdict['command']!r} was refused: {verdict['reason']}",
                "decision": "deny", "reason": verdict["reason"],
                "host": cfg["name"], "command": verdict["command"],
                "rule": verdict.get("rule"),
                "hard_block": verdict.get("hard_block", False)}

    if verdict["decision"] == "escalate":
        return _stage(cfg, verdict["command"], verdict, "esc")

    state = master.ensure_master(cfg)
    if state.get("master") == "unavailable":
        return {"error": f"could not reach {cfg['name']}: {state.get('detail')}",
                "host": cfg["name"], "decision": verdict["decision"]}

    out = master.run(cfg, verdict["command"], timeout=timeout)
    if out.get("error"):
        return {"error": out["error"], "host": cfg["name"], "decision": "allow",
                "command": verdict["command"]}
    return {"host": cfg["name"], "decision": "allow", "reason": verdict["reason"],
            "command": verdict["command"], "rc": out["rc"],
            "stdout": out["stdout"], "stderr": out["stderr"],
            "duration_ms": out["duration_ms"], "connection": state["master"]}


def ssh_session_start(host, command, name=None):
    cfg, err = _host(host)
    if err:
        return err
    if not command or not str(command).strip():
        return {"error": "give a command for the session to run"}
    if not cfg.get("tmux"):
        return {"error": f"host {cfg['name']} is declared without tmux"}

    verdict = policy.evaluate(cfg, str(command))
    if verdict["decision"] == "deny":
        return {"error": f"{verdict['command']!r} was refused: {verdict['reason']}",
                "decision": "deny", "reason": verdict["reason"],
                "host": cfg["name"], "rule": verdict.get("rule"),
                "hard_block": verdict.get("hard_block", False)}

    state = master.ensure_master(cfg)
    if state.get("master") == "unavailable":
        return {"error": f"could not reach {cfg['name']}: {state.get('detail')}",
                "host": cfg["name"]}

    if verdict["decision"] == "escalate":
        return _stage(cfg, verdict["command"], verdict, "esc")

    session = name or sessions_mod.make_name("run")
    if not sessions_mod.valid_name(session):
        return {"error": f"session name {session!r} must look like fd-<name>: this "
                         f"tool only owns names it can recognise later"}
    made = sessions_mod.create(cfg, session, verdict["command"], enter=True)
    if "error" in made:
        return {**made, "host": cfg["name"]}
    return {"host": cfg["name"], "decision": "allow", "reason": verdict["reason"],
            "command": verdict["command"], **made,
            "next": "read it with ssh_session_read; nothing can type into it"}


def ssh_session_list(host):
    cfg, err = _host(host)
    if err:
        return err
    if not cfg.get("tmux"):
        return {"error": f"host {cfg['name']} is declared without tmux"}
    state = master.ensure_master(cfg)
    if state.get("master") == "unavailable":
        return {"error": f"could not reach {cfg['name']}: {state.get('detail')}"}
    out = sessions_mod.listing(cfg)
    return out if "error" in out else {"host": cfg["name"], **out}


def ssh_session_read(host, name, lines=200):
    cfg, err = _host(host)
    if err:
        return err
    if not cfg.get("tmux"):
        return {"error": f"host {cfg['name']} is declared without tmux"}
    state = master.ensure_master(cfg)
    if state.get("master") == "unavailable":
        return {"error": f"could not reach {cfg['name']}: {state.get('detail')}"}
    out = sessions_mod.capture(cfg, name, lines=lines)
    return out if "error" in out else {"host": cfg["name"], **out}


def ssh_session_kill(host, name):
    cfg, err = _host(host)
    if err:
        return err
    if not cfg.get("tmux"):
        return {"error": f"host {cfg['name']} is declared without tmux"}
    if not sessions_mod.valid_name(name or ""):
        return {"error": f"refusing to kill {name!r}: this tool only kills sessions it "
                         f"created, whose names start with {sessions_mod.PREFIX!r}"}
    state = master.ensure_master(cfg)
    if state.get("master") == "unavailable":
        return {"error": f"could not reach {cfg['name']}: {state.get('detail')}"}
    out = sessions_mod.kill(cfg, name)
    return out if "error" in out else {"host": cfg["name"], **out}


def ssh_probe(host, samples=3):
    cfg, err = _host(host)
    if err:
        return err
    return probe_mod.run(cfg, samples=samples)


_HOST_PROP = {"type": "string",
              "description": "host name as declared in hosts.toml, not a "
                             "hostname or an IP address. Call ssh_hosts for "
                             "the declared names."}
_NAME_PROP = {"type": "string",
              "description": "session name; must start with fd-"}

TOOLS = {
    "ssh_hosts": (
        ssh_hosts,
        "Which machines are declared, how each is reached, and what each one permits: "
        "its policy mode, how many allow and deny rules it carries, and whether its "
        "password environment variable is set. It never shows a password, a key, or "
        "any other credential value, because the config file does not hold one — it "
        "holds the name of an environment variable.",
        {}, []),
    "ssh_run": (
        ssh_run,
        "Run one command on a declared host and get its exit code, stdout and stderr "
        "back. The host's policy decides first: a command matching an allow rule runs; "
        "one matching a deny rule is refused outright; anything else is NOT run — it "
        "is typed into a new tmux session on the host and left at the prompt for a "
        "person, and the reply gives the session name and the ssh line to attach. Use "
        "that path deliberately for anything needing a password or a confirmation "
        "typed. 'tmux' and 'send-keys' are blocked here in code, and so is any "
        "pipeline or command chain, because a policy rule matches one command.",
        {"host": _HOST_PROP,
         "command": {"type": "string", "description": "the command to run, as one "
                                                      "command with no pipes or "
                                                      "chaining"},
         "timeout": {"type": "integer",
                     "description": f"seconds, default {master.DEFAULT_TIMEOUT}, "
                                    f"capped at {master.MAX_TIMEOUT}"}},
        ["host", "command"]),
    "ssh_session_start": (
        ssh_session_start,
        "Start a tmux session on the host running a command that has passed the "
        "policy. Use it for work that outlives the connection — a build, a restore, "
        "anything that must survive a closed laptop — and for anything longer than a "
        "tool call may take. The command is approved once, when the session is "
        "created; afterwards the session can only be read, never typed into. A "
        "command the policy will not allow is staged unsent instead, exactly as "
        "ssh_run stages one.",
        {"host": _HOST_PROP,
         "command": {"type": "string", "description": "the command the session runs"},
         "name": {**_NAME_PROP,
                  "description": "optional session name, must start with fd-; "
                                 "one is generated if omitted"}},
        ["host", "command"]),
    "ssh_session_list": (
        ssh_session_list,
        "Every tmux session alive on the host, including ones a person started, each "
        "marked with whether this tool created it, whether someone is attached, and "
        "when it started. Sessions this tool did not create can be listed and read "
        "but not killed.",
        {"host": _HOST_PROP}, ["host"]),
    "ssh_session_read": (
        ssh_session_read,
        "A picture of a session's terminal, taken with capture-pane: the text on the "
        "screen and the scrollback above it. This is how to follow a long job, or "
        "watch what a person is doing in a session that was handed to them, without "
        "taking the keyboard off them. There is no tool that types into a session — "
        "that is the point of this one.",
        {"host": _HOST_PROP, "name": _NAME_PROP,
         "lines": {"type": "integer",
                   "description": "how much scrollback, default 200, up to 2000"}},
        ["host", "name"]),
    "ssh_session_kill": (
        ssh_session_kill,
        "End a session this tool created. It refuses any name not starting with fd-, "
        "so a person's own shell on the host cannot be closed from here.",
        {"host": _HOST_PROP, "name": _NAME_PROP}, ["host", "name"]),
    "ssh_probe": (
        ssh_probe,
        "Measure what the shared connection is worth on the route to one host. It "
        "times the same trivial command twice: once with the shared connection "
        "dropped "
        "so a full handshake has to happen, and once through an established socket. "
        "Returns both sets of milliseconds and the median of each, which is the number "
        "to judge the connection settings by.",
        {"host": _HOST_PROP,
         "samples": {"type": "integer",
                     "description": "timed runs of each kind, default 3, up to 10"}},
        ["host"]),
}
