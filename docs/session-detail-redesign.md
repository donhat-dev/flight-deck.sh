# Session detail, rebuilt around reading

Status: **built** (2026-08-25). Code under `frontend/src/session/`. Mock:
`~/.pencil/documents/34cfd228-b0b9-43de-83ab-012b50ed839a/pencil-new.pen`
(screens `S1 Reading view`, `S2 System`, `S3 Focus`, `S4/S5 typography`).

What shipped, and where:

| Piece | File |
|---|---|
| Type roles, status colours, ANSI palette, both themes | `session/session.css` |
| SGR parser | `session/ansi.js` (+ tests) |
| Unified diff with folded context | `session/diff.js` (+ tests) |
| Renderer registry | `session/registry.js` (+ tests) |
| terminal · diff · tree · image · table · json · web · widget | `session/renderers.jsx` |
| The card, its six header slots and the fold | `session/ToolCard.jsx` |
| Wiring, reading spine, focus mode, keys | `SessionDetail.jsx` |
| Header, prompts rail, instruments rail, filter chips, keys strip | `SessionDetail.jsx` + `session.css` |
| Per-session tokens and cost for the rail | `GET /api/session/{id}/usage` (+ test) |

The screen is the mock's S1: header with the meta line and the READ / FOCUS
pills, then three columns - prompts (250), stream, instruments (270). The rails
drop out before the stream is ever squeezed: instruments below 1560px, prompts
below 1240px.

Not built yet: `/` search and the `O` key. Flow and Clearance are parked behind
`SHOW_LEGACY_VIEWS = false`; Flow still renders inside the new stream column,
Clearance would need its grid rebuilt against the new layout.

## Renderer coverage, measured

`pickRenderer` run over every tool_use in session `395a72f8` (8,623 calls with a
result). This is the number that says whether the registry is finished:

| Renderer | Calls | Share |
|---|---|---|
| terminal (ANSI) | 4,975 | 57.7% |
| diff | 1,493 | 17.3% |
| table | 906 | 10.5% |
| inline (answer in the header) | 392 | 4.5% |
| code (syntax by extension) | 354 | 4.1% |
| image | 175 | 2.0% |
| snapshot (a11y tree) | 140 | 1.6% |
| tree | 112 | 1.3% |
| todo | 39 | 0.5% |
| json | 35 | 0.4% |
| web | 2 | 0.02% |

98% of the terminal bucket is Bash, which is what terminal is for. The non-Bash
strays are 82 calls in total (SendUserFile, Artifact, AskUserQuestion), so
effectively every call now reaches a renderer chosen for its shape.

Languages actually hit by the code renderer: python 147, markdown 107, xml 45,
plain 34, js 11, json 5, yaml 4, css 1 - which matches the file mix of the
session and is why those three were built first.

## What is wrong with the current view

A session is two documents braided together: the argument, and the work. The
current view renders both at the same size, weight and colour, inside the same
grey collapsible, so neither reads well.

Concretely:

- A tool call shows `🛠 BASH` plus a truncated command. Everything else about it
  (what it touched, how long it took, whether it failed) is behind a click.
- Tool input is `JSON.stringify` and tool output is one `<pre>`. A terminal run,
  a file edit and a search all look identical.
- `Edit` shows old string and new string as two stacked blocks. There is no diff.
- Bash output loses its ANSI colours, which is the one signal the terminal
  already encoded for us.
- Nothing distinguishes prose from thinking from output, so the eye has no
  anchor and long turns read as a grey wall.

## The shape of the new view

Three columns. Prompts rail (left, 250), stream (centre, fluid), instruments
rail (right, 270). `flow` and `clearance` are hidden for now; the code stays.

### 1. Text roles

Five roles, five treatments. This is the whole typographic system.

Shipped values are D-prime, measured rather than estimated - see
`session-detail-typography.md` for how they were arrived at.

| Role | Font | Size / line box | Colour |
|---|---|---|---|
| Prompt | Outfit 500 | 16 / 24px | `--fd-text`, coral left rule |
| Prose | Outfit 400 | 15 / 24px | `--fd-text` |
| Thinking | Outfit 400 upright | 14 / 20px | `--fd-dim`, hairline left rule |
| Mono | IBM Plex Mono 400 | 13.5 / 20px | `--fd-text` / `--fd-dim` |
| Instrument | Plex Mono 600, caps, .18em | 9 / 12px | `--fd-faint` |

