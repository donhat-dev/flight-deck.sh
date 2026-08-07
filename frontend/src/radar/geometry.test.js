/**
 * Every blip must land inside the ring and quadrant it claims.
 *
 * This is the test that exists because eyeballing failed. While drawing the radar
 * in Pencil the placement looked wrong twice, and both times measuring the actual
 * coordinates showed it was correct — so a screenshot is not evidence here, and
 * "fixing" what the eye reported would have broken a working layout. The check is
 * cheap and it is the only thing standing between a mirrored y-axis and a radar
 * that quietly puts Platforms where Tools should be.
 */
import { describe, expect, it } from "vitest";

import { BLIPS } from "./fixtures.js";
import {
  QUADRANTS, RINGS, RING_EDGE, arcFacing, directionTo, isStale, placeBlips, polar,
  quadrantOf, ringBand, sectorPath,
} from "./geometry.js";

describe("polar is measured the way a reader reads it", () => {
  it("puts 90 degrees UP, not down", () => {
    // SVG's y grows downward, so the y term has to be subtracted. Get this
    // backwards and the whole radar mirrors vertically.
    const p = polar(100, 100, 50, 90);
    expect(p.x).toBeCloseTo(100, 6);
    expect(p.y).toBeCloseTo(50, 6);
  });

  it("puts 0 degrees to the right and 180 to the left", () => {
    // toBeCloseTo, not toEqual: `cy - r*sin(0)` is 0 while the literal -0 the eye
    // expects is a different value to a deep-equality check, and signed zero is
    // not a fact about the radar.
    expect(polar(0, 0, 10, 0).x).toBeCloseTo(10, 6);
    expect(polar(0, 0, 10, 0).y).toBeCloseTo(0, 6);
    expect(polar(0, 0, 10, 180).x).toBeCloseTo(-10, 6);
  });
});

describe("ring bands tile the radius without gaps or overlap", () => {
  it("starts at zero, ends at one, and each band begins where the last ended", () => {
    let prev = 0;
    for (const ring of RINGS) {
      const [lo, hi] = ringBand(ring);
      expect(lo).toBeCloseTo(prev, 6);
      expect(hi).toBeGreaterThan(lo);
      prev = hi;
    }
    expect(prev).toBeCloseTo(1, 6);
  });

  it("gives Adopt the widest band and Caution the narrowest", () => {
    // Not decoration: equal bands would say the four rings carry equal weight.
    const width = (r) => { const [lo, hi] = ringBand(r); return hi - lo; };
    expect(width("adopt")).toBeGreaterThan(width("trial"));
    expect(width("caution")).toBeLessThan(width("assess"));
  });

  it("keeps the outermost edge at exactly the radar radius", () => {
    expect(RING_EDGE[RINGS[RINGS.length - 1]]).toBe(1);
  });
});

describe("every blip lands in its declared ring and quadrant", () => {
  const placed = placeBlips(BLIPS);

  it("places all of them", () => {
    expect(placed).toHaveLength(BLIPS.length);
    expect(BLIPS.length).toBeGreaterThan(20); // anti-vacuity: a trimmed seed would pass silently
  });

  it("keeps each radius inside its ring band", () => {
    for (const b of placed) {
      const [lo, hi] = ringBand(b.ring);
      expect(b.rFrac, `#${b.num} ${b.name}`).toBeGreaterThan(lo);
      expect(b.rFrac, `#${b.num} ${b.name}`).toBeLessThanOrEqual(hi);
    }
  });

  it("keeps each angle inside its quadrant", () => {
    for (const b of placed) {
      const turn = quadrantOf(b.quadrant).turn;
      expect(b.deg, `#${b.num} ${b.name}`).toBeGreaterThanOrEqual(turn * 90);
      expect(b.deg, `#${b.num} ${b.name}`).toBeLessThan(turn * 90 + 90);
    }
  });

  it("never sits on a quadrant seam", () => {
    // A blip exactly on a boundary reads as belonging to the neighbour, which is
    // worse than being slightly off-centre in its own sector.
    for (const b of placed) {
      const within = b.deg - quadrantOf(b.quadrant).turn * 90;
      expect(within).toBeGreaterThan(2);
      expect(within).toBeLessThan(88);
    }
  });

  it("keeps the innermost ring off the centre point", () => {
    // r=0 is a single pixel; a blip placed there cannot be drawn or clicked.
    for (const b of placed.filter((x) => x.ring === RINGS[0])) {
      expect(b.rFrac).toBeGreaterThan(RING_EDGE.adopt * 0.2);
    }
  });

  it("separates blips that share a ring and quadrant", () => {
    const seen = new Map();
    for (const b of placed) {
      const key = `${b.quadrant}|${b.ring}`;
      for (const other of seen.get(key) || []) {
        const apart = Math.abs(other.deg - b.deg) > 6
          || Math.abs(other.rFrac - b.rFrac) > 0.02;
        expect(apart, `#${b.num} overlaps #${other.num}`).toBe(true);
      }
      seen.set(key, [...(seen.get(key) || []), b]);
    }
  });

  it("is deterministic — the same input gives the same places", () => {
    // The whole page answers "did it move?". Placement that shifts between
    // renders makes two screenshots incomparable and the answer unknowable.
    const again = placeBlips(BLIPS);
    expect(again.map((b) => [b.num, b.rFrac, b.deg]))
      .toEqual(placed.map((b) => [b.num, b.rFrac, b.deg]));
  });
});

