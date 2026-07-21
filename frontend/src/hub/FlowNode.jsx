import React, { useContext, useState } from "react";
import { Handle, Position } from "reactflow";
import { HubContext } from "./Hub.jsx";
import KeyValueEditor from "./KeyValueEditor.jsx";

const SOCKET_COLOR = { success: "#4ade80", error: "#FF5133", true: "#8bd450",
  false: "#e0b341", main: "#8299b1" };

// Run-trace border tint. Deliberately genuine `green`/`rose` here rather than
// this app's `emerald` (remapped to the coral brand accent in
// tailwind.config.js and already used below for the selection border) --
// reusing the accent for "ran ok" would make a selected-but-unrun node look
// identical to a successfully-run one. `skipped` just dims the default border.
const STATUS_BORDER = { ok: "border-green-500", error: "border-rose-500",
  skipped: "border-zinc-800 opacity-60" };

const METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"];

// Collapsible wrapper for "json"/"list" params -- collapsed by default so a
// node with several object params doesn't balloon on the canvas.
function CollapsibleField({ label, count, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-zinc-800">
      <button type="button" onClick={() => setOpen((v) => !v)}
        className="nodrag flex w-full items-center gap-1 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-zinc-400 hover:text-zinc-200">
        <span>{open ? "▾" : "▸"}</span>
        <span>{label}{count != null ? ` (${count})` : ""}</span>
      </button>
      {open && <div className="border-t border-zinc-800 p-2">{children}</div>}
    </div>
  );
}

export default function FlowNode({ id, data, selected }) {
  const { credentials, updateNode } = useContext(HubContext);
  const def = data.def;                       // {type,label,inputs,outputs,params}
  const ins = def.inputs || [];
  const outs = def.outputs || [];
  const params = data.params || {};
  const schema = def.params || {};
  const borderClass = STATUS_BORDER[data.runStatus]
    || (selected ? "border-emerald-500" : "border-zinc-700");

  const upd = (patch) => updateNode(id, patch);
  const setParam = (k, v) => upd({ params: { ...params, [k]: v } });

  return (
    <div className={`min-w-[300px] max-w-[340px] rounded-xl border bg-zinc-900 px-3 py-2 text-zinc-100 shadow ${borderClass} ${
      selected && data.runStatus ? "ring-1 ring-white/40" : ""}`}>
      {/* header: type badge + editable label + (http) method select */}
      <div className="mb-2 flex items-center gap-1.5">
        <span className="shrink-0 font-mono text-[9px] uppercase tracking-wider text-zinc-500">{def.type}</span>
        <input value={data.label ?? def.label ?? ""} onChange={(e) => upd({ label: e.target.value })}
          className="nodrag min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-sm font-semibold text-zinc-100 outline-none hover:border-zinc-700 focus:border-zinc-600 focus:bg-zinc-950" />
        {schema.method === "string" && (
          <select value={params.method ?? ""} onChange={(e) => setParam("method", e.target.value)}
            className="nodrag shrink-0 rounded border border-zinc-700 bg-zinc-950 px-1 py-0.5 font-mono text-[10px] text-zinc-200">
            <option value="">METHOD</option>
            {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        )}
      </div>

      {/* input sockets */}
      {ins.map((name, i) => (
        <Handle key={"in" + i} id={String(i)} type="target" position={Position.Left}
          style={{ top: 44 + i * 16, background: "#647f9d" }} />
      ))}

      {/* config, driven by def.params */}
      <div className="space-y-1.5">
        {Object.entries(schema).map(([k, kind]) => {
          if (k === "method") return null; // rendered in the header instead
          if (kind === "string" && k === "credentialRef") {
            return (
              <div key={k}>
                <label className="mb-0.5 block font-mono text-[9px] uppercase tracking-wider text-zinc-500">{k}</label>
                <select value={params[k] ?? ""} onChange={(e) => setParam(k, e.target.value)}
                  className="nodrag w-full rounded border border-zinc-700 bg-zinc-950 px-1.5 py-1 font-mono text-[11px] text-zinc-100">
                  <option value="">(none)</option>
                  {credentials.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.kind})</option>)}
                </select>
              </div>
            );
          }
          if (kind === "string") {
            return (
              <div key={k}>
                <label className="mb-0.5 block font-mono text-[9px] uppercase tracking-wider text-zinc-500">{k}</label>
                <input value={params[k] ?? ""} onChange={(e) => setParam(k, e.target.value)}
                  className="nodrag w-full rounded border border-zinc-700 bg-zinc-950 px-1.5 py-1 font-mono text-[11px] text-zinc-100" />
              </div>
            );
          }
          if (kind === "json") {
            const obj = params[k] && typeof params[k] === "object" && !Array.isArray(params[k]) ? params[k] : null;
            const count = obj ? Object.keys(obj).length : 0;
            return (
              <CollapsibleField key={k} label={k} count={count}>
                <KeyValueEditor value={params[k]} kind="json" onChange={(v) => setParam(k, v)} />
              </CollapsibleField>
            );
          }
          if (kind === "list") {
            return (
              <CollapsibleField key={k} label={k}>
                <KeyValueEditor value={params[k]} kind="list" onChange={(v) => setParam(k, v)} />
              </CollapsibleField>
            );
          }
          return null;
        })}
      </div>

      {/* output sockets: label + its handle share one row via an INLINE
          (position:relative) handle, so the dot always sits next to its label
          regardless of the node's (variable) height -- fixes the prior
          fixed-pixel `top` that detached the dots from the labels. */}
      <div className="mt-2 space-y-1">
        {outs.map((name, i) => (
          <div key={"out" + i} className="flex items-center justify-end gap-1.5 font-mono text-[9px]"
               style={{ color: SOCKET_COLOR[name] || "#8299b1" }}>
            <span>{name}</span>
            <Handle id={String(i)} type="source" position={Position.Right}
              style={{ position: "relative", top: "auto", right: "auto", transform: "none",
                       width: 10, height: 10, background: SOCKET_COLOR[name] || "#647f9d" }} />
          </div>
        ))}
      </div>
    </div>
  );
}
