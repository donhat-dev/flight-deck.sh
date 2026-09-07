"""The live half: every guard proven against a real sshd, not read off the source.

The workspace rule is that a guard is finished only when the blocked thing has been
attempted and shown to be blocked. Reading `policy.py` and believing it is not enough,
so the refusals here run against a machine that would have carried them out.

The target is any host with sshd and tmux, named by four environment variables:

    FLIGHTDECK_SSH_TEST_KEY   private key with access to it   (required)
    FLIGHTDECK_SSH_TEST_PORT  default 22222
    FLIGHTDECK_SSH_TEST_ADDR  default 127.0.0.1
    FLIGHTDECK_SSH_TEST_USER  default probe

A throwaway container is what these were written against: `alpine` plus `openssh` and
`tmux`, published on 127.0.0.1:22222, with a keypair generated for it and nothing else
on it. Without the variables the whole file skips, so the suite stays runnable on a
machine with no sshd at all.
"""
import os
import socket

import pytest

from flightdeck.agentsurface import registry
from flightdeck.ssh import mcp_server

ADDR = os.environ.get("FLIGHTDECK_SSH_TEST_ADDR", "127.0.0.1")
PORT = int(os.environ.get("FLIGHTDECK_SSH_TEST_PORT", "22222"))
USER = os.environ.get("FLIGHTDECK_SSH_TEST_USER", "probe")
KEY = os.environ.get("FLIGHTDECK_SSH_TEST_KEY", "")

# The deny rule the tests aim at, spelled once so the assertion and the config cannot
# drift apart.
DENY_RULE = r"\brm\s+-rf\b"

_CONFIG = """
[hosts.probe]
hostname      = "{addr}"
user          = "{user}"
port          = {port}
identity_file = "{key}"
mode          = "escalate"
tmux          = true
allow = ['^echo\\b', '^ls\\b', '^(uptime|whoami|id|hostname)$']
deny  = ['{deny}', '\\bshutdown\\b|\\breboot\\b']
"""


def _reachable() -> bool:
    try:
        with socket.create_connection((ADDR, PORT), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not KEY or not os.path.isfile(KEY) or not _reachable(),
    reason=f"no ssh test host on {ADDR}:{PORT} with FLIGHTDECK_SSH_TEST_KEY set")


@pytest.fixture()
def host(tmp_path, monkeypatch):
    path = tmp_path / "hosts.toml"
    path.write_text(
        # A TOML single-quoted string is literal, so the pattern goes in as written —
        # doubling the backslashes here would put `\\b` into the regex.
        _CONFIG.format(addr=ADDR, user=USER, port=PORT, key=KEY, deny=DENY_RULE),
        encoding="utf-8")
    monkeypatch.setenv("FLIGHTDECK_HOSTS", str(path))
    # NOT tmp_path: a unix socket path stops at 108 bytes and pytest's directory names
    # eat most of that, which would turn connection sharing off for the very tests
    # that are here to measure it.
    monkeypatch.setenv("FLIGHTDECK_SSH_DIR", f"/tmp/fd-cm-{os.getpid()}")
    return "probe"


def call(name, args):
    return registry.dispatch(name, args, mcp_server.TOOLS)


def _sessions(host):
    return {s["name"] for s in call("ssh_session_list", {"host": host})["sessions"]}


# --- positive: the corridor works -------------------------------------------------

def test_an_allowed_command_runs_and_returns_its_output(host):
    out = call("ssh_run", {"host": host, "command": "echo hello-from-the-far-side"})
    assert out["decision"] == "allow"
    assert out["rc"] == 0
    assert out["stdout"].strip() == "hello-from-the-far-side"


def test_the_second_call_reuses_the_shared_connection(host):
    call("ssh_run", {"host": host, "command": "whoami"})
    out = call("ssh_run", {"host": host, "command": "whoami"})
    assert out["connection"] == "reused"
    assert out["stdout"].strip() == USER


def test_a_failing_remote_command_is_a_result_not_a_tool_error(host):
    """A non-zero exit code from the far side is payload; the tool call succeeded."""
    out = call("ssh_run", {"host": host, "command": "ls /no/such/path"})
    assert "error" not in out
    assert out["rc"] != 0
    assert out["stderr"].strip()


def test_a_session_runs_its_command_and_the_screen_can_be_read(host):
    started = call("ssh_session_start", {
        "host": host, "command": "echo marker-in-the-session"})
    assert "error" not in started, started
    name = started["session"]
    try:
        assert started["started"] is True
        assert name in _sessions(host)
        screen = call("ssh_session_read", {"host": host, "name": name})
        assert "marker-in-the-session" in screen["screen"]
    finally:
        call("ssh_session_kill", {"host": host, "name": name})
    assert name not in _sessions(host)


