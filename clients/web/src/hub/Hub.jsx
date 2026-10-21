import React, { useEffect, useState, useCallback } from "react";
import ReactFlow, { Background, Controls, addEdge, useNodesState, useEdgesState } from "reactflow";
import "reactflow/dist/style.css";
import FlowNode from "./FlowNode.jsx";
import Inspector from "./Inspector.jsx";
import Credentials from "./Credentials.jsx";
import { get, post } from "../api.js";
import { toBackendFlow, fromBackendFlow } from "./serialize.js";

// Shared by every FlowNode so config lives on the node (Postman-Flows style)
// instead of a side panel. `updateNode` is defined in Hub via its `setNodes`
// (from the controlled useNodesState) -- NOT useReactFlow().setNodes, which
// would fight the controlled nodes array.
export const HubContext = React.createContext({ credentials: [], presets: [], updateNode: () => {} });

export default function Hub() {
  const [nodeTypes, setNodeTypes] = useState([]);
  const [presets, setPresets] = useState([]);
  const [credentials, setCredentials] = useState([]);
  const [credsOpen, setCredsOpen] = useState(false);
  const [err, setErr] = useState(null);
  const [flowName, setFlowName] = useState("Untitled");
  const [flowId, setFlowId] = useState(null);
  const [flowList, setFlowList] = useState([]);
  const [run, setRun] = useState(null);       // last RunResult
  const [running, setRunning] = useState(false);

  const loadCreds = useCallback(async () => {
    try {
      setCredentials(await get("/api/hub/credentials"));
    } catch (e) { setErr(String(e.message || e)); }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        setNodeTypes(await get("/api/hub/node-types"));
        setPresets(await get("/api/hub/presets"));
        setFlowList(await get("/api/hub/flows"));
      } catch (e) { setErr(String(e.message || e)); }
    })();
    loadCreds();
  }, [loadCreds]);

  const nodeTypesRF = React.useMemo(() => ({ hub: FlowNode }), []);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedId, setSelectedId] = useState(null);
  const selected = nodes.find((n) => n.id === selectedId) || null;

  const save = useCallback(async () => {
    try {
      const body = toBackendFlow({ id: flowId, name: flowName, nodes, edges });
      const saved = await post("/api/hub/flows", body);
      setFlowId(saved.id);
      setFlowList(await get("/api/hub/flows"));
    } catch (e) { setErr(String(e.message || e)); }
  }, [flowId, flowName, nodes, edges]);

  const loadFlow = useCallback(async (id) => {
    if (!id) return;
    try {
      const flow = await get(`/api/hub/flows/${id}`);
      const rf = fromBackendFlow(flow, nodeTypes);
      setNodes(rf.nodes); setEdges(rf.edges);
      setFlowId(flow.id); setFlowName(flow.name || "Untitled");
    } catch (e) { setErr(String(e.message || e)); }
  }, [nodeTypes, setNodes, setEdges]);

  const newFlow = useCallback(() => {
    setNodes([]); setEdges([]); setFlowId(null); setFlowName("Untitled"); setSelectedId(null);
    setRun(null);
  }, [setNodes, setEdges]);

  const doRun = useCallback(async () => {
    setRunning(true);
    try {
      const body = { flow: toBackendFlow({ id: flowId, name: flowName, nodes, edges }) };
      const res = await post("/api/hub/run", body);
      setRun(res);
      setNodes((ns) => ns.map((n) => ({ ...n, data: { ...n.data, runStatus: res.nodes?.[n.id]?.status } })));
      // A flow-level failure (e.g. a cycle) has no per-node trace to surface
      // it through, so route it to the same error banner as fetch failures.
      setErr(res.error || null);
    } catch (e) { setErr(String(e.message || e)); }
    finally { setRunning(false); }
  }, [flowId, flowName, nodes, edges, setNodes]);

  const onConnect = useCallback((c) => setEdges((es) => addEdge({ ...c }, es)), [setEdges]);

  // Per-node config write-back, called by FlowNode as updateNode(id, patch)
  // via HubContext. Patch is shallow-merged into the node's `data`.
  const updateNode = useCallback((id, patch) => {
    setNodes((ns) => ns.map((n) => n.id === id
      ? { ...n, data: { ...n.data, ...patch, ...(patch.params ? { params: patch.params } : {}) } }
      : n));
  }, [setNodes]);

  const addNode = useCallback((def) => {
    setNodes((ns) => {
      // Compute the id from the CURRENT nodes inside the updater so rapid clicks
      // cannot reuse a stale counter (that produced duplicate "n1" ids, which
      // React Flow dedupes -> lost nodes on save/load).
      const existing = new Set(ns.map((x) => x.id));
      let k = ns.length + 1, id = "n" + k;
      while (existing.has(id)) { k += 1; id = "n" + k; }
      return ns.concat({
        id, type: "hub", position: { x: 120 + ns.length * 40, y: 80 + ns.length * 30 },
        data: { def, label: def.label, params: {} },
      });
    });
  }, [setNodes]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* top bar */}
      <div className="flex shrink-0 items-center gap-2 border-b border-zinc-800/80 bg-zinc-950/60 px-3 py-2">
        <input value={flowName} onChange={(e) => setFlowName(e.target.value)}
          placeholder="Flow name"
          className="w-48 rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100" />
        <button type="button" onClick={newFlow}
          className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-200 hover:border-zinc-600">
          New
        </button>
        <button type="button" onClick={save}
          className="rounded-lg border border-emerald-700/60 bg-emerald-900/30 px-3 py-1.5 text-sm text-emerald-200 hover:border-emerald-500">
          Save
        </button>
        <button type="button" onClick={doRun} disabled={running}
          className={`rounded-lg border px-3 py-1.5 text-sm font-semibold ${running
            ? "cursor-not-allowed border-zinc-800 bg-zinc-900/60 text-zinc-500"
            : "border-emerald-600/70 bg-emerald-600/30 text-emerald-100 hover:border-emerald-400 hover:bg-emerald-600/45"}`}>
          {running ? <span className="font-mono text-[11px] tracking-wider">RUNNING</span> : "Run"}
        </button>
        <select value={flowId ?? ""} onChange={(e) => loadFlow(e.target.value)}
          className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100">
          <option value="">Load flow…</option>
          {flowList.map((f) => <option key={f.id} value={f.id}>{f.name || f.id}</option>)}
        </select>
        <button type="button" onClick={() => setCredsOpen((v) => !v)}
          className="ml-auto rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-200 hover:border-zinc-600">
          Credentials
        </button>
      </div>
      {err && (
        <div className="shrink-0 border-b border-rose-900/60 bg-rose-950/30 px-3 py-1.5 text-sm text-rose-300">
          Hub API error: {err}
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        {/* palette */}
        <aside className="w-52 shrink-0 border-r border-zinc-800/80 bg-zinc-950/60 p-3">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-zinc-500">Nodes</div>
          <div className="flex flex-col gap-1.5" id="hub-palette">
            {nodeTypes.map((n) => (
              <button key={n.type} type="button" onClick={() => addNode(n)}
                className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-left text-sm text-zinc-200 hover:border-zinc-600">
                {n.label}
              </button>
            ))}
          </div>
        </aside>
        {/* canvas -- config now lives on each node (Postman-Flows style), via HubContext */}
        <main className="relative min-w-0 flex-1">
          <HubContext.Provider value={{ credentials, presets, updateNode }}>
            <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypesRF}
              onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect}
              onSelectionChange={({ nodes: ns }) => setSelectedId(ns[0]?.id ?? null)} fitView>
              <Background color="#26415a" gap={18} />
              <Controls />
            </ReactFlow>
          </HubContext.Provider>
        </main>
        {/* I/O panel -- only once a run exists; before that the canvas is full width */}
        {run && (
          <aside className="w-80 shrink-0 border-l border-zinc-800/80 bg-zinc-950/60 p-3">
            <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-zinc-500">Inspector</div>
            <Inspector node={selected} run={run} />
          </aside>
        )}
        {credsOpen && (
          <Credentials items={credentials} onChanged={loadCreds} onClose={() => setCredsOpen(false)} />
        )}
      </div>
    </div>
  );
}
