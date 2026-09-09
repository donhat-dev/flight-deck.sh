# Odoo 19 as FlightDeck's base application — what the experiment found

Built and measured 2026-09-08 against the live ledger. Scope was logbook, treasure and
spend; the module is `flight_deck`, the stack is `docker/odoo19/`, the import is
`odoo/migration/import_ledger.py`.

## What was measured

Import, one pass over the live ledger, batches of 1000 through the ORM:

| phase | rows | seconds |
|---|---:|---:|
| projects | 267 | 0.2 |
| sessions | 2,535 | 0.2 |
| messages | 103,905 | 8.4 |
| transcript lines | 83,778 | 8.2 |
| tool calls | 52,196 | 3.3 |
| treasures | 119 | 0.0 |
| rendered artifacts | 119 | 0.8 |
| session totals | 2,535 | 0.0 |

240,000 records in about 21 seconds. The ORM was not the bottleneck anyone expected it
to be, and the number is worth keeping: the usual reason given for keeping analytics out
of Odoo does not hold at this size.

Counts and money, both sides:

| claim | ledger | Odoo |
|---|---|---|
| messages | 103,915 | 103,915 |
| transcript lines | 83,789 | 83,788 |
| tool calls | 52,202 | 52,201 |
| treasures | 119 | 119 |
| total cost | $35,893.993849 | $35,894.014549 |

The one-row gaps are the ledger ingesting while the check ran. The cost gap is
0.00006 %: per-row rounding into a 6-decimal column against a running float sum.
160 messages are unpriced, which is the honest answer for `<synthetic>` and the two
`gpt-*` model IDs — no rate row matches them, and none was invented.

## Driven in a browser

Every screen was operated, not just loaded. Playwright against the real instance,
screenshots in `devtools_mcp_trace/flightdeck-odoo19/`, **zero page errors** across all runs.

| what was done | what came back |
|---|---|
| Spend pivot opens | first cell rendered in 0.3 s, nine model columns |
| Group the pivot by project | 269 rows |
| Add the Tokens measure | header goes Cost → Cost, Tokens per column |
| Filter to Unpriced | exactly `<synthetic>`, `gpt-5.5`, `gpt-5.6-sol` remain, all at 0.000000 |
| Spend list, footer sums | column sums render |
| Group the logbook by project | 16 groups; grand totals 103,967 messages, 52,228 tool calls, $35,902.32 — the same numbers the message table gives |
| Open a session, read its transcript | 1-100 / 4944, first line seq 3 |
| Page the transcript | 101-200 / 4944, first line seq 316 |
| Unfold a tool turn | the tool input appears under the turn |
| Session tabs | 40 tool call rows, 40 message rows |
| Treasure kanban | two status columns, Draft and Published |
| Open a treasure | artifact frame 1334×640 renders the real page, its own title |
| Taller | frame height 640 → 1280 |
| Model prices | 14 rows, two of them `claude-sonnet-5`: $3/$15 open-ended, and $2/$10 ending Sep 1 |

Two selector notes for anyone rerunning it: Odoo 19's pivot table has no `o_pivot_table`
class (wait on `.o_pivot td.o_pivot_cell_value`), and the transcript's own Next button must
be scoped inside `.fd_transcript` or the click lands on the form pager instead.

## Where native Odoo was enough, and where it won

**Spend is the clear win.** The pivot groups cost by day and model, switches measures,
and drills down, with no code behind it. The graph view then produced the stacked bars
plus cumulative line that FlightDeck builds by hand in React. This screen is cheaper to
own on Odoo than on the current stack, not more expensive.

**Kanban, list, search and the group-by menu were free** and needed nothing written.

**The statbutton form header** covers the session summary FlightDeck renders as custom
cards.

## What native could not do — one line per component written

| component | what native could not do |
|---|---|
| `fd_transcript` | A session is interleaved user, assistant and tool turns, up to 4,944 lines. A one2many list renders rows, not a transcript, and loading them all into the form is not viable. The component paginates 100 at a time and folds tool input. |
| `fd_artifact` | An `Html` field sanitizes what it stores, and an artifact is a whole page with its own styles and embedded fonts. The page is served from `ir.attachment` through a controller into a sandboxed frame. |

Nothing else was written. Spend stayed on the native pivot, and that decision held.

