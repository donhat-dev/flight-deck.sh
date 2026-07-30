/* ============================================================
   Composition lint — enforces docs/flightdeck-composition-and-radio.md §2.

   Why this exists: the tokens and components were already right. What drifted
   was how they are *composed* — depth applied to everything (so depth stopped
   meaning anything) and depth materials leaking into text and fills. Prose in
   DESIGN.md did not stop either drift, so the checkable half is here.

   Scope, stated honestly. Only some of the six rules are decidable from source:

     C1  at most one declared anchor per screen        checkable (via marker)
     C2  density rhythm                               NOT checkable — needs layout
     C3a depth only on interactive/frame/anchor        checkable
     C3b depth budget per screen                      checkable (base classes)
     C3c depth in a repeated list must be conditional  checkable (JSX)
     C4  asymmetry by default                         NOT checkable — a matrix
                                                      exemption is a content fact
     C5  metadata is distinct per region              NOT checkable here
     C6a the card tint is never a shadow or text      checkable
     C6b role token matches the selector's role       checkable, 3 directions

   The undecidable ones stay in review, not faked into a passing test.

   Two markers, both greppable, written here without their comment delimiters
   because a nested terminator would close this block early:

     composition: anchor                    declares C1's anchor region
     composition-lint-allow: C6a — reason   exemption; the reason is REQUIRED
   ============================================================ */

const MAX_VAR_HOPS = 8;

/** Pink is a CARD BACKGROUND and nothing else, so it must never appear in a
 *  shadow or on text. This inverts the old rule: pink and orange used to be
 *  depth-only materials banned from fills. Under the unified token style the
 *  control face and its depth trade places by theme (Day: orange face, black
 *  shadow; Night: black face, orange shadow), so orange is legitimately a fill —
 *  and pink is the one material with a single home. */
const CARD_ONLY = /--[\w-]*(pink|card-tint)\b/;

/** A shadow declared through a `*-shadow-block` token is GROUND material — the
 *  lift every panel has — not rationed control depth. The distinction is read off
 *  the token name rather than guessed from magnitude, for the same reason the
 *  composition markers exist: intent has to be stated, not inferred. Without it
 *  the block recipe (a softened accent offset plus a soft ambient) would read as
 *  a depth violation on every panel. */
const BLOCK_MATERIAL = /--[\w-]*shadow-block\b/;

/** Properties that put a colour on a surface or on glyphs. */
const FILL_PROPS =
  /^(color|background|background-color|background-image|border(-[a-z]+)?-color|border|outline-color|fill|stroke|text-decoration-color|-webkit-text-fill-color)$/;

