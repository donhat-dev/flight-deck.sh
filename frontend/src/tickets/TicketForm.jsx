import React, { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { get, post, patch } from "../api.js";
import { BATON, ageClass } from "./common.js";
// Milkdown is ~450kB of editor. Lazy here for the same reason TreasureDetail
// is lazy in App.jsx: only this one tab needs it.
const PlanPane = lazy(() => import("./PlanPane.jsx"));

// Only Plan is built. The rest are listed so the shape of the record is honest
// about what is missing, and disabled so nothing pretends to work.
const TABS = [["plan", "Plan", true], ["diff", "Diff", false], ["estimate", "Estimate", false],
              ["timeline", "Timeline", false], ["conversation", "Conversation", false]];

function Stat({ label, value, tone = "text-zinc-100" }) {
  return (
    <div className="border-l border-zinc-800 px-4 py-2 first:border-l-0">
      <div className={`font-mono text-[17px] font-semibold ${tone}`}>{value}</div>
      <div className="mt-0.5 font-mono text-[9px] font-semibold uppercase tracking-[0.12em] text-zinc-600">{label}</div>
    </div>
  );
}

function Row({ label, children, stamp }) {
  return (
    <div className="flex items-center gap-4 border-b border-zinc-800 py-2.5">
      <span className="w-[150px] shrink-0 font-mono text-[10px] font-semibold uppercase tracking-[0.1em] text-zinc-600">{label}</span>
      <span className="min-w-0 flex-1 text-[13px] text-zinc-200">{children}</span>
      {stamp && <span className="shrink-0 font-mono text-[9px] font-semibold uppercase tracking-wider text-zinc-600">{stamp}</span>}
    </div>
  );
}

// The stamp names where the link actually points; hardcoding "confluence" made
// a PlanOS URL claim to be a Confluence page.
function hostOf(url) {
  try { return new URL(url).hostname.split(".")[0]; } catch { return ""; }
}

function Slot({ value, href }) {
  if (!value) {
    return <span className="italic text-zinc-600">not linked</span>;
  }
  return href
    ? <a href={href} target="_blank" rel="noreferrer" className="text-zinc-200 underline decoration-zinc-700 underline-offset-2 hover:decoration-zinc-400">{value}</a>
    : <span className="font-mono text-xs text-zinc-200">{value}</span>;
}

export default function TicketForm({ ticketKey, onBack }) {
  const [t, setT] = useState(null);
  const [err, setErr] = useState("");
  const [gate, setGate] = useState("");
  const [tab, setTab] = useState("plan");
  const [newTag, setNewTag] = useState("");

  const load = useCallback(() => {
    get(`/api/tickets/${encodeURIComponent(ticketKey)}`).then(setT).catch((e) => setErr(e.message));
  }, [ticketKey]);
  useEffect(() => { load(); }, [load]);

  const movePhase = (phase) => {
    setGate("");
    post(`/api/tickets/${encodeURIComponent(ticketKey)}/phase`, { phase })
      .then(setT)
      .catch((e) => setGate(e.status === 409 ? `Cannot move to that phase — ${e.detail}` : e.message));
  };

  const save = (body) => patch(`/api/tickets/${encodeURIComponent(ticketKey)}`, body).then(setT);

  if (err) return <div className="p-6 text-sm text-rose-400">Error: {err}</div>;
  if (!t) return <div className="p-6 text-sm text-zinc-500">Loading…</div>;

  const phases = t.phases || [];
  const current = phases.findIndex((p) => p.key === t.phase);
  const done = (t.plan || []).filter((m) => m.status === "done").length;
  const openChildren = (t.plan || []).reduce(
    (n, m) => n + (m.children || []).filter((c) => c.status !== "done").length, 0);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button type="button" onClick={onBack}
          className="rounded-full border border-zinc-800 px-3 py-1 text-xs font-medium text-zinc-400 hover:text-zinc-200">
          ← Tickets
        </button>
        <span className="font-mono text-[13px] font-semibold text-zinc-200">{t.key}</span>
      </div>

      <div className="rounded-[5px] border border-zinc-800 bg-zinc-900/40 p-6">
        {/* action bar + the phase state machine */}
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="font-mono text-[9px] font-semibold uppercase tracking-[0.14em] text-zinc-600">Lane</span>
          <span className="text-xs font-semibold text-zinc-300">
            {t.lane === "SPEC_REVIEW" ? "Spec review" : "Implementation"}
          </span>
          <span className="flex-1" />
          <div className="flex items-center gap-1 rounded-full border border-zinc-800 bg-zinc-900 p-1">
            {phases.map((p, i) => {
              const isCurrent = p.key === t.phase;
              return (
                <button key={p.key} type="button" onClick={() => movePhase(p.key)}
                  aria-pressed={isCurrent} title={`Move to ${p.label}`}
                  className={`rounded-full px-3 py-1 font-mono text-[10px] font-semibold uppercase tracking-wider transition-colors ${
                    isCurrent ? "bg-emerald-500 text-zinc-950"
                      : i < current ? "text-zinc-400 hover:text-zinc-200"
                      : "text-zinc-600 hover:text-zinc-300"}`}>
                  {i < current ? "✓ " : ""}{p.label}
                </button>
              );
            })}
          </div>
        </div>
        {gate && (
          <div className="mt-3 rounded-[5px] border border-amber-500/50 bg-amber-500/5 px-3 py-2 text-xs text-amber-300">
            {gate}
          </div>
        )}

        {/* What is holding the ticket is a reason of its own, not a stage. The
            stage says how far the work is; this says why it is not moving. */}
        <div className={`mt-3 flex flex-wrap items-center gap-3 rounded-[5px] border px-3 py-2 ${
          t.blocked_reason ? "border-amber-400/50 bg-amber-400/5" : "border-zinc-800"}`}>
          <span className={`font-mono text-[9px] font-semibold uppercase tracking-[0.14em] ${
            t.blocked_reason ? "text-amber-400" : "text-zinc-600"}`}>
            {t.blocked_reason ? `Blocked ${t.blocked_days ? `${t.blocked_days}d` : "today"}` : "Not blocked"}
          </span>
          <select value={t.blocked_reason} aria-label="Blocked reason"
            onChange={(e) => save({ blocked_reason: e.target.value })}
            className={`min-w-[280px] flex-1 rounded-full border bg-zinc-900 px-3 py-1.5 text-xs focus:outline-none ${
              t.blocked_reason ? "border-amber-400/40 text-amber-200" : "border-zinc-800 text-zinc-400"}`}>
            <option value="">nothing is blocking this</option>
            {(t.block_reasons || []).map((r) => <option key={r} value={r}>{r}</option>)}
            {t.blocked_reason && !(t.block_reasons || []).includes(t.blocked_reason) && (
              <option value={t.blocked_reason}>{t.blocked_reason}</option>
            )}
          </select>
          {t.blocked_reason && (
            <button type="button" onClick={() => save({ blocked_reason: "" })}
              className="rounded-full border border-zinc-700 px-3 py-1 text-xs font-medium text-zinc-300 hover:text-zinc-100">
              Unblock
            </button>
          )}
        </div>

        {/* identity + the stat buttons */}
        <div className="mt-6 flex flex-wrap items-center gap-6">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-400">{t.key}</span>
              <span className={`rounded-full border px-2 py-0.5 font-mono text-[9px] font-semibold tracking-wider ${BATON[t.baton] || BATON.AGENT}`}>
                BATON: {t.baton}
              </span>
              <span className="rounded-full border border-zinc-800 px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-zinc-500">
                jira · {t.jira_status}
              </span>
              <span className={`rounded-full border border-current px-2 py-0.5 font-mono text-[9px] font-semibold tracking-wider ${ageClass(t.days_in_phase)}`}>
                {t.days_in_phase} DAYS IN PHASE
              </span>
            </div>
            <h2 className="mt-2 text-[30px] font-extrabold tracking-tight text-zinc-100">{t.title}</h2>
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
              {(t.tags || []).map((x) => (
                <span key={x} className="flex items-center gap-1.5 rounded-[5px] border border-zinc-800 px-2 py-0.5 font-mono text-[10px] tracking-wider text-zinc-400">
                  {x}
                  <button type="button" aria-label={`Remove tag ${x}`}
                    onClick={() => save({ tags: t.tags.filter((y) => y !== x) })}
                    className="text-zinc-600 hover:text-rose-400">✕</button>
                </span>
              ))}
              <form onSubmit={(e) => {
                e.preventDefault();
                const v = newTag.trim().toLowerCase();
                if (!v || t.tags.includes(v)) return;
                save({ tags: [...t.tags, v] }).then(() => setNewTag(""));
              }}>
                <input value={newTag} onChange={(e) => setNewTag(e.target.value)}
                  placeholder="+ tag" aria-label="Add a tag"
                  className="w-24 rounded-[5px] border border-dashed border-zinc-800 bg-transparent px-2 py-0.5 font-mono text-[10px] text-zinc-300 placeholder:text-zinc-600 focus:border-zinc-600 focus:outline-none" />
              </form>
            </div>
          </div>
          <span className="flex-1" />
          <div className="flex items-center rounded-[5px] border border-zinc-800">
            <Stat label="Plan steps" value={`${done}/${t.rollup.milestones}`} tone="text-emerald-400" />
            <Stat label="Child steps" value={t.rollup.children} />
            <Stat label="Open children" value={openChildren} tone={openChildren ? "text-amber-400" : "text-zinc-100"} />
            <Stat label="Emergent hours" value={`+${t.rollup.emergent_h}`} tone="text-amber-400" />
          </div>
        </div>

        {/* the two groups */}
        <div className="mt-6 grid gap-12 lg:grid-cols-2">
          <div>
            <div className="flex items-center gap-3 border-b border-zinc-700 pb-2">
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-200">Links</span>
              <span className="flex-1" />
              <span className="font-mono text-[9px] font-semibold uppercase tracking-wider text-zinc-600">typed slots</span>
            </div>
            <Row label="Parent feature" stamp="jira"><Slot value={t.parent_key} /></Row>
            <Row label="FRD" stamp={hostOf(t.frd_url)}><Slot value={t.frd_url} href={t.frd_url} /></Row>
            <Row label="Spec" stamp={hostOf(t.spec_url)}><Slot value={t.spec_url} href={t.spec_url} /></Row>
            <Row label="Worktree" stamp="local"><Slot value={t.worktree} /></Row>
            <Row label="Branch" stamp="git"><Slot value={t.branch} /></Row>
            <Row label="Merge request" stamp="gate for mr open"><Slot value={t.mr_url} href={t.mr_url} /></Row>
          </div>
          <div>
            <div className="flex items-center gap-3 border-b border-zinc-700 pb-2">
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-200">Jira mirror</span>
              <span className="flex-1" />
              <span className="font-mono text-[9px] font-semibold uppercase tracking-wider text-zinc-600">read only</span>
            </div>
            <Row label="Status" stamp={t.synced_at}>{t.jira_status}</Row>
            <Row label="Assignee">{t.assignee || "—"}</Row>
            <Row label="Priority">{t.priority}</Row>
            <Row label="Sprint">{t.sprint || "—"}</Row>
            <Row label="Track">{t.track || "—"}</Row>
            <Row label="Estimate posted">{t.estimate || <span className="italic text-zinc-600">not posted</span>}</Row>
          </div>
        </div>

        {/* notebook */}
        <div className="mt-7 flex border-b border-zinc-800">
          {TABS.map(([k, label, ready]) => (
            <button key={k} type="button" disabled={!ready} onClick={() => setTab(k)}
              title={ready ? undefined : "Not built yet"}
              className={`px-4 py-2.5 text-[13px] transition-colors ${
                tab === k ? "border-b-[3px] border-emerald-500 font-semibold text-zinc-100"
                  : ready ? "text-zinc-400 hover:text-zinc-200" : "cursor-not-allowed text-zinc-700"}`}>
              {label}
              {k === "plan" && (
                <span className="ml-2 rounded-full bg-emerald-500 px-1.5 py-0.5 font-mono text-[9px] font-semibold text-zinc-950">
                  {t.rollup.milestones}+{t.rollup.children}
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="pt-4">
          {tab === "plan" && (
            <Suspense fallback={<div className="p-6 text-sm text-zinc-500">Loading editor…</div>}>
              <PlanPane ticket={t} onChanged={load} />
            </Suspense>
          )}
        </div>
      </div>
    </div>
  );
}
