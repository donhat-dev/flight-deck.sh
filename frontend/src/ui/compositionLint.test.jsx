/**
 * Composition lint tests.
 *
 * Two halves. First, every rule gets a live negative test — a fixture that
 * SHOULD trip it — because a guard nobody watched fail is a guard nobody knows
 * is wired up. Second, a contract test runs the lint over the real stylesheets
 * by glob, so a stylesheet added later (radio.css) is covered without editing
 * this file.
 */
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  baseClass, collectVars, hasOffsetDepth, lintCss, lintJsx, resolveValue,
} from "./compositionLint.js";

const SRC = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

const ids = (result) => result.violations.map((v) => v.rule);

/** Every non-test .jsx under src, so a new screen is covered without an edit. */
const walkJsx = (dir = SRC) =>
  fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return walkJsx(full);
    return e.isFile() && /\.jsx$/.test(e.name) && !/\.test\./.test(e.name) ? [full] : [];
  });

// ---------------------------------------------------------------- primitives

describe("offset depth detection", () => {
  it("sees hard offset depth and ignores rings and soft shadows", () => {
    expect(hasOffsetDepth("4px 4px 0 #f47f96")).toBe(true);
    expect(hasOffsetDepth("0 0 0 3px rgba(0,0,0,.2)")).toBe(false); // focus ring
    expect(hasOffsetDepth("0 20px 50px -30px rgba(22,104,227,.18)")).toBe(false); // ambient
    expect(hasOffsetDepth("inset 0 -3px 0 #e84d2a")).toBe(false); // inset underline
  });

  it("is not fooled by percentages inside color-mix()", () => {
    // The `18%` and `srgb` must not be read as the shadow's lengths.
    expect(hasOffsetDepth("0 0 0 3px color-mix(in srgb, #e84d2a 18%, transparent)")).toBe(false);
    expect(hasOffsetDepth("2px 2px 0 color-mix(in srgb, #e84d2a 40%, transparent)")).toBe(true);
  });

  it("follows custom properties through two hops", () => {
    // The real shape in index.css: the button's offset is two aliases away, so
    // a lint that does not resolve var() reports the button as flat.
    const css = `
      :root {
        --shadow-control: 4px 4px 0 var(--depth-pink);
        --button-shadow: var(--shadow-control);
        --depth-pink: #f47f96;
      }
      .btn { box-shadow: var(--button-shadow); }
    `;
    const { violations, depthBases } = lintCss(css);
    expect(depthBases).toContain(".btn");
    expect(violations).toEqual([]); // a button is interactive — allowed
    const { vars } = { vars: new Map([["--a", "var(--b)"], ["--b", "2px 2px 0 red"]]) };
    expect(resolveValue("var(--a)", vars)).toBe("2px 2px 0 red");
  });

  it("resolves a token defined in another stylesheet", () => {
    // Both screens get their anchor's offset from --fdx-shadow-print, which is
    // declared in the kit. Without inherited vars the lint called both anchors
    // flat — it never checked the one region it most needed to.
    const kit = `:root { --fdx-shadow-print: 4px 4px 0 #d94625; }`;
    const screen = `/* composition: anchor */\n.burn { box-shadow: var(--fdx-shadow-print); }`;

    expect(lintCss(screen).depthBases).toEqual([]); // blind, as it was
    expect(lintCss(screen, { inheritedVars: collectVars([kit]) }).depthBases).toEqual([".burn"]);

    // A screen may still override the token for itself.
    const own = `.burn { --fdx-shadow-print: none; box-shadow: var(--fdx-shadow-print); }`;
    expect(lintCss(own, { inheritedVars: collectVars([kit]) }).depthBases).toEqual([]);
  });

  it("does not loop forever on a self-referencing property", () => {
    const vars = new Map([["--a", "var(--a)"]]);
    expect(resolveValue("var(--a)", vars)).toBe("var(--a)");
  });

  it("collapses state variants onto one base class", () => {
    expect(baseClass('.fd2-btn:hover:not(:disabled)')).toBe(".fd2-btn");
    expect(baseClass('.fdx-toggle[data-checked="true"] .fdx-toggle-track')).toBe(".fdx-toggle");
    expect(baseClass(".a, .b")).toBe(".a");
  });
});

// ---------------------------------------------------------------- C3a

