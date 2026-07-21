import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { get } from "../api.js";
import { streamRun, resumeRun } from "./client.js";
import { initialRun, applyEvent } from "./reducer.js";

/* ---- Relay: an AG-UI event flow, live ----------------------------------- */
// FlightDeck speaks to the agent backend over an AG-UI-shaped event stream:
// POST /api/agui/run returns Server-Sent Events (RUN_*, TEXT_MESSAGE_*,
// TOOL_CALL_*, STATE_SNAPSHOT/DELTA, CUSTOM). Two modes:
//   replay — a real Claude Code session rendered as AG-UI events;
//   demo   — a scripted run that PAUSES on a destructive tool for human
//            approval (interrupt / resume), the part a transcript can't show.
// All colours flow through the zinc/emerald ramps so Night + Day both read.

const SPEEDS = [
  { key: 2, label: "2×" },
  { key: 1, label: "1×" },
  { key: 4, label: "4×" },
  { key: 0, label: "Max" },
];

const STATUS = {
  idle: { label: "idle", dot: "bg-zinc-600", text: "text-zinc-400" },
  running: { label: "running", dot: "bg-emerald-400 animate-live-pulse", text: "text-emerald-300" },
  interrupted: { label: "interrupted", dot: "bg-amber-400", text: "text-amber-300" },
  done: { label: "done", dot: "bg-zinc-500", text: "text-zinc-300" },
  error: { label: "error", dot: "bg-rose-400", text: "text-rose-300" },
};

function Eyebrow({ children, className = "" }) {
  return (
    <div className={`font-mono text-[10px] uppercase leading-tight tracking-[0.17em] text-zinc-500 ${className}`}>
      {children}
    </div>
  );
}

