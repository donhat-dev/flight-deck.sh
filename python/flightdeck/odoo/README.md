# `odoo_*` — a controlled read/write corridor into live Odoo servers

Seven tools. Four read, three write, and every one of them names the server it talks to.

| Tool | Question it answers | Wire | R/W |
|---|---|---|---|
| `odoo_instances` | Which servers may I talk to, and how far | none | R |
| `odoo_ping` | Who is this server, and does the credential work | XML-RPC | R |
| `odoo_fields` | What does this model look like **on that server** | XML-RPC | R |
| `odoo_search` | What do these records say right now | XML-RPC | R |
| `odoo_create` | Make one record | JSON-RPC | W |
| `odoo_write` | Change records that exist | JSON-RPC | W |
| `odoo_call` | Run a method, as one transaction | JSON-RPC | W |

## Where this sits

`odoo_graph` reads the repository and answers how the code is put together — it cannot
tell you which server actually has a module installed. `Projects/scripts/` runs fixed
read-only probes against the one local stack. These tools are the live corridor: several
named servers, reads and guarded writes, answering about right now.

## Why reads and writes take different wires

Odoo 12 serves XML-RPC with `dumps(..., allow_none=False)`, and it serialises **after**
`dispatch_rpc` has committed. So a method returning `None` — every `action_*` written
the ordinary way — raises `TypeError: cannot marshal None` on a call that already
changed the database. Writes therefore go over `/jsonrpc`, which has no marshalling
layer to break.

`/jsonrpc` has its own trap, and it is the reason this is parsed by hand: a method
returning `None` produces `{"jsonrpc": "2.0", "id": 1}` — **no `result` key and no
`error` key**. `resp["result"]` would raise `KeyError` on exactly the case the switch
exists to fix. That shape is read as success with a null result.

Both live in `transport.execute(..., transport=)`, so collapsing onto one wire later
means changing a default, not rewriting the tools.

Measured on `staging-ce` (12.0), `res.partner.check_access_rule` — returns `None`,
writes nothing:

| Wire | Answer |
|---|---|
| `/jsonrpc` | `{"ok": true, "result": null}` |
| `/xmlrpc/2/object` | `TypeError: cannot marshal None unless allow_none is enabled` |

`tests/test_odoo_live.py` keeps that pair as a test. If the XML-RPC half ever stops
failing, the split can go.

## The write gate

```
layer 0   the tool is absent          there is no odoo_unlink            exit 3
layer 1   per-instance allowlist      model not in write_models          exit 2
                                      or model.method not in write_methods
layer 2   explicit confirmation       confirm is not true                exit 2
```

All three run before anything leaves the machine, which is what lets a refusal say
"nothing changed" and be believed. Past them Odoo's own access rights and record rules
still apply — this layer only narrows.

Three things no configuration can switch on: there is no delete tool and `odoo_call`
refuses `unlink` before consulting the allowlist; an empty allowlist means absolutely
read-only; a **missing** allowlist key also means empty.

A refusal names the change a confirmed call would make. A refusal that only says
"refused" turns the retry into a formality.

## Two limits Odoo will not impose for you

`limit=0` never reaches the server. Zero is falsy in Odoo, so `_search` emits no `LIMIT`
clause and `search_read(limit=0)` returns the **whole table**. `odoo_search` turns it
into a `search_count` and returns only the number. An omitted `limit` means 100, for the
same reason.

## Declaring an instance

Two ways in, and both end at the same object.

**A file** — `~/.flightdeck/odoo.toml`, `chmod 600`, never in git. It carries the *name*
of the environment variable holding the password, never the password, so a copy of the
file leaks nothing. See `odoo.toml.example`.

**Environment variables** — for a shell or a CI job that should leave no file:

```bash
export ODOO_STAGING_CE_URL=http://<staging-host>:<port>
export ODOO_STAGING_CE_DB=<staging-db>
export ODOO_STAGING_CE_USER=admin
export ODOO_STAGING_CE_PASSWORD=...
export ODOO_STAGING_CE_WRITE_MODELS=          # comma separated; empty = read only
```

The name maps both ways: `staging-ce` ↔ `ODOO_STAGING_CE_`, so instance names use dashes
and never underscores.

