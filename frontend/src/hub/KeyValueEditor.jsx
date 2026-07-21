import React, { useEffect, useRef, useState } from "react";

// Reusable key-value <-> raw-JSON editor for "json" (object) and "list"
// (array) params. "json" kind gets a table<->raw toggle; "list" kind is raw
// JSON only (arrays don't fit a key-value table).
//
// `rows` is the table-mode editing buffer. It is kept as local state (rather
// than derived fresh from `value` on every render) so an in-progress blank
// "+ Add" row, or a half-typed key, survives the round trip through the
// parent's controlled `value` prop. `lastEmitted` guards the sync effect: if
// the incoming `value` is exactly what we just emitted via onChange, it's our
// own edit echoing back, not an external change (e.g. switching nodes) -- so
// skip resetting `rows` and losing in-progress edits.

function parseObj(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) return value;
  if (typeof value === "string") {
    const s = value.trim();
    if (s) {
      try {
        const parsed = JSON.parse(s);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
      } catch { /* not valid JSON -- caller treats as null */ }
    }
  }
  return null;
}

function toRows(obj) {
  return Object.entries(obj || {}).map(([key, v]) => ({ key, value: v == null ? "" : String(v) }));
}

function toObj(rows) {
  return Object.fromEntries(rows.filter((r) => r.key.trim() !== "").map((r) => [r.key, r.value]));
}

export default function KeyValueEditor({ value, onChange, kind = "json" }) {
  const listOnly = kind === "list";
  const [mode, setMode] = useState(listOnly ? "raw" : "table");
  const [rows, setRows] = useState(() => toRows(parseObj(value)));
  const [invalid, setInvalid] = useState(false);
  const lastEmitted = useRef(JSON.stringify(value ?? null));

  useEffect(() => {
    if (listOnly) return; // list kind never uses the table buffer
    const asStr = JSON.stringify(value ?? null);
    if (asStr === lastEmitted.current) return; // our own edit echoing back -- don't clobber in-progress rows
    lastEmitted.current = asStr;
    setRows(toRows(parseObj(value)));
  }, [value, listOnly]);

  const commit = (nextRows) => {
    setRows(nextRows);
    const obj = toObj(nextRows);
    lastEmitted.current = JSON.stringify(obj);
    onChange(obj);
  };

  const setKey = (i, key) => commit(rows.map((r, idx) => (idx === i ? { ...r, key } : r)));
  const setVal = (i, val) => commit(rows.map((r, idx) => (idx === i ? { ...r, value: val } : r)));
  const addRow = () => setRows((rs) => rs.concat({ key: "", value: "" }));
  const removeRow = (i) => commit(rows.filter((_, idx) => idx !== i));

  const toTableMode = () => {
    const obj = parseObj(value);
    if (!obj) { setInvalid(true); return; } // stay raw, keep the user's text
    setInvalid(false);
    lastEmitted.current = JSON.stringify(obj);
    setRows(toRows(obj));
    setMode("table");
  };
  const toRawMode = () => { setInvalid(false); setMode("raw"); };

  const rawText = typeof value === "string" ? value : JSON.stringify(value ?? (listOnly ? [] : {}), null, 2);
  const onRawChange = (e) => {
    setInvalid(false);
    lastEmitted.current = JSON.stringify(e.target.value);
    onChange(e.target.value);
  };

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-2">
      {!listOnly && (
        <div className="mb-1.5 flex justify-end gap-1">
          <button type="button" onClick={toTableMode}
            className={`nodrag rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide ${
              mode === "table" ? "bg-zinc-700 text-zinc-100" : "border border-zinc-800 text-zinc-500 hover:text-zinc-300"}`}>
            table
          </button>
          <button type="button" onClick={toRawMode}
            className={`nodrag rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide ${
              mode === "raw" ? "bg-zinc-700 text-zinc-100" : "border border-zinc-800 text-zinc-500 hover:text-zinc-300"}`}>
            raw
          </button>
        </div>
      )}
      {mode === "table" ? (
        <div className="space-y-1">
          {rows.map((r, i) => (
            <div key={i} className="flex items-center gap-1">
              <input value={r.key} placeholder="key" onChange={(e) => setKey(i, e.target.value)}
                className="nodrag w-2/5 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 font-mono text-[11px] text-zinc-100" />
              <input value={r.value} placeholder="value" onChange={(e) => setVal(i, e.target.value)}
                className="nodrag min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 font-mono text-[11px] text-zinc-100" />
              <button type="button" onClick={() => removeRow(i)}
                className="nodrag shrink-0 px-1 text-zinc-500 hover:text-rose-400">×</button>
            </div>
          ))}
          <button type="button" onClick={addRow}
            className="nodrag mt-1 w-full rounded border border-dashed border-zinc-800 py-1 font-mono text-[10px] text-zinc-500 hover:border-zinc-600 hover:text-zinc-300">
            + Add
          </button>
        </div>
      ) : (
        <div>
          <textarea rows={4} value={rawText} onChange={onRawChange}
            className="nodrag w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 font-mono text-[11px] text-zinc-100" />
          {invalid && (
            <div className="mt-1 font-mono text-[10px] text-amber-400">invalid JSON — fix to switch to table</div>
          )}
        </div>
      )}
    </div>
  );
}
