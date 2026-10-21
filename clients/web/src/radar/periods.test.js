import { describe, expect, it } from "vitest";

import { currentQuarter, periodOptions } from "./periods.js";

/** The board's shape, oldest first, as `service._periods` returns it. */
const PERIODS = [
  { key: "Q1 2026", moves: 4, current: false },
  { key: "Q2 2026", moves: 9, current: false },
  { key: "Q3 2026", moves: 6, current: true },
];

describe("the quarter a move lands in", () => {
  it("maps each month to its quarter", () => {
    // Boundaries, not midpoints: an off-by-one in the /3 shows up at the edges.
    expect(currentQuarter(new Date("2026-01-01T00:00:00Z"))).toBe("Q1 2026");
    expect(currentQuarter(new Date("2026-03-31T23:59:59Z"))).toBe("Q1 2026");
    expect(currentQuarter(new Date("2026-04-01T00:00:00Z"))).toBe("Q2 2026");
    expect(currentQuarter(new Date("2026-08-07T00:00:00Z"))).toBe("Q3 2026");
    expect(currentQuarter(new Date("2026-12-31T00:00:00Z"))).toBe("Q4 2026");
  });

  it("reads the date in UTC, not the reader's zone", () => {
    // A move recorded from Vietnam (UTC+7) just after midnight on 1 April must not
    // land in Q1 because the local clock has not turned over yet.
    expect(currentQuarter(new Date("2026-04-01T00:30:00Z"))).toBe("Q2 2026");
  });
});

describe("the periods a move may be recorded into", () => {
  const today = new Date("2026-08-07T00:00:00Z");

  it("offers the current quarter first, then the known ones newest first", () => {
    expect(periodOptions(PERIODS, today).map((o) => o.key))
      .toEqual(["Q3 2026", "Q2 2026", "Q1 2026"]);
  });

  it("never lists the current quarter twice", () => {
    const keys = periodOptions(PERIODS, today).map((o) => o.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("marks the current quarter new only when it holds no moves yet", () => {
    // Q3 already has moves here, so it is a continuation…
    expect(periodOptions(PERIODS, today)[0]).toEqual({ key: "Q3 2026", isNew: false });
    // …but in Q4 nothing has been recorded, and the form says so.
    const q4 = periodOptions(PERIODS, new Date("2026-11-02T00:00:00Z"));
    expect(q4[0]).toEqual({ key: "Q4 2026", isNew: true });
    expect(q4.map((o) => o.key)).toEqual(["Q4 2026", "Q3 2026", "Q2 2026", "Q1 2026"]);
  });

  it("offers no future quarter", () => {
    // Recording a move into a quarter that has not started is a claim about work
    // not yet done, so the option does not exist.
    expect(periodOptions(PERIODS, today).map((o) => o.key)).not.toContain("Q4 2026");
  });

  it("still offers the current quarter on a radar with no history at all", () => {
    expect(periodOptions([], today)).toEqual([{ key: "Q3 2026", isNew: true }]);
  });
});
