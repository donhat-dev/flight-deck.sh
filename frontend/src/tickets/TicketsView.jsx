import React, { useCallback, useEffect, useMemo, useState } from "react";
import { get } from "../api.js";
import { BATON, ageClass, goTicket } from "./common.js";

const LANES = [["all", "All"], ["SPEC_REVIEW", "Spec review"], ["IMPLEMENTATION", "Implementation"]];
const FILTERS = [["all", "Everything"], ["MINE", "Baton: mine"],
                 ["BLOCKED", "Blocked"], ["FREE", "Nothing blocking"]];

function Counter({ label, value, tone }) {
  return (
    <div className="border-l border-zinc-800 pl-6">
      <div className="font-mono text-[9px] font-semibold uppercase tracking-[0.14em] text-zinc-600">{label}</div>
      <div className={`mt-1 font-mono text-xl font-semibold ${tone}`}>{value}</div>
    </div>
  );
}

function Card({ t, onOpen }) {
  return (
    <button type="button" onClick={() => onOpen(t.key)}
      className={`w-full rounded-[5px] border bg-zinc-900/60 p-3 text-left transition-colors hover:bg-zinc-900 ${
        t.blocked_reason ? "border-l-[3px] border-l-amber-400 border-y-zinc-800 border-r-zinc-800"
          : "border-zinc-800 hover:border-zinc-700"}`}>
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] font-semibold tracking-wide text-zinc-200">{t.key}</span>
        <span className="flex-1" />
        <span className={`font-mono text-[10px] font-semibold ${ageClass(t.days_in_phase)}`}>{t.days_in_phase}d</span>
      </div>
      <div className="mt-2 text-[13px] font-medium leading-snug text-zinc-100">{t.title}</div>
      <div className="mt-2.5 flex items-center gap-2">
        <span className={`rounded-full border px-2 py-0.5 font-mono text-[9px] font-semibold tracking-wider ${BATON[t.baton] || BATON.AGENT}`}>
          {t.baton}
        </span>
        <span className="flex-1" />
        <span className="font-mono text-[9px] font-semibold uppercase tracking-wider text-zinc-600">
          jira · {t.jira_status}
        </span>
      </div>
      {t.blocked_reason && (
        <div className="mt-2.5 rounded-[5px] border border-amber-400/40 bg-amber-400/5 px-2 py-1.5">
          <div className="font-mono text-[9px] font-semibold uppercase tracking-wider text-amber-400">
            blocked {t.blocked_days ? `${t.blocked_days}d` : "today"}
          </div>
          <div className="mt-0.5 text-[11px] leading-snug text-amber-200/80">{t.blocked_reason}</div>
        </div>
      )}
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-zinc-800 pt-2">
        {(t.tags || []).map((x) => (
          <span key={x} className="rounded-[5px] border border-zinc-800 px-1.5 py-0.5 font-mono text-[9px] tracking-wider text-zinc-500">
            {x}
          </span>
        ))}
        <span className="flex-1" />
        <span className="font-mono text-[9px] font-semibold uppercase tracking-wider text-zinc-600">
          {t.branch || t.track}
        </span>
      </div>
    </button>
  );
}

