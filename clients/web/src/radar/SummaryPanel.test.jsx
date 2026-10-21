/**
 * The summary panel's contract: everything it shows comes off the BOARD.
 *
 * That is the property worth a test rather than the markup. The panel exists so that
 * reading one blip costs no request and no navigation, and the way that guarantee breaks
 * is quietly — someone reaches for a field only `blip_detail` returns (`moves`,
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
  description: "A stdio JSON-RPC server that hands an agent the whole radar.",
  ref: "https://example.invalid/radar-mcp",
  ring: "trial",
  state: "in",
  period: "Q3 2026",
  lastMove: "Q3 2026 → Trial",
  why: "It closed the gap where adding a blip was reachable only from seed.py.",
  moveCount: 2,
  evidenceCount: 2,
  evidenceAgeDays: 0,
  related: [
    { num: 11, name: "Treasures MCP", quadrant: "tools", ring: "adopt" },
    { num: 19, name: "MUI", quadrant: "lang", ring: "caution" },
  ],
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
    expect(out).toContain("reachable only from seed.py");
    expect(out).not.toContain("undefined");
    expect(out).not.toContain("NaN");
  });

  it("shows the definition and the ring argument as two separate claims", () => {
    // The Thoughtworks anatomy this panel follows: what it IS, then why it is THERE.
    // They are different kinds of statement and a reader can accept one and reject the
    // other, which is why they are not one paragraph.
    const out = html();
    expect(out).toContain("What it is");
    expect(out).toContain("hands an agent the whole radar");
    expect(out).toContain("Why it is in Trial");
    expect(out).toContain('data-weight="argument"');
  });

  it("omits the definition block entirely when there is no definition", () => {
    // Rather than an empty section with a heading over nothing: most blips predate the
    // field, and a labelled blank reads as a loading failure.
    const out = html({ description: null });
    expect(out).not.toContain("What it is");
    expect(out).toContain("Why it is in Trial");
  });

  it("heads the argument with the ring it is arguing for", () => {
    expect(html({ ring: "adopt" })).toContain("Why it is in Adopt");
    // An unplaced blip has no ring to argue for, so the heading states the weaker claim.
    expect(html({ ring: null })).toContain("Why it is on the radar");
  });
});

describe("the related blips carry their own positions", () => {
  it("shows each one's name and its OWN ring", () => {
    const out = html();
    expect(out).toContain("Treasures MCP");
    expect(out).toContain("Adopt");
    expect(out).toContain("MUI");
    expect(out).toContain("Caution");
    // The ring is tagged so caution and adopt can be coloured; a related blip parked in
    // Caution is often the reason this one sits where it does.
    expect(out).toContain('data-ring="caution"');
  });

  it("omits the whole section when nothing is related", () => {
    expect(html({ related: [] })).not.toContain("Related blips");
  });

  it("survives a board that predates the field", () => {
    // `related` is absent, not empty, on any response older than the link table.
    const out = html({ related: undefined });
    expect(out).not.toContain("Related blips");
    expect(out).not.toContain("undefined");
  });
});

describe("the ways out are distinguishable", () => {
  it("offers the history from the move line and from the foot", () => {
    const out = html();
    expect(out).toContain("View blip history");
    expect(out).toContain("Open history");
    expect(out).toContain('data-variant="primary"');
    expect(out).toContain("Close the summary");
  });

  it("links out only when the blip has somewhere to link to", () => {
    expect(html()).toContain('href="https://example.invalid/radar-mcp"');
    // An always-present link that is sometimes dead is worse than an absent one.
    expect(html({ ref: null })).not.toContain("<a ");
  });

  it("is labelled for a screen reader by the blip it describes", () => {
    expect(html()).toContain('aria-label="Summary of blip 13"');
  });
});

describe("the move line states the transition", () => {
  it("names the period and the ring it landed in", () => {
    expect(html()).toContain("Q3 2026");
    expect(html()).toContain("Trial");
  });

  it("says so plainly when the blip has only entered", () => {
    expect(html({ ring: null, state: "new" })).toContain("Entered the radar");
  });
});