## What Odoo 19 costs you, found by hitting it

- **`res.groups.category_id` is gone.** Odoo 19 puts the category on a
  `res.groups.privilege` record and the group points at that.
- **`_sql_constraints` is gone.** Constraints are class attributes now:
  `_uuid_uniq = models.Constraint("unique(uuid)", "…")`.
- **A search view `<group>` takes neither `expand` nor `string`.** Both fail RelaxNG
  validation, and the message names the attribute rather than the view.
- **`fields.Integer` is `int4`.** A session's cached-token total reaches 3.2 billion and
  overflows on the way in. Those four columns are `Float` with zero decimals.
- **`NULLS LAST` is allowed in `_order`**, and is needed: sessions with no messages have
  no start time, and a plain `DESC` puts them at the top of the logbook.
- **A crashed first install leaves the auto-install pass unfinished, permanently.** Odoo
  installs `auto_install` modules at the end of `load_modules`; a data-file error before
  that point aborts the run, and later `-i <module>` runs never reconsider them. The
  database then runs without `html_editor`, and `web.assets_frontend` fails to compile —
  `Undefined variable: "$black"` — so every page outside the backend renders unstyled with
  "Style error. The style compilation failed." The backend keeps working, which is what
  makes it easy to miss. Fix: `-i html_editor`, then `-u base` to let the auto-install pass
  finish. The check that proves it is `select name from ir_module_module where
  auto_install and state != 'installed'` against a clean install of the same image.
- **The image entrypoint does not read the config file** when it waits for Postgres. Host,
  port, user and password have to be in the environment as well.

## What is not in this experiment

- **Live ingest.** The module reads a ledger; it does not read `~/.claude/projects`. That
  is host-side and would need its own mount plus a cron or a queue job.
- **The React deck.** Pulse, missions, systems, radar, the hub and the MCP surface were
  not ported and were never in scope.
- **The treasures pipeline.** Odoo displays the rendered page; wrapping, linting,
  rendering and publishing all still live in the FastAPI service.
- **Session totals do not follow edits.** They are written by the import. The form carries
  a Recompute totals button, and that is the whole of the correction path.

## What driving it found in the data

Grouping the logbook by project showed 2,440 sessions with no project and no cost. The
cause is in the ledger's shape, not in the module: **only 95 sessions carry token rows,
while 2,347 carry transcript lines and no usage at all**, and 93 are titles with nothing
attached. Reading a project from `messages` alone therefore left most of the library
ungrouped and undated.

The import now takes a session's project from whichever of the three child tables has one,
and falls back to the transcript for the start and end time. What remains unattributed is
the 93 sessions that genuinely hold nothing.

## A second display, tried: the transcript as chatter

One session — 432, `Mock pencil layout side nav bar`, 382 lines — was written into
`mail.message` so the chatter renders it, with the existing widget kept in its tab so the
two can be read side by side. `odoo/migration/transcript_to_chatter.py` does it; it clears
what it wrote before, so a session never ends up with two copies.

382 messages created in **0.3 s**. 312 of them carry a tool block.

| what was done | what came back |
|---|---|
| Open session 432 | chatter renders, 30 messages, Load More brings 30 more each click |
| Click through Load More | 60, 90, 120 — both authors appear, `User` and `Claude` |
| Open a tool block | `<details>` survives Odoo's sanitizer and folds; the class survives too |
| Transcript tab | still renders, 1-100 / 382, unchanged |
| Page errors | 0 |

**What the chatter gives for free:** avatars per speaker, a date separator, search over the
thread, reactions, permalinks per turn, and paging that already handles a long thread.

**What it costs, and both are real.** The order is newest first, which is backwards for a
transcript and is how the chatter is built. And `<pre>` inside a chatter bubble is narrow:
the same turn that the widget shows across the full sheet width wraps into a column here.

Two traps worth writing down:

- **`mail.thread` owns the name `message_ids`.** The session model had its own
  `message_ids` one2many pointing at `flightdeck.message`; inheriting `mail.thread`
  silently redefined it, so the chatter would have read token rows instead of messages.
  The only sign was a label-collision warning during the update. Renamed to `usage_ids`.
- **A turn that only ran a tool has no prose**, and an empty `<pre>` renders as a blank box
  above the fold. Emit the text block only when there is text.

