"""The policy rules, and the credential rule, with no host anywhere.

`policy.evaluate` is a pure function of (host config, command string), which is exactly
why these run in milliseconds and need nothing running. The live half — proving a
blocked command really is blocked on a real host — is in `test_ssh_live.py`.
"""
import pytest

from flightdeck.ssh import hosts as hosts_mod
from flightdeck.ssh import policy

HOST = {
    "name": "example",
    "mode": "escalate",
    "allow": [r"^docker (ps|logs|inspect)\b", r"^(ls|cat|df|uptime)\b"],
    "deny": [r"\brm\s+-rf\b", r"\bshutdown\b"],
}


def verdict(command, **over):
    return policy.evaluate({**HOST, **over}, command)


# --- normalisation ----------------------------------------------------------------

def test_whitespace_is_collapsed_before_anything_is_matched():
    assert policy.normalize("  docker    ps \n") == "docker ps"
    assert policy.normalize("docker\tps\n-a") == "docker ps -a"


def test_a_command_spaced_oddly_still_matches_its_allow_rule():
    """Without normalisation this falls out of allow and escalates for no visible
    reason — the near-miss the design named."""
    assert verdict("docker\t\tps   -a")["decision"] == "allow"


def test_the_normalised_form_is_what_comes_back():
    assert verdict("  uptime  ")["command"] == "uptime"


# --- the four decisions -----------------------------------------------------------

def test_a_command_matching_allow_is_allowed():
    out = verdict("docker ps -a")
    assert out["decision"] == "allow"
    assert out["rule"] == r"^docker (ps|logs|inspect)\b"


def test_a_command_matching_deny_is_denied():
    out = verdict("rm -rf /var/lib")
    assert out["decision"] == "deny"
    assert out["rule"] == r"\brm\s+-rf\b"


def test_a_command_matching_nothing_escalates():
    assert verdict("systemctl restart odoo")["decision"] == "escalate"


def test_an_unknown_mode_is_treated_as_escalate():
    cfg = hosts_mod._normalise("odd", {"mode": "whatever"})
    assert cfg["mode"] == "escalate"
    assert policy.evaluate(cfg, "systemctl restart odoo")["decision"] == "escalate"


def test_mode_allow_lets_an_unmatched_command_through():
    assert verdict("systemctl restart odoo", mode="allow")["decision"] == "allow"


def test_mode_deny_refuses_an_unmatched_command():
    assert verdict("systemctl restart odoo", mode="deny")["decision"] == "deny"


def test_an_empty_command_is_denied():
    assert verdict("   ")["decision"] == "deny"


# --- order of evaluation ----------------------------------------------------------

def test_deny_is_read_before_allow():
    """`ls` is allowed and `rm -rf` is denied; a command that is both must be denied."""
    out = verdict("ls && rm -rf /", mode="allow")
    assert out["decision"] == "deny"


def test_a_pipeline_cannot_ride_in_on_an_allow_rule():
    """The hole the ordering closes: the allow rule is anchored at the start, so it
    matches `docker ps | ...` and says nothing about what follows the pipe."""
    out = verdict("docker ps | rm -rf /")
    assert out["decision"] == "deny"
    assert out["hard_block"] is True


@pytest.mark.parametrize("command", [
    "uptime; rm -rf /",
    "uptime && rm -rf /",
    "uptime || rm -rf /",
    "uptime | tee /tmp/x",
    "echo $(rm -rf /)",
    "echo `rm -rf /`",
])
def test_every_chaining_character_is_blocked_on_an_escalate_host(command):
    assert verdict(command)["decision"] == "deny"


def test_chaining_is_allowed_only_on_a_host_explicitly_declared_allow():
    assert verdict("uptime | head", mode="allow")["decision"] == "allow"


# --- the hard blocks --------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "tmux send-keys -t s 'rm -rf /' Enter",
    "tmux ls",
    "TMUX=1 tmux attach",
    "something send-keys extra",
])
def test_tmux_and_send_keys_are_blocked_in_code(command):
    out = verdict(command)
    assert out["decision"] == "deny"
    assert out["hard_block"] is True


