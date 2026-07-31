/**
 * The appearance catalogue must not offer a weight a face cannot set.
 *
 * This is the failure worth guarding: a browser given an unavailable weight rounds
 * silently to the nearest one it has, or synthesises bold by smearing outlines. No
 * error, no warning — just type that looks slightly wrong for reasons nobody can
 * find. So the offered weights are checked against what `fonts.css` actually
 * declares and what the entries actually import.
 */
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { FONTS, ROLES, byId, defaults, normalise } from "./appearance.js";

const SRC = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const fontsCss = fs.readFileSync(path.join(SRC, "fonts.css"), "utf8");
const entry = fs.readFileSync(path.join(SRC, "main.jsx"), "utf8");

/** Weights declared in fonts.css for one family: static values and variable ranges. */
function declaredWeights(family) {
  const weights = new Set();
  const ranges = [];
  const blocks = fontsCss.split("@font-face").slice(1);
  for (const b of blocks) {
    if (!new RegExp(`font-family:\\s*"${family}"`).test(b)) continue;
    const m = b.match(/font-weight:\s*(\d+)(?:\s+(\d+))?/);
    if (!m) continue;
    if (m[2]) ranges.push([Number(m[1]), Number(m[2])]);
    else weights.add(Number(m[1]));
  }
  return { weights, ranges };
}

describe("every offered weight is real", () => {
  it("checks the self-hosted faces against fonts.css", () => {
    const selfHosted = { Satoshi: "satoshi", "Space Grotesk": "space-grotesk",
                         "JetBrains Mono": "jetbrains-mono" };
    // Anti-vacuity: if the parse found nothing, the loop below would prove nothing.
    expect(Object.keys(selfHosted).length).toBe(3);

    for (const [family, id] of Object.entries(selfHosted)) {
      const { weights, ranges } = declaredWeights(family);
      expect(weights.size + ranges.length, `${family} has no @font-face`).toBeGreaterThan(0);
      for (const w of byId(id).weights) {
        const ok = weights.has(w) || ranges.some(([lo, hi]) => w >= lo && w <= hi);
        expect(ok, `${family} offers ${w} but fonts.css does not declare it`).toBe(true);
      }
    }
  });

  it("checks IBM Plex Mono against what the entry imports", () => {
    // Not a @font-face here — fontsource ships the CSS, so the truth is which
    // weight files are imported. An un-imported weight would be synthesised.
    const imported = [...entry.matchAll(/ibm-plex-mono\/(\d+)\.css/g)].map((m) => Number(m[1]));
    expect(imported.length).toBeGreaterThan(0);
    for (const w of byId("ibm-plex-mono").weights) {
      expect(imported, `IBM Plex Mono offers ${w} but no entry imports it`).toContain(w);
    }
  });

  it("gives every candidate a non-empty weight list", () => {
    for (const f of FONTS) {
      expect(f.weights.length, `${f.id} has no weights`).toBeGreaterThan(0);
    }
  });
});

describe("normalise keeps a choice renderable", () => {
  it("keeps an exact weight the face carries", () => {
    expect(normalise({ primary: { font: "satoshi", weight: 500 } }).primary)
      .toEqual({ font: "satoshi", weight: 500 });
  });

  it("moves to the NEAREST weight rather than to a default", () => {
    // Satoshi has no 600, and IBM Plex Mono has no 900. Landing on the role
    // default would throw away the intent: 900 means heavy, so 700 is the answer.
    expect(normalise({ primary: { font: "satoshi", weight: 600 } }).primary.weight).toBe(500);
    expect(normalise({ mono: { font: "ibm-plex-mono", weight: 900 } }).mono.weight).toBe(700);
  });

  it("drops an unknown font instead of trusting it", () => {
    // A stale id from a face that no longer ships would otherwise resolve to
    // nothing at all.
    const out = normalise({ label: { font: "comic-sans-9000", weight: 700 } }).label;
    expect(out.font).toBe(byId(defaults().label.font).id);
  });

  it("fills in every role, whatever it was given", () => {
    for (const raw of [null, {}, { primary: null }, { nonsense: 1 }]) {
      const out = normalise(raw);
      expect(Object.keys(out).sort()).toEqual(ROLES.map((r) => r.id).sort());
      for (const r of ROLES) {
        expect(byId(out[r.id].font).weights).toContain(out[r.id].weight);
      }
    }
  });
});