**Reading:** for a long transcript the widget still wins on width and direction; the chatter
wins on everything that surrounds a message. The honest end state is not one or the other —
it is the widget's rendering inside the chatter's furniture, which is more work than this
experiment set out to do.

### Using the mixin, not only the message table

The role became a real `mail.message.subtype` — `User turn` and `Assistant turn`, both with
`res_model = flightdeck.session` so they never appear in another model's subscription
settings, and both with `default` off so nobody is subscribed to a transcript by accident.
The chatter topbar gained two toggles that hide one side of the conversation.

| what was done | what came back |
|---|---|
| Subtype per role on session 432 | 26 `User turn`, 356 `Assistant turn` |
| Load the whole thread | 382 messages |
| Hide Claude | 26 visible |
| Hide User | 356 visible |
| Clear both | 382 visible |
| Open a `res.partner` chatter | the two toggles are absent; that chatter is untouched |
| Page errors | 0 |

Four things this direction taught, and the first two cost time:

- **`registerThreadAction` does not reach the chatter.** The `mail.thread/actions` registry
  serves Discuss and chat windows; the chatter builds its topbar in its own `mail.Chatter`
  markup. A registered action simply never appears, with no error. The way in is template
  inheritance on `mail.Chatter` plus a patch on `Chatter.prototype`.
- **`mail.message.subtype.name` is a translated field**, so branching on it in the browser
  breaks under any other language. The role travels instead as a class on the turn's own
  body, which is data we write.
- **`Message.attClass` is the clean extension point.** It returns a class-to-boolean object,
  so a patch can add a role class and a hidden class without touching a template, and OWL
  re-renders because the components subscribe to the shared state through `useState`.
- **A subtype `description` prints under every bubble.** With the author already saying who
  spoke, it read as the same word twice, so the descriptions were dropped. The subtypes keep
  their real jobs: scoping to the model, and being the thing a role filter means.

**The limit, and it is the important one.** The filter acts on what the chatter has loaded,
30 at a time, not on the thread. A role count is a count of what is on screen. Filtering the
fetch would need a domain the chatter does not accept — which is the same wall the direction
hits everywhere: the chatter is generous with what surrounds a message and closed about
which messages it asks for.

### The same session as a Discuss channel

Session 432 also exists as a `discuss.channel` — 382 messages posted in 0.3 s by
`odoo/migration/session_to_channel.py`. The channel carries a `session_id`, so what makes it
a transcript is a field, not a naming convention, and the session form has an **Open in
Discuss** button.

| what was done | what came back |
|---|---|
| Open Discuss | the channel is in the sidebar beside `general` and `Administrators` |
| Open the channel | reads oldest first, top down; thread **1249 px** wide against the chatter's column |
| Role toggles | present in the Discuss topbar, and absent on `general` |
| Hide Claude | 3 visible of the 30 loaded — the user turns |
| Hide User | 57 visible of the 60 loaded |
| Message counts, in SQL | 26 `User turn`, 356 `Assistant turn` |
| Page errors | 0 |

**What is not proven.** The browser numbers are a window, not a total: Discuss loads 30 at a
time from an intersection observer, and no synthetic scroll, wheel or key press drove it past
60 messages. A person scrolling would very likely get there; this did not show it. The 26 /
356 / 382 split above is SQL, not the screen.

Four things this cost, and they are the reusable part:

- **`registerThreadAction` works here** — the same registry that never reaches the chatter is
  exactly right for Discuss. One filter, two mechanisms, and the split is not a choice.
- **An action's `name` computed from your own state goes stale.** The toolbar re-renders on
  the framework's action state, not on state an action closes over, so a "Hide / Show" label
  kept saying Hide after hiding. A fixed label plus `displayActive` is the honest shape.
- **`toggle: true` needs both `open` and `close`.** With only `open`, the second click calls
  `close`, the state never flips back, and the filter jams on.
- **`odoo shell` runs as OdooBot.** `_add_members(users=env.user)` puts the channel in a
  sidebar no person ever opens; the script adds every internal user instead.

**Reading, against the chatter.** Discuss wins the two things the chatter lost — full width
and chronological order — and adds search within the thread. It loses the point of the
logbook: cost, tokens and tool calls are on the session form, and Discuss is a different
screen. Neither surface is both.

