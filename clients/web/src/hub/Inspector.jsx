import React from "react";

// I/O-only panel: config now lives on the node (see FlowNode.jsx). This
// renders nothing until there's a run *and* a trace for the selected node --
// Hub only mounts the containing <aside> once `run` exists, but this guard
// also covers "run exists, nothing selected" and "selected node wasn't part
// of that run" (e.g. skipped/newly-added).

// Trace status -> text color. Genuine green/rose (not this app's coral-mapped
// `emerald`) so "ran ok" reads distinctly from the brand accent used for
// buttons/selection elsewhere in the Hub.
const STATUS_TEXT = { ok: "text-green-400", error: "text-rose-400", skipped: "text-zinc-500" };

export default function Inspector({ node, run }) {
  if (!run || !node || !run.nodes?.[node.id]) return null;
  const trace = run.nodes[node.id];
  const outs = node.data?.def?.outputs || [];
  return (
    <div className="mt-2 space-y-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate font-mono text-[11px] text-zinc-300">{node.data?.label || node.data?.def?.label}</span>
        <span className={`shrink-0 font-mono text-[11px] font-semibold uppercase ${STATUS_TEXT[trace.status] || "text-zinc-400"}`}>
          {trace.status}
        </span>
      </div>
      {trace.timeMs != null && (
        <div className="font-mono text-[10px] text-zinc-500">{trace.timeMs} ms</div>
      )}
      {trace.error && (
        <div>
          <div className="font-mono text-[9px] uppercase tracking-wider text-zinc-500">Error</div>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-rose-900/60 bg-rose-950/30 p-2 font-mono text-[10px] text-rose-300">
            {trace.error}
          </pre>
        </div>
      )}
      <div>
        <div className="font-mono text-[9px] uppercase tracking-wider text-zinc-500">Input</div>
        <pre className="mt-1 max-h-40 overflow-auto rounded-lg border border-zinc-800 bg-zinc-900/60 p-2 font-mono text-[10px] text-zinc-300">
          {JSON.stringify(trace.inputs ?? null, null, 2)}
        </pre>
      </div>
      {(trace.outputsPerSocket || []).map((items, i) => (
        <div key={i}>
          <div className="font-mono text-[9px] uppercase tracking-wider text-zinc-500">{outs[i] || `out ${i}`}</div>
          {!items || items.length === 0 ? (
            <div className="font-mono text-[10px] text-zinc-600">(no items)</div>
          ) : items.map((it, j) => (
            <pre key={j} className="mt-1 max-h-56 overflow-auto rounded-lg border border-zinc-800 bg-zinc-900/60 p-2 font-mono text-[10px] text-zinc-300">
              {JSON.stringify(it?.json ?? it, null, 2)}
            </pre>
          ))}
        </div>
      ))}
    </div>
  );
}