describe("C3a — depth marks interaction, not decoration", () => {
  it("FIRES on a static panel that lifts off the page", () => {
    const css = `.summary-card { box-shadow: 5px 5px 0 #ccc; }`;
    expect(ids(lintCss(css))).toEqual(["C3a"]);
  });

  it("allows an interactive control", () => {
    expect(ids(lintCss(`.fd2-btn:hover { box-shadow: 5px 5px 0 #ccc; }`))).toEqual([]);
  });

  it("allows the page shell, which is ground rather than figure", () => {
    expect(ids(lintCss(`.wb-shell { box-shadow: 6px 6px 0 #ccc; }`))).toEqual([]);
  });

  it("accepts an ARIA interaction state", () => {
    // aria-pressed IS an interaction state by definition, so a segmented
    // control whose selected segment stands proud is allowed — this is how
    // Spend's range control stopped putting depth on all four segments.
    expect(ids(lintCss(`.sc-range-seg[aria-pressed="true"] { box-shadow: 2px 2px 0 #ccc; }`)))
      .toEqual([]);
    // …but a plain aria label is not a state, so it earns nothing.
    expect(ids(lintCss(`.panel[aria-label="Signal"] { box-shadow: 2px 2px 0 #ccc; }`)))
      .toEqual(["C3a"]);
  });

  it("treats a trigger as the control it is", () => {
    expect(ids(lintCss(`.wb-trace-trigger { box-shadow: 4px 4px 0 #ccc; }`))).toEqual([]);
  });

  it("attaches a marker whose reason wrapped onto a second line", () => {
    const css = `/* composition-lint-allow: C3a — a reason long enough that it
       wraps, which is the normal case for an honest one */
      .summary-card { box-shadow: 5px 5px 0 #ccc; }`;
    expect(ids(lintCss(css))).toEqual([]);
  });

  it("allows a static region ONLY when it is the declared anchor", () => {
    const bare = `.on-air { box-shadow: 5px 5px 0 #ccc; }`;
    expect(ids(lintCss(bare))).toEqual(["C3a"]);
    const marked = `/* composition: anchor */\n.on-air { box-shadow: 5px 5px 0 #ccc; }`;
    expect(ids(lintCss(marked))).toEqual([]);
  });
});

// ---------------------------------------------------------------- C1

describe("C1 — one anchor per screen", () => {
  it("FIRES on a second anchor", () => {
    const css = `
      /* composition: anchor */
      .on-air { padding: 2rem; }
      /* composition: anchor */
      .channels { padding: 1rem; }
    `;
    expect(ids(lintCss(css))).toEqual(["C1"]);
  });

  it("accepts zero anchors, which is correct for a component library", () => {
    expect(lintCss(`.fdx-btn { color: red; }`).anchors).toBe(0);
  });

  it("FIRES when a sheet claims to be a screen and names no anchor", () => {
    const css = `/* composition: screen */\n.panel { padding: 1rem; }`;
    expect(ids(lintCss(css))).toEqual(["C1"]);

    const withAnchor = `/* composition: screen */
      /* composition: anchor */
      .on-air { padding: 4rem; }`;
    expect(ids(lintCss(withAnchor))).toEqual([]);
  });
});

// ---------------------------------------------------------------- C3b

describe("C3b — the depth budget", () => {
  it("counts only depth visible at rest", () => {
    // Depth that appears while the pointer is down is not competing for
    // attention, so it must not spend the budget.
    const css = [1, 2, 3, 4, 5, 6]
      .map((n) => `.btn-${n}:hover { box-shadow: 4px 4px 0 #ccc; }`)
      .join("\n");
    expect(ids(lintCss(css))).toEqual([]);
    expect(lintCss(css).depthBases).toEqual([]);
  });

  it("does not spend budget on frames", () => {
    const css = ["shell", "page", "board", "sheet", "frame"]
      .map((n) => `.x-${n} { box-shadow: 6px 6px 0 #ccc; }`)
      .join("\n");
    expect(ids(lintCss(css))).toEqual([]);
  });

  it("exempts a sheet that declares itself a component library", () => {
    const five = [1, 2, 3, 4, 5]
      .map((n) => `.btn-${n} { box-shadow: 4px 4px 0 #ccc; }`)
      .join("\n");
    expect(ids(lintCss(five))).toEqual(["C3b"]);
    const library = `/* composition: library */\n${five}`;
    expect(ids(lintCss(library))).toEqual([]);
    expect(lintCss(library).kind).toBe("library");
  });

  it("FIRES past the budget, counting bases not declarations", () => {
    const variants = ["", ":hover", ":active", ":disabled"]
      .map((s) => `.btn${s} { box-shadow: 4px 4px 0 #ccc; }`)
      .join("\n");
    expect(ids(lintCss(variants))).toEqual([]); // four states, one element

    const five = [1, 2, 3, 4, 5]
      .map((n) => `.btn-${n} { box-shadow: 4px 4px 0 #ccc; }`)
      .join("\n");
    expect(ids(lintCss(five))).toEqual(["C3b"]);
    expect(ids(lintCss(five, { budget: 5 }))).toEqual([]);
  });
});

