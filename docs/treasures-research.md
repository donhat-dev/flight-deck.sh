# Treasures — research record

Prior art and measured evidence behind `treasures-design.md`. Kept because the
conclusions overturn what the documentation alone suggests.

> Date: 2026-07-21. Method: three parallel web-research passes (embed engines /
> store+MCP surface / unify+publish) plus a local PoC run. Sources linked inline.

## 1. PoC — pandoc, run locally (highest-tier evidence here)

Static `pandoc 3.10.1` (linux-amd64 tarball, no root) against a fixture with a
local `.woff2` referenced from CSS `@font-face`, a CSS `background:url(png)`, a
markdown image, and a custom template.

```
pandoc doc.md --standalone --embed-resources -c style.css -o out.html
pandoc frag.html -f html --standalone --embed-resources \
       --template tmpl.html -c style.css -M title=… -M lang=vi -o out2.html
```

Measured result:

| Question | Result |
|---|---|
| markdown input → one standalone HTML | yes |
| HTML-fragment input (`-f html`) → same | yes |
| **`@font-face url(font.woff2)` inside a linked CSS** | **embedded** as `data:font/woff2;base64` |
| CSS `background:url(img.png)` + markdown `![](img.png)` | embedded as `data:image/png;base64` |
| external refs remaining in output | **0** |
| custom template as a full artifact shell | works — `<title>`, emoji favicon as an SVG data URI, `data-theme`, `$body$`, `$lang$` |

