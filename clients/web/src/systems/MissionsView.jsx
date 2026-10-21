import React, { useEffect, useState, useCallback, useRef } from "react";
import { get, post, del } from "../api.js";

const REDUCED = typeof window !== "undefined" && window.matchMedia
  && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Reveal text with a type-out effect when it CHANGES (not on first mount), so an
// agent's note edit visibly "types" in. Static on first render / reduced-motion.
function TypeOut({ text, className }) {
  const [shown, setShown] = useState(text);
  const first = useRef(true);
  useEffect(() => {
    if (first.current || REDUCED) { first.current = false; setShown(text); return; }
    const full = text || "";
    const step = Math.max(1, Math.ceil(full.length / 44));
    let i = 0;
    setShown("");
    const iv = setInterval(() => {
      i += step;
      setShown(full.slice(0, i));
      if (i >= full.length) { setShown(full); clearInterval(iv); }
    }, 22);
    return () => clearInterval(iv);
  }, [text]);
  return <span className={className}>{shown}</span>;
}

// Diff successive polls to drive per-card FX: `justMoved` (brief, on status change,
// for the column-move animation) and `lastKind` (persistent: "content" vs "meta",
// which steers the collaborator cursor to where the last edit happened).
function useBoardFx(missions) {
  const prev = useRef({});
  const [fx, setFx] = useState({});
  useEffect(() => {
    const p = prev.current, nextPrev = {}, moved = [], kind = {};
    for (const m of missions) {
      const mtags = JSON.stringify(m.tags || []);
      const old = p[m.id];
      if (old) {
        const sc = old.status !== m.status;
        const nc = old.note !== m.note;
        const tc = old.tags !== mtags;
        if (sc) moved.push(m.id);
        if (nc) kind[m.id] = "content";
        else if (sc || tc) kind[m.id] = "meta";
      }
      nextPrev[m.id] = { status: m.status, note: m.note, tags: mtags };
    }
    prev.current = nextPrev;
    if (moved.length || Object.keys(kind).length) {
      setFx((f) => {
        const nf = { ...f };
        for (const id in kind) nf[id] = { ...(nf[id] || {}), lastKind: kind[id] };
        for (const id of moved) nf[id] = { ...(nf[id] || {}), justMoved: true };
        return nf;
      });
      if (moved.length) setTimeout(() => setFx((f) => {
        const nf = { ...f };
        for (const id of moved) if (nf[id]) nf[id] = { ...nf[id], justMoved: false };
        return nf;
      }), 650);
    }
  }, [missions]);
  return fx;
}

/* Missions — a personal TODO/Note kanban whose defining trait is session-hold.
   The board polls /api/missions every 2s, so a claim made by a separate agent
   session (via the missions MCP, which shares the same store) shows up here
   within ~2s. This UI claims under a fixed session id so its own holds are
   distinguishable from agent-session holds. Styled to match the live app
   (zinc surfaces + emerald accent), not the coral design mock (deferred). */

const SESSION = "WEBUI";

// This browser's stable friendly identity (like a Figma collaborator name), so a
// take-over shows a named cursor rather than the raw session id.
const IDENTITY_NAMES = ["Daedalus", "Icarus", "Theseus", "Orion", "Atlas", "Perseus", "Hermes", "Odysseus", "Ariadne", "Persephone", "Helios", "Selene", "Calypso", "Nyx"];
const IDENTITY_WORDS = ["Ember", "Quill", "Falcon", "Wildwood", "Cinder", "Harbor", "Vesper", "Marlow", "Thorn", "Sable", "Onyx", "Slate", "Wren", "Cove"];
function myIdentity() {
  try {
    let n = localStorage.getItem("fd_missions_identity");
    if (!n) {
      n = IDENTITY_NAMES[Math.floor(Math.random() * IDENTITY_NAMES.length)] + " " +
          IDENTITY_WORDS[Math.floor(Math.random() * IDENTITY_WORDS.length)];
      localStorage.setItem("fd_missions_identity", n);
    }
    return n;
  } catch { return "You"; }
}
function hashStr(s) { let h = 0; for (let i = 0; i < (s || "").length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h; }
// Identity palette (distinct per collaborator; separate from the liveness colors).
const CURSOR_COLORS = ["#38bdf8", "#a78bfa", "#34d399", "#fbbf24", "#fb7185", "#22d3ee", "#fb923c", "#a3e635"];
function identityColor(name) { return CURSOR_COLORS[hashStr(name || "") % CURSOR_COLORS.length]; }

const COLUMNS = [
  { k: "INBOX", label: "Inbox" },
  { k: "TODO", label: "To do" },
  { k: "IN_FLIGHT", label: "In flight", live: true },
  { k: "DONE", label: "Done" },
];
const PRIORITIES = ["LOW", "NORMAL", "HIGH"];

function holdState(m) {
  if (m.status === "DONE") return "landed";
  if (m.hold) return "active";
  return "open";
}

/* ---- data hook: poll every 2s ---------------------------------------- */
function useMissions() {
  const [data, setData] = useState({ missions: [], sessions: [] });
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(() => {
    return get("/api/missions")
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message || String(e)))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [load]);
  return { data, error, loading, reload: load };
}

