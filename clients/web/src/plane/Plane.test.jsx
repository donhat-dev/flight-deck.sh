/**
 * The plane's core invariant, guarded at render time.
 *
 * The lint proves each stylesheet declares at most one anchor. That is not the
 * same claim as "the running page shows one anchor": a shell that mounted both
 * views at once would satisfy every sheet individually and still put two anchors
 * on screen — the failure C1 exists to prevent.
 *
 * So this renders the real shell and counts anchor-marked elements. The anchor
 * class list is read from the stylesheets via the lint, not hand-kept, so adding
 * a third tab with its own anchor is covered without editing this file.
 */
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import Plane from "./Plane.jsx";
import { collectVars, lintCss } from "../ui/compositionLint.js";

const SRC = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

/** Anchor class names, taken from the `composition: anchor` markers themselves. */
function anchorClasses() {
  const sheets = fs.readdirSync(SRC).filter((f) => f.endsWith(".css"));
  const kit = collectVars(sheets.map((f) => fs.readFileSync(path.join(SRC, f), "utf8")));
  return sheets
    .flatMap((f) => lintCss(fs.readFileSync(path.join(SRC, f), "utf8"), { inheritedVars: kit })
      .anchorSelectors)
    .map((sel) => (sel.match(/^\.([a-z0-9_-]+)/i) || [])[1])
    .filter(Boolean);
}

/** Count elements whose class list contains `name` as a whole token — substring
 *  matching would count `radio-onair-title` as an anchor. */
function countByClass(markup, name) {
  const attrs = markup.match(/class="[^"]*"/g) || [];
  return attrs.filter((a) => a.slice(7, -1).split(/\s+/).includes(name)).length;
}

describe("the plane mounts exactly one anchor", () => {
  const classes = anchorClasses();

  it("knows which classes are anchors", () => {
    // Anti-vacuity: an empty list would make the count assertion pass trivially.
    expect(classes.length).toBeGreaterThanOrEqual(2);
    expect(classes).toContain("radio-onair");
    expect(classes).toContain("sc-burn");
  });

  it("renders one anchor and one view, not both views", () => {
    const markup = renderToStaticMarkup(<Plane />);
    const total = classes.reduce((n, c) => n + countByClass(markup, c), 0);
    expect(total).toBe(1);
    // The shell itself must not be an anchor: the region belongs to the view.
    expect(countByClass(markup, "radio-plane")).toBe(1);
    expect(classes).not.toContain("radio-plane");
  });

  it("marks exactly one tab selected", () => {
    const markup = renderToStaticMarkup(<Plane />);
    expect((markup.match(/aria-selected="true"/g) || []).length).toBe(1);
    expect((markup.match(/role="tab"/g) || []).length).toBe(2);
  });
});