**This contradicts the docs-based finding.** The research pass over pandoc's
manual concluded that recursion into `@font-face url()` inside a linked
stylesheet is *undocumented and unverified*, and therefore recommended
hand-embedding fonts plus a second `monolith` pass. Running it shows pandoc does
recurse. Consequence: **pandoc alone is sufficient** for our wrap step, and the
second stage is dropped. Sources: [Pandoc manual](https://pandoc.org/MANUAL.html),
[pandoc issue #8362 (embed only local resources)](https://github.com/jgm/pandoc/issues/8362).

Caveats confirmed from the manual: resources loaded dynamically by JavaScript are
never embedded; absolute URLs are downloaded at build time; `pypandoc` is a
subprocess wrapper, not a native library ([pypandoc](https://pypi.org/project/pypandoc/)).

## 2. Embed engines compared

| Engine | Invocation | Embeds | Fit |
|---|---|---|---|
| **pandoc** | CLI (`pypandoc` shells out) | CSS/JS/img/video/fonts-via-CSS → data URIs; templates control the shell | **chosen** — markdown/fragment → styled standalone |
| **monolith** (Rust, [Y2Z/monolith](https://github.com/Y2Z/monolith)) | CLI; local input via `cat f.html \| monolith -b <base> -o out.html -` | everything, plus domain allow/deny (`-d`/`-B`) for CSP enforcement | not needed for our flow; the right tool for *already-rendered* HTML carrying remote refs |
| **SingleFile** ([gildas-lormeau](https://github.com/gildas-lormeau/singlefile)) | CLI + headless Chrome | full rendered-page capture | overkill (needs a browser); AGPL-3.0 |
| **juice** ([Automattic](https://github.com/Automattic/juice)) | Node lib/CLI | CSS → inline `style=` only | wrong problem (email-style inlining) |
| **inliner** ([remy](https://github.com/remy/inliner)) | Node lib/CLI | CSS/JS/images → base64 | weaker, less-maintained monolith |

## 3. Format evaluation — `.pen` and `.od` as an intermediate layer

The idea tested: `markdown → .od/.pen → html → pandoc → artifact`.

**`.pen` (Pencil)** — from the pencil MCP's own instructions and its
`export_html` schema:
- **Encrypted; accessible only through the pencil MCP** ("never use Read or Grep
  on .pen files") → not diffable, not scriptable without the service.
- Represents **UI/app/website designs** (frames, components, nodes, design
  variables), not prose documents.
- `export_html` emits HTML+Tailwind/CSS, but **"image assets are always
  referenced with relative paths (never embedded)"** → not self-contained.

**`.od` (Open Design)** — from the repo: **not a file format**. It is a
filesystem-first workspace plus a SQLite metadata index; artifacts are ordinary
files (HTML/React/markdown) and **there is no document IR**
([nexu-io/open-design](https://github.com/nexu-io/open-design), [opendesigner.io](https://opendesigner.io/)).

**Verdict — neither belongs in the pipeline**, for different reasons:
- `.od` is a *store*, not a *representation*; "convert markdown to .od" is a
  category error (you place files *into* a store).
- `.pen` is the wrong domain (UI canvas, not documents), its export is not
  self-contained (so pandoc/monolith would still be required afterwards), and its
  encryption plus MCP-only access removes the local ownership Treasures exists to
  provide.

The failure mode of the 5-stage chain is that it conflates **representation**,
**store**, and **renderer**. What is worth borrowing sits at other layers:
`.od`'s store model (files + thin index), and pencil's *interaction* model (the
agent supplies structure; the tool owns render/export).

## 4. Store model — field set worth borrowing

| System | Idea borrowed |
|---|---|
| Odoo **`ir.attachment`** ([source](https://github.com/odoo/odoo/blob/18.0/odoo/addons/base/models/ir_attachment.py)) | polymorphic link-back (`res_model`/`res_id`) → our `origin_kind`/`origin_id`; `public` + `access_token` for draft-vs-shareable; indexed `checksum` for dedup |
| **Paperless-ngx** ([API docs](https://docs.paperless-ngx.com/api/)) | original-vs-archive split with **two checksums** → our `source_checksum` + `render_checksum`; three distinct timestamps (authored / added / modified) |
| **Directus** ([files API](https://directus.io/docs/api/files)) | `filename_disk` vs `filename_download` — opaque storage name separate from the human title |
| **Open Design** | files on disk + SQLite only as index; per-project subtree; sandboxed-iframe preview with edit-in-place |

## 5. MCP surface — prior art

| MCP | Shape worth copying |
|---|---|
| **pencil** (live MCP instructions) | agent sends structural ops; MCP owns render + `export_html`. The core split Treasures adopts. |
| **ghost-mcp-server** ([repo](https://github.com/mtane0412/ghost-mcp-server)) | publish is a **status transition on the same object**, not a separate entity |
| **paperless-mcp** ([repo](https://github.com/baruchiro/paperless-mcp)) | `download_document` distinguishing original vs archive → our `variant` (skeleton vs rendered) |
| **directus-mcp** ([repo](https://github.com/pixelsock/directus-mcp/)) | generic CRUD + a couple of asset-specific verbs is enough |
| **Outline MCP** | `archive` as a state distinct from delete |
| **hugo-mcp** ([repo](https://github.com/SunnyCloudYang/hugo-mcp)) | tools split by lifecycle stage (create → build → preview → deploy) |

**Gap across all six:** none exposes a provenance/link-back verb, and none
harvests pre-existing scattered drafts. Those become Treasures'
`treasure_link_source` and `treasure_discover`.

## 6. Discovery + rendering + publishing

- **Ingestion shape**: [cass](https://github.com/Dicklesworthstone/coding_agent_session_search)
  normalizes 23+ agent-session formats into one canonical SQLite store with
  derived, rebuildable search indexes, tagging every record with `source_id` /
  `source_kind` / original workspace path. Same shape our indexer needs.
- **Transcript-tree tooling already exists**:
  [claude-code-log](https://github.com/daaain/claude-code-log) (master index of
  project cards → per-session pages, cache-validated incremental reload),
  [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts)
  (paginated static site per session), [claude-JSONL-browser](https://github.com/withLinda/claude-JSONL-browser).
  Borrow the hierarchy and the incremental reindex.
- **Dedup by content hash** is standard artifact-repo practice, not something to
  invent ([JFrog checksum-based storage](https://docs.jfrog.com/installation/docs/checksum-based-storage));
  identical content from two sessions collapses to one blob with two provenance
  rows.
- **Rendering safely**: keep untrusted HTML in a separate sandboxed origin
  communicating over `postMessage` — the split that
  [open-artifacts](https://github.com/13point5/open-artifacts) uses via a distinct
  [renderer service](https://github.com/13point5/open-artifacts-renderer), and that
  claude.ai itself uses. [LibreChat](https://www.librechat.ai/docs/features/artifacts)
  reaches for Sandpack when live React execution is needed;
  [Open WebUI](https://docs.openwebui.com/features/chat-conversations/chat-features/code-execution/artifacts/)
  splits a code canvas from type-specific viewers.
- **claude.ai artifact contract**: single self-contained page, strict CSP (no
  external requests), `.html`/`.htm`/`.md` source, **16 MiB rendered cap**,
  private by default, full version history where the author picks which version
  viewers see, and **URL as the stable identity** (no URL ⇒ a new artifact)
  ([Claude Code docs](https://code.claude.com/docs/en/artifacts)).
- **No create/update API.** The only documented programmatic surface is the
  org-admin **Compliance API** — list, get a version's content, delete
  ([reference](https://docs.claude.com/en/api/compliance/code/artifacts)).
  Publishing therefore stays a live-session action.

## 7. Edit layer — candidates

| Tier | Tool | License | Note |
|---|---|---|---|
| 1 — markdown WYSIWYG | **Milkdown** ([site](https://milkdown.dev/), [repo](https://github.com/Milkdown/milkdown)) | MIT | ProseMirror-based, headless, markdown in/out → keeps markdown primary. **Chosen for v1.** |
| 1 — alternative | TipTap | MIT | same ProseMirror base; more UI to build by hand |
| 3 — visual canvas | **GrapesJS** ([guide](https://gjs.market/blogs/grapesjs-the-complete-guide-to-the-open-source-web-builder-f)) | BSD-3 | the direct Odoo-Website analog: blocks, style manager, inline editing, embeddable |
| 3 — component-shaped | Puck ([overview](https://dev.to/fede_bonel_tozzi/top-5-page-builders-for-react-190g)) | MIT | edits a JSON tree of React components |
| 3 — avoid | Silex | AGPL-3.0 | copyleft obligation, same concern as Lago in this workspace |

**Structural constraint found here, not a tool limitation:** a visual canvas and
markdown-primary round-tripping cannot both hold for one artifact. A canvas edits
rendered DOM; markdown is the pre-render source; un-rendering free-form layout
back into markdown is not possible. Odoo Website resolves it by dropping the
markdown source entirely — the page *is* the source. Treasures therefore treats
"detach to visual edit" as an explicit, one-way, per-artifact decision.