/* ---- small pieces ----------------------------------------------------- */
// Presence liveness on a held card: ACTIVE (fresh heartbeat, pulsing = "working"),
// HELD (holder went quiet), STALE (likely died without releasing).
const LIVE = {
  ACTIVE: { dot: "bg-emerald-400 animate-live-pulse", text: "text-emerald-400", label: "working" },
  HELD: { dot: "bg-zinc-500", text: "text-zinc-400", label: "held" },
  STALE: { dot: "bg-rose-500", text: "text-rose-400", label: "stale" },
};

function HoldStrip({ m }) {
  const s = holdState(m);
  if (s === "landed") {
    return <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-zinc-500">✓ landed</span>;
  }
  if (s === "open") {
    return <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-zinc-600">unclaimed</span>;
  }
  const cfg = LIVE[m.hold?.state] || LIVE.ACTIVE;
  return (
    <div className="flex items-center gap-1.5">
      <span className={`h-1.5 w-1.5 rounded-full ${cfg.dot}`} />
      <span className={`text-[11px] font-semibold ${cfg.text}`}>{m.hold.name}</span>
      <span className="font-mono text-[9px] uppercase tracking-wider text-zinc-600">{cfg.label}</span>
    </div>
  );
}

// Figma-style collaborator cursor: shows on a held card at a pseudo-random spot
// (stable per mission id) and drifts gently, so "someone is working on this note"
// reads without the agent ever reporting a position. Colored + named per holder.
function Cursor({ name, lastKind }) {
  const color = identityColor(name);
  // Static, frontend-computed position by the action the card last received.
  let left, top;
  if (lastKind === "content") { top = 72; left = 12; }   // note edit -> after the text
  else if (lastKind === "meta") { top = 8; left = 64; }  // status/tag edit -> top-right
  else { top = 8; left = 8; }                            // just claimed -> top-left
  return (
    <div className="pointer-events-none absolute z-20" style={{ left: `${left}%`, top: `${top}%`, transition: "top .5s cubic-bezier(.32,.72,0,1), left .5s cubic-bezier(.32,.72,0,1)" }}>
      <svg width="15" height="15" viewBox="0 0 20 20" style={{ filter: "drop-shadow(0 1px 2px rgba(0,0,0,.55))" }}>
        <path d="M2 2 L2 15.5 L6 12 L8.6 17.6 L10.8 16.6 L8.2 11 L14 11 Z" fill={color} stroke="#0a0a0a" strokeWidth="0.6" />
      </svg>
      <span className="absolute left-3 top-2.5 whitespace-nowrap rounded px-1.5 py-0.5 text-[9px] font-semibold text-zinc-950 shadow-md" style={{ backgroundColor: color }}>
        {name}
      </span>
    </div>
  );
}

function Tag({ children }) {
  return (
    <span className="rounded-full border border-zinc-800 bg-white/5 px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wide text-zinc-400">
      {children}
    </span>
  );
}