describe("the single-quadrant view places its blips in the local sector", () => {
  const platforms = BLIPS.filter((b) => b.quadrant === "platforms");
  const placed = placeBlips(platforms, { quadrant: "platforms" });

  it("folds every angle into 0-90 whichever quadrant is on screen", () => {
    // The quadrant view draws only one sector and always draws it at the origin,
    // so a blip carrying its global angle would land outside the drawn area.
    expect(placed.length).toBeGreaterThan(4);
    for (const b of placed) {
      expect(b.deg).toBeGreaterThan(0);
      expect(b.deg).toBeLessThan(90);
    }
  });

  it("does the same for a quadrant that is not the first one", () => {
    const tools = placeBlips(BLIPS.filter((b) => b.quadrant === "tools"), { quadrant: "tools" });
    expect(tools.length).toBeGreaterThan(2);
    for (const b of tools) expect(b.deg).toBeLessThan(90);
  });
});

describe("the movement arc faces the direction the blip travelled", () => {
  it("turns an inward move toward the centre and an outward move away", () => {
    expect(arcFacing("in", 30)).toBe(210);
    expect(arcFacing("out", 30)).toBe(30);
  });

  it("gives a held or new blip no facing arc", () => {
    // `new` wears a full ring and `held` wears nothing; neither has a direction,
    // and inventing one would claim a movement that did not happen.
    expect(arcFacing("held", 30)).toBeNull();
    expect(arcFacing("new", 30)).toBeNull();
  });
});

describe("a proposed move's direction agrees with the server's", () => {
  // The one derivation the client repeats. It has to match `service._direction`,
  // which orders the rings caution < assess < trial < adopt and calls a step toward
  // Adopt `in`. If these two ever disagree the form previews one direction and the
  // radar then draws the other, which is the exact failure the "derive server-side"
  // rule exists to prevent — so the agreement is pinned here.
  it("calls a step toward Adopt inward and a step toward Caution outward", () => {
    expect(directionTo("adopt", "trial")).toBe("out");
    expect(directionTo("adopt", "caution")).toBe("out");
    expect(directionTo("caution", "adopt")).toBe("in");
    expect(directionTo("assess", "trial")).toBe("in");
  });

  it("calls re-selecting the same ring a hold, not a move", () => {
    // Not `null` and not an error: holding a position with fresh evidence is a real
    // move the store accepts, and the form has to be able to label it.
    for (const r of RINGS) expect(directionTo(r, r)).toBe("held");
  });

  it("treats a blip with no ring yet as entering, whatever it enters at", () => {
    expect(directionTo(null, "assess")).toBe("new");
    expect(directionTo(undefined, "adopt")).toBe("new");
  });

  it("agrees with the radial order the drawing already uses", () => {
    // Derived from RINGS rather than a table of its own, so this checks the two are
    // still the same order: an inward move must always shorten the radius.
    for (let i = 1; i < RINGS.length; i++) {
      expect(directionTo(RINGS[i], RINGS[i - 1])).toBe("in");
      expect(ringBand(RINGS[i - 1])[1]).toBeLessThan(ringBand(RINGS[i])[1]);
    }
  });
});

describe("sector paths close", () => {
  it("draws a donut band as a closed path with both arcs", () => {
    const d = sectorPath(100, 100, 40, 80, 0, 90);
    expect(d.startsWith("M ")).toBe(true);
    expect(d.trim().endsWith("Z")).toBe(true);
    expect((d.match(/ A /g) || []).length).toBe(2);
  });

  it("draws the innermost ring as a pie wedge, not a donut", () => {
    // rIn=0 has no inner arc to trace; emitting one produces a degenerate path
    // that some renderers fill and others drop.
    const d = sectorPath(100, 100, 0, 80, 0, 90);
    expect((d.match(/ A /g) || []).length).toBe(1);
    expect(d).toContain("M 100 100");
  });
});

describe("staleness is a property of the evidence, not of the blip", () => {
  it("flags a blip whose newest evidence is over 60 days old", () => {
    expect(isStale({ evidenceAgeDays: 61 })).toBe(true);
    expect(isStale({ evidenceAgeDays: 60 })).toBe(false);
    expect(isStale({})).toBe(false);
  });

  it("finds the stale blips the radar header counts", () => {
    const stale = BLIPS.filter(isStale).map((b) => b.num).sort((a, b) => a - b);
    expect(stale).toEqual([7, 8, 9]);
  });
});

describe("the four quadrants cover the circle exactly once", () => {
  it("has four, with turns 0 to 3", () => {
    expect(QUADRANTS.map((q) => q.turn).sort()).toEqual([0, 1, 2, 3]);
    expect(new Set(QUADRANTS.map((q) => q.k)).size).toBe(4);
  });

  it("falls back to the first quadrant rather than returning undefined", () => {
    expect(quadrantOf("nope").k).toBe(QUADRANTS[0].k);
  });
});
