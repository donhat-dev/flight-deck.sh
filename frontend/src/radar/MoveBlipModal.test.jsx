/**
 * The move-blip form's guard, tested by attempting the blocked action.
 *
 * The point of this feature is that a position cannot exist without the reason that
 * produced it. `refusal()` is the client half of that promise, so every way of
 * getting past it gets a live negative test — a guard nobody has watched fail is a
 * guard nobody knows is wired up. The server half is tested in
 * `backend/tests/test_radar.py`, and it is the half that actually holds.
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import MoveBlipModal, { payload, refusal } from "./MoveBlipModal.jsx";

const BLIP = {
  id: "blip_5",
  num: 5,
  name: "OCA subscription_oca",
  quadrant: "platforms",
  ring: "adopt",
  state: "in",
  period: "Q2 2026",
  moveCount: 4,
  evidenceCount: 7,
  evidenceAgeDays: 12,
  moves: [],
  evidence: [],
};

const PERIODS = [
  { key: "Q2 2026", moves: 9, current: false },
  { key: "Q3 2026", moves: 6, current: true },
];

const complete = (over = {}) => ({
  ring: "trial",
  period: "Q3 2026",
  why: "The 1.0 upgrade changed the invoice hook signature.",
  evidence: [{ kind: "trace", title: "odoo19-oca invoice regression run", dated: "2026-08-04" }],
  ...over,
});

describe("a move with no reason is not recordable", () => {
  it("refuses an empty reason", () => {
    expect(refusal(complete({ why: "" }))).toMatch(/no reason/);
  });

  it("refuses a reason that is only whitespace", () => {
    // The one that a `!why` check would let through, and the only one a reader can
    // produce by accident.
    expect(refusal(complete({ why: "   \n\t " }))).toMatch(/no reason/);
  });
});

describe("a move with no evidence is not recordable", () => {
  it("refuses an empty evidence list", () => {
    expect(refusal(complete({ evidence: [] }))).toMatch(/at least one/);
  });

  it("refuses rows that exist but cite nothing", () => {
    // The shape the form actually starts in: one row, present and blank.
    expect(refusal(complete({ evidence: [{ kind: "treasure", title: "", dated: "" }] })))
      .toMatch(/at least one/);
  });

  it("accepts one cited row among blank ones", () => {
    expect(refusal(complete({
      evidence: [
        { kind: "treasure", title: "", dated: "" },
        { kind: "jira", title: "CRM-11197", dated: "" },
      ],
    }))).toBeNull();
  });
});

describe("a move with no ring or period is not recordable", () => {
  it("refuses each in turn", () => {
    expect(refusal(complete({ ring: "" }))).toMatch(/ring/);
    expect(refusal(complete({ period: "" }))).toMatch(/period/);
  });
});

it("accepts a complete draft", () => {
  // Anti-vacuity: without this, a `refusal` that returned a string unconditionally
  // would pass every test above.
  expect(refusal(complete())).toBeNull();
});

describe("the payload matches what the API accepts", () => {
  it("drops blank rows, trims, and nulls an empty date", () => {
    const body = payload(complete({
      why: "  held, with fresh evidence  ",
      evidence: [
        { kind: "treasure", title: "  doc 23  ", dated: "" },
        { kind: "note", title: "", dated: "2026-08-01" },
      ],
    }));
    expect(body.why).toBe("held, with fresh evidence");
    expect(body.evidence).toEqual([{ kind: "treasure", title: "doc 23", dated: null }]);
  });

  it("never produces an empty evidence array from an accepted draft", () => {
    // The API declares `evidence` with min_length=1 and no default, so an empty
    // array is a 422 the reader cannot act on. Refusal and payload have to agree
    // about which drafts get that far.
    const draft = complete();
    expect(refusal(draft)).toBeNull();
    expect(payload(draft).evidence.length).toBeGreaterThan(0);
  });
});

describe("the form as it first renders", () => {
  const html = renderToStaticMarkup(
    <MoveBlipModal slug="subscription-migration" blip={BLIP} periods={PERIODS}
                   onClose={() => {}} onRecorded={() => {}} />,
  );

  it("opens with the record key disabled, because the reason starts empty", () => {
    // Fail-closed at first paint. A form that starts submittable and only refuses
    // on click has already told the reader the move is fine.
    const key = html.match(/<button[^>]*type="submit"[^>]*>/)[0];
    expect(key).toContain("disabled");
    expect(html).toContain("Record move");
  });

  it("names both required fields before the reader submits anything", () => {
    expect(html).toContain("required");
    expect(html).toContain("at least one");
  });

  it("prints the rule the reason has to satisfy", () => {
    expect(html).toContain("A move with no reason is not recordable.");
  });

  it("is a labelled modal dialog", () => {
    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('aria-labelledby="rdr-move-title"');
    expect(html).toContain("OCA subscription_oca");
  });

  it("reads the rings outward-in, the way the radar is read", () => {
    const order = ["Caution", "Assess", "Trial", "Adopt"]
      .map((label) => html.indexOf(`>${label}<`));
    expect(order.every((i) => i >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("labels what each ring choice would mean for THIS blip", () => {
    // The blip sits in Adopt, so Trial is outward and Adopt itself is the position
    // being held. Both words have to be on screen before the choice is made.
    expect(html).toContain("outward");
    expect(html).toContain("now");
  });

  it("says re-selecting the current ring records a hold, since it opens there", () => {
    expect(html).toContain("records the position being held");
  });

  it("starts on the current quarter with one blank evidence row", () => {
    expect(html).toContain('value="Q3 2026"');
    expect((html.match(/Remove evidence/g) || []).length).toBe(1);
  });

  it("cannot remove the only evidence row", () => {
    // Reaching zero rows is the one state the form must not be able to enter, so
    // the key is disabled rather than the row silently un-removable.
    const remove = html.match(/<button[^>]*aria-label="Remove evidence 1"[^>]*>/)[0];
    expect(remove).toContain("disabled");
  });

  it("mirrors the server's own length bound on the reason", () => {
    // Matched case-insensitively: React's server renderer emits the attribute as
    // `maxLength` on a textarea, and HTML attribute names are case-insensitive, so
    // asserting the lowercase spelling would fail on markup that is in fact correct.
    expect(html).toMatch(/maxlength="2000"/i);
  });
});
