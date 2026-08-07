/**
 * The summary panel's contract: everything it shows comes off the BOARD.
 *
 * That is the property worth a test rather than the markup. The panel exists so that
 * reading one blip costs no request and no navigation, and the way that guarantee
 * breaks is quietly — someone reaches for a field only `blip_detail` returns (`moves`,
 * `evidence`), it renders as undefined in development where the detail happens to be
 * cached, and the panel silently starts needing a fetch it never makes.
 *
 * So these tests render it with a BOARD-SHAPED blip only: exactly the keys
 * `service.blip_view` produces, and nothing from `blip_detail`.
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import SummaryPanel from "./SummaryPanel.jsx";

/** Exactly `service.blip_view`'s output. No `moves`, no `evidence` — on purpose. */
const BOARD_BLIP = {
  id: "blip_f0e14f1b8e3b",
  num: 13,
  name: "Radar MCP",
  quadrant: "tools",
  ring: "trial",
  state: "in",
  period: "Q3 2026",
  lastMove: "Q3 2026 → Trial",
  why: "15 tools, verified over the real stdio transport.",
  moveCount: 2,
  evidenceCount: 2,
  evidenceAgeDays: 0,
};

const html = (over = {}) =>
  renderToStaticMarkup(
    <SummaryPanel blip={{ ...BOARD_BLIP, ...over }} onOpenDetail={() => {}} onClose={() => {}} />,
  );

describe("it renders from board data alone", () => {
  it("needs no field that only the detail endpoint returns", () => {
    // The board blip has no `moves` and no `evidence` array. If the panel ever reads
    // one, this render throws or silently prints nothing — either way the test moves.
    const out = html();
    expect(out).toContain("Radar MCP");
    expect(out).toContain("15 tools, verified over the real stdio transport.");
    expect(out).toContain("Q3 2026 → Trial");
    expect(out).not.toContain("undefined");
    expect(out).not.toContain("NaN");
  });

  it("labels every number it shows", () => {
    // Four bare numbers in a row is a puzzle. Each is a dt/dd pair instead.
    const out = html();
    for (const label of ["Quadrant", "Last move", "Moves", "Evidence"]) {
      expect(out).toContain(label);
    }
    expect(out).toContain("Tools");
  });

  it("marks the current ring on the position track, and only that one", () => {
    // Rewritten: the first version asserted the strings "Trial" and "Entered" were
    // present, and kept passing after the ring badge was removed — the track prints
    // all four ring names and `lastMove` happened to contain "Entered". It was testing
    // that the words exist somewhere, which they always do.
    const out = html({ ring: "trial" });
    expect((out.match(/aria-current="true"/g) || []).length).toBe(1);
    // The marked segment is the Trial one: its label follows the marker.
    const marked = out.slice(out.indexOf('aria-current="true"'));
    expect(marked.slice(0, 200)).toContain("Trial");
  });

  it("marks nothing when the blip has entered but is not placed", () => {
    const out = html({ ring: null, state: "new", lastMove: "Q3 2026 → Entered" });
    expect(out).not.toContain('aria-current="true"');
    // And the reader is still told what happened, from the move rather than the ring.
    expect(out).toContain("Q3 2026 → Entered");
  });
});

describe("the two ways out are distinguishable", () => {
  it("offers the detail as the primary action and a close beside it", () => {
    const out = html();
    expect(out).toContain("Open detail");
    expect(out).toContain('data-variant="primary"');
    expect(out).toContain("Close the summary");
  });

  it("is labelled for a screen reader by the blip it describes", () => {
    expect(html()).toContain('aria-label="Summary of blip 13"');
  });
});

describe("staleness is stated where it belongs", () => {
  it("hangs the age off EVIDENCE, not off the ring", () => {
    const out = html({ evidenceAgeDays: 94 });
    expect(out).toContain("94d old");
    // A blip does not go stale; its citations do. The flag must not attach itself to
    // the position, which is a different claim and a different colour.
    const evidenceAt = out.indexOf("Evidence");
    expect(out.indexOf("94d old")).toBeGreaterThan(evidenceAt);
  });

  it("says nothing at all when the evidence is fresh", () => {
    expect(html({ evidenceAgeDays: 0 })).not.toContain("old");
    expect(html({ evidenceAgeDays: 60 })).not.toContain("old"); // 60 is the boundary
  });
});