/** A selector may carry offset depth if it can be acted on… */
const INTERACTIVE =
  /(:hover|:active|:focus|:focus-visible|:focus-within|:checked|:disabled|\[data-(checked|selected|active|pressed|open)|\[aria-(pressed|selected|current|expanded|checked)|\b(button|btn|toggle|checkbox|check|input|tab|link|control|knob|switch|slider|trigger|dial|tune)\b|(^|[\s>+~])(a|button|input|select|textarea|summary|label)([[:.\s]|$))/i;
// `data-state` is deliberately NOT in that list. It usually carries a *display*
// state — a log line reading "failed", a badge reading "waiting" — which is not
// an affordance, so accepting it would have let any static row claim depth.
// Genuine interaction states are already covered by checked/selected/pressed/open.

/** Depth that exists only while the pointer or focus is on an element is not
 *  competing for attention at rest, so it does not spend budget. */
const TRANSIENT = /:(hover|active|focus|focus-visible|focus-within)\b/i;

/** …or if it is the ground rather than a figure. A page shell lifting off the
 *  canvas is a frame; DESIGN.md §5 already separates frame from depth. */
const FRAME = /\.[a-z0-9-]*(shell|page|canvas|board|root|frame|sheet)\b/i;

/** C6b — the "Never used for" column of §2's table, in checkable form. Only the
 *  three unambiguous directions: a selector that names its own role must not
 *  reach for a different role's token. */
const ROLE_RULES = [
  {
    id: "error-wearing-action",
    selector: /(error|invalid|failed|danger|destructive)/i,
    banned: /--[\w]+-(action|signal)(\b|-)/,
    why: "an error state must not wear the action colour",
  },
  {
    id: "loading-wearing-error",
    selector: /(loading|pending|busy|spinner|skeleton)/i,
    banned: /--[\w]+-critical\b/,
    why: "loading must differ from failure by material, not borrow its colour",
  },
  {
    id: "caution-wearing-error",
    selector: /(warn|caution|delayed|stale)/i,
    banned: /--[\w]+-critical\b/,
    why: "caution is not failure",
  },
];

const DEPTH_BUDGET = 4;

// ---------------------------------------------------------------- parsing

/** Blank comments out while keeping every newline, so reported line numbers
 *  stay true, and hand the comment text back for marker detection. */
function stripComments(src) {
  const comments = [];
  const clean = src.replace(/\/\*[\s\S]*?\*\//g, (match, offset) => {
    const line = src.slice(0, offset).split("\n").length;
    // endLine matters: a marker whose reason wraps onto a second line still has
    // to attach to the rule beneath it.
    const endLine = line + (match.match(/\n/g) || []).length;
    comments.push({ line, endLine, text: match.slice(2, -2).trim() });
    return match.replace(/[^\n]/g, " ");
  });
  return { clean, comments };
}

function splitTop(value, sep) {
  const out = [];
  let depth = 0;
  let buf = "";
  for (const ch of value) {
    if (ch === "(") depth++;
    else if (ch === ")") depth--;
    if (ch === sep && depth === 0) {
      out.push(buf);
      buf = "";
    } else buf += ch;
  }
  out.push(buf);
  return out;
}

/**
 * Flat rule list plus every custom property seen. Brace-depth scan rather than
 * a regex, because `@media` nests and a regex would attribute a nested rule's
 * declarations to the at-rule.
 */
export function parseCss(src) {
  const { clean, comments } = stripComments(src);
  const rules = [];
  const vars = new Map();
  const stack = [];
  let token = "";
  let tokenLine = 1;
  let line = 1;

  const flush = () => {
    const text = token.trim();
    token = "";
    if (!text || !text.includes(":")) return;
    const at = text.indexOf(":");
    const prop = text.slice(0, at).trim();
    const value = text.slice(at + 1).trim();
    if (prop.startsWith("--")) vars.set(prop, value);
    const frame = stack[stack.length - 1];
    if (frame) frame.decls.push({ prop, value, line: tokenLine });
  };

  for (let i = 0; i < clean.length; i++) {
    const ch = clean[i];
    if (!token.trim() && !/\s/.test(ch)) tokenLine = line;
    if (ch === "\n") line++;

    if (ch === "{") {
      const selector = token.trim();
      token = "";
      stack.push({ selector, line: tokenLine, decls: [] });
    } else if (ch === "}") {
      flush();
      const frame = stack.pop();
      if (frame && !frame.selector.startsWith("@")) rules.push(frame);
    } else if (ch === ";") {
      flush();
    } else {
      token += ch;
    }
  }
  return { rules, vars, comments };
}

/** Merge the custom properties of several stylesheets into one map, so a screen
 *  sheet can be linted with the kit's tokens in scope. Later sources win. */
export function collectVars(sources) {
  const out = new Map();
  for (const src of sources) for (const [k, v] of parseCss(src).vars) out.set(k, v);
  return out;
}

/** Substitute `var(--x)` until it stops changing. Without this the lint is
 *  blind: `.fdx-button` reaches its offset through TWO hops
 *  (`--fdx-button-shadow` → `--fdx-shadow-control-alert` → `4px 4px 0 …`), so a
 *  naive regex reports the button as flat. */
export function resolveValue(value, vars) {
  let out = value;
  for (let hop = 0; hop < MAX_VAR_HOPS && out.includes("var("); hop++) {
    const next = out.replace(
      /var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\)[^()]*)*))?\)/g,
      (match, name, fallback) =>
        vars.has(name) ? vars.get(name) : fallback !== undefined ? fallback.trim() : match,
    );
    if (next === out) break;
    out = next;
  }
  return out;
}

/** True when a resolved box-shadow carries HARD offset depth — a non-inset
 *  layer with zero blur and a non-zero offset. This is the `4px 4px 0` material
 *  from DESIGN.md §5. A focus ring (`0 0 0 3px`) has no offset, and a soft
 *  ambient shadow has blur, so neither counts. */
export function hasOffsetDepth(resolved) {
  return splitTop(resolved, ",").some((layer) => {
    if (/\binset\b/.test(layer)) return false;
    // Drop function calls so percentages inside color-mix() are not read as lengths.
    let bare = layer;
    for (let i = 0; i < 4; i++) bare = bare.replace(/[\w-]*\([^()]*\)/g, " ");
    const lengths = bare.match(/-?\d*\.?\d+(?:px|rem|em)?\b/g) || [];
    if (lengths.length < 3) return false;
    const [x, y, blur] = lengths.map(parseFloat);
    return blur === 0 && (x !== 0 || y !== 0);
  });
}

/** Collapse `.fd2-btn:hover:not(:disabled)` and `.fd2-btn[data-tone="x"]` to
 *  `.fd2-btn`, so four state variants of one control count as one element. */
