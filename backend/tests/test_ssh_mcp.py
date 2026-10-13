"""The ssh domain as it appears on the agent surface, with no host to talk to.

These go through `registry.dispatch` with the domain's own TOOLS table passed in, so
the real dispatch path is exercised — error-as-data included — without the domain
having to be wired into `_DOMAINS` first.
"""
import pytest

from flightdeck.agentsurface import registry
from flightdeck.ssh import master
from flightdeck.ssh import mcp_server

EXPECTED = {"ssh_hosts", "ssh_run", "ssh_session_start", "ssh_session_list",
            "ssh_session_read", "ssh_session_kill", "ssh_probe"}


@pytest.fixture()
def config(tmp_path, monkeypatch):
    path = tmp_path / "hosts.toml"
    path.write_text("""
[hosts.example]
hostname = "198.51.100.1"
user = "probe"
mode = "escalate"
allow = ['^uptime$']
deny  = ['\\brm\\s+-rf\\b']

[hosts.notmux]
hostname = "198.51.100.2"
mode = "escalate"
tmux = false
""", encoding="utf-8")
    monkeypatch.setenv("FLIGHTDECK_HOSTS", str(path))
    monkeypatch.setenv("FLIGHTDECK_SSH_DIR", str(tmp_path / "cm"))
    return path


def call(name, args):
    return registry.dispatch(name, args, mcp_server.TOOLS)


# --- the registry contract --------------------------------------------------------

def test_the_seven_tools_are_there_and_every_name_wears_its_prefix():
    assert set(mcp_server.TOOLS) == EXPECTED
    assert all(n.startswith("ssh_") for n in mcp_server.TOOLS)


def test_every_tool_has_a_description_and_a_schema():
    for name, (fn, description, props, required) in mcp_server.TOOLS.items():
        assert callable(fn), name
        assert len(description) > 40, name
        assert isinstance(props, dict) and isinstance(required, list), name
        assert set(required) <= set(props), name


def test_the_schemas_render():
    names = {s["name"] for s in registry.schemas(mcp_server.TOOLS)}
    assert names == EXPECTED


def test_there_is_no_tool_that_types_into_a_session():
    """The read-only rule, checked at the surface rather than in prose."""
    assert not any("send" in n or "write" in n or "keys" in n for n in mcp_server.TOOLS)


# --- refusals are data ------------------------------------------------------------

def test_an_undeclared_host_is_an_error_not_a_crash(config):
    out = call("ssh_run", {"host": "nowhere", "command": "uptime"})
    assert "error" in out and "example" in out["error"]


def test_a_missing_host_argument_is_refused_as_data(config):
    """`host` has no default, so the schema can mark it required and the registry-wide
    signature check stays true. A caller that omits it still gets an error naming the
    argument rather than a crash, and the schema points at ssh_hosts for the names."""
    out = call("ssh_run", {"command": "uptime"})
    assert "error" in out and "host" in out["error"]
    assert "ssh_hosts" in mcp_server.TOOLS["ssh_run"][2]["host"]["description"]


def test_hosts_lists_mode_and_rule_counts_without_credentials(config):
    out = call("ssh_hosts", {})
    example = next(h for h in out["hosts"] if h["name"] == "example")
    assert example["mode"] == "escalate"
    assert (example["allow_rules"], example["deny_rules"]) == (1, 1)
    assert example["password_env"] is None


def test_a_denied_command_never_reaches_the_network(config):
    """The host address is documentation-range and unreachable; a deny that tried to
    connect would hang instead of answering."""
    out = call("ssh_run", {"host": "example", "command": "rm -rf /var"})
    assert out["decision"] == "deny"
    assert "error" in out


def test_send_keys_is_refused_as_a_hard_block(config):
    out = call("ssh_run", {"host": "example",
                           "command": "tmux send-keys -t fd-x 'rm -rf /' Enter"})
    assert out["decision"] == "deny"
    assert out["hard_block"] is True


def test_killing_a_session_this_tool_did_not_create_is_refused(config):
    out = call("ssh_session_kill", {"host": "example", "name": "someones-shell"})
    assert "error" in out and "fd-" in out["error"]


def test_an_escalate_command_on_a_host_without_tmux_is_still_not_run(config):
    out = call("ssh_run", {"host": "notmux", "command": "systemctl restart odoo"})
    assert out["decision"] == "escalate"
    assert "error" in out


def test_an_unknown_ssh_tool_is_reported_as_data(config):
    assert "error" in registry.dispatch("ssh_send_keys", {}, mcp_server.TOOLS)


# --- the shared connection, without connecting ------------------------------------

def test_a_short_control_directory_gets_the_shared_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("FLIGHTDECK_SSH_DIR", "/tmp/fd-cm-short")
    assert master.multiplexing()[0] is True
    assert "ControlMaster=auto" in master.base_options({"port": 22})


def test_a_control_path_over_the_socket_limit_turns_sharing_off_rather_than_failing(
        tmp_path, monkeypatch):
    """A unix socket path stops at 108 bytes and `%C` eats 40 of them. A deep
    directory used to make every call fail with `ControlPath too long`; now it costs a
    handshake per call and says so."""
    deep = tmp_path / ("d" * 60) / ("e" * 60)
    monkeypatch.setenv("FLIGHTDECK_SSH_DIR", str(deep))
    available, why = master.multiplexing()
    assert available is False
    assert "108" in why
    assert "ControlMaster=auto" not in master.base_options({"port": 22})