// Kind marker: NOTE = memory to read (sky), TODO = task to act on (emerald).
// The card SHAPE also encodes it (see MissionCard): NOTE is a sharp square,
// TODO is rounded -- so an agent/human reads the split at a glance.
function KindBadge({ kind }) {
  const note = kind === "NOTE";
  return (
    <span className={`shrink-0 rounded-full px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wide ${
      note ? "bg-sky-500/15 text-sky-400" : "bg-emerald-500/10 text-emerald-400/80"
    }`}>
      {note ? "▢ note" : "☑ todo"}
    </span>
  );
}

function MissionCard({ m, onOpen, fx, parentTitle, childStats }) {
  const s = holdState(m);
  const note = m.kind === "NOTE";
  const doneCls = m.is_read ? "border-zinc-800 opacity-60" : "border-emerald-500/60 fd-done-glow";
  return (
    <button
      type="button"
      draggable={m.kind === "TODO"}
      onDragStart={(e) => { e.dataTransfer.setData("text/plain", m.id); e.dataTransfer.effectAllowed = "move"; }}
      onClick={() => onOpen(m)}
      className={`relative w-full border bg-zinc-900/60 p-3 text-left transition-colors hover:bg-zinc-900 ${fx?.justMoved ? "fd-just-moved" : ""} ${m.kind === "TODO" && s !== "landed" ? "cursor-grab active:cursor-grabbing" : ""} ${
        note ? "rounded-none" : "rounded-xl"
      } ${s === "landed" ? doneCls
          : m.hold ? (m.hold.state === "STALE" ? "border-rose-500/50"
                    : m.hold.state === "HELD" ? "border-zinc-700" : "border-emerald-500/40")
          : "border-zinc-800"}`}
    >
      {m.hold?.state === "ACTIVE" && (
        <div className="mb-2 h-0.5 w-full overflow-hidden rounded-full bg-emerald-500/20">
          <div className="h-full w-1/2 animate-live-pulse rounded-full bg-emerald-400" />
        </div>
      )}
      {m.hold && <Cursor name={m.hold.name} lastKind={fx?.lastKind} />}
      <div className="flex items-center justify-between gap-2">
        {note
          ? <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-sky-400/80">memory</span>
          : <HoldStrip m={m} />}
        <KindBadge kind={m.kind} />
      </div>
      <div className="mt-1.5 text-sm font-semibold leading-snug text-zinc-100">{m.title}</div>
      {parentTitle && (
        <div className="mt-1 truncate font-mono text-[10px] text-zinc-600">↳ {parentTitle}</div>
      )}
      {m.note && <div className="mt-1 line-clamp-4 text-xs leading-relaxed text-zinc-500"><TypeOut text={m.note} /></div>}
      {childStats && (
        <div className="mt-2 flex items-center gap-2">
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-zinc-800">
            <div className="h-full rounded-full bg-emerald-500" style={{ width: `${childStats.total ? Math.round(100 * childStats.done / childStats.total) : 0}%` }} />
          </div>
          <span className="font-mono text-[10px] text-zinc-500">◑ {childStats.done}/{childStats.total}</span>
        </div>
      )}
      <div className="mt-2 flex items-center gap-1.5">
        {(m.tags || []).map((t) => <Tag key={t}>{t}</Tag>)}
        {m.priority === "HIGH" && <Tag>high</Tag>}
      </div>
    </button>
  );
}

