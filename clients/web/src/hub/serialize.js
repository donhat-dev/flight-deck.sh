// JSON/list params may be held as raw strings while editing; parse at boundary.
function parseMaybe(v) {
  if (typeof v !== "string") return v;
  const s = v.trim();
  if (s === "") return v;
  if ("{[\"".includes(s[0]) || /^-?\d/.test(s)) {
    try { return JSON.parse(s); } catch { return v; }
  }
  return v;
}

export function toBackendFlow({ id, name, nodes, edges }) {
  return {
    id, name,
    nodes: nodes.map((n) => ({
      id: n.id, type: n.data.def.type, label: n.data.label || n.data.def.label,
      position: n.position,
      params: Object.fromEntries(Object.entries(n.data.params || {}).map(([k, v]) => [k, parseMaybe(v)])),
    })),
    connections: edges.map((e) => ({
      from: [e.source, parseInt(e.sourceHandle ?? "0", 10)],
      to: [e.target, parseInt(e.targetHandle ?? "0", 10)],
    })),
  };
}

export function fromBackendFlow(flow, nodeDefs) {
  const byType = Object.fromEntries(nodeDefs.map((d) => [d.type, d]));
  return {
    nodes: (flow.nodes || []).map((n) => ({
      id: n.id, type: "hub", position: n.position || { x: 0, y: 0 },
      data: { def: byType[n.type] || { type: n.type, inputs: ["main"], outputs: ["main"], params: {} },
              label: n.label, params: n.params || {} },
    })),
    edges: (flow.connections || []).map((c, i) => ({
      id: "e" + i, source: c.from[0], sourceHandle: String(c.from[1]),
      target: c.to[0], targetHandle: String(c.to[1]),
    })),
  };
}
