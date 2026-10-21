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
    // Under the depth ladder this was allowed, because a button was interactive.
    // Flat removed the allowance: what the test still proves is that the lint
    // sees a shadow two aliases away rather than reporting the button as flat.
    expect(violations.map((v) => v.rule)).toEqual(["C3"]);
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

describe("C3 — the product is flat", () => {
  it("FIRES on a static panel that lifts off the page", () => {
    expect(ids(lintCss(`.summary-card { box-shadow: 5px 5px 0 #ccc; }`))).toEqual(["C3"]);
  });

  it("FIRES on an interactive control too — this is the inversion", () => {
    // Under the depth ladder a control was the ONE thing allowed to lift. Flat
    // removed the rungs, so the allowance went with them: a button separates by
    // fill, a one-pixel rule and its pill, and none of those cast anything.
    expect(ids(lintCss(`.fd2-btn:hover { box-shadow: 5px 5px 0 #ccc; }`))).toEqual(["C3"]);
    expect(ids(lintCss(`.sc-range-seg[aria-pressed="true"] { box-shadow: 2px 2px 0 #ccc; }`)))
      .toEqual(["C3"]);
  });

  it("FIRES on the page shell, which used to be exempt as ground", () => {
    expect(ids(lintCss(`.wb-shell { box-shadow: 6px 6px 0 #ccc; }`))).toEqual(["C3"]);
  });

  it("catches the blurred form, which the old rule let through", () => {
    // `0 18px 48px` sat on the Day page shell for weeks: hasOffsetDepth only
    // looked for a hard offset, so a blur was invisible to the lint.
    expect(ids(lintCss(`.fd-shell { box-shadow: 0 18px 48px rgba(0,0,0,.28); }`))).toEqual(["C3"]);
    expect(ids(lintCss(`.fd-core { box-shadow: 0 1px 2px rgba(60,52,40,.12); }`))).toEqual(["C3"]);
  });

  it("allows inset, which describes an edge and casts nothing", () => {
    expect(ids(lintCss(`.row[data-selected="true"] { box-shadow: inset 2px 0 0 #e84d2a; }`)))
      .toEqual([]);
    expect(ids(lintCss(`.tab[aria-selected="true"] { box-shadow: inset 0 -4px 0 #e84d2a; }`)))
      .toEqual([]);
    expect(ids(lintCss(`.fd-core { box-shadow: inset 0 1px 0 rgba(255,255,255,.55); }`)))
      .toEqual([]);
  });

  it("allows the declared overlay, and still demands a reason", () => {
    const bare = `.fdx-console { box-shadow: 0 18px 48px rgba(0,0,0,.28); }`;
    expect(ids(lintCss(bare))).toEqual(["C3"]);
    const marked = `/* composition-lint-allow: C3 — an overlay separates from live
       content underneath, which a hairline cannot do */
      .fdx-console { box-shadow: 0 18px 48px rgba(0,0,0,.28); }`;
    expect(ids(lintCss(marked))).toEqual([]);
    const noReason = `/* composition-lint-allow: C3 — x */
      .fdx-console { box-shadow: 0 18px 48px rgba(0,0,0,.28); }`;
    expect(ids(lintCss(noReason))).toEqual(["C3"]);
  });

  it("resolves through a variable, so a token cannot hide a shadow", () => {
    const kit = `:root { --fdx-shadow-float: 0 18px 48px rgba(0,0,0,.28); }`;
    const use = `.panel { box-shadow: var(--fdx-shadow-float); }`;
    const vars = collectVars([kit]);
    expect(ids(lintCss(use, { inheritedVars: vars }))).toEqual(["C3"]);
  });

  it("has no budget any more, because zero is the budget", () => {
    const five = [1, 2, 3, 4, 5]
      .map((n) => `.btn-${n} { box-shadow: 4px 4px 0 #ccc; }`)
      .join("\n");
    // Five shadows are five violations, not one budget overrun.
    expect(ids(lintCss(five))).toEqual(["C3", "C3", "C3", "C3", "C3"]);
  });

  it("still catches an exempted shadow rendered once per row", () => {
    const jsx = `
      <ul>
        {sessions.map((s) => (
          <li key={s.id} className="channel-row raised">{s.name}</li>
        ))}
      </ul>`;
    expect(ids(lintJsx(jsx, [".raised"]))).toEqual(["C3"]);
  });

  it("allows a class gated on the selected row", () => {
    const jsx = `
      {sessions.map((s) => (
        <li className={s.id === selected ? "channel-row raised" : "channel-row"}>{s.name}</li>
      ))}`;
    expect(ids(lintJsx(jsx, [".raised"]))).toEqual([]);
  });
});

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