function Seg({ options, value, onChange }) {
  return (
    <div className="inline-flex overflow-hidden rounded-lg border border-[color:var(--fd-hair-2)]">
      {options.map((o) => (
        <button key={o.key} type="button" onClick={() => onChange(o.key)}
          className={`px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide transition-colors ${
            value === o.key ? "bg-emerald-500/15 text-emerald-300" : "text-zinc-500 hover:bg-zinc-500/10 hover:text-zinc-300"
          }`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ---- timeline items ----------------------------------------------------- */
function ToolCard({ it }) {
  let args = it.args;
  try { args = JSON.stringify(JSON.parse(it.args), null, 2); } catch { /* partial */ }
  return (
    <div className="rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-500/[0.03]">
      <div className="flex items-center gap-2 border-b border-[color:var(--fd-hair-2)] px-3 py-1.5">
        <span className={`h-1.5 w-1.5 rounded-full ${it.status === "done" ? (it.isError ? "bg-rose-400" : "bg-emerald-400") : "bg-amber-400 animate-live-pulse"}`} />
        <span className="font-mono text-[11px] font-semibold text-sky-300">{it.name}</span>
        <span className="font-mono text-[9px] uppercase tracking-wide text-zinc-600">tool call</span>
      </div>
      {args && (
        <pre className="overflow-x-auto px-3 py-2 font-mono text-[10px] leading-[1.5] text-zinc-400">{args}</pre>
      )}
      {it.result != null && (
        <pre className={`overflow-x-auto border-t border-[color:var(--fd-hair-2)] px-3 py-2 font-mono text-[10px] leading-[1.5] ${it.isError ? "text-rose-300/90" : "text-zinc-500"}`}>{String(it.result).slice(0, 800)}</pre>
      )}
    </div>
  );
}

function TimelineItem({ it }) {
  if (it.kind === "step") {
    return (
      <div className="flex items-center gap-2 pt-1">
        <span className="h-px w-4 bg-[color:var(--fd-hair-2)]" />
        <span className="font-mono text-[9px] uppercase tracking-[0.15em] text-zinc-600">{it.name}</span>
        <span className="h-px flex-1 bg-[color:var(--fd-hair-2)]" />
      </div>
    );
  }
  if (it.kind === "message") {
    return (
      <div className="rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-500/[0.04] px-3.5 py-2.5">
        <Eyebrow className="mb-1">{it.role}</Eyebrow>
        <div className="whitespace-pre-wrap text-[12.5px] leading-[1.6] text-zinc-200">
          {it.text}
          {!it.done && <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-emerald-400/70 align-middle" />}
        </div>
      </div>
    );
  }
  if (it.kind === "tool") return <ToolCard it={it} />;
  if (it.kind === "reasoning") {
    return (
      <div className="px-1 text-[11px] italic leading-[1.5] text-zinc-500">
        <span className="mr-1.5 not-italic text-zinc-600">✦ thinking</span>{it.text}
      </div>
    );
  }
  if (it.kind === "activity") {
    return (
      <div className="flex items-center gap-1.5 px-1 font-mono text-[10px] text-zinc-500">
        <span className="text-zinc-600">▸</span>{it.text}
      </div>
    );
  }
  return null;
}

/* ---- interrupt / approval card ------------------------------------------ */
function ApprovalCard({ interrupt, busy, onDecide }) {
  const [editing, setEditing] = useState(false);
  const [cmd, setCmd] = useState(interrupt.command || "");
  return (
    <section className="fd-shell">
      <div className="fd-core border-l-2 border-amber-400/60 p-4">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-amber-400" />
          <span className="text-[13px] font-semibold text-amber-200">Human approval required</span>
          <span className="font-mono text-[9px] uppercase tracking-wide text-zinc-500">interrupt</span>
        </div>
        <div className="mt-1.5 text-[11px] text-zinc-400">{interrupt.reason}</div>
        <div className="mt-3">
          <Eyebrow className="mb-1">{interrupt.toolName} command</Eyebrow>
          {editing ? (
            <textarea value={cmd} onChange={(e) => setCmd(e.target.value)} rows={2}
              className="w-full rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-500/5 px-3 py-2 font-mono text-[11px] text-zinc-200 outline-none focus:border-emerald-500/40" />
          ) : (
            <pre className="overflow-x-auto rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-500/5 px-3 py-2 font-mono text-[11px] text-zinc-300">{cmd}</pre>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" disabled={busy}
            onClick={() => onDecide(editing ? "edit" : "approve", editing ? cmd : undefined)}
            className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-emerald-300 transition-colors hover:bg-emerald-500/20 disabled:opacity-50">
            {editing ? "Approve edited" : "Approve"}
          </button>
          <button type="button" disabled={busy} onClick={() => onDecide("reject")}
            className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-rose-300 transition-colors hover:bg-rose-500/20 disabled:opacity-50">
            Reject
          </button>
          <button type="button" disabled={busy} onClick={() => setEditing((v) => !v)}
            className="rounded-lg border border-[color:var(--fd-hair-2)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-zinc-400 transition-colors hover:bg-zinc-500/10 disabled:opacity-50">
            {editing ? "Cancel edit" : "Edit"}
          </button>
        </div>
      </div>
    </section>
  );
}

/* ---- state panel -------------------------------------------------------- */
function StatePanel({ state, flash }) {
  const files = Array.isArray(state.filesTouched) ? state.filesTouched : [];
  const scalars = Object.entries(state).filter(([, v]) => typeof v !== "object");
  return (
    <section className="fd-shell">
      <div className={`fd-core p-4 transition-colors duration-300 ${flash ? "bg-emerald-500/[0.06]" : ""}`}>
        <Eyebrow className="mb-2">Shared state</Eyebrow>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5">
          {scalars.map(([k, v]) => (
            <React.Fragment key={k}>
              <dt className="truncate font-mono text-[10px] text-zinc-500">{k}</dt>
              <dd className="text-right font-mono text-[11px] text-zinc-200">{String(v)}</dd>
            </React.Fragment>
          ))}
        </dl>
        {files.length > 0 && (
          <div className="mt-3 border-t border-[color:var(--fd-hair-2)] pt-2">
            <Eyebrow className="mb-1">files touched · {files.length}</Eyebrow>
            <div className="space-y-0.5">
              {files.slice(-8).map((f, i) => (
                <div key={i} className="truncate font-mono text-[10px] text-zinc-400" title={f}>{f.split("/").slice(-2).join("/")}</div>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

/* ---- Relay view --------------------------------------------------------- */
export default function RelayView() {
  const [mode, setMode] = useState("demo");
  const [speed, setSpeed] = useState(2);
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState("");
  const [run, setRun] = useState(initialRun);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef(null);
  const scrollRef = useRef(null);
  const stateVersion = useRef(0);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    get("/api/agui/sessions?limit=12")
      .then((d) => {
        const list = d.sessions || [];
        setSessions(list);
        if (list.length && !sessionId) setSessionId(list[0].id);
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // auto-scroll the timeline as events arrive
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [run.timeline]);

  // flash the state panel briefly whenever the shared state changes
  useEffect(() => {
    stateVersion.current += 1;
    if (stateVersion.current > 1) {
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 320);
      return () => clearTimeout(t);
    }
  }, [run.state]);

  const onEvent = useCallback((e) => setRun((r) => applyEvent(r, e)), []);

  const start = useCallback(async () => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setRun(initialRun);
    setBusy(true);
    const body = {
      forwardedProps: { mode, speed, ...(mode === "replay" ? { sessionId } : {}) },
    };
    try {
      await streamRun(body, { onEvent, signal: ac.signal });
    } catch (e) {
      if (!ac.signal.aborted) setRun((r) => ({ ...r, status: "error", error: String(e.message || e) }));
    } finally {
      setBusy(false);
    }
  }, [mode, speed, sessionId, onEvent]);

  const decide = useCallback(async (decision, command) => {
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await resumeRun(
        { threadId: run.threadId, runId: run.runId, decision, command },
        { onEvent, signal: ac.signal });
    } catch (e) {
      if (!ac.signal.aborted) setRun((r) => ({ ...r, status: "error", error: String(e.message || e) }));
    } finally {
      setBusy(false);
    }
  }, [run.threadId, run.runId, onEvent]);

  const st = STATUS[run.status] || STATUS.idle;
  const empty = run.timeline.length === 0;

  return (
    <div className="flex flex-col gap-4">
      {/* Controls */}
      <section className="fd-shell">
        <div className="fd-core flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-3.5">
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${st.dot}`} />
            <span className={`font-mono text-[11px] uppercase tracking-wide ${st.text}`}>{st.label}</span>
            {run.runId && <span className="font-mono text-[10px] text-zinc-600">{run.runId}</span>}
          </div>
          <div className="h-6 w-px bg-[color:var(--fd-hair-2)]" />
          <div className="flex items-center gap-2">
            <Eyebrow>mode</Eyebrow>
            <Seg options={[{ key: "demo", label: "Demo" }, { key: "replay", label: "Replay" }]} value={mode} onChange={setMode} />
          </div>
          {mode === "replay" && (
            <select value={sessionId} onChange={(e) => setSessionId(e.target.value)}
              className="max-w-[260px] rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-500/5 px-2.5 py-1 font-mono text-[11px] text-zinc-300 outline-none">
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>{(s.title || s.id).slice(0, 44)}</option>
              ))}
            </select>
          )}
          <div className="flex items-center gap-2">
            <Eyebrow>speed</Eyebrow>
            <Seg options={SPEEDS} value={speed} onChange={setSpeed} />
          </div>
          <div className="ml-auto flex items-center gap-2">
            {busy ? (
              <button type="button" onClick={() => abortRef.current?.abort()}
                className="rounded-lg border border-[color:var(--fd-hair-2)] px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-zinc-400 hover:bg-zinc-500/10">
                Stop
              </button>
            ) : (
              <button type="button" onClick={start}
                className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-1.5 font-mono text-[11px] uppercase tracking-wide text-emerald-300 transition-colors hover:bg-emerald-500/20">
                {mode === "demo" ? "Run demo" : "Replay session"}
              </button>
            )}
          </div>
        </div>
      </section>

      {/* Approval card when interrupted */}
      {run.status === "interrupted" && run.interrupt && (
        <ApprovalCard interrupt={run.interrupt} busy={busy} onDecide={decide} />
      )}

      {/* Timeline + state */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section className="fd-shell">
          <div className="fd-core">
            <div className="flex min-h-[42px] items-center justify-between border-b border-[color:var(--fd-hair-2)] px-5">
              <div className="text-xs font-bold tracking-tight text-zinc-100">Event stream</div>
              <div className="font-mono text-[10px] text-zinc-500">{run.timeline.length} items</div>
            </div>
            <div ref={scrollRef} className="max-h-[62vh] space-y-2.5 overflow-y-auto px-5 py-4">
              {empty ? (
                <div className="grid place-items-center py-16 text-center">
                  <div className="max-w-sm">
                    <div className="font-mono text-xs text-zinc-500">No run yet.</div>
                    <div className="mt-1.5 text-[11px] leading-[1.6] text-zinc-600">
                      {mode === "demo"
                        ? "Run the demo to watch a live AG-UI flow pause on a destructive tool for your approval."
                        : "Replay a real Claude Code session as an AG-UI event stream."}
                    </div>
                  </div>
                </div>
              ) : (
                run.timeline.map((it) => <TimelineItem key={it.id} it={it} />)
              )}
              {run.error && (
                <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 font-mono text-[11px] text-rose-300">
                  {run.error}
                </div>
              )}
            </div>
          </div>
        </section>

        <div className="flex flex-col gap-4">
          <StatePanel state={run.state} flash={flash} />
          {run.result && (
            <div className="px-1 font-mono text-[10px] text-zinc-600">
              result: {run.result.reason}{run.result.turns != null ? ` · ${run.result.turns} turns` : ""}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
