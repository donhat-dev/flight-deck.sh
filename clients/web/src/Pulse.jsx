import React, { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./api.js";

/* FlightDeck Pulse - attention-first board over Claude Code background agents.
   Reads /api/pulse (the ~/.claude/jobs store projection) every few seconds.
   Coral (emerald, remapped) is reserved for "Needs me": the one thing that
   wants a human. Other lanes stay calm (sky / amber / zinc). V1 is read-only:
   observe state, read the pending question, copy the attach/logs commands. */

const POLL_MS = 3000;

const LANE_META = [
  { k: "needs", label: "Needs me", dot: "bg-emerald-400", text: "text-emerald-400", ring: "shadow-[0_0_8px_rgba(255,81,51,0.8)]" },
  { k: "flight", label: "In flight", dot: "bg-sky-400", text: "text-sky-300", ring: "" },
  { k: "review", label: "Review", dot: "bg-amber-300", text: "text-amber-200", ring: "" },
  { k: "parked", label: "Parked", dot: "bg-zinc-500", text: "text-zinc-400", ring: "" },
];

const STATE_LABEL = {
  working: "In flight", busy: "In flight", blocked: "Needs me",
  needs_input: "Needs me", failed: "Failed", error: "Failed",
  review: "Review", review_ready: "Review", ready_for_review: "Review",
  stopped: "Parked", idle: "Idle", cancelled: "Parked",
  completed: "Finished", done: "Finished", finished: "Finished",
};
const STATE_TONE = {
  needs: "text-emerald-400", flight: "text-sky-300", review: "text-amber-200",
  parked: "text-zinc-400", done: "text-zinc-500",
};

function stateView(s) {
  if (s.stale) return { label: "Stale", tone: "text-amber-300", dot: "bg-amber-300" };
  const dot = LANE_META.find((l) => l.k === s.lane)?.dot || "bg-zinc-500";
  return { label: STATE_LABEL[s.state] || s.state, tone: STATE_TONE[s.lane] || "text-zinc-400", dot };
}

function shortModel(m) {
  if (!m) return null;
  const s = m.replace(/^claude-/, "").replace(/-\d{8}$/, "");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
function elapsed(iso) {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return "";
  const m = Math.round(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h${m % 60 ? ` ${m % 60}m` : ""}`;
}
function clock(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function CopyCmd({ cmd, label }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button"
      onClick={() => {
        try { navigator.clipboard?.writeText(cmd); } catch { /* ignore */ }
        setDone(true); setTimeout(() => setDone(false), 1400);
      }}
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider transition-colors ${
        done ? "border-sky-500 text-sky-300" : "border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200"
      }`}>
      {done ? "✓ Copied" : label}
    </button>
  );
}

function Card({ s, onOpen }) {
  const needs = s.lane === "needs";
  const kids = s.childCount || 0;
  const inflight = (s.inFlight?.tasks || 0) + (s.inFlight?.queued || 0);
  return (
    <button type="button" onClick={() => onOpen(s)}
      className={`group relative w-full rounded-2xl border p-4 text-left transition-colors ${
        needs
          ? "border-emerald-500/45 bg-zinc-900/60 shadow-[0_0_0_1px_rgba(255,81,51,0.18),0_0_26px_rgba(255,81,51,0.10)]"
          : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700 hover:bg-zinc-900/70"
      }`}>
      {needs && <span className="absolute left-0 top-3.5 bottom-3.5 w-[3px] rounded bg-emerald-400 shadow-[0_0_10px_rgba(255,81,51,0.7)]" />}
      <div className="mb-2 flex items-center gap-2">
        <span className="rounded-full border border-zinc-800 px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider text-zinc-400">
          {shortModel(s.model) || "Agent"}
        </span>
        {(() => { const sv = stateView(s); return (
          <span className={`ml-auto inline-flex items-center gap-1.5 font-mono text-[9px] font-semibold uppercase tracking-wider ${sv.tone}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${sv.dot}`} />
            {sv.label}
          </span>
        ); })()}
      </div>
      <div className="truncate text-sm font-semibold text-zinc-100">{s.name}</div>
      <div className="truncate font-mono text-[10px] text-zinc-500">{s.project || s.cwd || "-"}</div>
      {(s.needs || s.intent) && (
        <div className={`mt-2.5 line-clamp-2 text-[12.5px] leading-snug ${needs ? "text-zinc-100" : "text-zinc-400"}`}>
          {needs && s.needs ? s.needs : s.intent}
        </div>
      )}
      <div className="mt-3 flex items-center gap-3 border-t border-zinc-800/70 pt-2.5 font-mono text-[10px] text-zinc-500">
        {s.createdAt && <span>{elapsed(s.createdAt)}</span>}
        {s.alive === true && <span className="inline-flex items-center gap-1 text-sky-300"><span className="h-1.5 w-1.5 animate-live-pulse rounded-full bg-sky-400" />live</span>}
        {s.stale && <span className="text-amber-300">stale</span>}
        {inflight > 0 && <span className="text-zinc-400">{inflight} in flight</span>}
        {kids > 0 && <span className="text-zinc-400">{kids} child</span>}
        {typeof s.tokens === "number" && <span className="ml-auto">{s.tokens.toLocaleString()} tok</span>}
      </div>
    </button>
  );
}

function Lane({ meta, sessions, onOpen }) {
  return (
    <div className="min-w-0">
      <div className="mb-3 flex items-center gap-2 px-1">
        <span className={`h-2.5 w-2.5 rounded-full ${meta.dot} ${meta.ring}`} />
        <span className={`font-mono text-[11px] font-semibold uppercase tracking-[0.18em] ${meta.k === "needs" ? "text-emerald-400" : "text-zinc-200"}`}>
          {meta.label}
        </span>
        <span className="ml-auto rounded-full border border-zinc-800 bg-zinc-900 px-2 py-0.5 font-mono text-[10px] text-zinc-500">
          {sessions.length}
        </span>
      </div>
      <div className={`flex min-h-[110px] flex-col gap-3 rounded-2xl p-1 ${meta.k === "needs" ? "bg-gradient-to-b from-emerald-500/[0.06] to-transparent" : ""}`}>
        {sessions.length === 0 ? (
          <div className="rounded-xl border border-dashed border-zinc-800 px-3 py-4 text-center font-mono text-[10px] uppercase tracking-wider text-zinc-600">
            Clear
          </div>
        ) : (
          sessions.map((s) => <Card key={s.id} s={s} onOpen={onOpen} />)
        )}
      </div>
    </div>
  );
}

function Drawer({ s, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  if (!s) return null;
  const tl = [...(s.timeline || [])].reverse();
  return (
    <>
      <button type="button" aria-label="Close" onClick={onClose}
        className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm" />
      <aside className="fixed inset-y-0 right-0 z-[51] flex w-full max-w-[520px] flex-col border-l border-zinc-800 bg-zinc-950 shadow-[-30px_0_80px_rgba(0,0,0,0.6)]">
        <div className="flex items-start gap-3 border-b border-zinc-800/70 px-6 py-5">
          <div className="min-w-0">
            {(() => { const sv = stateView(s); return (
              <span className={`inline-flex items-center gap-1.5 font-mono text-[9px] font-semibold uppercase tracking-wider ${sv.tone}`}>
                <span className={`h-1.5 w-1.5 rounded-full ${sv.dot}`} />
                {sv.label}
                {s.alive === true && <span className="text-sky-300">· live{s.pid ? ` pid ${s.pid}` : ""}</span>}
              </span>
            ); })()}
            <h3 className="mt-1 truncate text-lg font-bold tracking-tight text-zinc-100">{s.name}</h3>
            <div className="mt-1 truncate font-mono text-[10px] text-zinc-500">
              {s.project}{s.model ? ` · ${shortModel(s.model)}` : ""}{s.createdAt ? ` · ${elapsed(s.createdAt)}` : ""}
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close"
            className="ml-auto grid h-10 w-10 shrink-0 place-items-center rounded-full border border-zinc-800 bg-white/5 text-lg text-zinc-400 hover:border-emerald-500 hover:text-zinc-100">
            {"×"}
          </button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
          {s.lane === "needs" && s.needs && (
            <div className="rounded-2xl border border-emerald-500/40 bg-gradient-to-b from-emerald-500/[0.07] to-transparent p-4">
              <div className="mb-2 font-mono text-[9px] font-semibold uppercase tracking-[0.2em] text-zinc-500">Needs your response</div>
              <div className="text-[15px] leading-relaxed text-zinc-100">{s.needs}</div>
              <div className="mt-3 font-mono text-[10px] text-zinc-500">
                Reply by attaching to the session (V1 is read-only; in-app reply is a later step).
              </div>
            </div>
          )}

          {s.intent && (
            <div>
              <div className="mb-2 font-mono text-[9px] font-semibold uppercase tracking-[0.2em] text-zinc-500">Task</div>
              <div className="text-[13.5px] leading-relaxed text-zinc-300">{s.intent}</div>
            </div>
          )}

          {s.output && (
            <div>
              <div className="mb-2 font-mono text-[9px] font-semibold uppercase tracking-[0.2em] text-zinc-500">Output</div>
              <div className="whitespace-pre-wrap rounded-xl border border-zinc-800 bg-zinc-900/50 p-3 text-[13px] leading-relaxed text-zinc-300">{s.output}</div>
            </div>
          )}

          <div className="grid grid-cols-3 gap-3">
            <Stat label="In flight" value={(s.inFlight?.tasks || 0) + (s.inFlight?.queued || 0)} />
            <Stat label="Children" value={s.childCount || 0} />
            <Stat label="Tokens" value={typeof s.tokens === "number" ? s.tokens.toLocaleString() : "-"} />
          </div>

          <div>
            <div className="mb-2 font-mono text-[9px] font-semibold uppercase tracking-[0.2em] text-zinc-500">Timeline</div>
            {tl.length === 0 ? (
              <div className="font-mono text-[11px] text-zinc-600">No transitions recorded yet.</div>
            ) : (
              <ul className="space-y-3">
                {tl.map((e, i) => (
                  <li key={i} className="relative border-l border-zinc-800 pl-4">
                    <span className={`absolute -left-[5px] top-1 h-2.5 w-2.5 rounded-full border-2 ${e.state === "blocked" ? "border-emerald-400 bg-emerald-400/20" : "border-zinc-600 bg-zinc-900"}`} />
                    <div className="flex items-baseline gap-2">
                      <span className="font-mono text-[9px] uppercase tracking-wider text-zinc-500">{e.state || "event"}</span>
                      <span className="ml-auto font-mono text-[9px] text-zinc-600">{clock(e.at)}</span>
                    </div>
                    {(e.text || e.detail) && <div className="mt-0.5 text-[13px] leading-snug text-zinc-200">{e.text || e.detail}</div>}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <div className="mb-2 font-mono text-[9px] font-semibold uppercase tracking-[0.2em] text-zinc-500">Quick actions</div>
            <div className="flex flex-wrap gap-2">
              <CopyCmd cmd={`claude attach ${s.id}`} label={`⌘ attach ${s.id}`} />
              <CopyCmd cmd={`claude logs ${s.id}`} label="logs" />
              <CopyCmd cmd={`claude stop ${s.id}`} label="stop" />
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}

function Stat({ label, value }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-3 py-2.5">
      <div className="font-mono text-[15px] font-semibold tabular-nums text-zinc-100">{value}</div>
      <div className="font-mono text-[9px] uppercase tracking-wider text-zinc-500">{label}</div>
    </div>
  );
}

export default function Pulse() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [showDone, setShowDone] = useState(false);
  const timer = useRef(null);

  const poll = useCallback(async () => {
    try {
      const d = await get("/api/pulse");
      setData(d); setErr(null);
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, []);

  useEffect(() => {
    poll();
    timer.current = setInterval(poll, POLL_MS);
    return () => clearInterval(timer.current);
  }, [poll]);

  const lanes = data?.lanes || {};
  const done = lanes.done || [];
  const openSession =
    openId && data
      ? Object.values(lanes).flat().find((s) => s.id === openId) || null
      : null;

  return (
    <main className="mx-auto max-w-[1400px] space-y-5 px-5 py-6 md:px-8 md:py-8">
      <div className="flex flex-wrap items-center gap-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-zinc-100">Pulse</h1>
          <p className="mt-0.5 max-w-2xl text-[13px] text-zinc-500">
            Background agents you own, grouped by what they need. Coral is the only thing asking for you.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-3">
          {data && (
            <span className="inline-flex items-center gap-2 rounded-full border border-emerald-500/50 bg-emerald-500/[0.08] px-3 py-1.5">
              <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(255,81,51,0.7)]" />
              <span className="font-mono text-[11px] uppercase tracking-wider text-zinc-300">
                <b className="text-emerald-400">{data.attention}</b> need you
              </span>
            </span>
          )}
          {data && data.liveness && data.daemonWorkers > 0 && (
            <span className="font-mono text-[10px] uppercase tracking-wider text-sky-300">
              {data.daemonWorkers} live
            </span>
          )}
          <span className="inline-flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-zinc-500">
            <span className="h-1.5 w-1.5 animate-live-pulse rounded-full bg-sky-400" /> live
          </span>
        </div>
      </div>

      {err && (
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          Pulse feed error: {err}
        </div>
      )}

      {data && !data.available && (
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 px-5 py-8 text-center">
          <div className="font-mono text-[11px] uppercase tracking-wider text-zinc-500">Jobs store not found</div>
          <div className="mx-auto mt-2 max-w-md text-sm text-zinc-400">
            No background-agent store at <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">{data.jobsDir}</code>.
            Start one with <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">claude --bg "your task"</code>.
          </div>
        </div>
      )}

      {data && data.available && data.total === 0 && (
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 px-5 py-8 text-center">
          <div className="font-mono text-[11px] uppercase tracking-wider text-zinc-500">No background agents</div>
          <div className="mx-auto mt-2 max-w-md text-sm text-zinc-400">
            Dispatch one with <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">claude --bg "your task"</code> or from <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">claude agents</code>.
          </div>
        </div>
      )}

      {data && data.available && data.total > 0 && (
        <>
          <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {LANE_META.map((m) => (
              <Lane key={m.k} meta={m} sessions={lanes[m.k] || []} onOpen={(s) => setOpenId(s.id)} />
            ))}
          </div>

          {done.length > 0 && (
            <div className="mt-2">
              <button type="button" onClick={() => setShowDone((v) => !v)}
                className="flex w-full items-center gap-2 border-t border-zinc-800/70 py-3 font-mono text-[11px] uppercase tracking-[0.16em] text-zinc-400 hover:text-zinc-200">
                <span className={`transition-transform ${showDone ? "rotate-90" : ""}`}>{"▸"}</span>
                Finished
                <span className="ml-1 rounded-full border border-zinc-800 bg-zinc-900 px-2 py-0.5 text-[10px] text-zinc-500">{done.length}</span>
              </button>
              {showDone && (
                <div className="grid grid-cols-1 gap-3 pt-1 sm:grid-cols-2 xl:grid-cols-4">
                  {done.map((s) => <Card key={s.id} s={s} onOpen={(x) => setOpenId(x.id)} />)}
                </div>
              )}
            </div>
          )}
        </>
      )}

      <Drawer s={openSession} onClose={() => setOpenId(null)} />
    </main>
  );
}