/* ---- quick-add (inline, Odoo-style) ----------------------------------- */
function QuickAdd({ status, onCreated }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const add = (openAfter) => {
    if (!title.trim()) return;
    setBusy(true);
    // Quick-create captures a NOTE (memory) by default; use the full New form for a TODO.
    post("/api/missions", { title: title.trim(), status, kind: "NOTE" })
      .then((m) => { setTitle(""); setOpen(false); onCreated(openAfter ? m : null); })
      .finally(() => setBusy(false));
  };
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}
        className="w-full rounded-lg border border-dashed border-zinc-800 py-2 text-xs font-medium text-zinc-500 transition-colors hover:border-zinc-700 hover:text-zinc-300">
        + Note
      </button>
    );
  }
  return (
    <div className="rounded-xl border border-emerald-500/40 bg-zinc-900/60 p-3">
      <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") add(false); if (e.key === "Escape") setOpen(false); }}
        placeholder="Quick note (memory)" disabled={busy}
        className="w-full rounded-md border border-zinc-800 bg-zinc-950 px-2.5 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none focus:border-emerald-500/50" />
      <div className="mt-2 flex items-center gap-2">
        <button type="button" onClick={() => add(false)} disabled={busy}
          className="rounded-full bg-emerald-500/90 px-3 py-1 font-mono text-[10px] font-semibold uppercase tracking-wider text-zinc-950 hover:bg-emerald-400">Add</button>
        <button type="button" onClick={() => add(true)} disabled={busy}
          className="rounded-full border border-zinc-800 px-3 py-1 font-mono text-[10px] font-semibold uppercase tracking-wider text-zinc-300 hover:bg-white/5">Save &amp; open</button>
        <button type="button" onClick={() => setOpen(false)} className="ml-auto text-zinc-600 hover:text-zinc-300" aria-label="Discard">✕</button>
      </div>
    </div>
  );
}

