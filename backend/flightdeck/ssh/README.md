# `ssh_*` — running commands on another machine, under a policy

Seven tools. Six of them read; one can change something on a remote host, and it is the
one the rest of this file is about.

| Tool | Question it answers |
|---|---|
| `ssh_hosts` | Which machines are declared, and what is each one allowed to do |
| `ssh_run` | Run this one command — or refuse, or hand it to a person |
| `ssh_session_start` | Run this where it survives a closed laptop |
| `ssh_session_list` | What is alive on the host right now |
| `ssh_session_read` | What is on that terminal at this moment |
| `ssh_session_kill` | End a session this tool created |
| `ssh_probe` | What is the shared connection worth on this route |

## What happens to a command

`ssh_run` reads the host's policy from disk, normalises the command, and gets one of
four answers back.

```
allow     run it
escalate  do NOT run it — type it into a tmux session and hand that to a person
deny      refuse; no argument changes it
unknown   read as escalate
```

Order: hard blocks in code → the host's `deny` → the host's `allow` → whatever `mode`
says for everything left over. Hard blocks come first, and that ordering is the guard
itself: run them last and `docker ps | rm -rf /` matches an allow rule anchored at
`^docker (ps|logs|inspect)` and goes straight through.

## Escalate hands the keyboard to a person

The design first sketched an approval queue in the FlightDeck UI. This is simpler and
gives up nothing:

> A command that needs a person is typed into a new tmux session on the host and
> **left at the prompt, unsent**. The reply carries the session name and the exact
> ssh line to attach to it.

```json
{
  "error": "'apk add curl' was not run: no rule matched, so it needs a person. It is
            typed into session fd-esc-20260907-112258-868 on probe, waiting at the
            prompt for a person to press Enter.",
  "decision": "escalate",
  "session": "fd-esc-20260907-112258-868",
  "attach": "ssh -p 22222 -i … -t probe@127.0.0.1 tmux attach -t fd-esc-…"
}
```

The person attaches, reads what is sitting there, and presses Enter — or does not. This
is also the answer for anything that needs a **password typed**: sudo, a key passphrase,
a y/n prompt. The agent never types a password, and never has to: `BatchMode=yes` on
every ssh call means it could not even be asked for one.

Meanwhile the agent can watch, with `ssh_session_read`, which is `capture-pane` and
nothing else. It sees the screen and cannot touch the keyboard.

## Why `tmux` and `send-keys` are blocked in code

Without the block, the whole policy is one string away from useless:

```
ssh_run "tmux send-keys -t fd-esc-… 'rm -rf /' Enter"
```

To a regex that is a command starting with `tmux`. So a session is **approved once, at
the moment it is created**, and is read-only afterwards — and `tmux`, `send-keys`, and
every chaining character (`;` `&&` `||` `|` `$(` backtick) are refused by
`policy.py` on any host not explicitly declared `mode = "allow"`. No config can turn
those off. The domain's own tmux calls are built in code, from a command that already
has a decision.

## Configuration

`~/.flightdeck/hosts.toml`, chmod 600. See `hosts.example.toml` beside this file.

The file holds **no password** — it holds the NAME of an environment variable
(`password_env = "SSH_STAGING_CE_PASS"`), and the value is read from the environment at
call time. A key *path* may sit there; the key does not. `FLIGHTDECK_HOSTS` points the
loader at a different file, which is how the tests aim at a throwaway host.

Nothing here reads or writes `~/.ssh`. Every invocation passes `-F /dev/null`, and the
sockets and known-hosts file live in `~/.flightdeck/cm/`, mode 700.

## The shared connection

Each tool call is a fresh process, so a connection pool in memory would be built and
thrown away every time. `ControlMaster=auto` puts it on disk instead: the first ssh
forks a master holding the authenticated connection, and later processes multiplex onto
it. `ControlPersist=60s` carries it across spawns.

**The connection may live between calls; the decision may not.** The master holds a
socket and none of this code — policy is re-read from disk and evaluated fresh every
single call.

Two things learned by running it:

- A unix socket path stops at **108 bytes** and `%C` expands to 40 characters. A deep
  control directory made every call fail with `ControlPath too long`. Sharing is an
  optimisation, so a path that will not fit now turns it off with a reason attached
  rather than making the host unreachable.
- **tmux 3.4 rewrites a tab inside `-F` output to an underscore**, so a tab-separated
  list format comes back as one unsplittable field. `ssh_session_list` uses a colon,
  which tmux forbids inside a session name.

### Measured, 2026-09-07

Against a throwaway `alpine` + `openssh` + `tmux` container on `127.0.0.1:22222`, five
samples each, `ssh_probe --host probe --samples 5`:

| | ms |
|---|---|
| Cold handshake, no socket to join | 183.7 · 178.2 · 183.2 · 182.2 · 181.0 — **median 182.2** |
| Through an established socket | 7.2 · 7.3 · 7.0 · 6.0 · 6.7 — **median 7.0** |

**175ms saved per call, 26× faster** — and this is loopback, with no network at all
between the two ends. The handshake cost is key exchange and authentication, not
distance, so a real route pays the same 180ms *on top of* its latency. Every call within
60s of the last one avoids it.

That settles what `ControlPersist=60s` is for: the honest reading is not "the window is
60 seconds" but "how many calls fall inside the window", and at 175ms each, a session
doing a handful of calls a minute gets all of it.

## Running the tests

```
cd backend && ../.venv/bin/python -m pytest tests/test_ssh_policy.py tests/test_ssh_mcp.py -q
```

The live tests skip unless a host is named:

```
FLIGHTDECK_SSH_TEST_KEY=/path/to/key \
  ../.venv/bin/python -m pytest tests/test_ssh_live.py -q -s
```

They were written against a throwaway container — `alpine`, `openssh`, `tmux`, a
generated keypair, published on `127.0.0.1:22222` and holding nothing. Three of them are
the negative tests the workspace convention requires: a `deny` command blocked on a host
that would have run it, `send-keys` blocked on a host running tmux 3.4, and an
`escalate` command shown sitting unsent at a prompt with the file it would have created
absent.

## Not in version 1

- **The Postgres ledger of §1.6.** Every reply carries its `decision`, but no row is
  written. The tool that would read such a ledger, `ssh_audit`, is not in this version
  either, so the table would have been written and never read. Worth adding with its
  reader, not before.
- **Password authentication.** `password_env` is resolved and reported, never used:
  every call runs with `BatchMode=yes`. A host that needs a password typed is a host
  whose work goes through the escalate path, where a person types it.
