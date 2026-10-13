# `memory_*` — four read-only views over the auto-memory store

Claude Code keeps a per-project memory store at
`~/.claude/projects/<sanitised-cwd>/memory/`: one markdown file per fact, plus a
`MEMORY.md` index. The index is the **only** part injected into a session's system
prompt; recall of anything else is the model deciding to `Read` a file. So the store
drifts in ways nothing reports — a file absent from the index is permanently invisible,
a renamed memory leaves broken `[[links]]` behind it, and a claim outlives the session
that produced it.

These four tools report that drift. **None of them writes to the store.** The only
thing that writes is a git repository kept *beside* the store, never inside it.

| Tool | Question it answers |
|---|---|
| `memory_lint` | Is this store still trustworthy, and what needs doing |
| `memory_search` | What did I write about X, when the summary line does not say so |
| `memory_graph` | How do these connect, and where is the graph torn |
| `memory_history` | What did this memory say before, and why did it change |

## Why the git repository lives outside the store

`memoryScan.ts` in the harness scans the memory directory with
`readdir(dir, {recursive: true})` and filters on **nothing but the `.md` extension** —
hidden directories included. A `.git/` placed inside would be walked on every scan, and
any `.md` that ever landed under it would be adopted as a memory.

So the repository sits at `<project>/memory.git` and is reached with
`--git-dir`/`--work-tree`. The scanned directory keeps only its memories.

Restores go through `git show <ref>:<file>`, never `git checkout --`: the local command
guard rejects checkout-discard, and reading a blob out cannot destroy an uncommitted
edit anyway.

## Measured on the real store

Every row is a command you can re-run. Taken 2026-09-07 against
`/home/nathando/Documents/Projects`.

| Claim | Command | Output |
|---|---|---|
| The store holds 38 memories | `flightdeck memory_lint --cwd <proj>` | `total_memories: 38` |
| Eight drift kinds, counted | same | `{"no_source": 17, "dead_origin": 18, "not_in_index": 1, "broken_link": 3, "not_linked": 3, "future_target": 1}` |
| 34 of 38 memories have never been read in full | same | `never_read: 34 of 38` |
| One file is invisible to recall | same, `not_in_index` | `research-subagents-use-cheap-models` |
| Three links point at names that do not exist | `flightdeck memory_graph --cwd <proj>` | `missing: ['artifact-genre-and-style-conventions', 'playwright-vs-agent-browser-for-odoo', 'workers-0-isolates-the-running-odoo']` |
| 38 nodes, 48 edges, one real hub | same | `38 nodes, 48 edges` · `top hub: nakivo-local-docker-setup 6 in` |
| Search finds what the index does not | `flightdeck memory_search --query worktree --limit 3 --cwd <proj>` | `matches 26 in 6 memories`; all three top hits matched in the body only |
| A refusal is data, not a crash | `flightdeck memory_history --name nope --cwd <proj>` | exit `2`, `{"error": "no memory named 'nope' ..."}` |
| There is no delete tool | `flightdeck memory_delete` | exit `3` (unknown tool) |

### What moved since the design was written (2026-08-28)

The design doc recorded the store at 37 memories, 3 unlinked, 10 dead origins. Three
numbers moved in ten days, and each has a cause worth knowing:

- **37 → 38 memories.** `pencil-icons-and-cross-axis-collapse` was saved after the
  measurement.
- **Unlinked stayed at 3, but the membership changed.** The new memory links to
  `pencil-absolute-children-do-not-render`, which rescued it from the isolated set;
  `claude-usage-cap-and-fable-fallback` took its place, because an alias link pointing at
  something that does not exist is not a connection. Both views agree on the same three
  names: `claude-usage-cap-and-fable-fallback`, `research-subagents-use-cheap-models`,
  `wvt-facts-doc`.
- **10 → 18 dead origins.** Not the store's doing: the transcript count fell from **69
  to 38**. Nearly half the store now points at a session that no longer exists, up from
  just over a quarter. Provenance decays faster than memories do, which is the whole
  argument for recording a checkable `source` rather than leaning on `originSessionId`.

## Setting up version history

Once per store:

```bash
flightdeck memory_history --init true --cwd /path/to/project
```

That creates `<project>/memory.git` and takes the first snapshot.

Note the explicit `true`. The CLI parses `--key value` pairs, so a bare `--init` would
swallow the next flag as its value. This applies to every boolean argument on every
tool, not just this one.

Then wire the Stop hook so each turn is captured, by adding to
`~/.claude/settings.json`:

```json
"Stop": [{"matcher": "*", "hooks": [{"type": "command",
  "command": "/path/to/flight-deck.sh/.venv/bin/python -m flightdeck.memory.hook"}]}]
```

The hook always exits `0`. A missing store, a missing repository and a git error all
come back as `{"committed": false}` — a hook that fails must never be the reason a
session stops.

## Module layout

| File | Responsibility |
|---|---|
| `paths.py` | Resolve the store from a cwd, mirroring the harness's own rule |
| `store.py` | Parse frontmatter, body links, and the index. The only file that knows the format |
| `usage.py` | Count how often each memory was read, out of the transcripts |
| `lint.py` | The eight rules |
| `search.py` | Full-text search with one-hop neighbour expansion |
| `graph.py` | Nodes, edges, degrees, ghost targets |
| `vcs.py` | The sidecar git repository |
| `history.py` | `memory_history`, on top of `vcs` |
| `hook.py` | Stop hook |
| `mcp_server.py` | The `TOOLS` table |

`store.py` imports none of the others. Everything else sits on top of it, so a format
change lands in exactly one place.

## Not built yet, and why

- **`memory_add`** — the harness stamps `node_type`/`originSessionId`/`modified` by
  intercepting its own Write tool. A write from a subprocess is invisible to it, so
  those fields would likely go unstamped. Needs a real experiment before a design.
- **A derived search index** (SQLite FTS, local embeddings). `search` scans the files
  directly; at this size that costs nothing. When it stops being free, the index goes in
  as a **disposable derived artifact** — markdown stays the original.
- **A `tier` rule.** `store.py` already parses the field; no rule uses it yet. It is
  read ahead of time so a future "claim exceeds its recorded evidence tier" check can be
  added without touching `store`.
