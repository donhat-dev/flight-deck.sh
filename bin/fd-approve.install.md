# Installing `fd-approve` as a PreToolUse hook

`bin/fd-approve` is not wired into `~/.claude/settings.json` by this change —
editing that file is left to the operator, on purpose. Add an entry like this
to the `PreToolUse` array (merge with whatever is already there; do not
replace the array):

```json
{
  "matcher": "*",
  "hooks": [
    {
      "type": "command",
      "command": "/home/nathando/Documents/Projects/flight-deck.sh/bin/fd-approve"
    }
  ]
}
```

Use an absolute path (as above), not a relative one — a hook command runs
with an unpredictable working directory.

## Why `matcher: "*"`, not `"Bash"`

The escalation check (`flightdeck.decisions.should_escalate`) matches the
operator-authored regex lines in `~/.flightdeck/approve.conf` against
whichever text the tool call carries — `tool_input.command` for a Bash-shaped
tool, or a JSON dump of `tool_input` for anything else (a file write, an MCP
call). Scoping the hook to `Bash` only would silently exempt every other tool
from ever escalating. A `*` matcher costs nothing for the common case: a tool
call that matches no pattern in `approve.conf` returns from the hook
immediately (see `bin/fd-approve`'s step 1) without ever reaching the network.

## Coexisting with the existing Bash-scoped hooks

The current `PreToolUse` config already runs `dcg`, `git-owner-guard.sh` and
`rtk hook claude` under a `"matcher": "Bash"` entry. Claude Code runs every
matching entry for a given tool call; a `dcg`/`git-owner-guard.sh` **deny**
on a Bash call short-circuits before `fd-approve` would otherwise escalate it
(and adding `fd-approve` changes nothing about that — those guards keep
fail-closing at their own layer, underneath this channel's fail-open timeout,
exactly as `bin/fd-approve`'s own header comment states). Two separate
`PreToolUse` array entries — the existing `"matcher": "Bash"` one, and a new
`"matcher": "*"` one for `fd-approve` — is the natural way to add this without
touching the existing entry.

## `~/.flightdeck/approve.conf`

One regex per line, matched against the tool's command/input text with
`re.search` (Python). `#` starts a comment; blank lines are ignored. The file
does not exist by default — with it missing, `should_escalate` returns an
empty pattern list and **nothing escalates**, so `fd-approve` is a no-op
until this file is created. Example:

```
# Escalate anything that looks like a forced push or a hard reset.
push\s+--force
reset\s+--hard
```

Edits take effect on the next tool call — `should_escalate` reloads on an
mtime change, no FlightDeck restart required (see `decisions.py`'s
`_load_patterns`).

## Runtime requirements

- FlightDeck running at `http://127.0.0.1:8010` (override with `FD_APPROVE_URL`
  for a different port/host, e.g. a non-default `docker compose` mapping).
- `notify-send` at `/usr/bin/notify-send` (verified present on this machine) —
  fired by the hook itself, not by the browser; see the plan this shipped
  under for why (the browser audio/notification layer is a separate track).
- `jq` and `curl` on PATH.
- The repo's `.venv` (`bin/fd-approve` invokes `.venv/bin/python` directly, the
  same way `bin/flightdeck` does).

If FlightDeck is not running, or any HTTP call to it fails, `fd-approve`
allows the tool call immediately — a dead dashboard must never block a
session. See the plan's live negative test for a demonstration.
