# Treasures — design spec

A local-first artifact library: an MCP wraps agent-written content into
publish-ready self-contained HTML, stores it in one place, and FlightDeck
becomes the deck where every artifact — Vietnamese draft, pre-release, or
published — is visible, traceable to its source, and editable.

> Status: design / approved to plan. Owner: nathan. Date: 2026-07-21.
> Research backing: `docs/treasures-research.md` (prior art + PoC evidence).

## 1. Problem

Two concrete pains, both measured against how this workspace actually works
(markdown = primary source, HTML artifact = secondary, shared with the team):

1. **Fragmentation.** Published artifacts are grouped and findable at
   `claude.ai/code/artifacts`, but **pre-release versions and Vietnamese drafts
   live scattered inside `~/.claude/projects/**` transcripts and ad-hoc repo
   folders.** There is no index, no provenance, no way to ask "where is the VN
   draft of this report, and which session produced it?"
2. **Repeated manual embedding.** The artifact standard (no external CDN, strict
   CSP) forces hand-generating base64 for fonts and libs on every artifact — a
   mechanical tax paid over and over.

## 2. Decisions locked (with rationale)

| Decision | Choice | Why |
|---|---|---|
| Wrap engine | **pandoc only** (2-stage pipeline, not 5) | PoC proved `--embed-resources` **recurses into CSS**: `@font-face url(woff2)` → `data:font/woff2;base64`, CSS `url(png)` + markdown images → data URIs, **0 external refs left**. monolith is unnecessary for our flow (it earns its place only for already-rendered HTML with remote refs). |
| Intermediate format | **none** — markdown (or an HTML fragment) goes straight to pandoc | `.pen` is an encrypted UI-design canvas, and its HTML export explicitly does *not* embed assets. `.od` is a workspace store, not a format. Neither is a document IR, so inserting either would add a stage without removing one. See research §"format evaluation". |
| Store | **files on disk + Postgres index** (FlightDeck's DB) | Open Design's proven shape (real files, thin index) keeps drafts greppable/diffable and edit-in-place trivial; blob-in-DB (LibreChat/Open WebUI) does not. Postgres because FlightDeck already runs on it — see the join payoff in §4. |
| Source of truth | **markdown stays primary**; `artifact.html` is derived | Matches how the workspace already works and keeps re-render idempotent. A visual HTML canvas would fork this (see §8). |
| Publish | **human/agent-in-the-loop**, not automated | claude.ai has **no create/update artifact API**. The only programmatic surface is the org-admin **Compliance API** (list / get-version / delete). Creation happens by a live session publishing a file — exactly what the `Artifact` tool does. This is a hard boundary, not a temporary gap. |
| Edit (v1) | **Milkdown** (WYSIWYG over markdown) | Edits stay in the primary source; re-render regenerates the artifact. ProseMirror-based, headless, MIT. |
| Home | `treasures/` inside the **flight-deck.sh monorepo** | Shares the store code, templates, and docs with the dashboard that renders it. |

## 3. Architecture

```
agent (any session)
  │  MCP: treasure_wrap(title, content, kind, language, origin…)
  ▼
treasures/  (MCP server, in-repo)
  ├─ wrap:  content ─┐
  │                  ├─ pandoc --standalone --embed-resources
  │   template.html ─┤     --template treasures/templates/<kind>.html
  │   tokens.css ────┘     -c treasures/templates/tokens.css
  │                  └──►  artifact.html  (self-contained, CSP-safe)
  ├─ store: writes the artifact dir to the FILESTORE
  └─ index: upserts a row in Postgres `treasures`
                    │
FILESTORE  ~/.flightdeck/treasures/<slug>-<id>/     Postgres (flightdeck DB)
  meta.json                                          treasures  ── origin_id ──┐
  assets/                                                                      │
  v1/{source.md, artifact.html}                      messages / tool_calls  ◄──┘
  v2/{source.md, artifact.html}                      session_meta
                    │
FlightDeck "Treasures" view
  list (status/lang/kind badges, origin link) → detail:
    [ preview (sandboxed iframe) | source | edit (Milkdown) ]
```

**How the MCP runs.** A stdio MCP server in `treasures/` (Python, sharing the
backend's `.venv` and its `db.py` seam so it talks to the same Postgres),
registered per-project in `.mcp.json` and available to any session in the
workspace. It writes files and index rows; it never renders UI.

**Why the filestore sits outside the repo** (`~/.flightdeck/treasures/`, override
with `TREASURES_STORE`): artifacts are *data*, not code — several are internal
NAKIVO material, and the repo is pushed. Keeping them out avoids both repo bloat
and accidental publication. `meta.json` is written beside each artifact so the
Postgres index is **rebuildable from disk** (the store, not the DB, is the
system of record for content).

## 4. Data model

One LOGGED table in the FlightDeck database (this is user content, not derived
ingest data — unlike `messages`/`tool_calls`, it must survive):

```sql
CREATE TABLE IF NOT EXISTS treasures (
  id              text PRIMARY KEY,          -- short id, slug-friendly
  title           text NOT NULL,
  slug            text NOT NULL,
  dir_path        text NOT NULL UNIQUE,      -- artifact dir in the filestore
  kind            text NOT NULL,             -- report | spec-review | note | dataflow | deck
  language        text NOT NULL DEFAULT 'en',-- en | vi
  status          text NOT NULL DEFAULT 'draft', -- draft | published | archived
  version         integer NOT NULL DEFAULT 1,
  source_format   text NOT NULL,             -- markdown | html
  source_checksum text,                      -- sha256 of the skeleton  (dedup drafts)
  render_checksum text,                      -- sha256 of artifact.html (dedup renders)
  render_bytes    bigint,                    -- guard the claude.ai 16 MiB cap
  -- provenance: Odoo ir.attachment's res_model/res_id, adapted
  origin_kind     text,                      -- claude_session | manual | discovered
  origin_id       text,                      -- session id  → joins FlightDeck's ledger
  origin_path     text,                      -- source .jsonl / original file
  published_url   text,                      -- claude.ai URL = identity once published
  duplicate_of    text REFERENCES treasures(id),  -- suggested link (VN draft ↔ EN published)
  authored_at     timestamptz,               -- when the content was written
  ingested_at     timestamptz NOT NULL DEFAULT now(),  -- when Treasures learned of it
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_treasures_origin  ON treasures(origin_id);
CREATE INDEX IF NOT EXISTS idx_treasures_status  ON treasures(status);
CREATE INDEX IF NOT EXISTS idx_treasures_srcsum  ON treasures(source_checksum);
```

Three field-group choices worth naming:

- **`origin_id` = the Claude session id.** This is the payoff of putting the
  index in FlightDeck's Postgres: an artifact joins directly against the
  existing `messages` / `tool_calls` / `session_meta` rows, so the deck can show
  *"this report came from session X, which cost $Y and is titled Z"* — and the
  Logbook can link the other way. No other store choice buys that for free.
- **Two checksums** (`source_checksum`, `render_checksum`), borrowed from
  Paperless-ngx's original-vs-archive split: the skeleton the agent supplied and
  the rendered artifact are independently addressable and dedupable. A
  re-render with an unchanged source is detectable and skippable.
- **Three timestamps** (`authored_at` / `ingested_at` / `updated_at`): a VN
  draft can be written days before Treasures ever sees it. Collapsing these
  would lie about the timeline.

`duplicate_of` is a **suggestion, never an enforced constraint** — the same
content legitimately exists as an EN and a VN artifact. Discovery proposes the
link; a human/agent confirms it via `treasure_link_source`.

## 5. MCP tool surface

The agent supplies content; **the MCP owns format, style, and render** (the
pencil model). Seven tools in v1:

| Tool | Inputs | Returns |
|---|---|---|
| `treasure_wrap` | `title`, `content`, `source_format` (markdown\|html), `kind`, `language`, optional `origin_kind/origin_id/origin_path`, optional `template` | `id`, `dir_path`, `artifact_path`, `render_bytes`, `render_checksum`, `preview_url`, `warnings[]` |
| `treasure_list` | filters: `status`, `language`, `kind`, `origin_id`, `query` | rows (paginated) |
| `treasure_get` | `id` \| `slug`, optional `include_source`, `include_html` | full record (+ content) |
| `treasure_update` | `id`, optional metadata fields, optional new `content` (⇒ new version + re-render) | updated record |
| `treasure_link_source` | `id`, any of `origin_*`, `published_url`, `duplicate_of` | updated record |
| `treasure_discover` | `roots[]` (default `~/.claude/projects`), `language`, `import` (bool) | candidates with checksums + suggested links; ingests when `import: true` |
| `treasure_publish_prepare` | `id` | `artifact_path`, suggested `title`/`description`/`favicon`, plus a reminder to call `treasure_link_source` with the returned URL |

Two of these are **Treasures' differentiators — no prior-art MCP exposes them**
(verified across pencil, hugo-mcp, directus-mcp, ghost-mcp, paperless-mcp,
Outline MCP): `treasure_link_source` (provenance as a first-class verb) and
`treasure_discover` (harvest scattered drafts). They are precisely the two
things the fragmentation pain needs.

`treasure_publish_prepare` exists **because publishing cannot be a tool call**:
it hands the agent everything needed to invoke the `Artifact` tool, and the
returned URL comes back through `treasure_link_source`. Status becomes
`published` as a **state transition on the same row** (Ghost's pattern), never a
second entity.

## 6. Wrap pipeline

```
pandoc <source> [-f html] --standalone --embed-resources \
       --template treasures/templates/<kind>.html \
       -c treasures/templates/tokens.css \
       -M title="…" -M lang=vi -o artifact.html
```

- **Template** = the artifact shell: `<title>`, emoji favicon as an SVG data
  URI, `data-theme`, `$body$`. Verified in the PoC.
- **tokens.css** carries `@font-face` pointing at local `.woff2` files;
  pandoc base64-embeds them. This is the whole base64 pain, gone.
- **Fonts must be Vietnamese-subset** (`latin-ext`/`vietnamese`) — a
  latin-only subset silently drops VN diacritics and the draft renders broken.
  The existing artifact-style guidance ("latin-only for EN artifacts") is an
  EN-only optimization and must not be applied to `language: vi`.
- **Size guard**: warn in `treasure_wrap`'s output when `render_bytes`
  approaches the claude.ai **16 MiB** rendered cap (embedded fonts + images add
  up fast).
- **Absolute URLs**: pandoc *downloads and inlines* `https://…` resources at
  build time. That is convenient and CSP-correct in the output, but it is a
  network fetch during wrap. v1: allow, and report every fetched host in
  `warnings[]` so nothing is embedded silently.
- **pandoc dependency**: pinned static binary fetched to `~/.flightdeck/bin/`
  by a `make pandoc` target (needs no root — proven in the PoC), falling back to
  a system pandoc when present.

## 7. FlightDeck "Treasures" view

- **Nav**: its own top-level item (a content library, not a "system" board).
- **List**: title, kind, badges (`draft`/`published`/`archived`, `en`/`vi`),
  origin as a **link into the Logbook session**, size, updated-at; filter chips +
  search; duplicate clusters flagged.
- **Detail**: three panes — **preview** (rendered artifact in a **sandboxed
  iframe**, never same-origin eval — the pattern both open-artifacts and Open
  Design use), **source** (read-only markdown/HTML), **edit** (§8).
- **Realtime**: the filestore joins the existing watchdog + `/api/stream` SSE
  path, so a wrap performed by an agent in another session appears immediately.
- **Read-only rule**: published artifacts can be re-rendered locally, but the
  published copy on claude.ai cannot be updated from the dashboard (no API) —
  the UI must say so rather than implying a sync that cannot happen.

## 8. Edit layer (v1 = tier 1 only)

**Tier 1 — Milkdown on markdown (in scope).** `GET /api/treasures/{id}/source`
→ markdown; save → new version dir → re-render via pandoc → preview refreshes.
Markdown remains the single source of truth, so re-render stays idempotent and
the artifact never diverges from its source.

**Deliberately out of scope for v1** (documented so the boundary is a decision,
not an accident):

- **Tier 2 — inline "tweaks"** (element-picker in the preview patching back to
  markdown or a small per-artifact overrides layer).
- **Tier 3 — visual canvas** (GrapesJS, BSD-3 — the true Odoo-Website analog;
  Puck for component-shaped artifacts; *not* Silex, AGPL-3.0). **Entering tier 3
  is a one-way "detach" that makes HTML the source of truth**, because a canvas
  edits rendered DOM and cannot be un-rendered back into markdown. Odoo Website
  resolves this by having no markdown source at all. Treasures must make the
  detach explicit and per-artifact — silently mixing the two models would
  recreate the fragmentation this project exists to remove.

## 9. Verification plan

- **Wrap correctness**: assert the output contains **zero** non-`data:` external
  refs, that a `@font-face` woff2 became `data:font/woff2;base64`, and that a
  VN sample renders diacritics with the subset font.
- **Round-trip**: wrap → edit in Milkdown → re-render ⇒ `source_checksum`
  changes, `render_checksum` changes, `version` increments, previous version
  still on disk.
- **Discovery**: run `treasure_discover` against the real
  `~/.claude/projects` tree; report how many artifact bodies were found and how
  many deduped, and confirm re-running is idempotent (no duplicate rows).
- **Rebuildability**: drop the `treasures` table, re-index from `meta.json`
  sidecars, confirm the list is identical.
- **Publish path**: `treasure_publish_prepare` → `Artifact` tool → URL →
  `treasure_link_source` ⇒ status flips to `published` and the deck shows the
  link.

## 10. Open questions / risks

| Item | Note |
|---|---|
| **How does the MCP learn `origin_id`?** | A calling agent usually does not know its own session id, so provenance cannot simply be passed in. Three candidates: (a) the MCP infers it from the most-recently-appended `.jsonl` under `~/.claude/projects` matching the cwd — cheap but racy with parallel sessions; (b) the caller passes it when known and it stays NULL otherwise; (c) FlightDeck backfills it later by matching `source_checksum` against transcript content during discovery. Decide before implementing `treasure_wrap`; (b)+(c) is the safe combination. |
| Reverse sync of published artifacts | Needs the Compliance API (org-admin, Enterprise/Team) or manual URL paste. v1 assumes manual. |
| Discovery precision | Extracting "artifact bodies" from transcripts will surface false positives (code blocks that merely look like documents). Start conservative: only fenced HTML documents and markdown blocks the agent explicitly wrote to a file. |
| Template proliferation | One `default.html` + `kind` as a body class in v1; split per-kind templates only when a real difference appears. |
| Size cap | 16 MiB rendered; the guard warns but does not block. |
| Milkdown ↔ pandoc markdown flavour | Milkdown emits CommonMark/GFM; pandoc reads it, but exotic pandoc extensions typed by hand could be normalized away on save. Round-trip a representative doc before trusting the editor with existing drafts. |
