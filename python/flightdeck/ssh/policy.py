"""Deciding what a command is allowed to do, before anything is sent anywhere.

Pure functions, no I/O, no subprocess: the whole decision is `(host config, command
string) -> decision`. That is what lets the rules be tested without a host, and it is
why the policy can be re-read from disk on every call without anything being cached.

Four decisions, and the fallback is the strict one:

    allow     run it
    escalate  do not run it; stage it in a tmux session for a person to run
    deny      refuse, with no way round
    unknown   treated as escalate

Order of evaluation, and the order matters more than the rules do:

    1. normalise the command
    2. hard blocks, which no config can turn off
    3. the host's `deny` patterns
    4. the host's `allow` patterns
    5. whatever the host's `mode` says for everything left over

Hard blocks come FIRST, before `allow`, and that ordering is the whole guard. Run them
last and `docker ps | rm -rf /` matches the `^docker (ps|logs|inspect)` allow rule and
goes straight through — an allow pattern anchored at the start of the string says
nothing about what follows a pipe.

The two hard blocks:

- **`tmux` and `send-keys`.** A session is approved once, when it is created, and is
  read-only to the agent afterwards. `ssh_run "tmux send-keys -t s 'rm -rf /' Enter"`
  would be a way around every rule above, because to a regex it is just a command
  starting with `tmux`. So the word cannot be sent at all. (This domain's own tmux
  calls are built here in code, from a command that already has a decision; the block
  is on what a caller supplies.)
- **Shell metacharacters** — `;` `&&` `||` `|` `$(` and backticks — on any host that
  is not explicitly `mode = "allow"`. A regex matches one command; it cannot read a
  pipeline. Rather than pretend to parse shell, refuse the shape.
"""
import re

_HARD_BLOCK = (
    (re.compile(r"\btmux\b", re.I), "tmux is blocked in code: a session is approved "
                                    "once when it is created and is read-only after"),
    (re.compile(r"send-keys", re.I), "send-keys is blocked in code: it would put an "
                                     "unchecked command into an approved session"),
)

_METACHARACTERS = (
    (";", ";"),
    ("&&", "&&"),
    ("||", "||"),
    ("|", "|"),
    ("$(", "$("),
    ("`", "`"),
)

# tmux session names this domain is allowed to touch. Also stops a name from being a
# way to smuggle shell into a tmux command line.
SESSION_NAME = re.compile(r"^fd-[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")


def normalize(command: str) -> str:
    """One canonical spelling before any pattern is tried.

    Newlines, tabs and runs of spaces all collapse to a single space, and the ends are
    trimmed. Without this, `docker    ps` and `docker\\tps` are three different strings
    to a regex written with one space, and each near-miss falls out of `allow` into
    `escalate` for no reason a reader could see.
    """
    return re.sub(r"\s+", " ", (command or "").replace("\x00", "")).strip()


def _decision(decision, reason, command, **extra):
    out = {"decision": decision, "reason": reason, "command": command}
    out.update(extra)
    return out


def evaluate(host_cfg: dict, command: str) -> dict:
    """`{decision, reason, command, rule?}`: the policy, in evaluation order."""
    normalized = normalize(command)
    mode = host_cfg.get("mode") or "escalate"

    if not normalized:
        return _decision("deny", "empty command", normalized)

    for pattern, reason in _HARD_BLOCK:
        if pattern.search(normalized):
            return _decision("deny", reason, normalized, hard_block=True)

    if mode != "allow":
        for token, shown in _METACHARACTERS:
            if token in normalized:
                return _decision(
                    "deny",
                    f"{shown!r} makes this a pipeline, and a policy rule matches one "
                    f"command. Send the parts as separate calls, or start a session.",
                    normalized, hard_block=True)

    hit = _first_match(host_cfg.get("deny") or [], normalized)
    if hit is not None:
        pattern, broken = hit
        if broken:
            return _decision("deny", f"deny pattern {pattern} does not compile: "
                                     f"{broken}", normalized, rule=pattern)
        return _decision("deny", f"matches the host's deny rule {pattern}",
                         normalized, rule=pattern)

    hit = _first_match(host_cfg.get("allow") or [], normalized)
    if hit is not None:
        pattern, broken = hit
        if broken:
            # An allow rule that does not compile must not widen anything.
            return _decision("deny", f"allow pattern {pattern} does not compile: "
                                     f"{broken}", normalized, rule=pattern)
        return _decision("allow", f"matches the host's allow rule {pattern}",
                         normalized, rule=pattern)

    if mode == "allow":
        return _decision("allow", "no rule matched, and the host's mode is allow",
                         normalized)
    if mode == "deny":
        return _decision("deny", "no rule matched, and the host's mode is deny",
                         normalized)
    return _decision("escalate", "no rule matched, so it needs a person", normalized)


def _first_match(patterns, normalized):
    """The first pattern that matches, or the first that will not compile.

    A pattern that does not compile is reported rather than skipped: silently dropping
    a broken deny rule is the failure mode this whole module exists to avoid.
    """
    for raw in patterns:
        try:
            if re.search(str(raw), normalized):
                return str(raw), None
        except re.error as e:
            return str(raw), str(e)
    return None