// ---------------------------------------------------------------- C6a

describe("C6a — depth materials are not accents", () => {
  it("FIRES when Pink Depth becomes a fill or a text colour", () => {
    expect(ids(lintCss(`.chart-bar { background: var(--fdx-depth-pink); }`))).toEqual(["C6a"]);
    expect(ids(lintCss(`.label { color: var(--fd2-depth-orange); }`))).toEqual(["C6a"]);
    expect(ids(lintCss(`.pill { border-color: var(--fdx-depth-pink); }`))).toEqual(["C6a"]);
  });

  it("allows Pink Depth in the offset it exists for", () => {
    expect(ids(lintCss(`.btn:hover { box-shadow: 4px 4px 0 var(--fdx-depth-pink); }`))).toEqual([]);
  });

  it("leaves ink and muted alone — frame and mass are legitimate fills", () => {
    // The toggle knob is filled with ink. Banning every --*-depth-* token would
    // have flagged it, and 'fixing' it would have been a change for nothing.
    expect(ids(lintCss(`.fdx-toggle-track i { background: var(--fdx-depth-ink); }`))).toEqual([]);
    expect(ids(lintCss(`.bar { background: var(--fd2-depth-muted); }`))).toEqual([]);
  });

  it("honours an exemption that carries a reason, and rejects one that does not", () => {
    const withReason = `/* composition-lint-allow: C6a — a legend swatch displays the material itself */
      .depth-key i { background: var(--fdx-depth-pink); }`;
    expect(ids(lintCss(withReason))).toEqual([]);

    const bare = `/* composition-lint-allow: C6a */
      .depth-key i { background: var(--fdx-depth-pink); }`;
    expect(ids(lintCss(bare))).toEqual(["C6a"]);
  });
});

// ---------------------------------------------------------------- C6b

describe("C6b — a role wears its own colour", () => {
  it("FIRES on an error state wearing the action colour", () => {
    expect(ids(lintCss(`.field-error { color: var(--fdx-action-face); }`))).toEqual(["C6b"]);
  });

  it("FIRES on loading borrowing the failure colour", () => {
    expect(ids(lintCss(`.is-loading { border-color: var(--fd2-critical); }`))).toEqual(["C6b"]);
  });

  it("FIRES on caution borrowing the failure colour", () => {
    expect(ids(lintCss(`.badge-warning { color: var(--fd2-critical); }`))).toEqual(["C6b"]);
  });

  it("leaves each role using its own token", () => {
    const css = `
      .field-error { color: var(--fdx-critical); }
      .is-loading { color: var(--fdx-warning); }
      .badge-warning { color: var(--fd2-warning); }
      .btn-primary { background: var(--fdx-action-face); }
    `;
    expect(ids(lintCss(css))).toEqual([]);
  });
});

// ---------------------------------------------------------------- C3c (JSX)