export function baseClass(selector) {
  const first = splitTop(selector, ",")[0].trim();
  const match = first.match(/\.[a-z0-9_-]+/i);
  return match ? match[0] : first.split(/[\s>+~:[]/)[0] || first;
}

// ---------------------------------------------------------------- rules

function markersFor(comments, line) {
  return comments.filter((c) => c.endLine === line || c.endLine === line - 1);
}

/**
 * An exemption may sit above the selector or beside the declaration. C3a is a
 * judgement about the whole rule, and its offset is often declared several
 * lines below the selector, so both positions have to count — otherwise the
 * only place a marker works is directly above the box-shadow, which reads as if
 * it excused the shadow rather than the region.
 */
function allowed(comments, lines, ruleId) {
  const candidates = [].concat(lines).flatMap((line) => markersFor(comments, line));
  return candidates.some((c) => {
    const match = c.text.match(/^composition-lint-allow:\s*([\w,\s]+?)\s*[—-]\s*(.+)$/s);
    if (!match) return false;
    const ids = match[1].split(/[,\s]+/).filter(Boolean);
    // A reason is mandatory: an exemption nobody had to justify is a silent
    // rule deletion, so an empty one stays a violation.
    return match[2].trim().length > 3 && ids.includes(ruleId);
  });
}

/**
 * Lint one stylesheet. Returns violations plus the measurements a reviewer
 * needs even when nothing failed (which regions carry depth, and where).
 */
export function lintCss(src, { file = "<css>", budget = DEPTH_BUDGET, inheritedVars } = {}) {
  const { rules, vars: own, comments } = parseCss(src);
  // A screen sheet reaches for tokens defined in the kit — `.sc-burn` gets its
  // offset from `var(--fdx-shadow-print)`, which lives in index.css. With a
  // per-file var map the lint could not resolve it and reported both anchors as
  // FLAT: the same blind spot as the two-hop alias, one level up. Own
  // definitions still win, so a screen can override a token locally.
  const vars = inheritedVars ? new Map([...inheritedVars, ...own]) : own;
  const violations = [];
  const push = (rule, line, message, scope = null) => {
    const at = scope === null ? line : [line, scope];
    if (!allowed(comments, at, rule)) violations.push({ rule, file, line, message });
  };

  // C1 — at most one anchor. Zero is correct for a component library.
  const anchors = comments.filter((c) => /^composition:\s*anchor\b/.test(c.text));
  if (anchors.length > 1) {
    for (const extra of anchors.slice(1)) {
      push(
        "C1",
        extra.line,
        `${anchors.length} anchors declared in one stylesheet; a screen with two anchors has none`,
      );
    }
  }
  const anchorLines = new Set(anchors.map((a) => a.endLine));

  // A component library defines depth per component; how many of them a screen
  // puts on one page is a composition fact its own stylesheet decides. So the
  // budget is a screen rule, and a sheet may declare itself out of it.
  const isLibrary = comments.some((c) => /^composition:\s*library\b/.test(c.text));

  // A screen with no anchor has not decided what it is for — C1's other half.
  // It is opt-in because declaring which region is the anchor is a design
  // decision, so an existing proposal is not retro-fitted with one by a lint;
  // a sheet that writes `composition: screen` is accepting the full contract.
  const declaresScreen = comments.some((c) => /^composition:\s*screen\b/.test(c.text));
  if (declaresScreen && anchors.length === 0) {
    violations.push({
      rule: "C1",
      file,
      line: 1,
      message: "declares itself a screen but no region is marked `composition: anchor`",
    });
  }

  // Which selector each anchor marker attaches to. A renderer can then be checked
  // against the same source of truth the lint uses, instead of a hand-kept list.
  const anchorSelectors = [];

  const depthBases = new Map();
  // Classes whose depth is UNCONDITIONAL — the rule carrying the offset names no
  // attribute and no pseudo-class, so every element with the class gets depth.
  // C3c needs this distinction: `.radio-channel[data-selected="true"]` gives
  // depth to one row, and flagging the class would punish the very pattern that
  // makes the gate visible.
  const unconditional = new Set();

  for (const rule of rules) {
    const isAnchor = [...anchorLines].some((l) => l === rule.line || l === rule.line - 1);
    if (isAnchor) anchorSelectors.push(rule.selector);

    for (const decl of rule.decls) {
      const resolved = resolveValue(decl.value, vars);

      // C3a / C3b — offset depth, excluding declared block material LAYER BY
      // LAYER. Testing the whole declaration was wrong: both anchors write
      // `var(--fdx-shadow-block), var(--fdx-shadow-print)`, so one block-material
      // layer made the lint blind to the print offset beside it — re-opening
      // exactly the blind spot the cross-file var fix closed.
      const controlLayers =
        decl.prop === "box-shadow"
          ? splitTop(decl.value, ",").filter((layer) => !BLOCK_MATERIAL.test(layer))
          : [];
      if (controlLayers.some((layer) => hasOffsetDepth(resolveValue(layer, vars)))) {
        const base = baseClass(rule.selector);
        const atRest = !TRANSIENT.test(rule.selector) && !FRAME.test(rule.selector);
        if (atRest && !depthBases.has(base)) {
          depthBases.set(base, { line: decl.line, selector: rule.selector });
        }
        const gated = /[[:]/.test(splitTop(rule.selector, ",")[0].trim());
        if (atRest && !gated) unconditional.add(base);
        if (!INTERACTIVE.test(rule.selector) && !FRAME.test(rule.selector) && !isAnchor) {
          push(
            "C3a",
            decl.line,
            `offset depth on \`${rule.selector}\`, which is neither interactive, a frame, nor the declared anchor — depth every element has is not depth`,
            rule.line,
          );
        }
      }

      // C6a — the card tint has exactly one home.
      if (CARD_ONLY.test(decl.value) && (decl.prop === "box-shadow" || decl.prop === "color")) {
        push(
          "C6a",
          decl.line,
          `\`${decl.prop}\` uses the card tint on \`${rule.selector}\`; pink is a card background, never a shadow and never text`,
          rule.line,
        );
      }

      // C6b — role token against the role the selector names.
      for (const role of ROLE_RULES) {
        if (role.selector.test(rule.selector) && role.banned.test(decl.value)) {
          push("C6b", decl.line, `${role.why} (\`${rule.selector}\` → \`${decl.value.trim()}\`)`, rule.line);
        }
      }
    }
  }

  if (!isLibrary && depthBases.size > budget) {
    const [, worst] = [...depthBases][budget];
    push(
      "C3b",
      worst.line,
      `${depthBases.size} elements carry depth at rest (budget ${budget}): ${[...depthBases.keys()].join(", ")}`,
    );
  }

  return {
    violations,
    depthBases: [...depthBases.keys()],
    unconditionalDepth: [...unconditional],
    anchorSelectors,
    anchors: anchors.length,
    kind: isLibrary ? "library" : "screen",
    exemptions: comments.filter((c) => /^composition-lint-allow:/.test(c.text)).length,
  };
}

// ---------------------------------------------------------------- JSX

/**
 * C3c — depth inside a repeated list.
 *
 * A depth-bearing class written as a static string inside a `.map()` renders
 * one depth element per row, so a ten-session list ships ten. C3 allows depth
 * on "the active/selected item", which means the class must arrive through a
 * conditional expression. Static count elsewhere in the file is left alone —
 * that is C3b's job, and it reads the stylesheet.
 */
export function lintJsx(src, depthClasses, { file = "<jsx>" } = {}) {
  const violations = [];
  const names = depthClasses.map((c) => c.replace(/^\./, "")).filter(Boolean);
  if (!names.length) return { violations };

  // Comments are blanked first for two reasons: an exemption marker has to be
  // findable by line, and a `.map(` inside a comment must not count as a list.
  const { clean, comments } = stripComments(src);

  // Scanned as one string rather than line by line: `rows.map(r => <li
  // className="raised"/>)` opens and closes on a single line, and a per-line
  // scan that only checks openness at the end of the line misses exactly that.
  let parens = 0;
  // Each entry records the paren depth the .map( opened at AND its line, because
  // an exemption belongs above the `.map(` — the line the reader would annotate —
  // not above the className several lines inside it.
  const openMaps = [];
  let line = 1;

  for (let i = 0; i < clean.length; i++) {
    const ch = clean[i];
    if (ch === "\n") line++;
    if (clean.startsWith(".map(", i)) openMaps.push({ depth: parens, line });
    if (ch === "(") parens++;
    else if (ch === ")") {
      parens--;
      while (openMaps.length && parens <= openMaps[openMaps.length - 1].depth) openMaps.pop();
    }

    if (!openMaps.length || !clean.startsWith("className=", i)) continue;

    // Only a className written as a plain literal is unbounded; an expression
    // (`?`, `&&`, or a template hole) can gate depth on the selected row.
    const attr = /^className=(?:"([^"]*)"|'([^']*)')/.exec(clean.slice(i, i + 200));
    if (!attr) continue;
    const value = attr[1] ?? attr[2] ?? "";
    const hit = names.find((n) => value.split(/\s+/).includes(n));
    const mapLine = openMaps[openMaps.length - 1].line;
    if (hit && !allowed(comments, [line, mapLine], "C3c")) {
      violations.push({
        rule: "C3c",
        file,
        line,
        message: `\`${hit}\` carries offset depth and is rendered unconditionally inside a .map(); depth in a list belongs to the selected row only`,
      });
    }
  }

  return {
    violations,
    exemptions: comments.filter((c) => /^composition-lint-allow:/.test(c.text)).length,
  };
}

export const RULES = {
  CARD_ONLY,
  BLOCK_MATERIAL,
  FILL_PROPS,
  INTERACTIVE,
  FRAME,
  ROLE_RULES,
  DEPTH_BUDGET,
};