/* ---- detail modal ----------------------------------------------------- */
function DetailModal({ mid, onClose, onChanged }) {
  const [m, setM] = useState(null);
  const [err, setErr] = useState(null);
  const load = useCallback(() => get(`/api/missions/${mid}`).then(setM).catch((e) => setErr(e.message)), [mid]);
  useEffect(() => { load(); }, [load]);
  const act = (path, body) => post(`/api/missions/${mid}${path}`, body).then((x) => { setM(x); onChanged(); });
  const setKind = (kind) => fetch(`/api/missions/${mid}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind }),
  }).then((r) => r.json()).then((x) => { setM(x); onChanged(); });
  const remove = () => {
    if (window.confirm("Delete this mission permanently?"))
      del(`/api/missions/${mid}`).then(() => { onChanged(); onClose(); }).catch(() => {});
  };

  return (
    <div className="fixed inset-0 z-[55] flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/70 backdrop-blur-sm" />
      <div className="relative z-10 flex max-h-[86dvh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950 shadow-2xl">
        {!m ? (
          <div className="p-8 text-sm text-zinc-500">{err ? `Error: ${err}` : "Loading…"}</div>
        ) : (
          <>
            <div className="flex items-start justify-between gap-4 border-b border-zinc-800/80 p-5">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="rounded-full border border-zinc-800 bg-white/5 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-zinc-300">
                    {m.status.replace("_", " ")}
                  </span>
                  {m.kind !== "NOTE" && m.hold && (
                    <span className={`flex items-center gap-1.5 font-mono text-[10px] font-semibold uppercase tracking-wider ${(LIVE[m.hold.state] || LIVE.ACTIVE).text}`}>
                      <span className={`h-1.5 w-1.5 rounded-full ${(LIVE[m.hold.state] || LIVE.ACTIVE).dot}`} />
                      {(LIVE[m.hold.state] || LIVE.ACTIVE).label} · session {m.hold.session_id}
                    </span>
                  )}
                </div>
                <h2 className="mt-2 text-xl font-bold tracking-tight text-zinc-100">{m.title}</h2>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button type="button" onClick={remove} aria-label="Delete" title="Delete mission"
                  className="grid h-9 w-9 place-items-center rounded-full border border-zinc-800 bg-white/5 text-zinc-500 hover:border-rose-500/50 hover:text-rose-400">🗑</button>
                <button type="button" onClick={onClose} aria-label="Close"
                  className="grid h-9 w-9 place-items-center rounded-full border border-zinc-800 bg-white/5 text-zinc-400 hover:text-zinc-100">✕</button>
              </div>
            </div>
            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
              <div className="flex items-center gap-3">
                <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-600">Kind</span>
                <div className="inline-flex overflow-hidden rounded-xl border border-zinc-800" role="group" aria-label="Kind">
                  {[["NOTE", "▢ Note", "memory"], ["TODO", "☑ Todo", "task"]].map(([k, label, hint]) => (
                    <button key={k} type="button" onClick={() => setKind(k)} aria-pressed={m.kind === k}
                      title={hint}
                      className={`px-6 py-2.5 font-mono text-xs font-semibold uppercase tracking-wider transition-colors ${
                        m.kind === k
                          ? (k === "NOTE" ? "bg-sky-400 text-zinc-950" : "bg-emerald-500 text-zinc-950")
                          : "text-zinc-500 hover:bg-white/5 hover:text-zinc-300"
                      }`}>
                      {label}
                    </button>
                  ))}
                </div>
                <span className="font-mono text-[10px] text-zinc-600">{m.kind === "NOTE" ? "memory to read" : "task to act on"}</span>
              </div>
              {m.note && (
                <div>
                  <div className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-600">Note</div>
                  <div className="mt-1.5 text-sm leading-relaxed text-zinc-200"><TypeOut text={m.note} /></div>
                </div>
              )}
              <div>
                <div className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-600">History</div>
                <ol className="relative mt-3 space-y-3.5 border-l border-zinc-800 pl-4">
                  {(m.log || []).map((e, i) => (
                    <li key={i} className="relative">
                      <span className={`absolute -left-[21px] top-0.5 h-2.5 w-2.5 rounded-full ring-4 ring-zinc-950 ${
                        i === 0 ? "bg-emerald-400" : "bg-zinc-600"
                      }`} />
                      <div className="flex items-baseline justify-between gap-2">
                        <div className="flex items-baseline gap-2">
                          <span className={`font-mono text-xs font-semibold ${i === 0 ? "text-zinc-100" : "text-zinc-300"}`}>
                            {e.session_id ? `session ${e.session_id}` : "system"}
                          </span>
                          <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-500">{e.action}</span>
                        </div>
                        <span className="shrink-0 font-mono text-[10px] text-zinc-600">{e.at}</span>
                      </div>
                    </li>
                  ))}
                  {!(m.log || []).length && <li className="text-xs text-zinc-600">No history yet</li>}
                </ol>
              </div>
              {m.children && m.children.length > 0 && (
                <div>
                  <div className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-600">
                    Subtasks ({m.children.filter((c) => c.status === "DONE").length}/{m.children.length})
                  </div>
                  <div className="mt-2 space-y-1.5">
                    {m.children.map((c) => (
                      <div key={c.id} className="flex items-center justify-between gap-3 rounded-lg border border-zinc-800 px-3 py-2">
                        <div className="min-w-0">
                          <div className="truncate text-sm text-zinc-200">{c.title}</div>
                          {c.hold && <div className="font-mono text-[10px] text-zinc-500">{c.hold.name} · {c.hold.state.toLowerCase()}</div>}
                        </div>
                        <span className="shrink-0 rounded-full border border-zinc-800 bg-white/5 px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider text-zinc-400">{c.status.replace("_", " ")}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
            {m.kind !== "NOTE" && (
              <div className="flex items-center gap-2 border-t border-zinc-800/80 p-4">
                <button type="button" onClick={() => act("/claim", { session_id: SESSION, name: myIdentity() })}
                  className="rounded-full bg-emerald-500/90 px-4 py-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-zinc-950 hover:bg-emerald-400">
                  Take over hold
                </button>
                <button type="button" onClick={() => act("/release")}
                  className="rounded-full border border-zinc-800 px-4 py-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-zinc-300 hover:bg-white/5">
                  Release
                </button>
                <button type="button" onClick={() => act("/land")}
                  className="ml-auto rounded-full border border-zinc-800 px-4 py-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-zinc-300 hover:bg-white/5">
                  Mark landed
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ---- new-mission modal ------------------------------------------------ */
function NewModal({ onClose, onCreated }) {
  const [f, setF] = useState({ title: "", note: "", status: "INBOX", priority: "NORMAL", tags: "", kind: "TODO", claim: true });
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setF((p) => ({ ...p, [k]: v }));
  const submit = () => {
    if (!f.title.trim()) return;
    setBusy(true);
    post("/api/missions", {
      title: f.title.trim(), note: f.note, status: f.status, priority: f.priority,
      tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean),
      kind: f.kind,
      claim_session: (f.kind === "TODO" && f.claim) ? SESSION : null,
    }).then(() => { onCreated(); onClose(); }).finally(() => setBusy(false));
  };
  const inp = "w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 outline-none focus:border-emerald-500/50";
  const lbl = "font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500";
  return (
    <div className="fixed inset-0 z-[55] flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/70 backdrop-blur-sm" />
      <div className="relative z-10 w-full max-w-lg space-y-4 rounded-2xl border border-zinc-800 bg-zinc-950 p-5 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold tracking-tight text-zinc-100">New mission</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="grid h-9 w-9 place-items-center rounded-full border border-zinc-800 bg-white/5 text-zinc-400 hover:text-zinc-100">✕</button>
        </div>
        <div className="space-y-1"><div className={lbl}>Title</div>
          <input autoFocus className={inp} value={f.title} onChange={(e) => set("title", e.target.value)} placeholder="Mission title" /></div>
        <div className="space-y-1"><div className={lbl}>Note</div>
          <textarea className={`${inp} h-24 resize-none`} value={f.note} onChange={(e) => set("note", e.target.value)} placeholder="Details" /></div>
        <div className="space-y-1"><div className={lbl}>Kind</div>
          <div className="inline-flex overflow-hidden rounded-lg border border-zinc-800" role="group" aria-label="Kind">
            {["TODO", "NOTE"].map((k) => (
              <button key={k} type="button" onClick={() => set("kind", k)} aria-pressed={f.kind === k}
                className={`px-4 py-1.5 font-mono text-[10px] font-semibold uppercase tracking-wide transition-colors ${
                  f.kind === k
                    ? (k === "NOTE" ? "bg-sky-400 text-zinc-950" : "bg-emerald-500 text-zinc-950")
                    : "text-zinc-500 hover:text-zinc-300"
                }`}>{k}</button>
            ))}
          </div>
          <div className="pt-0.5 font-mono text-[9px] text-zinc-600">{f.kind === "NOTE" ? "Memory / reference to read" : "Task to act on"}</div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1"><div className={lbl}>Column</div>
            <select className={inp} value={f.status} onChange={(e) => set("status", e.target.value)}>
              {COLUMNS.map((c) => <option key={c.k} value={c.k}>{c.label}</option>)}
            </select></div>
          <div className="space-y-1"><div className={lbl}>Priority</div>
            <select className={inp} value={f.priority} onChange={(e) => set("priority", e.target.value)}>
              {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
            </select></div>
        </div>
        <div className="space-y-1"><div className={lbl}>Tags (comma-separated)</div>
          <input className={inp} value={f.tags} onChange={(e) => set("tags", e.target.value)} placeholder="BILLING, LAGO" /></div>
        {f.kind === "TODO" && (
          <label className="flex items-center gap-2.5 rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
            <input type="checkbox" checked={f.claim} onChange={(e) => set("claim", e.target.checked)} className="accent-emerald-500" />
            <span className="text-sm text-zinc-200">Claim hold on create <span className="font-mono text-[10px] text-zinc-500">(session {SESSION})</span></span>
          </label>
        )}
        <div className="flex items-center justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="rounded-full px-4 py-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-zinc-400 hover:text-zinc-200">Cancel</button>
          <button type="button" onClick={submit} disabled={busy || !f.title.trim()}
            className="rounded-full bg-emerald-500/90 px-4 py-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-zinc-950 hover:bg-emerald-400 disabled:opacity-40">
            Create mission
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---- board ------------------------------------------------------------ */
export default function MissionsView() {
  const { data, error, loading, reload } = useMissions();
  const [openId, setOpenId] = useState(null);
  const [showNew, setShowNew] = useState(false);
  const [dragOverCol, setDragOverCol] = useState(null);
  const fx = useBoardFx(data.missions);

  const byCol = (k) => data.missions.filter((m) => m.status === k);
  const activeCount = data.sessions.length;
  // parent/child rollup (A2): children live in their own columns; a parent shows a
  // progress rollup, a child shows a "↳ parent" lineage chip.
  const byId = {};
  data.missions.forEach((m) => { byId[m.id] = m; });
  const childStatsOf = {};
  data.missions.forEach((m) => {
    if (!m.parent_id) return;
    const s = childStatsOf[m.parent_id] || { total: 0, done: 0 };
    s.total += 1; if (m.status === "DONE") s.done += 1;
    childStatsOf[m.parent_id] = s;
  });

  // Open a card; opening a freshly-done (glowing) one marks it read -> it dims.
  const open = (m) => {
    setOpenId(m.id);
    if (m.status === "DONE" && !m.is_read) post(`/api/missions/${m.id}/read`).then(reload).catch(() => {});
  };
  // Drag-and-drop a TODO to another column = change its status (notes are not draggable).
  const moveCard = (id, toStatus) => {
    const m = data.missions.find((x) => x.id === id);
    if (!m || m.kind !== "TODO" || m.status === toStatus) return;
    fetch(`/api/missions/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: toStatus }),
    }).then(reload).catch(() => {});
  };

  return (
    <div className="space-y-4">
      <style>{`
        @keyframes fd-glow { 0%,100%{box-shadow:0 0 0 0 rgba(52,211,153,0)} 50%{box-shadow:0 0 18px 1px rgba(52,211,153,.45)} }
        @keyframes fd-moved { 0%{transform:scale(.92);opacity:.35} 60%{transform:scale(1.01)} 100%{transform:scale(1);opacity:1} }
        .fd-done-glow { animation: fd-glow 1.8s ease-in-out infinite; }
        .fd-just-moved { animation: fd-moved .5s cubic-bezier(.32,.72,0,1); }
        @media (prefers-reduced-motion: reduce) { .fd-done-glow, .fd-just-moved { animation: none !important; } }
      `}</style>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-zinc-500">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
          {activeCount} session{activeCount === 1 ? "" : "s"} holding
        </div>
        <button type="button" onClick={() => setShowNew(true)}
          className="rounded-full bg-emerald-500/90 px-4 py-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-zinc-950 hover:bg-emerald-400">
          + New mission
        </button>
      </div>

      {error && <div className="rounded-lg border border-red-900/50 bg-red-950/30 px-4 py-3 text-sm text-red-300">Could not load missions: {error}</div>}
      {loading && !data.missions.length && <div className="py-16 text-center text-sm text-zinc-600">Loading missions…</div>}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {COLUMNS.map((c) => {
          const cards = byCol(c.k);
          return (
            <div key={c.k}
              onDragOver={(e) => { e.preventDefault(); if (dragOverCol !== c.k) setDragOverCol(c.k); }}
              onDragLeave={() => setDragOverCol((v) => (v === c.k ? null : v))}
              onDrop={(e) => { e.preventDefault(); setDragOverCol(null); moveCard(e.dataTransfer.getData("text/plain"), c.k); }}
              className={`space-y-3 rounded-xl p-1 transition-colors ${dragOverCol === c.k ? "bg-emerald-500/5 ring-1 ring-emerald-500/30" : ""}`}>
              <div className="flex items-center justify-between px-1">
                <div className="flex items-center gap-2">
                  {c.live && <span className="h-1.5 w-1.5 animate-live-pulse rounded-full bg-emerald-400" />}
                  <span className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-zinc-300">{c.label}</span>
                </div>
                <span className="font-mono text-[11px] text-zinc-600">{cards.length}</span>
              </div>
              {c.k === "INBOX" && <QuickAdd status="INBOX" onCreated={(m) => { reload(); if (m) setOpenId(m.id); }} />}
              {cards.map((m) => <MissionCard key={m.id} m={m} onOpen={open} fx={fx[m.id]}
                parentTitle={m.parent_id ? (byId[m.parent_id] && byId[m.parent_id].title) : null}
                childStats={childStatsOf[m.id]} />)}
              {!cards.length && c.k !== "INBOX" && (
                <div className="rounded-xl border border-dashed border-zinc-900 py-6 text-center font-mono text-[10px] uppercase tracking-wider text-zinc-700">Empty</div>
              )}
            </div>
          );
        })}
      </div>

      {openId && <DetailModal mid={openId} onClose={() => setOpenId(null)} onChanged={reload} />}
      {showNew && <NewModal onClose={() => setShowNew(false)} onCreated={reload} />}
    </div>
  );
}