Prose, prompt and thinking are capped at 660px (103 characters); tool cards keep
the full width, where 13.5px mono gives 109 columns.

### 2. Colour

Status colours are separate from the brand accent, per the design system:
`ok #3FB950`, `warn #E3B341`, `err #F85149`, `sky #4E93CC` for informational.
Coral stays interaction only: focus ring, active pill, jump target.

ANSI SGR 30-37 maps to a fixed palette tuned for the night ground
(`#6E7681 #FF6B60 #3FB950 #E3B341 #6CB6FF #D2A8FF #56D4DD #F4F3EF`), bright
variants lift by one step. Parse only SGR; drop cursor and erase sequences.

### 3. Tool call anatomy

Header rail, always the same six slots, so the collapsed state is still useful:

1. **Status dot** - the only place colour means state.
2. **Tool chip** - mono caps. An MCP tool shows its server (`FLIGHTDECK`), never
   `mcp__flightdeck__session_search`.
3. **Target** - the one thing acted on: command, path, pattern, url.
4. **Result badge** - fixed slot, tabular mono: duration, then `EXIT 0` /
   `+18 -6` / `7 FILES`.
5. **Body** - drawn by a renderer, not by `<pre>`.
6. **Fold** - body caps at 18 lines with "show the rest".

### 4. Renderer registry

`pickRenderer(toolName, input, result) -> Renderer`. Match on the tool first,
then on the shape of the result. Every renderer is a leaf component with its own
tests.

| Renderer | Chosen for | Shows |
|---|---|---|
| `terminal` | Bash, shell-ish MCP | ANSI-coloured lines, exit code, stderr tinted |
| `diff` | Edit, MultiEdit, Write | unified diff, line numbers, +/- gutter, folded context |
| `tree` | Grep, Glob, path-shaped Bash output | file tree with hit counts (exists today) |
| `image` | `*take_screenshot*`, image results | fixed 320px box so nothing reflows on load |
| `table` | array-of-objects JSON | column-aligned table, tabular nums |
| `json` | any other object | collapsible tree, not a wall of text |
| `web` | WebFetch, WebSearch | title, host, excerpt |
| `agent` | Agent | the nested thread (exists today) |
| `widget` | `fd-widget` envelope | see below |

### 5. Widget envelope (FlightDeck MCP)

Forward-looking, so the MCP can be built against it. A tool result whose first
line is a fenced `fd-widget` block is drawn as a widget; the rest of the result
stays available under "raw".

    ```fd-widget
    {"v":1,"type":"table","title":"session_search","metrics":[{"label":"MATCHES","value":148}],
     "columns":["session","when","hits"],"rows":[["subscription poc","Aug 12",41]]}
    ```

`type` starts as `table | metrics | timeline | diff | chart`. Unknown types fall
back to `json`, so an older deck never breaks on a newer MCP.

### 6. Two reading modes

- **Read** (default): prose full, tool calls folded to their header line.
- **Focus tools**: prose folds to "2 paragraphs hidden", every call opens, and a
  left rail lists what ran in order with duration bars. Same idea as the Claude
  Code extension's focus view, applied to a finished transcript.

Keys, as built: `F` toggle focus, `J`/`K` next/previous tool call, `E` errors
only. `O` (open the call under the cursor) and `/` (search in session) are not
built yet.

## Component inventory to build

Leaves first, each in its own file under `frontend/src/session/`:

`Prose` `Prompt` `Thinking` `ToolHeader` `StatusDot` `ToolChip` `ResultBadge`
`Fold` `AnsiLine` `Terminal` `Diff` `Tree` `ImageBox` `DataTable` `JsonTree`
`WebCard` `Widget` `AgentThread` `ToolCard` `TurnRow` `PromptsRail`
`InstrumentsRail` `ActivityRail` `SessionStream`.

`ToolCard` composes header + renderer + fold and knows nothing about any
specific tool. Adding a tool means adding a renderer and one registry line.

## Order of work

1. Leaves + registry, rendered against fixtures from real sessions (no UI wiring).
2. Swap `Block`'s tool branch to `ToolCard`; keep the old path behind a flag for
   one release.
3. Text roles and the two rails.
4. Focus mode and keys.
5. Widget envelope, once one FlightDeck MCP tool emits it.

Row virtualization, tail-anchored paging and lazy subagents already landed and
are not part of this change.
