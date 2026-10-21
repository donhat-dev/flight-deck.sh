import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { get } from "./api.js";

// palette (aligned with the FlightDeck Night theme: coral signal accent)
const C = {
  accent: "#FF5133", up: "#38bdf8", down: "#fb923c",
  risk: "#fb7185", warn: "#fbbf24", good: "#34d399", faint: "#52525b",
  dim: "rgba(140,140,150,0.09)", dimLink: "rgba(120,120,135,0.05)",
};
const CAT_PALETTE = ["#2dd4bf", "#60a5fa", "#c084fc", "#fbbf24", "#f472b6",
  "#a3e635", "#38bdf8", "#fb923c", "#818cf8", "#34d399"];
const commColor = (id) => `hsl(${(id * 57) % 360} 55% 62%)`;
const lid = (x) => (typeof x === "object" && x ? x.id : x);

function hex(h) { h = h.replace("#", ""); if (h.length === 3) h = h.split("").map((c) => c + c).join(""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); }
function mixHex(a, b, t) { const A = hex(a), B = hex(b); return `rgb(${A.map((v, i) => Math.round(v + (B[i] - v) * t)).join(",")})`; }

function closure(start, adj) { const seen = new Set(), st = [start]; while (st.length) { const c = st.pop(); for (const n of adj.get(c) || []) if (!seen.has(n)) { seen.add(n); st.push(n); } } return seen; }
function bfsPath(a, b, fwd) { if (a === b) return [a]; const prev = new Map([[a, null]]); const q = [a]; while (q.length) { const c = q.shift(); for (const n of fwd.get(c) || []) if (!prev.has(n)) { prev.set(n, c); if (n === b) { const p = [b]; let k = c; while (k != null) { p.unshift(k); k = prev.get(k); } return p; } q.push(n); } } return null; }

