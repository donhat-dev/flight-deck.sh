import React, { useState } from "react";
import { post, del } from "../api.js";

export default function Credentials({ items, onChanged, onClose }) {
  const [kind, setKind] = useState("odoo");
  const [f, setF] = useState({ name: "" });
  const [err, setErr] = useState(null);
  const add = async () => {
    const data = kind === "odoo"
      ? { base: f.base, db: f.db, user: f.user, secret: f.secret }
      : { token: f.token };
    try {
      await post("/api/hub/credentials", { name: f.name, kind: kind === "odoo" ? "odoo" : "bearer", data });
      setF({ name: "" }); setErr(null); onChanged();
    } catch (e) { setErr(String(e.message || e)); }
  };
  return (
    <div className="absolute right-4 top-16 z-20 w-96 rounded-2xl border border-zinc-700 bg-zinc-950 p-4 shadow-xl">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-mono text-[11px] uppercase tracking-wider text-zinc-300">Credentials</span>
        <button onClick={onClose} className="text-zinc-500 hover:text-zinc-200">×</button>
      </div>
      {err && <div className="mb-2 text-xs text-rose-300">{err}</div>}
      <ul className="mb-3 space-y-1">
        {items.map((c) => (
          <li key={c.id} className="flex items-center gap-2 text-sm text-zinc-200">
            {c.name} <span className="font-mono text-[10px] text-zinc-500">{c.kind}</span>
            <button onClick={async () => { try { await del(`/api/hub/credentials/${c.id}`); setErr(null); onChanged(); } catch (e) { setErr(String(e.message || e)); } }}
              className="ml-auto text-rose-400">delete</button>
          </li>
        ))}
      </ul>
      <select value={kind} onChange={(e) => setKind(e.target.value)} className="mb-2 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm">
        <option value="odoo">Odoo XML-RPC</option><option value="http">HTTP bearer</option>
      </select>
      <input placeholder="name" value={f.name || ""} onChange={(e) => setF({ ...f, name: e.target.value })}
        className="mb-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm" />
      {kind === "odoo" ? ["base","db","user","secret"].map((k) => (
        <input key={k} placeholder={k} type={k === "secret" ? "password" : "text"} value={f[k] || ""}
          onChange={(e) => setF({ ...f, [k]: e.target.value })}
          className="mb-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm" />
      )) : (
        <input placeholder="token" type="password" value={f.token || ""} onChange={(e) => setF({ ...f, token: e.target.value })}
          className="mb-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm" />
      )}
      <button onClick={add} className="mt-1 w-full rounded-lg bg-emerald-600 py-1.5 text-sm font-semibold text-white">Add</button>
    </div>
  );
}