# --- negative, run for real -------------------------------------------------------

def test_a_denied_command_is_blocked_on_a_host_that_could_have_run_it(host):
    """Negative test 1. A file is put on the host first, so the refusal is checked
    against what is still there rather than against the reply alone."""
    proof = f"/tmp/fd-deny-{os.getpid()}"
    made = call("ssh_run", {"host": host, "command": f"echo alive > {proof}"})
    assert made["rc"] == 0, made

    out = call("ssh_run", {"host": host, "command": f"rm -rf {proof}"})
    assert out["decision"] == "deny"
    assert "error" in out
    assert out["rule"] == DENY_RULE

    # Nothing was sent: the file the command would have removed is still there.
    listed = call("ssh_run", {"host": host, "command": f"ls {proof}"})
    assert listed["rc"] == 0
    assert proof in listed["stdout"]


def test_send_keys_is_blocked_even_though_the_host_runs_tmux(host):
    """Negative test 2. The host has tmux 3.4 and would have obeyed."""
    made = call("ssh_session_start", {"host": host, "command": "echo waiting"})
    name = made["session"]
    try:
        out = call("ssh_run", {
            "host": host,
            "command": f"tmux send-keys -t {name} 'echo broke-in' Enter"})
        assert out["decision"] == "deny"
        assert out["hard_block"] is True

        screen = call("ssh_session_read", {"host": host, "name": name})["screen"]
        assert "broke-in" not in screen
    finally:
        call("ssh_session_kill", {"host": host, "name": name})


def test_an_escalated_command_is_staged_unsent_instead_of_run(host):
    """Negative test 3. The command would leave a file behind if it ran. It does not
    run: it sits at a prompt in a session, waiting for a person to press Enter."""
    marker = f"/tmp/fd-escalate-{os.getpid()}"
    out = call("ssh_run", {"host": host, "command": f"touch {marker}"})

    assert out["decision"] == "escalate"
    assert "error" in out
    session = out["session"]
    try:
        assert session.startswith("fd-")
        assert out["attach"].startswith("ssh ") and session in out["attach"]

        # The command is on the screen, typed at the prompt...
        screen = call("ssh_session_read", {"host": host, "name": session})["screen"]
        assert f"touch {marker}" in screen

        # ...and it did not run: the file it would have created is absent.
        listed = call("ssh_run", {"host": host, "command": f"ls {marker}"})
        assert listed["rc"] != 0
        assert marker not in listed["stdout"]
    finally:
        call("ssh_session_kill", {"host": host, "name": session})


def test_a_staged_session_cannot_be_typed_into_through_any_tool(host):
    out = call("ssh_run", {"host": host, "command": "systemctl restart nothing"})
    session = out["session"]
    try:
        assert not any(name for name in mcp_server.TOOLS if "send" in name)
        screen = call("ssh_session_read", {"host": host, "name": session})
        assert "systemctl restart nothing" in screen["screen"]
    finally:
        call("ssh_session_kill", {"host": host, "name": session})


def test_a_session_this_tool_did_not_create_cannot_be_killed(host):
    """`ssh_session_kill` refuses any name not starting with fd-, and the session the
    refusal was aimed at is still there afterwards."""
    theirs = "someones-shell"
    made = call("ssh_session_start", {"host": host, "command": "echo mine"})
    ours = made["session"]
    try:
        # Create a session the tool does not own, the only way it can be done: the
        # policy blocks `tmux` from ssh_run, so this goes round the tool on purpose.
        from flightdeck.ssh import hosts as hosts_mod, master
        cfg = hosts_mod.get(host)
        master.run(cfg, master.remote_argv(
            ["tmux", "new-session", "-d", "-s", theirs]), timeout=20)
        assert theirs in _sessions(host)

        out = call("ssh_session_kill", {"host": host, "name": theirs})
        assert "error" in out and "fd-" in out["error"]
        assert theirs in _sessions(host)
    finally:
        from flightdeck.ssh import hosts as hosts_mod, master
        master.run(hosts_mod.get(host), master.remote_argv(
            ["tmux", "kill-session", "-t", theirs]), timeout=20)
        call("ssh_session_kill", {"host": host, "name": ours})


# --- the measurement --------------------------------------------------------------

def test_the_probe_reports_both_numbers(host):
    out = call("ssh_probe", {"host": host, "samples": 3})
    assert "error" not in out, out
    assert out["cold_median_ms"] > 0 and out["shared_median_ms"] > 0
    assert len(out["cold_handshake_ms"]) == 3
    assert len(out["shared_socket_ms"]) == 3
    print(f"\ncold handshake {out['cold_median_ms']}ms · "
          f"shared socket {out['shared_median_ms']}ms · "
          f"saved {out['saved_ms']}ms per call")