### How Odoo decides what goes in the side pane

The width problem the chatter section ends on has a mechanism behind it, and the mechanism is
open. Odoo compiles a form's arch through a registry of **form compilers**, each keyed by a CSS
selector matched against the arch itself rather than the rendered DOM:

```js
registry.category("form_compilers").add("chatter_compiler", { selector: "chatter", fn: ... });
registry.category("form_compilers").add("attachment_preview_compiler", {
    selector: "div.o_attachment_preview", fn: ...,
});
```

So a bare `<div class="o_attachment_preview"/>` written in a form arch is not a div. The
compiler finds it and swaps it for the `AttachmentView` component. The class IS the API.

Placement is then decided at render time by `FormRenderer.mailLayout()`, from three inputs —
whether the screen is at least `SIZES.XXL`, whether the record actually carries an attachment,
and whether a popout window is open — returning one of six layouts:

| layout | where the chatter goes | where the side pane goes |
|---|---|---|
| `SIDE_CHATTER` | aside, class `o-aside` | — |
| `BOTTOM_CHATTER` | under the sheet, full width | — |
| `COMBO` | **inside the sheet, full width** | the attachment, aside |
| `EXTERNAL_COMBO` / `_XXL` | bottom / aside | a separate browser window |
| `NONE` | not rendered | — |

Two consequences worth keeping.

**The full-width chatter already exists**, and it is `COMBO`: as soon as something else claims
the side pane, the chatter moves into the sheet at full width. The earlier finding said the
narrow column would need frontend work to fix; it needs a side-pane occupant instead.

**A custom side pane is a registry entry, not a patch.** Any module can add a compiler for its
own marker class and mount its own component where the attachment preview would have gone. Two
conditions the compiler enforces: the arch must have a `.o_form_sheet_bg`, and the chatter hook
must not already sit inside `.o_form_sheet`. A debounced resize listener re-renders the form, so
the layout follows the window.

### A transcript box in the side pane

The mechanism above is what the last piece uses. `<div class="o_fd_transcript"/>` in the
session arch is compiled by a registry entry of ours into `TranscriptBox`, an OWL chat box that
reads `flightdeck.message.text` directly. The same component backs the `fd_transcript` field
widget, so the notebook tab and the side pane cannot drift apart.

A turn's prose is markdown, and it is rendered as markdown; a tool call is not, and stays raw
in a `pre`. Odoo ships no markdown library, so the box carries a small renderer for the subset a
Claude Code transcript actually contains — headings, emphasis, inline and fenced code, lists,
tables, quotes, rules, links. It escapes the source **before** emitting any tag, so the output
can hold no markup the transcript did not spell out and needs no sanitizer behind it; a link
target is checked separately, because escaping an attribute value does not make `javascript:`
safe.

Nothing inside a turn scrolls. A turn grows to whatever height its answer needs and the box
scrolls past it; only tool input keeps a cap, because it is machine output opened to be glanced
at. Wrapping needed three things that a chat column makes easy to miss: `min-width: 0` on every
flex ancestor, since a flex item will not shrink below its content and pushes a long path off
the edge instead of breaking it; `overflow-wrap: anywhere` on prose and inline code, because a
file path has nowhere a normal break may go; and `display: block` on a markdown table, so a wide
one scrolls by itself rather than widening the turn around it.

It opens at the last turn, the way a chat window does. Rows are held newest first and the
scroll container is `column-reverse`, so they paint bottom up: the newest sits at the bottom,
the view starts there, and older turns load on scrolling up without the reading position
jumping.

The layout comes from asking for `COMBO` — Odoo reaches it only when a record carries a real
attachment, and a transcript is not an attachment, so the session form requests it by name. The
answer stays `BOTTOM_CHATTER` below the XXL breakpoint, which is how the attachment viewer
behaves too.