export default function TicketsView({ onOpen = goTicket }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [lane, setLane] = useState("all");
  const [baton, setBaton] = useState("all");
  const [tag, setTag] = useState("");
  const [q, setQ] = useState("");

  const load = useCallback(() => {
    get("/api/tickets").then(setData).catch((e) => setErr(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);

  const tickets = useMemo(() => {
    const all = data?.tickets || [];
    const needle = q.trim().toLowerCase();
    return all.filter((t) => {
      if (lane !== "all" && t.lane !== lane) return false;
      if (baton === "MINE" && t.baton !== "MINE") return false;
      if (baton === "BLOCKED" && !t.blocked_reason) return false;
      if (baton === "FREE" && t.blocked_reason) return false;
      if (tag && !(t.tags || []).includes(tag)) return false;
      if (!needle) return true;
      return `${t.key} ${t.title} ${t.branch} ${t.track} ${(t.tags || []).join(" ")}`.toLowerCase().includes(needle);
    });
  }, [data, lane, baton, tag, q]);

  // A lane shows every one of its phases, empty columns included, because the
  // gap is part of the state machine. "All" would be 12 columns, so there it
  // shows only the phases that currently hold a card.
  const columns = data?.phases || [];

  if (err) return <div className="p-6 text-sm text-rose-400">Error: {err}</div>;
  if (!data) return <div className="p-6 text-sm text-zinc-500">Loading…</div>;

  const totals = data.totals;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-6">
        <div className="flex items-center gap-3">
          {LANES.map(([k, label]) => (
            <button key={k} type="button" onClick={() => setLane(k)} aria-pressed={lane === k}
              className={`rounded-full border px-3.5 py-1.5 text-xs font-medium transition-colors ${
                lane === k ? "border-zinc-600 bg-white/5 text-zinc-100" : "border-zinc-800 text-zinc-500 hover:text-zinc-300"}`}>
              {label}
            </button>
          ))}
        </div>
        <span className="flex-1" />
        <Counter label="My baton" value={totals.mine} tone="text-emerald-400" />
        <Counter label="Blocked" value={totals.blocked} tone="text-amber-400" />
        <Counter label="Stalled > 5d" value={totals.stalled} tone="text-rose-400" />
        <Counter label="All tickets" value={totals.all} tone="text-zinc-200" />
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        <input value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Search ticket, branch, track…" aria-label="Search tickets"
          className="h-9 min-w-[260px] flex-1 rounded-full border border-zinc-800 bg-zinc-900/60 px-4 text-[13px] text-zinc-200 placeholder:text-zinc-600 focus:border-zinc-600 focus:outline-none" />
        {FILTERS.map(([k, label]) => (
          <button key={k} type="button" onClick={() => setBaton(k)} aria-pressed={baton === k}
            className={`h-9 rounded-full border px-3.5 text-xs font-medium transition-colors ${
              baton === k ? "border-zinc-600 bg-white/5 text-zinc-100" : "border-zinc-800 text-zinc-500 hover:text-zinc-300"}`}>
            {label}
          </button>
        ))}
        <select value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Filter by tag"
          className="h-9 rounded-full border border-zinc-800 bg-zinc-900 px-3 text-xs text-zinc-400 focus:outline-none">
          <option value="">All tags</option>
          {(data.tags || []).map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
        <button type="button" onClick={load}
          className="h-9 rounded-full border border-zinc-800 px-3.5 text-xs font-medium text-zinc-400 hover:text-zinc-200">
          Refresh
        </button>
        <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-zinc-600">
          {tickets.length} of {totals.all} shown
        </span>
      </div>

      <div className="flex gap-3 overflow-x-auto pb-2">
        {columns.map((p) => {
          const rows = tickets.filter((t) => t.phase === p.key);
          return (
            <div key={p.key} className="flex min-w-[240px] flex-1 flex-col gap-2.5">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-400">{p.label}</span>
                  <span className="flex-1" />
                  <span className="font-mono text-[11px] font-semibold text-zinc-600">{rows.length}</span>
                </div>
                <div className={`mt-2 h-0.5 ${rows.length ? "bg-emerald-500/70" : "bg-zinc-800"}`} />
              </div>
              {rows.map((t) => <Card key={t.key} t={t} onOpen={onOpen} />)}
              {!rows.length && (
                <div className="rounded-[5px] border border-dashed border-zinc-800 p-3 font-mono text-[10px] uppercase tracking-wider text-zinc-700">
                  empty
                </div>
              )}
            </div>
          );
        })}
        {!columns.length && (
          <div className="p-6 text-sm text-zinc-500">Nothing matches this filter.</div>
        )}
      </div>
    </div>
  );
}
