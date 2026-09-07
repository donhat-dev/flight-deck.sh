import { describe, expect, it } from "vitest";

import { MISSING_COLUMN, layoutGraph } from "./graphLayout.js";

const DATA = {
  nodes: [
    { name: "alone", type: "feedback", in: 0, out: 0 },
    { name: "beta", type: "project", in: 1, out: 1 },
    { name: "alpha", type: "project", in: 0, out: 2 },
  ],
  edges: [
    { source: "alpha", target: "beta", broken: false },
    { source: "beta", target: "alpha", broken: false },
    { source: "alpha", target: "ghost", broken: true },
  ],
  missing_targets: ["ghost"],
  groups: { project: ["alpha", "beta"], feedback: ["alone"] },
};

describe("columns", () => {
  it("puts the biggest type first and the broken links last", () => {
    const { columns } = layoutGraph(DATA);
    expect(columns.map((c) => c.title)).toEqual(["project", "feedback", MISSING_COLUMN]);
  });

  it("drops the broken column when every link lands somewhere", () => {
    const clean = { ...DATA, missing_targets: [], edges: DATA.edges.slice(0, 2) };
    expect(layoutGraph(clean).columns.map((c) => c.title))
      .toEqual(["project", "feedback"]);
  });

  it("survives an empty payload instead of dividing by nothing", () => {
    const empty = layoutGraph({});
    expect(empty.nodes).toEqual([]);
    expect(empty.columns).toEqual([]);
    expect(empty.width).toBeGreaterThan(0);
  });
});

describe("positions", () => {
  it("gives the same answer twice, which is the whole reason not to run physics", () => {
    expect(layoutGraph(DATA).nodes).toEqual(layoutGraph(DATA).nodes);
  });

  it("stacks a column in the order the payload already sorted", () => {
    const column = layoutGraph(DATA).nodes.filter((n) => n.column === "project");
    expect(column.map((n) => n.name)).toEqual(["beta", "alpha"]);
    expect(column[0].x).toBe(column[1].x);
    expect(column[1].y).toBeGreaterThan(column[0].y);
  });

  it("marks the memory with no links either way", () => {
    const byName = Object.fromEntries(layoutGraph(DATA).nodes.map((n) => [n.name, n]));
    expect(byName.alone.alone).toBe(true);
    expect(byName.beta.alone).toBe(false);
    expect(byName.ghost.missing).toBe(true);
  });

  it("is tall enough for its longest column and wide enough for every column", () => {
    const { width, height, nodes } = layoutGraph(DATA);
    expect(Math.max(...nodes.map((n) => n.x + n.width))).toBeLessThanOrEqual(width);
    expect(Math.max(...nodes.map((n) => n.y + n.height))).toBeLessThanOrEqual(height);
  });
});

describe("links", () => {
  it("keeps a broken link and points it at the column of names that do not exist", () => {
    const broken = layoutGraph(DATA).edges.filter((e) => e.broken);
    expect(broken).toHaveLength(1);
    expect(broken[0].target).toBe("ghost");
    expect(broken[0].path).toMatch(/^M[\d.]+ [\d.]+ C/);
  });

  it("leaves a node on the side its target is on", () => {
    const [forward, backward] = layoutGraph(DATA).edges;
    // alpha -> beta runs down its own column, so it still exits on the right.
    expect(forward.x1).toBeGreaterThanOrEqual(forward.x2);
    // Every drawn link starts and ends on a real node edge, never at 0,0.
    expect(backward.x1).toBeGreaterThan(0);
    expect(backward.y2).toBeGreaterThan(0);
  });

  it("drops a link whose ends are not both on the drawing", () => {
    const stray = { ...DATA, edges: [{ source: "alpha", target: "nowhere" }] };
    expect(layoutGraph(stray).edges).toEqual([]);
  });
});