describe("C3c — depth inside a repeated list", () => {
  it("FIRES on a depth class rendered once per row", () => {
    const jsx = `
      <ul>
        {sessions.map((s) => (
          <li key={s.id} className="channel-row raised">{s.name}</li>
        ))}
      </ul>`;
    expect(ids(lintJsx(jsx, [".raised"]))).toEqual(["C3c"]);
  });

  it("allows depth gated on the selected row", () => {
    const jsx = `
      {sessions.map((s) => (
        <li className={s.id === selected ? "channel-row raised" : "channel-row"}>{s.name}</li>
      ))}`;
    expect(ids(lintJsx(jsx, [".raised"]))).toEqual([]);
  });

  it("separates unconditional depth from attribute-gated depth", () => {
    // This distinction was found by the lint flagging Radio's own channel rows.
    // The class is written statically in the .map(), but the depth belongs to
    // `[data-selected="true"]`, so only one row ever has it. Flagging the class
    // would have punished the pattern that makes the gate visible in the first
    // place; the fix is to feed C3c only classes whose depth has no gate.
    const gated = `.row { padding: 1rem; }
      .row[data-selected="true"] { box-shadow: 2px 2px 0 #ccc; }`;
    expect(lintCss(gated).unconditionalDepth).toEqual([]);
    expect(lintCss(gated).depthBases).toEqual([".row"]);

    const always = `.row:not(.x) { box-shadow: 2px 2px 0 #ccc; }
      .card-raised { box-shadow: 2px 2px 0 #ccc; }`;
    expect(lintCss(always).unconditionalDepth).toEqual([".card-raised"]);

    const jsx = `{rows.map((r) => <li className="row" key={r.id}>{r.n}</li>)}`;
    expect(ids(lintJsx(jsx, lintCss(gated).unconditionalDepth))).toEqual([]);
    expect(ids(lintJsx(jsx, [".row"]))).toEqual(["C3c"]);
  });

  it("honours a JSX exemption above the .map(, and still needs a reason", () => {
    // Same mechanism as the CSS side, because the neo-brutalist tab bank is a
    // real override: every key in a preset bank stands proud.
    const withReason = `
      {/* composition-lint-allow: C3c — a preset bank, bounded by a code change */}
      {TABS.map((t) => <button className="raised" key={t.k}>{t.label}</button>)}`;
    expect(ids(lintJsx(withReason, [".raised"]))).toEqual([]);

    const bare = `
      {/* composition-lint-allow: C3c */}
      {TABS.map((t) => <button className="raised" key={t.k}>{t.label}</button>)}`;
    expect(ids(lintJsx(bare, [".raised"]))).toEqual(["C3c"]);
  });

  it("does not treat a .map( inside a comment as a list", () => {
    const jsx = `/* rows.map((r) => <li className="raised" />) */
<b className="raised" />`;
    expect(ids(lintJsx(jsx, [".raised"]))).toEqual([]);
  });

  it("ignores depth outside any list", () => {
    const jsx = `<section className="on-air raised">…</section>`;
    expect(ids(lintJsx(jsx, [".raised"]))).toEqual([]);
  });

  it("stops treating code as list-scoped once the map closes", () => {
    const jsx = `
      {rows.map((r) => <li key={r.id}>{r.n}</li>)}
      <footer className="raised">total</footer>`;
    expect(ids(lintJsx(jsx, [".raised"]))).toEqual([]);
  });
});

// ---------------------------------------------------------------- the contract

describe("the real stylesheets satisfy the contract", () => {
  const sheets = fs
    .readdirSync(SRC, { withFileTypes: true })
    .filter((e) => e.isFile() && e.name.endsWith(".css"))
    .map((e) => e.name);

  it("finds the stylesheets it is supposed to guard", () => {
    // Guards the glob itself: a rename that silently emptied this list would
    // otherwise make every test below pass by vacuity.
    expect(sheets).toContain("index.css");
    expect(sheets.length).toBeGreaterThanOrEqual(3);
  });

  const read = (name) => fs.readFileSync(path.join(SRC, name), "utf8");
  const kitVars = collectVars(sheets.map(read));

  it.each(sheets)("%s — no C3 or C6 violations", (name) => {
    const src = read(name);
    const { violations } = lintCss(src, { file: name, inheritedVars: kitVars });
    const detail = violations.map((v) => `${v.file}:${v.line} [${v.rule}] ${v.message}`);
    expect(detail).toEqual([]);
  });

  it("no screen renders depth once per list row", () => {
    // The class set comes from the stylesheets rather than a hand-written list,
    // so a new depth-bearing class is covered the day it is written.
    const depthClasses = sheets.flatMap(
      (name) => lintCss(read(name), { inheritedVars: kitVars }).unconditionalDepth,
    );
    const files = walkJsx();
    // Without these two, an empty class set or an empty file list would make
    // the assertion below pass while checking nothing.
    expect(depthClasses.length).toBeGreaterThan(3);
    expect(files.length).toBeGreaterThan(5);

    const violations = files.flatMap(
      (file) =>
        lintJsx(fs.readFileSync(file, "utf8"), depthClasses, {
          file: path.relative(SRC, file),
        }).violations,
    );
    expect(violations.map((v) => `${v.file}:${v.line} ${v.message}`)).toEqual([]);
  });

  it("does not accumulate exemptions", () => {
    // A ratchet. Every exemption is a place the rule was overridden, so their
    // number may fall but not rise: without this, the cheapest way past the
    // lint would be to keep adding markers until it guards nothing. Lowering
    // this number is a normal part of stage 3+; raising it needs a decision.
    //
    // Raised from 3 to 4 when the plane's tabs became neo-brutalist keys: every
    // key in a preset bank stands proud, which C3c reads as depth per list row.
    // That override is deliberate and its reason is in Plane.jsx.
    const CEILING = 4;
    const cssMarkers = sheets.reduce((sum, name) => sum + lintCss(read(name)).exemptions, 0);
    const jsxMarkers = walkJsx().reduce(
      (sum, file) => sum + lintJsx(fs.readFileSync(file, "utf8"), [".x"]).exemptions,
      0,
    );
    expect(cssMarkers + jsxMarkers).toBeLessThanOrEqual(CEILING);
  });
});
