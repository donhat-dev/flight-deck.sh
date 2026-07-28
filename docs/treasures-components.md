# Treasures component contract

**Status:** decided 2026-07-28, not yet implemented.
**Goal:** an agent writes ONE markdown file; it reads correctly in any common
markdown tool (GitHub, VS Code preview, Obsidian) AND renders with the house
artifact components (hero, card, badge) once wrapped into HTML.

Every rule below was verified by running the real pipeline, not inferred from
documentation. The verification table is at the bottom.

---

## 1. The syntax, and why this one

**Block component**

```markdown
<div data-component="hero">

CRM-11198 · Billing modernization

# Discount Service

## *PoC plan.*

</div>
```

**Inline component**

```markdown
Status: <span data-component="badge" data-tone="good">PASS</span> today.
```

Blank lines around the block content are **mandatory** — that is the whole
mechanism. Both CommonMark and pandoc close an HTML block at the first blank
line, so everything between the tags returns to being ordinary markdown and
both parsers agree. Without the blank line the two diverge (§4).

### Rejected alternatives (measured, not assumed)

| Syntax | Why rejected |
|---|---|
| `::: hero` … `:::` (pandoc fenced div) | In a CommonMark viewer `::: hero` and the line after it **merge into one paragraph**, so the eyebrow line loses its position and two junk paragraphs appear. It renders correctly only in pandoc. |
| `[PASS]{.badge}` (bracketed span) | **Silent data loss.** Milkdown reads `[PASS]` as a possible link and escapes it on save, so one click of Save in the Edit tab turns the badge into the literal text `[PASS]{.badge}` forever. |
| `` `PASS`{.badge} `` (code-span attrs) | Survives Milkdown, but a CommonMark viewer shows `PASS` in code style followed by the junk text `{.badge}` — it fails the "common tool" requirement that motivated this whole design. |

### Why `data-component` rather than `class`

The component vocabulary is a closed, greppable allowlist that cannot collide
with any styling class, and the attribute survives both pandoc and Milkdown
unchanged. Variants ride along as sibling data attributes (`data-tone`,
`data-tint`) instead of a space-separated class soup.

### CSS selector rule

Target `[data-component="hero"]`, **never** `div[data-component="hero"]`.
pandoc rewrites a div whose first child is a heading into
`<section id="..." data-component="hero">`, so the element name is not stable
— only the attribute is.

---

## 2. Component inventory, v1

Three components, exactly the ones with a markdown-expressible shape. The rest
of `ARTIFACT_STYLE.md` §5 (stats trio, rating matrix, profile cards, TL;DR
trio) stays **out of scope**: they need multi-part structure that markdown
cannot carry without inventing a second syntax.

### `hero`

| | |
|---|---|
| Contains | one eyebrow paragraph, one `#` title, optionally one `##` accent line |
| Style | oversized title; the accent line's `*emphasis*` renders in Playfair Display italic 600 — the only place canon §1 permits Playfair |
| Notes | needs the Playfair face added to `tokens.css` (file already present, currently not declared) |

### `card`

| | |
|---|---|
| Contains | any block content (paragraphs, lists, tables) |
| Variants | `data-tone="good" \| "mid" \| "weak"` picks the tint from the existing token trio |
| Style | white card, 14px radius, 1px border — borders over shadows, per canon §3 |

### `badge`

| | |
|---|---|
| Contains | inline text only |
| Variants | `data-tone="good" \| "mid" \| "weak"` |
| Style | full pill, uppercase, tone-coloured background + border |

Adding a component means: one entry here, one rule block in `tokens.css`, one
name in the lint allowlist. Nothing else.

---

## 3. Flow and tools

```
skill (authoring)  ->  lint  ->  render (unchanged)  ->  validate  ->  artifact
                        |                                   |
                        +-- autofix blank lines             +-- on mismatch: degrade
                        +-- reject unknown component            to plain markdown
```

### Stage 1 — authoring skill

`.claude/skills/treasure-components/SKILL.md` carries §1 and §2 of this file:
the two syntax forms, the blank-line rule, the allowlist, and the three
rejected alternatives (an agent that knows *why* `:::` is banned will not
reintroduce it).

### Stage 2 — lint (new)

`flightdeck/treasures/lint.py`, called from `service.wrap` **before** render, so
neither the MCP tool nor the dashboard can bypass it.

- **Autofix, not error, for missing blank lines.** The repair is deterministic
  and unique — insert the blank line — so failing the wrap would only cost a
  round trip.
- **Hard error for an unknown component name.** Intent cannot be guessed, so
  this one is fail-closed.
- Reports every fix it applied, through the existing `warnings` channel.

### Stage 3 — render

`render.render` unchanged. `markdown_in_html_blocks` and `native_divs` are
already enabled in the current pandoc invocation; no flag change is needed.

### Stage 4 — validate (new)

Post-render check in the same module: every `data-component` present in the
source must appear in the output as a real **attribute**. A component that
degraded to text shows up as the escaped literal `&lt;div data-component=`
instead — that asymmetry is the whole test.

**Fallback on failure — the safe direction.** Re-render with every component
marker stripped, yielding plain markdown, and attach a loud warning. A plain
artifact still carries the full house typography, colour and background; only
the custom elements are missing. Shipping a readable artifact without
components always beats shipping one with raw tags printed as text.

### Negative tests required before this is "done"

Per the workspace rule that a guard needs a live negative test:

1. markdown with a component div and no blank line → lint reports the fix, the
   rendered HTML has a real element.
2. markdown with `data-component="nope"` → wrap refuses, nothing written to
   disk or index.
3. a forced degradation → the artifact contains no escaped `&lt;div
   data-component`, and the warning is present.
4. a document using no components at all → output byte-identical to today, so
   the existing library cannot regress.

---

## 4. The one real risk

A missing blank line does **not** break the artifact and does **not** warn —
pandoc's `markdown_in_html_blocks` parses the inner markdown anyway. It breaks
only in the common tools this design exists to serve, so the failure is
invisible from inside the pipeline:

| | Artifact (pandoc) | CommonMark viewer |
|---|---|---|
| blank line present | `<h1>Discount Service</h1>` | `<h1>Discount Service</h1>` |
| blank line missing | `<h1>Discount Service</h1>` — still fine | `# Discount Service` — printed raw |

Stage 2's autofix exists specifically to close this, because no downstream
stage can detect it.

---

## 5. Verification log

All measured on 2026-07-28 against the running pipeline (pandoc via
`render.render`, Milkdown round-trip via a real Save in the Edit tab, common-tool
behaviour via `pandoc -f commonmark`).

| Case | pandoc → artifact | Milkdown Save | CommonMark viewer |
|---|---|---|---|
| `<div data-component="hero">` + blank lines | attributes kept, inner markdown parsed (h1/em/strong/list) | **byte-perfect** | clean, no junk |
| `<span data-component="badge" data-tone="good">` | real element, both attributes kept | **byte-perfect** | renders the text plainly |
| `::: hero` | correct | survives verbatim | **junk + eyebrow swallowed into one paragraph** |
| `[PASS]{.badge}` | correct | **destroyed → `\[PASS]`** | junk |
| `` `PASS`{.badge} `` | `<code class="badge">` | survives | `PASS` + junk `{.badge}` |
| div, blank line missing | correct (`<section>`) | — | **raw `#` printed** |

Two incidental findings worth keeping:

- pandoc emits `<section>` instead of `<div>` when a heading leads the block —
  hence the selector rule in §1.
- Milkdown renders the tag as a `contenteditable="false"` chip, so a user
  cannot corrupt it by typing — strictly safer than `:::`, which is ordinary
  editable text.