| what was done | what came back |
|---|---|
| Open session 432 | side pane 607×929 in a 1050 px viewport, **page overflow 0** — the box scrolls, the form does not |
| Order | newest at the bottom: seq 2132 there, 1716 at the top of the same window |
| On open | `scrollTop` 0 — the newest end — with 3202 px of transcript above it |
| Scrolling up | 60 → 120 → 180 turns, loaded as the top comes near |
| Role chips | shared with the chatter and Discuss toggles; hiding Claude leaves 3 of the 120 loaded |
| Composer | present and disabled — a transcript is a record, there is nothing to send |
| User turns | indented 40 px, so the two sides read apart without colour alone |
| Pane width | 1080 px of 1800 — 60 vw |
| Markdown | across 16 bodies: 11 headings, 66 bold spans, 102 code spans, 7 list items, 2 tables; **0** bodies still opening with a raw marker |
| Turn height | uncapped — the tallest measured 5194 px in a 1050 px viewport, and the box is the only thing that scrolls |
| Wrapping | across 420 loaded turns and 9 tables, **no element overflows horizontally** |
| Tool input | still raw and still capped, and only when opened |
| Notebook tab | same component, 70 vh tall |
| Chatter | full width, inside the sheet |
| `res.partner` form | chatter still aside, no pane of ours |
| Page errors | 0 |

One trap, and it cost a round: adding `o_attachment_preview` to the compiled hook is what makes
mail's own `FormCompiler` patch treat it as the side pane — but that patch also stamps
`t-if="mailLayout(true).includes('COMBO')"` on it. Return anything else from `mailLayout` and
the pane compiles, mounts nothing, and reports no error.

### Live turns, on the transport Odoo already runs

A transcript that only ever grows wants to append while someone is reading it. Odoo's own
answer is `bus.bus`, and the decisive fact was in this instance's log before anything was
written: **155 websocket upgrades already answered `101`**, with `workers = 0`. The transport
was running; nothing had to be introduced.

The convention has three named parts, and each does one job:

| part | job |
|---|---|
| `bus.listener.mixin` on the model | makes the record itself a channel — `record._bus_send(type, payload)` |
| `bus_service.addChannel` / `.subscribe` in the browser | asks for a channel and a notification type |
| `ir.websocket._build_bus_channel_list` | resolves the string a client asked for into a record **after checking read access** |

The third is the one worth copying carefully: a browser may ask for any string, so the server
turns `flightdeck.session_<id>` into the record and verifies access before it becomes a channel.
`html_editor` is the reference implementation.

Sending lives in `flightdeck.message.text.create`, not in a script. Any writer inside Odoo then
gets live append for free, and the importer turns it off by context because during a load of
tens of thousands nobody is watching.

| what was done | what came back |
|---|---|
| A turn created from `odoo shell`, browser untouched | appeared **in ~0.5 s**, no reload and no click |
| Where it landed | bottom-most turn, seq 2134 |
| Counters | `60 of 383` → `61 of 384` |
| Markdown on the live turn | rendered — 2 list items, 1 bold span |
| The notebook widget | same figures, from the same channel |
| Page errors | 0 |

**What this does not settle**, and it is the part that matters for a real port: `_bus_send`
only runs when something goes through Odoo's ORM. FlightDeck's ingest writes to its own
Postgres ledger, so no transport reaches the browser until a writer calls into Odoo — a
controller, or a job. The open question is who writes, not what pushes.

Two options were rejected. **Polling** — the bus exists so that nobody has to, and it would
cost a request per interval per tab while still lagging by that interval; it is only the answer
where no websocket survives the deployment, which is not the case here. **A worker of our own**
— Odoo already runs its websocket in a `SharedWorker`, one socket for every tab of a browser,
so a second connection would add a second authentication model and a second way to fail without
adding reach.

### Odoo reading the transcripts itself

The live turn above was injected by hand. This is the real thing: Odoo reads the session's
own `.jsonl`, owns the position state, and depends on nothing FlightDeck runs.

**The shape is a thin watcher and a fat cron.** The watcher reads nothing and writes nothing —
its only act is `ir.cron._trigger()`, which inserts a trigger row and sends
`pg_notify('cron_trigger')`; the cron thread waits on that notification with `select`, so the
read starts in milliseconds instead of at the next 60-second wake-up. All the work stays in the
cron, which is Odoo's own single runner with its row lock, its cursor and its transaction. A
watcher that dies costs latency and nothing else. One advisory lock (`pg_try_advisory_lock`) on
a connection outside Odoo's pool keeps a second process from watching the same database.