**Precedence is whole-instance.** If `ODOO_<NAME>_URL` is set, that instance comes
entirely from the environment and any file entry of the same name is shadowed — the two
are never merged field by field, because "where did this password come from" should have
a one-word answer.

There is no `default` key and there will not be one. Two of the declared servers hold
records with the same ids, and a mis-aimed link has already opened an unrelated real
record here with no error at all. A wrong name is a refusal that lists the right ones.

`FLIGHTDECK_ODOO_CONFIG` points the registry somewhere else, which is how the tests get
a throwaway one.

### Password, not API key

Odoo 12 has no API keys — `res.users.apikeys` arrives in Odoo 14 — so a 12.0 server can
only use a login and a password. On Odoo 19 put an API key in the same variable and
nothing else changes: `execute_kw` takes either in the same slot.

### uid is not cached

Every call authenticates again. The password travels with every `execute_kw` regardless,
so a cache would save one round trip in exchange for a state file that can hand a
rebuilt database a uid from the database before it.

## Errors, in five layers

Every shape has `error` at the top level, so the CLI exits 2 and the agent reads it as
data. `layer` says which one:

| `layer` | When | Did anything change |
|---|---|---|
| `config` | unknown instance, unreadable or absent registry | no |
| `guard` | allowlist or `confirm` refused it | no, provably |
| `transport` | host down, wrong port, timeout | unknown |
| `auth` | `uid=False`, missing password variable, `AccessDenied` | no |
| `odoo_acl` | `AccessError`, a record rule | no |
| `orm` | `UserError`, `ValidationError`, constraint, anything else | maybe |

`traceback_tail` keeps at most the last three lines of the server traceback. An
auth-layer error names the environment variable that is missing and never its value; on
top of that every payload is scrubbed of the password on the way out.

**A timeout on a write is an unknown state**, not a rollback. Nothing here can tell "Odoo
never saw it" from "Odoo committed and the answer was lost". Read the records back before
retrying, and keep id lists short.

**There is no transaction across tool calls.** Three calls are three transactions with no
rollback. A flow that must be atomic belongs inside one method reached with `odoo_call`.

## Module layout

| File | Responsibility |
|---|---|
| `config.py` | The instance registry: file, environment, precedence, resolution |
| `transport.py` | Both wires behind one `execute()`, and the error classification |
| `guard.py` | The three gates and the shape of a refusal |
| `mcp_server.py` | The seven tools and the `TOOLS` table |

This domain opens no FlightDeck database table of its own.

## Measured on the real servers

Taken 2026-09-07 against `staging-ce` (the staging database, 12.0, shared, near-empty data).

| Claim | Command | Output |
|---|---|---|
| The credential works and the server is 12.0 | `odoo_ping --instance staging-ce` | `uid: 2`, `version: "12.0"`, `read_only: true` |
| Reads come back as records | `odoo_search --instance staging-ce --model res.partner --limit 5` | `count: 5` |
| `limit=0` counts instead of dumping the table | `odoo_search --instance staging-ce --model res.users --limit 0` | `{"count": 15630}` — no `records` key |
| A write outside `write_models` is refused, and nothing moved | `odoo_write ... --confirm true` | `layer: "guard"`, and `write_date` read straight off XML-RPC is unchanged |
| A mistyped instance name lands nowhere | `odoo_write --instance staging-cee ...` | `layer: "config"`, `known: ["staging-ce"]` |
| There is no delete tool | `flightdeck odoo_unlink ...` | exit `3` (unknown tool) |

## Not built, and why

- **`odoo_groups` (`read_group`).** Odoo 12 has `read_group`, Odoo 19 renamed it
  `formatted_read_group`. That needs a version branch; it waits for a real need.
- **Cross-instance fan-out.** One call touching several servers is exactly the ambiguity
  the no-default rule exists to remove.
- **Anything that keeps state between calls** — approval tokens, connection pools, async
  task handles. Each call is a fresh process; there is nowhere for them to live.
- **A ledger row per call.** §1.6 of the domains design wants one; it is shared plumbing
  across the three domains rather than this one's to invent.