export default function GraphView() {
  const [data, setData] = useState(null);
  const [dims, setDims] = useState({ w: 800, h: 600 });
  const wrapRef = useRef(null), fgRef = useRef(null);

  const [sel, setSel] = useState(null);          // selected module id
  const [selModel, setSelModel] = useState(null);
  const [group, setGroup] = useState(null);      // {cid, ids:Set, label}
  const [impact, setImpact] = useState(false);
  const [pathTo, setPathTo] = useState(null);
  const [colorMode, setColorMode] = useState("category");
  const [hideExt, setHideExt] = useState(false);
  const [eeSpot, setEeSpot] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => { get("/api/graph").then(setData).catch(() => setData({ available: false })); }, []);

  useEffect(() => {
    const el = wrapRef.current; if (!el) return;
    const ro = new ResizeObserver(() => setDims({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el); setDims({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, [data]);

  // stronger repulsion + link distance than react-force-graph's defaults so 256
  // nodes actually spread out instead of collapsing into a tight blob.
  useEffect(() => {
    const fg = fgRef.current; if (!fg || !fg.d3Force) return;
    const t = setTimeout(() => {
      fg.d3Force("charge") && fg.d3Force("charge").strength(-190).distanceMax(600);
      fg.d3Force("link") && fg.d3Force("link").distance(42).strength(0.5);
      fg.d3ReheatSimulation && fg.d3ReheatSimulation();
    }, 0);
    return () => clearTimeout(t);
  }, [data]);

  // ---- indexes (built once per data load; nodes persist so layout is stable) ----
  const idx = useMemo(() => {
    if (!data || !data.available) return null;
    const byId = new Map(data.modules.map((m) => [m.id, m]));
    const fwd = new Map(), rev = new Map();
    data.modules.forEach((m) => { fwd.set(m.id, new Set()); rev.set(m.id, new Set()); });
    data.edges.forEach((e) => { if (byId.has(e.target)) { fwd.get(e.source).add(e.target); rev.get(e.target).add(e.source); } });
    const cats = [...new Set(data.modules.filter((m) => m.present).map((m) => m.category))]
      .map((c) => [c, data.modules.filter((m) => m.present && m.category === c).length])
      .sort((a, b) => b[1] - a[1]).map((x) => x[0]);
    const catColor = new Map(cats.map((c, i) => [c, i < CAT_PALETTE.length ? CAT_PALETTE[i] : "#8b93a0"]));
    const modelById = new Map((data.models || []).map((m) => [m.id, m]));
    const commLabel = new Map((data.communities || []).map((c) => [c.id, c.label || "Community " + c.id]));
    const nodes = data.modules.map((m) => ({ ...m }));
    const links = data.edges.filter((e) => byId.has(e.target)).map((e) => ({ source: e.source, target: e.target }));
    return { byId, fwd, rev, cats: cats.slice(0, 7), catColor, modelById, commLabel,
      graphData: { nodes, links }, communities: data.communities || [], meta: data.meta };
  }, [data]);

  // ---- highlight sets from current state ----
  const hl = useMemo(() => {
    if (!idx) return { active: false, focusAll: new Set() };
    if (selModel) {
      const mm = idx.modelById.get(selModel) || { defined_by: [], extended_by: [] };
      const def = new Set(mm.defined_by), ext = new Set(mm.extended_by);
      return { active: true, def, ext, focusAll: new Set([...def, ...ext]) };
    }
    if (group) return { active: true, group: group.ids, focusAll: group.ids };
    if (sel) {
      if (impact) { const up = closure(sel, idx.fwd), down = closure(sel, idx.rev); const focus = new Set([sel]); return { active: true, up, down, focus, focusAll: new Set([sel, ...up, ...down]) }; }
      if (pathTo) { const p = new Set(bfsPath(sel, pathTo, idx.fwd) || []); return { active: true, path: p, focus: new Set([sel, pathTo]), focusAll: p }; }
      const nb = new Set([...(idx.fwd.get(sel) || []), ...(idx.rev.get(sel) || [])]);
      const focus = new Set([sel, ...nb]); return { active: true, focus, focusAll: focus };
    }
    return { active: false, focusAll: new Set() };
  }, [idx, sel, selModel, group, impact, pathTo]);

  const searchHits = useMemo(() => {
    if (!idx || !query.trim()) return null;
    const q = query.trim().toLowerCase(), hits = new Set();
    for (const m of idx.byId.values()) if (m.id.toLowerCase().includes(q) || (m.label || "").toLowerCase().includes(q)) hits.add(m.id);
    for (const md of idx.modelById.values()) if (md.id.toLowerCase().includes(q)) (md.defined_by || []).concat(md.extended_by || []).forEach((x) => hits.add(x));
    return hits;
  }, [idx, query]);

  const visible = useCallback((m) => {
    if (hideExt && !m.present) return false;
    if (eeSpot && !((m.ee_blocked && m.ee_blocked.length) || m.ee_gap)) return false;
    return true;
  }, [hideExt, eeSpot]);

  const baseColor = useCallback((m) => {
    if (colorMode === "community") return (!m.present || m.community < 0) ? C.faint : commColor(m.community);
    if (colorMode === "ee") { if (m.ee_gap) return C.risk; if (m.ee_blocked && m.ee_blocked.length) return C.warn; return m.present ? C.good : C.faint; }
    if (colorMode === "dependents") return mixHex(C.faint, C.accent, Math.min(1, (m.dependents || 0) / 40));
    if (!m.present) return C.faint;
    return idx.catColor.get(m.category) || "#8b93a0";
  }, [colorMode, idx]);

  const nodeColor = useCallback((n) => {
    const dimmed = (hl.active && !hl.focusAll.has(n.id)) || (searchHits && !searchHits.has(n.id));
    if (dimmed) return C.dim;
    if (hl.down?.has(n.id) || hl.ext?.has(n.id)) return C.down;
    if (hl.up?.has(n.id)) return C.up;
    if (hl.def?.has(n.id) || hl.path?.has(n.id)) return C.accent;
    return baseColor(n);
  }, [hl, searchHits, baseColor]);

  const linkVisible = useCallback((l) => {
    const s = lid(l.source), t = lid(l.target);
    if (!visible(idx.byId.get(s)) || !visible(idx.byId.get(t))) return false;
    if (searchHits) return searchHits.has(s) && searchHits.has(t);
    if (hl.active) return hl.focusAll.has(s) && hl.focusAll.has(t);
    return true;
  }, [idx, hl, searchHits, visible]);

  const linkColor = useCallback((l) => {
    if (!hl.active) return "rgba(120,120,135,0.16)";
    const s = lid(l.source), t = lid(l.target);
    if (hl.path?.has(s) && hl.path?.has(t)) return C.accent;
    if (hl.down?.has(s) || hl.down?.has(t)) return C.down;
    if (hl.up?.has(s) || hl.up?.has(t)) return C.up;
    if (hl.def || hl.ext) return "rgba(200,200,210,0.35)";
    return "rgba(180,180,190,0.28)";
  }, [hl]);

  // selection helpers
  const pickModule = (id) => { setSelModel(null); setGroup(null); setPathTo(null); setSel(id); };
  const pickModel = (id) => { setSel(null); setGroup(null); setPathTo(null); setImpact(false); setSelModel(idx.modelById.has(id) ? id : null); };
  const pickCommunity = (cid) => { setSel(null); setSelModel(null); setPathTo(null); setImpact(false); const ids = new Set(idx.byId ? [...idx.byId.values()].filter((m) => m.community === cid).map((m) => m.id) : []); setGroup({ cid, ids, label: idx.commLabel.get(cid) }); };
  const clearSel = () => { setSel(null); setSelModel(null); setGroup(null); setPathTo(null); setImpact(false); };
  const focusOn = (id) => { pickModule(id); const n = idx.graphData.nodes.find((x) => x.id === id); if (n && fgRef.current && n.x != null) fgRef.current.centerAt(n.x, n.y, 600); };

  const onNodeClick = (n, ev) => {
    if (ev && ev.shiftKey && sel) { setSelModel(null); setGroup(null); setImpact(false); setPathTo(n.id); return; }
    pickModule(n.id);
  };

  if (!data) return <div className="p-10 text-sm text-zinc-500">Loading graph…</div>;
  if (!data.available) return (
    <div className="mx-auto mt-16 max-w-md rounded-xl border border-zinc-800 bg-zinc-900/60 p-6 text-center">
      <div className="font-mono text-sm text-zinc-300">No dependency graph found.</div>
      <p className="mt-2 text-xs leading-relaxed text-zinc-500">
        Generate it with <code className="rounded bg-zinc-800 px-1 py-0.5 text-emerald-400">python3 ingest.py</code> in the
        <code className="rounded bg-zinc-800 px-1 py-0.5">nakivo-graph/</code> project, then reload.
      </p>
    </div>
  );

  const mt = idx.meta;
  return (
    <div className="relative h-full w-full overflow-hidden">
      {/* controls */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex flex-wrap items-center gap-2 p-3">
        <div className="pointer-events-auto flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950/80 px-3 py-1.5 font-mono text-xs text-zinc-400 backdrop-blur">
          <span><b className="text-zinc-100">{mt.module_count}</b> modules</span>
          <span className="text-zinc-700">·</span>
          <span><b className="text-zinc-100">{mt.edge_count}</b> deps</span>
          <span className="text-zinc-700">·</span>
          <span className="text-rose-400"><b>{mt.ee_blocked_count}</b> EE-blocked</span>
        </div>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="search module / model…" name="graph-search" aria-label="Search modules or models"
          className="pointer-events-auto w-52 rounded-lg border border-zinc-800 bg-zinc-900/70 px-3 py-1.5 font-mono text-xs text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-emerald-500" />
        <button onClick={() => setEeSpot((v) => !v)}
          className={`pointer-events-auto rounded-lg border px-2.5 py-1.5 font-mono text-xs ${eeSpot ? "border-rose-500 bg-rose-500/15 text-rose-300" : "border-zinc-800 bg-zinc-900/70 text-zinc-400 hover:text-zinc-200"}`}>EE-gap spotlight</button>
        <button onClick={() => setHideExt((v) => !v)}
          className={`pointer-events-auto rounded-lg border px-2.5 py-1.5 font-mono text-xs ${hideExt ? "border-emerald-500 bg-emerald-500/15 text-emerald-300" : "border-zinc-800 bg-zinc-900/70 text-zinc-400 hover:text-zinc-200"}`}>hide external</button>
        <button onClick={() => setColorMode((m) => ({ category: "community", community: "ee", ee: "dependents", dependents: "category" }[m]))}
          className="pointer-events-auto rounded-lg border border-zinc-800 bg-zinc-900/70 px-2.5 py-1.5 font-mono text-xs text-zinc-400 hover:text-zinc-200">color: {colorMode}</button>
      </div>

      {/* graph */}
      <div ref={wrapRef} className="h-full w-full">
        <ForceGraph2D
          ref={fgRef}
          width={dims.w} height={dims.h}
          graphData={idx.graphData}
          backgroundColor="rgba(0,0,0,0)"
          nodeId="id"
          nodeVal={(n) => 1 + (n.dependents || 0)}
          nodeRelSize={4}
          nodeColor={nodeColor}
          nodeVisibility={(n) => visible(n)}
          nodeLabel={(n) => `<div style="font-family:monospace;font-size:11px"><b>${n.id}</b><br>${n.label || ""} · ${n.category}<br>${n.dependents || 0} dependents · ${(idx.fwd.get(n.id)?.size || 0)} deps${n.ee_blocked?.length ? `<br><span style="color:#fb7185">⚠ EE: ${n.ee_blocked.join(", ")}</span>` : ""}</div>`}
          linkColor={linkColor}
          linkVisibility={linkVisible}
          linkWidth={(l) => (hl.active && hl.focusAll.has(lid(l.source)) && hl.focusAll.has(lid(l.target)) ? 1.4 : 0.5)}
          linkDirectionalParticles={0}
          onNodeClick={onNodeClick}
          onBackgroundClick={clearSel}
          cooldownTicks={220}
          d3VelocityDecay={0.32}
          onEngineStop={() => { if (!hl.active && fgRef.current) fgRef.current.zoomToFit(400, 60); }}
          nodeCanvasObjectMode={(n) => ((n.dependents || 0) >= 8 || n.id === sel || (searchHits && searchHits.has(n.id))) ? "after" : undefined}
          nodeCanvasObject={(n, ctx, scale) => {
            if (!visible(n)) return;
            const dimmed = (hl.active && !hl.focusAll.has(n.id)) || (searchHits && !searchHits.has(n.id));
            const fs = 11 / scale;
            ctx.font = `${fs}px ui-monospace, monospace`;
            ctx.textAlign = "center"; ctx.textBaseline = "bottom";
            ctx.fillStyle = dimmed ? "rgba(160,160,170,0.25)" : "#F4F3EF";
            ctx.fillText(n.id, n.x, n.y - (Math.sqrt(1 + (n.dependents || 0)) * 4) / scale - 2 / scale);
          }}
        />
      </div>

      <Legend colorMode={colorMode} idx={idx} />
      <SidePanel {...{ idx, sel, selModel, group, impact, pathTo, setImpact, pickModule, pickModel, pickCommunity, clearSel, focusOn, fgRef }} />
    </div>
  );
}

function Legend({ colorMode, idx }) {
  let rows;
  if (colorMode === "category") rows = idx.cats.map((c) => [idx.catColor.get(c), c]).concat([[C.faint, "external / other"]]);
  else if (colorMode === "community") rows = idx.communities.filter((c) => c.size >= 3).slice(0, 8).map((c) => [commColor(c.id), `${c.label || "Community " + c.id} · ${c.size}`]).concat([[C.faint, "small / external"]]);
  else if (colorMode === "ee") rows = [[C.risk, "EE module (absent)"], [C.warn, "blocked (needs EE)"], [C.good, "installable"]];
  else rows = [[C.accent, "hub"], [C.faint, "leaf"]];
  return (
    <div className="absolute bottom-3 left-3 z-20 max-w-[15rem] rounded-lg border border-zinc-800 bg-zinc-950/85 p-2.5 font-mono text-[11px] backdrop-blur">
      <div className="mb-1 uppercase tracking-wider text-zinc-500">{colorMode}</div>
      {rows.map(([col, label], i) => (
        <div key={i} className="flex items-center gap-2 text-zinc-400">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: col }} />{label}
        </div>
      ))}
      <div className="mt-1.5 text-zinc-600">click node · shift-click = path</div>
    </div>
  );
}

function Chip({ children, onClick, tone }) {
  const tones = { ee: "border-rose-500/60 text-rose-300", model: "border-zinc-700 text-zinc-300 hover:border-emerald-500", default: "border-zinc-700 text-zinc-400 hover:border-zinc-500" };
  return <button onClick={onClick} className={`rounded-md border bg-zinc-900/60 px-1.5 py-0.5 font-mono text-[11px] ${tones[tone] || tones.default}`}>{children}</button>;
}
function Sec({ children }) { return <h3 className="mt-3 border-t border-zinc-800 pt-2 font-mono text-[10px] uppercase tracking-widest text-zinc-500">{children}</h3>; }
function Metric({ n, l, tone }) {
  const col = { up: "text-sky-400", down: "text-orange-400" }[tone] || "text-zinc-100";
  return <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-2.5 py-1.5"><div className={`font-mono text-lg tabular-nums ${col}`}>{n}</div><div className="text-[10px] uppercase tracking-wide text-zinc-500">{l}</div></div>;
}

function SidePanel({ idx, sel, selModel, group, impact, pathTo, setImpact, pickModule, pickModel, pickCommunity, clearSel, focusOn, fgRef }) {
  const wrap = "absolute right-3 top-3 bottom-3 z-20 w-[21rem] overflow-y-auto rounded-xl border border-zinc-800 bg-zinc-950/90 p-4 backdrop-blur";
  const modChips = (ids) => ids.length
    ? <div className="flex flex-wrap gap-1">{ids.map((id) => <Chip key={id} tone={(idx.byId.get(id)?.ee_gap || idx.byId.get(id)?.ee_blocked?.length) ? "ee" : "default"} onClick={() => focusOn(id)}>{id}</Chip>)}</div>
    : <span className="text-xs text-zinc-600">none</span>;
  const modelChips = (ids) => ids.length
    ? <div className="flex flex-wrap gap-1">{ids.map((id) => <Chip key={id} tone="model" onClick={() => pickModel(id)}>{id}</Chip>)}</div>
    : <span className="text-xs text-zinc-600">none</span>;

  if (selModel) {
    const mm = idx.modelById.get(selModel) || { defined_by: [], extended_by: [] };
    return (
      <aside className={wrap}>
        <div className="flex items-start justify-between"><div><h2 className="font-mono text-base text-zinc-100">{selModel}</h2><div className="font-mono text-[11px] text-zinc-500">Odoo model · lens</div></div><button onClick={clearSel} className="text-xl leading-none text-zinc-500 hover:text-zinc-200">×</button></div>
        <p className="mt-2 text-xs leading-relaxed text-zinc-400"><b className="text-emerald-400">Defines</b> = declares the model · <b className="text-orange-400">Extends</b> = adds/overrides fields &amp; methods.</p>
        <div className="mt-3 grid grid-cols-2 gap-2"><Metric n={mm.defined_by.length} l="Defined by" tone="up" /><Metric n={mm.extended_by.length} l="Extended by" tone="down" /></div>
        <Sec>Defined by ({mm.defined_by.length})</Sec>{modChips([...mm.defined_by].sort())}
        <Sec>Extended by ({mm.extended_by.length})</Sec>{modChips([...mm.extended_by].sort())}
      </aside>
    );
  }
  if (sel) {
    const m = idx.byId.get(sel); const deps = [...(idx.fwd.get(sel) || [])].sort(), dpts = [...(idx.rev.get(sel) || [])].sort();
    const commTxt = m.community >= 0 ? (m.community_label || idx.commLabel.get(m.community)) : null;
    return (
      <aside className={wrap}>
        <div className="flex items-start justify-between"><div><h2 className="font-mono text-base text-zinc-100">{m.label}</h2><div className="font-mono text-[11px] text-zinc-500">{m.id}</div></div><button onClick={clearSel} className="text-xl leading-none text-zinc-500 hover:text-zinc-200">×</button></div>
        <div className="mt-2 flex flex-wrap gap-1">
          <span className="rounded-md border border-zinc-700 px-1.5 py-0.5 font-mono text-[11px] text-zinc-400">{m.category}</span>
          {m.version && <span className="rounded-md border border-zinc-700 px-1.5 py-0.5 font-mono text-[11px] text-zinc-400">v{m.version}</span>}
          {!m.present && <span className="rounded-md border border-amber-500/60 px-1.5 py-0.5 font-mono text-[11px] text-amber-300">external</span>}
          {(m.ee_blocked?.length || m.ee_gap) && <span className="rounded-md border border-rose-500/60 px-1.5 py-0.5 font-mono text-[11px] text-rose-300">{m.ee_gap ? "Enterprise gap" : "EE-blocked"}</span>}
        </div>
        {commTxt && <p className="mt-2 text-xs text-zinc-400">subsystem: <button onClick={() => pickCommunity(m.community)} style={{ color: commColor(m.community) }} className="font-medium">{commTxt}</button></p>}
        {m.ee_blocked?.length ? <p className="mt-1 text-xs text-rose-400">⚠ Can't install until present: <b>{m.ee_blocked.join(", ")}</b></p> : null}
        <div className="mt-3 grid grid-cols-2 gap-2">
          <Metric n={m.dependents || 0} l="Direct dependents" tone="down" />
          <Metric n={m.dependents_transitive || 0} l="Transitive dependents" tone="down" />
          <Metric n={deps.length} l="Direct deps" tone="up" />
          <Metric n={m.deps_transitive || 0} l="Transitive deps" tone="up" />
        </div>
        <div className="mt-3 flex gap-2">
          <button onClick={() => setImpact((v) => !v)} className={`flex-1 rounded-lg border px-2 py-1.5 font-mono text-xs ${impact ? "border-emerald-500 bg-emerald-500/15 text-emerald-300" : "border-zinc-800 bg-zinc-900/60 text-zinc-300 hover:border-zinc-600"}`}>{impact ? "◧ Blast radius ON" : "◧ Blast radius"}</button>
          <button onClick={() => fgRef.current && fgRef.current.zoomToFit(500, 60)} className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-2 py-1.5 font-mono text-xs text-zinc-300 hover:border-zinc-600">Fit</button>
        </div>
        <p className="mt-2 text-xs text-zinc-500">{pathTo ? <>Shortest path to <b className="text-zinc-300">{pathTo}</b> highlighted.</> : "Shift-click another node → shortest path."}</p>
        <Sec>Depends on ({deps.length})</Sec>{modChips(deps)}
        <Sec>Depended on by ({dpts.length})</Sec>{modChips(dpts)}
        <Sec>Models defined ({(m.defines || []).length})</Sec>{modelChips(m.defines || [])}
        <Sec>Models extended ({(m.extends || []).length})</Sec>{modelChips(m.extends || [])}
      </aside>
    );
  }
  // overview
  const hubs = [...idx.byId.values()].filter((m) => m.present).sort((a, b) => b.dependents - a.dependents).slice(0, 10);
  const topModels = [...idx.modelById.values()].map((m) => ({ id: m.id, n: (m.extended_by || []).length })).sort((a, b) => b.n - a.n).slice(0, 12);
  const comms = idx.communities.filter((c) => c.size >= 3).slice(0, 16);
  return (
    <aside className={wrap}>
      <h2 className="font-mono text-base text-zinc-100">Dependency graph</h2>
      <p className="mt-2 text-xs leading-relaxed text-zinc-400">Click any node, hub, model, or subsystem. <b className="text-zinc-200">Blast radius</b> (on a module) shows what breaks if it changes.</p>
      <div className="mt-3 grid grid-cols-2 gap-2"><Metric n={idx.meta.module_count} l="Modules" /><Metric n={idx.meta.ee_blocked_count} l="EE-blocked" tone="down" /></div>
      <Sec>Most depended-on</Sec>
      <div className="flex flex-wrap gap-1">{hubs.map((h) => <Chip key={h.id} onClick={() => focusOn(h.id)}>{h.id} · {h.dependents}</Chip>)}</div>
      <Sec>Most extended models</Sec>
      <div className="flex flex-wrap gap-1">{topModels.map((m) => <Chip key={m.id} tone="model" onClick={() => pickModel(m.id)}>{m.id} · {m.n}</Chip>)}</div>
      <Sec>Subsystems ({comms.length})</Sec>
      <div className="flex flex-wrap gap-1">{comms.map((c) => (
        <button key={c.id} onClick={() => pickCommunity(c.id)} className="rounded-md border bg-zinc-900/60 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300" style={{ borderColor: commColor(c.id) }}>
          <span className="mr-1 inline-block h-2 w-2 rounded-full align-middle" style={{ background: commColor(c.id) }} />{c.label || "Community " + c.id} · {c.size}
        </button>))}
      </div>
      {group && <p className="mt-3 text-xs text-zinc-400">Subsystem <b style={{ color: commColor(group.cid) }}>{group.label}</b> highlighted · {group.ids.size} modules. Click background to clear.</p>}
    </aside>
  );
}