**Position and identity are different jobs.** Byte offset says where to resume, cheaply; `uuid`
says whether a line is already stored. A wrong offset then costs a re-read, never a duplicate.
Only newline-terminated lines are consumed, so a line still being written is left for the next
pass, and decoding catches `UnicodeDecodeError` beside the JSON error — a torn write leaves NUL
bytes that `json.loads` reads as UTF-32.

**Tool output exists only here.** The ledger has none: FlightDeck's ingest drops `tool_result`
on purpose. Reading the file gives it back — a `tool_use` block becomes the turn's `tool_name`
and `tool_input`, and the `tool_result` that arrives on a **later** line is written onto that
turn and announced separately, because the turn is already on screen by then.

Measured end to end, on this session's own transcript while it was being written:

| what was done | what came back |
|---|---|
| Container reading mode-600 transcripts | ACL for uid 100, including a **default** ACL so new session files stay readable |
| Following turned on | offset set to the file's current end, 2924 lines counted |
| A real turn appended by the conversation | row created with `tool_name` = `Bash` |
| Its result, lines later | `tool_output` written, 511 bytes, and the turn replaced on screen |
| Collapsed tool input | 35 px — one line, no output block |
| Expanded | 227 px, full input plus the output |
| Page errors | 0 |

Latency, measured rather than estimated: **0.17 s** for a turn to reach disk, **4 ms** (n=6,
3–5) from Odoo's commit to the turn being painted. What sat between them was the cron's floor —
`interval_type` starts at minutes and `SLEEP_INTERVAL = 60` in the server — which is what the
watcher removes.

One trap cost a round: `seq` is the **absolute line number in the file**. Starting a follow with
`follow_lines` set to the number of rows already stored gave new turns a lower seq than the
imported ones, so live turns sorted into the middle of the transcript instead of the end. The
count has to come from the file.

### Shortening the loop: what can be swapped, what must be reloaded

The line is not "state versus template". It is **what travels by RPC versus what the browser
has already evaluated**. A view arch is XML, but it arrives as an RPC result, so it can be
swapped in place. An OWL template is also XML, but it ships inside an asset bundle, so only a
reload replaces it. Same language, opposite answers, decided by the delivery route.

| lever | measured |
|---|---|
| A view arch changed while a form was open | the form matched the new arch in **1293 ms**, and the page never reloaded — a marker set on `window` survived |
| A module upgrade that changed an asset | the page reloaded **by itself 2113 ms** after the upgrade started, about 200 ms after it finished, and came back up |

**The arch lever.** `view_service` reads `get_views` through an encrypted IndexedDB cache that
serves the stale value first and refreshes behind it, so re-running the action is not enough —
it renders the old arch again. Two things make it work: `ir.ui.view` announces a change on the
bus (from `_write` as well as `write`, because assigning `arch` defers to a flush that takes
the low-level path), and the client drops the cache through the framework's own
`CLEAR-CACHES` event before calling `soft_reload`. The cache is keyed by the RPC **method**, so
the entry to clear is `get_views`, not a URL.

**The bundle lever.** Odoo already broadcasts `bundle_changed`, and its own watchdog ignores it
unless `release.version` differs — the Odoo version, which a module rebuild never moves. So in
a module-dev loop that watchdog never fires; it also waits 10 to 60 seconds before asking. Two
changes: any `bundle_changed` reloads at once, and `ir.module.module` sends one when an upgrade
finishes. That last part matters because Odoo's own signal comes from bundle **regeneration**,
which is lazy — an open page never asks for the bundle again until it reloads, so it would have
been waiting for the reload it was supposed to be told about.

**The service worker** caches only `/odoo` and `/odoo/offline` — an app shell for offline use
and a fast boot. It makes the reload cheaper, and it cannot avoid one: a service worker
controls `fetch`, and an ES module already evaluated cannot be evaluated again. Its cache is
dropped before reloading, or it would serve the page from before the change.

`--dev=xml` covers the third side: QWeb is read from file per request, so a template change
needs no server restart.

## The answer

For the three tabs measured, Odoo 19 CE holds the data at full size and covers spend
better than the current stack does. It does not cover a transcript or a rendered document
without help, and two small components were enough to close both. Nothing found here
argues against the direction; what it does argue is that the parts of FlightDeck worth
moving first are the ones that are already tables and numbers.