def test_a_host_cannot_configure_its_way_past_the_tmux_block():
    out = verdict("tmux ls", mode="allow", allow=[r"^tmux\b"], deny=[])
    assert out["decision"] == "deny"
    assert out["hard_block"] is True


# --- fail-closed on bad configuration ---------------------------------------------

def test_a_deny_pattern_that_does_not_compile_denies():
    out = verdict("uptime", deny=["(unclosed"])
    assert out["decision"] == "deny"
    assert "does not compile" in out["reason"]


def test_an_allow_pattern_that_does_not_compile_widens_nothing():
    out = verdict("uptime", allow=["(unclosed"], deny=[])
    assert out["decision"] == "deny"


# --- session names ----------------------------------------------------------------

@pytest.mark.parametrize("name,ok", [
    ("fd-esc-20260907-101010-001", True),
    ("fd-build", True),
    ("build", False),
    ("", False),
    ("fd-x; rm -rf /", False),
    ("fd-$(whoami)", False),
    ("../fd-x", False),
])
def test_only_fd_prefixed_simple_names_are_ours(name, ok):
    assert bool(policy.SESSION_NAME.match(name)) is ok


# --- credentials ------------------------------------------------------------------

def _write_config(tmp_path, body):
    path = tmp_path / "hosts.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_config_names_the_variable_and_the_environment_holds_the_value(
        tmp_path, monkeypatch):
    monkeypatch.setenv("FLIGHTDECK_HOSTS", str(_write_config(tmp_path, """
[hosts.example]
hostname = "10.0.0.1"
user = "root"
password_env = "EXAMPLE_PASS"
mode = "escalate"
""")))
    monkeypatch.setenv("EXAMPLE_PASS", "hunter2")
    cfg = hosts_mod.get("example")
    assert cfg["password_env"] == "EXAMPLE_PASS"
    assert hosts_mod.credential(cfg)["password_available"] is True


def test_the_password_value_never_appears_in_what_a_tool_returns(
        tmp_path, monkeypatch):
    monkeypatch.setenv("FLIGHTDECK_HOSTS", str(_write_config(tmp_path, """
[hosts.example]
hostname = "10.0.0.1"
password_env = "EXAMPLE_PASS"
""")))
    monkeypatch.setenv("EXAMPLE_PASS", "hunter2")
    shown = hosts_mod.public(hosts_mod.get("example"))
    assert "hunter2" not in repr(shown)
    assert shown["password_env"] == "EXAMPLE_PASS"
    assert shown["password_available"] is True


def test_an_unset_variable_is_reported_as_unset_not_as_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("FLIGHTDECK_HOSTS", str(_write_config(tmp_path, """
[hosts.example]
hostname = "10.0.0.1"
password_env = "NOT_SET_ANYWHERE"
""")))
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    assert hosts_mod.credential(hosts_mod.get("example"))["password_available"] is False


def test_a_host_with_no_declared_mode_escalates(tmp_path, monkeypatch):
    monkeypatch.setenv("FLIGHTDECK_HOSTS", str(_write_config(tmp_path, """
[hosts.example]
hostname = "10.0.0.1"
""")))
    assert hosts_mod.get("example")["mode"] == "escalate"


def test_an_undeclared_host_is_an_error_naming_what_is_declared(tmp_path, monkeypatch):
    monkeypatch.setenv("FLIGHTDECK_HOSTS", str(_write_config(tmp_path, """
[hosts.example]
hostname = "10.0.0.1"
""")))
    with pytest.raises(KeyError) as e:
        hosts_mod.get("nope")
    assert "example" in str(e.value)


def test_a_missing_config_file_is_an_empty_table(tmp_path, monkeypatch):
    monkeypatch.setenv("FLIGHTDECK_HOSTS", str(tmp_path / "absent.toml"))
    assert hosts_mod.load() == {}