describe("C6a — the card tint has one home", () => {
  it("FIRES when pink becomes a shadow or text", () => {
    // Two violations now: pink in a shadow (C6a) and a shadow at all (C3).
    expect(ids(lintCss(`.btn { box-shadow: 4px 4px 0 var(--fdx-pink); }`))).toEqual(["C3", "C6a"]);
    expect(ids(lintCss(`.label { color: var(--fdx-card-tint); }`))).toEqual(["C6a"]);
  });

  it("allows pink where it belongs — a card background", () => {
    expect(ids(lintCss(`.row[data-selected="true"] { background: var(--fdx-card-tint); }`)))
      .toEqual([]);
  });

  it("leaves orange alone, because the token style makes it a face too", () => {
    // Day is an orange key with a black shadow, Night the reverse, so orange is
    // legitimately a fill. Banning it as "depth only" was the previous contract.
    expect(ids(lintCss(`.btn { background: var(--fdx-orange); }`))).toEqual([]);
    // C6a still leaves orange alone; C3 fires because it is a shadow.
    expect(ids(lintCss(`.btn:hover { box-shadow: 4px 4px 0 var(--fdx-orange); }`))).toEqual(["C3"]);
  });

  it("no longer exempts declared block material, because flat has no ground lift", () => {
    // This exemption existed so a softened block offset would not be read as
    // rationed control depth. Flat deleted the distinction: an outset is an
    // outset whatever the token is called, and the token name can no longer
    // buy one. The block recipe itself was removed from index.css.
    const kit = `:root { --fdx-shadow-block: 0.2rem 0.2rem 0 rgba(0,0,0,.2), 0 1rem 2rem -1rem rgba(0,0,0,.3); }`;
    const block = `.panel { box-shadow: var(--fdx-shadow-block); }`;
    const vars = collectVars([kit]);
    expect(ids(lintCss(block, { inheritedVars: vars }))).toEqual(["C3"]);

    // Written inline it fires for the same reason, not a different one.
    const inline = `.panel { box-shadow: 0.2rem 0.2rem 0 rgba(0,0,0,.2); }`;
    expect(ids(lintCss(inline))).toEqual(["C3"]);
  });

  it("honours an exemption that carries a reason, and rejects one that does not", () => {
    const withReason = `/* composition-lint-allow: C6a — a legend swatch displays the material itself */
      .depth-key i { color: var(--fdx-pink); }`;
    expect(ids(lintCss(withReason))).toEqual([]);

    const bare = `/* composition-lint-allow: C6a */
      .depth-key i { color: var(--fdx-pink); }`;
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
    // The old guard here asserted at least four depth-bearing classes existed,
    // which was true while depth was rationed and is false now that it is gone.
    // An empty set IS the contract, so what needs guarding is the file walk.
    // Exactly the one declared overlay. It stays in the set on purpose: if
    // someone ever renders the console once per row, the JSX half still fires.
    expect(depthClasses).toEqual([".fdx-console"]);
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
    // Went 3 → 4 while the plane's tabs were neo-brutalist keys, then back to 3
    // when that treatment was reverted. The mechanism the raise needed — JSX
    // exemptions — is kept and still tested; it just has no live user.
    const CEILING = 3;
    const cssMarkers = sheets.reduce((sum, name) => sum + lintCss(read(name)).exemptions, 0);
    const jsxMarkers = walkJsx().reduce(
      (sum, file) => sum + lintJsx(fs.readFileSync(file, "utf8"), [".x"]).exemptions,
      0,
    );
    expect(cssMarkers + jsxMarkers).toBeLessThanOrEqual(CEILING);
  });
});
