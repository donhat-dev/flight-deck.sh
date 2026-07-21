import { describe, it, expect } from "vitest";
import { toBackendFlow, fromBackendFlow } from "./serialize.js";

const defs = [
  { type: "start", inputs: [], outputs: ["main"], params: { seed: "json" } },
  { type: "http", inputs: ["main"], outputs: ["success", "error"], params: { method: "string", url: "string", headers: "json" } },
];

it("round-trips a two-node flow with an edge on the success socket", () => {
  const be = {
    id: "f", name: "t",
    nodes: [
      { id: "s", type: "start", label: "Start", position: { x: 0, y: 0 }, params: { seed: { a: 1 } } },
      { id: "h", type: "http", label: "Call", position: { x: 200, y: 0 }, params: { method: "GET", url: "/x", headers: {} } },
    ],
    connections: [{ from: ["s", 0], to: ["h", 0] }],
  };
  const rf = fromBackendFlow(be, defs);
  expect(rf.nodes).toHaveLength(2);
  expect(rf.edges[0].sourceHandle).toBe("0");
  const back = toBackendFlow({ id: "f", name: "t", nodes: rf.nodes, edges: rf.edges });
  expect(back.connections).toEqual([{ from: ["s", 0], to: ["h", 0] }]);
  expect(back.nodes[0].params.seed).toEqual({ a: 1 });   // JSON preserved, not a string
});

it("parses JSON-typed param strings edited as text", () => {
  const rf = { id: "f", name: "t",
    nodes: [{ id: "h", type: "http", position: { x: 0, y: 0 },
      data: { def: defs[1], label: "Call", params: { method: "GET", url: "/x", headers: '{"A":"1"}' } } }],
    edges: [] };
  const back = toBackendFlow(rf);
  expect(back.nodes[0].params.headers).toEqual({ A: "1" });
});
