import React, { useEffect, useState, useMemo } from "react";
import {
  Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell,
  ComposedChart, Line, ReferenceLine,
} from "recharts";
import Ring from "../ui/Ring.jsx";
import { usd, compact, pct, shortModel } from "../format.js";
import { get, post } from "../api.js";

/* ---- KPI loading skeleton ---------------------------------------------- */
function KpiSkeleton() {
  return (
    <div className="fd-core grid grid-cols-2 divide-x divide-y divide-zinc-800/80 lg:grid-cols-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="px-6 py-5">
          <div className="h-2.5 w-20 rounded bg-zinc-800" />
          <div className="mt-3 h-6 w-24 rounded bg-zinc-800/70" />
        </div>
      ))}
    </div>
  );
}

/* ---- Insights (from the `/usage` command) ------------------------------ */
// Static snapshot of the `/usage` breakdown, trimmed to the highest-signal
// lines and visualized: usage-profile percentages as absolute meters, top
// skills / MCP servers as ranked bars, plugins/subagents as chips. UI-only
// (wire to a live source later). Skill/agent name prefixes are stripped.
const USAGE_INSIGHTS = [
  {
    label: "Last 24h", short: "24h", requests: 525, sessions: 3,
    profile: [
      { label: "subagent-heavy sessions", pct: 100 },
      { label: ">150k context", pct: 87 },
    ],
    skills: [
      { name: "using-git-worktrees", pct: 18 },
      { name: "loop", pct: 2 },
      { name: "brainstorming", pct: 1 },
    ],
    mcp: [
      { name: "chrome-devtools", pct: 12 },
      { name: "odoo_graph", pct: 1 },
    ],
    chips: [
      { k: "plugin", name: "superpowers", pct: 26 },
      { k: "subagent", name: "brainstorming", pct: 4 },
    ],
  },
  {
    label: "Last 7d", short: "7d", requests: 4282, sessions: 34,
    profile: [
      { label: ">150k context", pct: 91 },
      { label: "sessions active 8+ hours", pct: 69 },
      { label: "subagent-heavy sessions", pct: 54 },
    ],
    skills: [
      { name: "subagent-driven-development", pct: 2 },
      { name: "using-git-worktrees", pct: 2 },
      { name: "update-config", pct: 1 },
    ],
    mcp: [{ name: "chrome-devtools", pct: 21 }],
    chips: [
      { k: "plugin", name: "superpowers", pct: 7 },
      { k: "subagent", name: "general-purpose", pct: 1 },
    ],
  },
];

/* ---- Spend view primitives --------------------------------------------- */
// Small presentational blocks for the redesigned Spend page. All colors flow
// through FlightDeck tokens / the theme-bound Tailwind ramps (zinc = neutral,
// emerald = coral SIGNAL), so every one renders correctly in Night and Day.

// Mono uppercase section label (the "eyebrow" above a metric).
function Eyebrow({ children, className = "" }) {
  return (
    <div className={`font-mono text-[10px] uppercase leading-tight tracking-[0.17em] text-zinc-500 ${className}`}>
      {children}
    </div>
  );
}

// Tiny hairline info dot next to an eyebrow (title-only affordance).
function InfoDot({ title }) {
  return (
    <span title={title}
      className="grid h-[17px] w-[17px] shrink-0 cursor-help place-items-center rounded-full border border-[color:var(--fdx-rule)] font-serif text-[10px] text-zinc-500">
      i
    </span>
  );
}

// Mono micro label + value used in the value-card footer.
function MicroStat({ label, value }) {
  return (
    <div className="min-w-[68px]">
      <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-zinc-500">{label}</div>
      <div className="mt-1 font-mono text-xs text-zinc-100">{value}</div>
    </div>
  );
}

// Coral sparkline of recent daily costs (decorative; scaled to its own max).
function Sparkline({ values }) {
  const max = Math.max(...values, 1);
  return (
    <div className="flex h-[29px] flex-1 items-end gap-[3px] opacity-80" aria-hidden="true">
      {values.map((v, i) => (
        <i key={i} className="min-w-[3px] flex-1 rounded-t-[2px]"
           style={{ height: `${Math.max(4, (v / max) * 100)}%`, background: "rgb(var(--e-500))" }} />
      ))}
    </div>
  );
}

// Label / value row inside the Efficiency list.
function EffRow({ label, value }) {
  return (
    <div className="flex items-baseline justify-between gap-2.5">
      <span className="text-[10px] text-zinc-500">{label}</span>
      <strong className="font-mono text-[11px] font-medium text-zinc-100">{value}</strong>
    </div>
  );
}

// One ranked Operational-signals row: colored dot + title + copy + mono value.
function SignalRow({ mark, title, copy, value }) {
  return (
    <div className="grid grid-cols-[9px_1fr_auto] items-start gap-2.5 border-b border-[color:var(--fdx-rule)] py-2.5 last:border-b-0">
      <span className="mt-1 h-[7px] w-[7px] shrink-0 rounded-full" style={{ background: mark }} />
      <div className="min-w-0">
        <div className="text-[11px] font-semibold text-zinc-100">{title}</div>
        <div className="mt-1 text-[11px] leading-[1.45] text-zinc-400">{copy}</div>
      </div>
      <div className="font-mono text-[11px] text-zinc-200">{value}</div>
    </div>
  );
}

// Mono chip (skill / MCP usage) in the signals footer.
function SignalChip({ children }) {
  return (
    <span className="inline-flex min-h-[22px] items-center rounded-full border border-[color:var(--fdx-rule)] bg-zinc-500/5 px-2 font-mono text-[9px] text-zinc-400">
      {children}
    </span>
  );
}

// Panel head shared by Cost trend / Operational signals / Model breakdown:
// title + mono meta line on the left, an arbitrary `right` node on the right.
function PanelHead({ title, meta, right }) {
  return (
    <div className="flex min-h-[42px] items-center justify-between gap-4 border-b border-[color:var(--fdx-rule)] px-5">
      <div>
        <div className="text-xs font-bold tracking-tight text-zinc-100">{title}</div>
        {meta && <div className="mt-1 font-mono text-[10px] text-zinc-500">{meta}</div>}
      </div>
      {right}
    </div>
  );
}

// "MMM D" x-axis / tooltip date label from a YYYY-MM-DD key.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtDay(d) {
  if (!d || typeof d !== "string") return "";
  const parts = d.split("-");
  if (parts.length < 3) return d;
  const mo = MONTHS[Number(parts[1]) - 1] || "";
  return `${mo} ${Number(parts[2])}`;
}

// Resolve CSS custom properties to concrete color strings. Recharts renders
// colors as SVG presentation attributes, where var() does not resolve reliably;
// reading the computed values (and re-reading when the theme flips via
// documentElement[data-theme]) keeps the chart correct in Night AND Day.
function useTokens(names) {
  const key = names.join(",");
  const [vals, setVals] = useState({});
  useEffect(() => {
    const read = () => {
      const cs = getComputedStyle(document.documentElement);
      const out = {};
      for (const n of names) out[n] = cs.getPropertyValue(n).trim();
      setVals(out);
    };
    read();
    const mo = new MutationObserver(read);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return vals;
}

/* ---- Workload trends (tokens, agent work-hours) ------------------------ */
// Cost falls whenever list prices fall, so the cost trend cannot show how much
// work was done. These two series carry the workload: tokens processed and agent
// work-hours per day, drawn the same way as the cost trend.

// Each point gains a trailing 7-day mean of `key` (avg7).
function withAvg7(series, key) {
  return series.map((d, i) => {
    const win = series.slice(Math.max(0, i - 6), i + 1);
    return { ...d, avg7: win.reduce((s, x) => s + (x[key] || 0), 0) / (win.length || 1) };
  });
}

function meanOf(series, key) {
  return series.length ? series.reduce((s, x) => s + (x[key] || 0), 0) / series.length : 0;
}

function Swatch({ color, label, dashed }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {dashed
        ? <i className="w-[9px] border-t border-dashed" style={{ borderColor: color }} />
        : <i className="h-[3px] w-[9px] rounded-sm" style={{ background: color }} />}
      {label}
    </span>
  );
}

// Tooltip body: the date, one row per series, then the 7-day mean.
function WorkloadTip({ active, payload, label, rows, fmt }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-[14px] border border-[color:var(--fdx-rule)] bg-[color:var(--fdx-surface-raised)] px-3 py-2 font-mono text-[11px] text-[color:var(--fdx-text)]">
      <div className="mb-1 text-[color:var(--fdx-text-muted)]">{fmtDay(label)}</div>
      {rows.map((r) => (
        <div key={r.key} className="flex justify-between gap-4">
          <span>{r.label}</span><span>{(r.fmt || fmt)(d[r.key] || 0)}</span>
        </div>
      ))}
      <div className="mt-1 flex justify-between gap-4 text-[color:var(--fdx-text-muted)]">
        <span>7-day avg</span><span>{fmt(d.avg7 || 0)}</span>
      </div>
    </div>
  );
}

// One trend panel. `stacks` are the bar segments (bottom first); `rows` are the
// tooltip lines (each may carry its own `fmt`); `refValue` is the period mean
// drawn as the reference line; `controls` renders under the head.
function WorkloadTrend({ title, meta, data, stacks, rows, refValue, fmt, axisFmt, colors, controls }) {
  const lastIdx = data.length - 1;
  return (
    <section className="fd-shell">
      <div className="fd-core flex flex-col">
        <PanelHead
          title={title}
          meta={meta}
          right={
            <div className="flex flex-wrap items-center justify-end gap-3 font-mono text-[9px] text-zinc-500">
              {stacks.map((st) => <Swatch key={st.key} color={st.color} label={st.label} />)}
              <Swatch color={colors.sky} label="7-day avg" />
              <Swatch color="#d97706" label="reference" dashed />
            </div>
          }
        />
        {controls}
        <div className="h-[260px] px-3 py-4">
          {data.length === 0 ? (
            <div className="grid h-full place-items-center font-mono text-xs text-zinc-500">
              NO DATA in this range
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={data} margin={{ top: 16, right: 18, left: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} stroke={colors.hair} />
                <XAxis dataKey="date" tickLine={false} axisLine={false} minTickGap={40}
                       tickFormatter={fmtDay}
                       tick={{ fill: colors.faint, fontSize: 10, fontFamily: "IBM Plex Mono" }} />
                <YAxis tickLine={false} axisLine={false} width={48} tickFormatter={axisFmt}
                       tick={{ fill: colors.faint, fontSize: 10, fontFamily: "IBM Plex Mono" }} />
                <Tooltip cursor={{ fill: "rgba(217,58,24,0.08)" }}
                         content={<WorkloadTip rows={rows} fmt={fmt} />} />
                <ReferenceLine y={refValue} stroke="#d97706" strokeDasharray="4 3"
                  label={{
                    value: `reference ${axisFmt(refValue)}`, position: "insideTopRight",
                    fill: "#d97706", fontSize: 10, fontFamily: "IBM Plex Mono",
                  }} />
                {stacks.map((st, si) => (
                  <Bar key={st.key} dataKey={st.key} stackId="w" maxBarSize={28}
                       radius={si === stacks.length - 1 ? [4, 4, 1, 1] : [0, 0, 0, 0]}>
                    {data.map((_, i) => (
                      <Cell key={i} fill={i === lastIdx && st.last ? st.last : st.color} />
                    ))}
                  </Bar>
                ))}
                <Line type="monotone" dataKey="avg7" stroke={colors.sky} strokeWidth={2.2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </section>
  );
}

const hoursFmt = (v) => `${(Math.round(v * 10) / 10).toFixed(1)} h`;

/* ---- Thinking (flightdeck.thinking) ------------------------------------ */
// Thinking time and tokens per API response, averaged per day over the responses
// that thought. Time runs from the request to the end of the thinking block, so it
// includes the wait for the first token; compare within one model and effort.
const secsFmt = (v) => `${(Math.round(v * 10) / 10).toFixed(1)} s`;
const THINK_MEASURES = [["avg_s", "Time"], ["avg_tokens", "Tokens"]];
const THINK_AGENTS = [["all", "All"], ["main", "Main"], ["sub", "Subagents"]];

function ChipGroup({ label, options, value, onChange }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 font-mono text-[9px] uppercase text-zinc-500">{label}</span>
      {options.map(([k, lbl]) => (
        <button key={k} type="button" onClick={() => onChange(k)}
                className={`rounded-full border px-2 font-mono text-[9px] leading-[18px] transition-colors ${
                  value === k
                    ? "border-[color:var(--fdx-signal-active)] text-[color:var(--fdx-signal-active)]"
                    : "border-[color:var(--fdx-rule)] text-zinc-400 hover:text-zinc-100"}`}>
          {lbl}
        </button>
      ))}
    </div>
  );
}

function ThinkingPanel({ range, colors }) {
  const [data, setData] = useState(null);
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [agent, setAgent] = useState("all");
  const [measure, setMeasure] = useState("avg_s");
  useEffect(() => {
    let alive = true;
    const q = new URLSearchParams({ range, model, effort, agent });
    get(`/api/thinking?${q}`).then((d) => alive && setData(d)).catch(() => alive && setData(null));
    return () => { alive = false; };
  }, [range, model, effort, agent]);
  if (!data) return null;
  // A day with no timed response has no value; it must not pull the 7-day mean to 0.
  const vals = data.series.map((d) => d[measure]);
  const series = data.series.map((d, i) => {
    const win = vals.slice(Math.max(0, i - 6), i + 1).filter((v) => v != null);
    return { ...d, avg7: win.length ? win.reduce((a, b) => a + b, 0) / win.length : null };
  });
  const t = data.totals;
  const tokFmt = (v) => compact(Math.round(v));
  const isTime = measure === "avg_s";
  return (
    <WorkloadTrend
      title="Thinking"
      meta={`per response that thought · ${t.avg_s == null ? "—" : secsFmt(t.avg_s)} · ${
        t.avg_tokens == null ? "—" : `${tokFmt(t.avg_tokens)} tokens`} · ${pct(t.thinking_share)} of ${
        compact(t.responses)} responses`}
      data={series}
      stacks={[{ key: measure, label: isTime ? "avg time" : "avg tokens", color: colors.coralLite, last: colors.coral }]}
      rows={[
        { key: "avg_s", label: "avg time", fmt: secsFmt },
        { key: "p90_s", label: "p90 time", fmt: secsFmt },
        { key: "avg_tokens", label: "avg tokens", fmt: tokFmt },
        { key: "thinking_share", label: "thought", fmt: pct },
        { key: "responses", label: "responses", fmt: compact },
      ]}
      refValue={isTime ? t.avg_s || 0 : t.avg_tokens || 0}
      fmt={isTime ? secsFmt : tokFmt}
      axisFmt={isTime ? (v) => `${Math.round(v)}s` : (v) => compact(v)}
      colors={colors}
      controls={
        <div className="flex flex-wrap gap-x-5 gap-y-2 border-b border-[color:var(--fdx-rule)] px-5 py-2.5">
          <ChipGroup label="Measure" options={THINK_MEASURES} value={measure} onChange={setMeasure} />
          <ChipGroup label="Model" value={model} onChange={setModel}
                     options={[["", "All"], ...data.filters.models.map((m) => [m, shortModel(m)])]} />
          <ChipGroup label="Effort" value={effort} onChange={setEffort}
                     options={[["", "All"], ...data.filters.efforts.map((e) => [e, e])]} />
          <ChipGroup label="Agent" options={THINK_AGENTS} value={agent} onChange={setAgent} />
        </div>
      }
    />
  );
}

/* ---- Cost per task ------------------------------------------------------ */
// A task is a goal and its follow-ups; labels come from rules, Jev, GPT-6 Luna
// or a person (flightdeck/tasks.py). The review queue holds the labels the
// classifier was unsure of; one click stores a person's label, which wins.

const KIND_STYLE = {
  task: { label: "task", color: "var(--fdx-signal-active)" },
  subtask: { label: "task", color: "var(--fdx-signal-active)" }, // a root subtask has no parent
  merge: { label: "merge", color: "var(--fdx-environment)" },
  automated: { label: "automated", color: "var(--fdx-text-muted)" },
  empty: { label: "no work", color: "var(--fdx-text-muted)" },
};
const SOURCE_LABEL = { rule: "rule", jev: "Jev", llm: "Luna", human: "you" };
const REVIEW_KINDS = [["task", "task"], ["subtask", "subtask"], ["merge", "merge"], ["empty", "no work"]];

function KindChip({ kind }) {
  const st = KIND_STYLE[kind] || { label: "unlabeled", color: "var(--fdx-text-muted)" };
  return (
    <span className="inline-flex items-center rounded-full border px-1.5 font-mono text-[9px] leading-[16px]"
          style={{ borderColor: st.color, color: st.color }}>
      {st.label}
    </span>
  );
}

function TasksPanel({ range }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(null);
  const load = React.useCallback(async () => {
    try {
      setData(await get(`/api/tasks?range=${range}&limit=12`));
      setErr(null);
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, [range]);
  useEffect(() => { load(); }, [load]);

  const label = async (turnId, kind) => {
    setBusy(turnId);
    try {
      await post(`/api/tasks/${encodeURIComponent(turnId)}/label`, { kind });
      await load();
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(null);
    }
  };

  const s = data?.summary;
  return (
    <section className="fd-shell">
      <div className="fd-core">
        <PanelHead
          title="Cost per task"
          meta="a task is a goal and its follow-ups · cost and agent-hours include subtasks"
          right={s && (
            <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1 font-mono text-[10px] text-zinc-500">
              <span>{s.tasks} tasks</span>
              <span>median <b className="font-medium text-zinc-100">{usd(s.median_cost)}</b></span>
              <span>p90 <b className="font-medium text-zinc-100">{usd(s.p90_cost)}</b></span>
              {s.unlabeled > 0 && <span>{s.unlabeled} unlabeled</span>}
            </div>
          )}
        />
        {err && <div className="px-5 py-3 font-mono text-[11px] text-amber-400">{err}</div>}
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.65fr)_minmax(300px,0.6fr)]">
          <div className="overflow-x-auto border-b border-[color:var(--fdx-rule)] lg:border-b-0 lg:border-r">
            <table className="w-full min-w-[560px] table-fixed">
              <thead>
                <tr className="font-mono text-[9px] uppercase text-zinc-500">
                  <th className="border-b border-[color:var(--fdx-rule)] px-4 py-3 pl-5 text-left font-medium">Task</th>
                  <th className="w-[92px] border-b border-[color:var(--fdx-rule)] px-4 py-3 text-left font-medium">Kind</th>
                  <th className="w-[64px] border-b border-[color:var(--fdx-rule)] px-4 py-3 text-right font-medium">Turns</th>
                  <th className="w-[76px] border-b border-[color:var(--fdx-rule)] px-4 py-3 text-right font-medium">Agent-h</th>
                  <th className="w-[92px] border-b border-[color:var(--fdx-rule)] px-4 py-3 text-right font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {data && data.tasks.length === 0 && (
                  <tr><td colSpan={5} className="px-5 py-10 text-center text-sm text-zinc-500">
                    No tasks in this range.
                  </td></tr>
                )}
                {(data?.tasks || []).map((t) => (
                  <tr key={t.task_id}
                      className="border-b border-[color:var(--fdx-rule)] transition-colors last:border-b-0 hover:bg-zinc-500/5">
                    <td className="px-4 py-2.5 pl-5">
                      <div className="truncate text-[11px] font-semibold text-zinc-100" title={t.title}>{t.title || "(no prompt text)"}</div>
                      <div className="mt-0.5 truncate font-mono text-[9px] text-zinc-500" title={t.cwd || ""}>
                        {fmtDay((t.started_at || "").slice(0, 10))} · {(t.cwd || "").split("/").slice(-1)[0]}
                        {t.label_source && ` · ${SOURCE_LABEL[t.label_source] || t.label_source}`}
                      </div>
                    </td>
                    <td className="px-4 py-2.5"><KindChip kind={t.kind} /></td>
                    <td className="px-4 py-2.5 text-right font-mono text-[11px] text-zinc-300">{t.turns}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-[11px] text-zinc-300">{t.agent_hours.toFixed(1)}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-[11px]" style={{ color: "var(--fdx-signal-active)" }}>
                      {usd(t.cost)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <aside className="p-5">
            <Eyebrow>Review queue</Eyebrow>
            <div className="mt-1.5 text-[10px] text-zinc-400">
              {s ? `${s.needs_review} labels the classifier was unsure of. Your label replaces it and is kept to check the thresholds.` : "Loading…"}
            </div>
            <div className="mt-3 grid max-h-[420px] gap-2 overflow-y-auto pr-1">
              {(data?.review || []).map((r) => (
                <div key={r.turn_id} className="rounded-[10px] border border-[color:var(--fdx-rule)] p-2.5">
                  <div className="line-clamp-2 text-[11px] text-zinc-100" title={r.prompt}>{r.prompt || "(no prompt text)"}</div>
                  <div className="mt-1 font-mono text-[9px] text-zinc-500">
                    {fmtDay((r.started_at || "").slice(0, 10))} · {SOURCE_LABEL[r.label_source] || "-"} said {r.kind || "-"}
                    {r.label_confidence != null && ` · ${Math.round(r.label_confidence * 100)}%`}
                    {r.jev_kind && r.jev_kind !== r.kind && ` · Jev said ${r.jev_kind}`}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {REVIEW_KINDS.map(([k, lbl]) => (
                      <button key={k} type="button" disabled={busy === r.turn_id}
                              onClick={() => label(r.turn_id, k)}
                              className={`rounded-full border px-2 font-mono text-[9px] leading-[18px] transition-colors disabled:opacity-40 ${
                                r.kind === k
                                  ? "border-[color:var(--fdx-signal-active)] text-[color:var(--fdx-signal-active)]"
                                  : "border-[color:var(--fdx-rule)] text-zinc-400 hover:text-zinc-100"}`}>
                        {lbl}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              {data && data.review.length === 0 && (
                <div className="font-mono text-[10px] text-zinc-500">Nothing to review.</div>
              )}
            </div>
          </aside>
        </div>
      </div>
    </section>
  );
}

/* ---- Spend view -------------------------------------------------------- */
// The full Spend dashboard body. Computes its own derived state from the
// summary / daily / by-model props and renders the KPI skeleton while summary
// is still loading. Extracted verbatim from App.jsx (no behavior change).
/* ---- Burn rate (flightdeck.burn) --------------------------------------- */
// Quota meter points used per hour, and how many tokens of each model one point
// buys. Weights are quota use per list-price dollar relative to Opus 5.5.
const BURN_METERS = [["session", "5h"], ["week", "Week"]];
const burnRate = (v) => (v == null ? "—" : `${v.toFixed(1)}%/h`);
const hoursLabel = (s) => (s === 3600 ? "hour" : `${Math.round(s / 3600)}h`);
const mtok = (v) => (v == null ? "—" : v < 10 ? v.toFixed(1) : String(Math.round(v)));

function BurnRate({ range }) {
  const [data, setData] = useState(null);
  const [meter, setMeter] = useState("session");
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const d = await get(`/api/burn/stats?range=${range}&window=3600`);
        if (alive) setData(d);
      } catch {
        if (alive) setData(null);
      }
    };
    load();
    const id = setInterval(load, 60_000);   // the /usage poll runs every minute when active
    return () => { alive = false; clearInterval(id); };
  }, [range]);
  if (!data) return null;
  const m = data.meters[meter];
  // Heaviest burn first; the backend order (the model picker's) breaks ties.
  const rows = [...data.models].sort(
    (a, b) => (b.now_per_hour[meter] || 0) - (a.now_per_hour[meter] || 0));
  return (
    <div className="mt-5 border-t border-[color:var(--fdx-rule)] pt-4">
      <div className="flex items-center justify-between gap-2">
        <Eyebrow>Burn rate · last {hoursLabel(m.window_s)}</Eyebrow>
        <div className="flex gap-1">
          {BURN_METERS.map(([k, lbl]) => (
            <button key={k} type="button" onClick={() => setMeter(k)}
                    className={`rounded-full border px-2 font-mono text-[9px] leading-[18px] transition-colors ${
                      meter === k
                        ? "border-[color:var(--fdx-signal-active)] text-[color:var(--fdx-signal-active)]"
                        : "border-[color:var(--fdx-rule)] text-zinc-400 hover:text-zinc-100"}`}>
              {lbl}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-2.5 flex items-baseline gap-2">
        <span className="font-mono text-[24px] tracking-[-0.05em] text-zinc-100">{burnRate(m.now.per_hour)}</span>
        <span className="font-mono text-[10px] text-zinc-500">
          meter at {m.now.current == null ? "—" : `${m.now.current}%`}
          {m.now.resets > 0 && ` · ${m.now.resets} reset`}
        </span>
      </div>
      <div className="mt-1.5 text-[10px] leading-[1.5] text-zinc-400">
        ledger predicts {burnRate(m.now.ledger_per_hour)} · 1% ≈ {usd(m.base.usd_per_point)} at Opus 5.5
        rates ({m.base.source === "measured" ? `measured, ${m.base.spans} spans` : "calibrated"})
      </div>
      <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto_auto_auto] gap-x-3 gap-y-2 text-[10px]">
        <span className="font-mono text-[9px] uppercase text-zinc-500">Model</span>
        <span className="text-right font-mono text-[9px] uppercase text-zinc-500"
              title="Quota use of one list-price dollar, relative to Opus 5.5">Weight</span>
        <span className="text-right font-mono text-[9px] uppercase text-zinc-500"
              title="Million tokens of this model, at its token mix in this range, per meter point">M tok/1%</span>
        <span className="text-right font-mono text-[9px] uppercase text-zinc-500"
              title="Meter points per hour this model is using now, predicted from the ledger">Now</span>
        {rows.map((x) => (
          <React.Fragment key={x.model}>
            <span className="truncate text-zinc-400" title={x.model}>{shortModel(x.model)}</span>
            <span className="text-right font-mono text-zinc-300">{x.weight.toFixed(2)}</span>
            <span className="text-right font-mono text-zinc-100">{mtok(x.mtok_per_point[meter])}</span>
            <span className="text-right font-mono"
                  style={{ color: x.now_usd[meter] ? "var(--fdx-signal-active)" : undefined }}>
              {x.now_usd[meter] ? burnRate(x.now_per_hour[meter]) : "—"}
            </span>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

export default function SpendView({ summary, daily, byModel, range = "all" }) {
  // Cost-trend series: each daily point carries a 7-day trailing mean (avg7)
  // for the overlay line; the reference line sits at the period average.
  const trend = useMemo(
    () =>
      daily.map((d, i) => {
        const win = daily.slice(Math.max(0, i - 6), i + 1);
        const avg7 = win.reduce((s, x) => s + (x.cost || 0), 0) / (win.length || 1);
        return { ...d, avg7 };
      }),
    [daily]);
  const periodAvg = useMemo(
    () => (daily.length ? daily.reduce((s, x) => s + (x.cost || 0), 0) / daily.length : 0),
    [daily]);
  const spark = useMemo(() => daily.slice(-11).map((d) => d.cost || 0), [daily]);
  const tokenTrend = useMemo(
    () => withAvg7(daily.map((d) => ({ ...d, uncached: d.input || 0 })), "tokens"),
    [daily]);
  const hoursTrend = useMemo(
    () => withAvg7(daily.map((d) => ({
      ...d,
      hours_main: Math.max(0, (d.agent_hours || 0) - (d.agent_hours_sub || 0) - (d.agent_hours_est || 0)),
      hours_sub: d.agent_hours_sub || 0,
      hours_est: d.agent_hours_est || 0,
    })), "agent_hours"),
    [daily]);

  // Concrete chart colors, theme-resolved (see useTokens).
  const tk = useTokens(useMemo(
    () => ["--fdx-signal", "--e-300", "--fdx-environment", "--fdx-text-muted", "--fdx-rule",
      "--e-600"], []));
  const chartColors = {
    coral: tk["--fdx-signal"] || "#e84d2a",
    coralLite: tk["--e-300"] ? `rgb(${tk["--e-300"]})` : "#ff6344",
    sky: tk["--fdx-environment"] || "#4e93cc",
    faint: tk["--fdx-text-muted"] || "#aaa195",
    hair: tk["--fdx-rule"] || "rgba(244,237,224,0.22)",
    coralDeep: tk["--e-600"] ? `rgb(${tk["--e-600"]})` : "#b02c0c",
  };

  // Cost concentration: top-two models' share of total API-equivalent value.
  const conc = useMemo(() => {
    const total = byModel.reduce((s, m) => s + (m.cost || 0), 0);
    const share = (m) => (total ? (m?.cost || 0) / total : 0);
    const top2Share = share(byModel[0]) + share(byModel[1]);
    const otherShare = byModel.slice(2).reduce((s, m) => s + share(m), 0);
    const palette = [
      "rgb(var(--e-500))", "rgb(var(--e-300))", "var(--fdx-environment)",
      "var(--fdx-environment-deep)", "var(--fdx-text-muted)",
    ];
    return {
      total,
      top2Share,
      otherShare,
      segments: byModel.slice(0, 5).map((m, i) => ({
        pct: share(m), color: palette[i] || "var(--fdx-text-muted)",
      })),
      list: [
        { name: shortModel(byModel[0]?.model), pct: share(byModel[0]), color: palette[0] },
        { name: shortModel(byModel[1]?.model), pct: share(byModel[1]), color: palette[1] },
        { name: "Other models", pct: otherShare, color: "var(--fdx-text-muted)" },
      ],
    };
  }, [byModel]);

  if (!summary) return <KpiSkeleton />;

  return (
    <>
    {/* Overview: 3 columns in one double-bezel card */}
    <section className="fd-shell">
      <div className="fd-core grid grid-cols-1 min-[900px]:grid-cols-[1.25fr_1fr_0.92fr]">
        {/* API-equivalent value */}
        <div className="p-4">
          <div className="flex items-center justify-between gap-3">
            <Eyebrow>API-equivalent value</Eyebrow>
            <InfoDot title="Comparable list-price value, not an invoice total" />
          </div>
          <div className="mt-3 font-mono text-[33px] leading-none tracking-[-0.02em]"
               style={{ color: "var(--fdx-signal-active)" }}>
            {usd(summary.total_cost)}
          </div>
          <div className="mt-2 text-[11px] leading-[1.45] text-zinc-400">
            Comparable list-price value across all captured usage. This is not the amount charged.
          </div>
          <div className="mt-3.5 flex items-end justify-between gap-5">
            <MicroStat label="Per session"
              value={usd(summary.session_count ? summary.total_cost / summary.session_count : 0)} />
            <MicroStat label="Per 1K turns"
              value={usd(summary.message_count ? summary.total_cost / (summary.message_count / 1000) : 0)} />
            <Sparkline values={spark.length ? spark : [0]} />
          </div>
        </div>

        {/* Avoided value */}
        <div className="border-t border-[color:var(--fdx-rule)] p-4 min-[900px]:border-l min-[900px]:border-t-0">
          <div className="flex items-center justify-between gap-3">
            <Eyebrow>Avoided value</Eyebrow>
            <InfoDot title="Savings estimates use current API list prices" />
          </div>
          <div className="mt-4 grid gap-4">
            <div className="grid grid-cols-[1fr_auto] items-end gap-3">
              <div>
                <div className="text-[11px] text-zinc-400">Cache savings</div>
                <div className="mt-0.5 text-[10px] text-zinc-500">cache read + write value</div>
              </div>
              <div className="font-mono text-[19px] tracking-[-0.04em] text-zinc-100">
                {usd(summary.cache_savings)}
              </div>
            </div>
            <div className="h-px bg-[color:var(--fdx-rule)]" />
            <div className="grid grid-cols-[1fr_auto] items-end gap-3">
              <div>
                <div className="text-[11px] text-zinc-400">Versus subscription</div>
                <div className="mt-0.5 text-[10px] text-zinc-500">API equivalent minus flat plan</div>
              </div>
              <div className="font-mono text-[19px] tracking-[-0.04em] text-zinc-100">
                {usd(summary.saved_vs_subscription)}
              </div>
            </div>
          </div>
        </div>

        {/* Efficiency */}
        <div className="border-t border-[color:var(--fdx-rule)] p-4 min-[900px]:border-l min-[900px]:border-t-0">
          <div className="flex items-center justify-between gap-3">
            <Eyebrow>Efficiency</Eyebrow>
            <InfoDot title="Input-token cache performance" />
          </div>
          <div className="mt-4 grid grid-cols-[82px_1fr] items-center gap-4">
            <div className="relative grid place-items-center" style={{ width: 78, height: 78 }}>
              <Ring pct={(summary.cache_hit_rate ?? 0) * 100} size={78} stroke={6} color="var(--fdx-signal)" />
              <span className="absolute inset-0 grid place-items-center font-mono text-[15px] tracking-[-0.04em] text-zinc-100">
                {pct(summary.cache_hit_rate)}
              </span>
            </div>
            <div className="grid gap-2.5">
              <EffRow label="Cache hit" value={pct(summary.cache_hit_rate)} />
              <EffRow label="Avg context / turn" value={compact(summary.avg_context_per_turn)} />
              <EffRow label="Uncached input" value={compact(summary.input_tokens)} />
              <EffRow label="Output tokens" value={compact(summary.output_tokens)} />
            </div>
          </div>
        </div>
      </div>
    </section>

    {/* Cost trend + Operational signals */}
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.78fr)_minmax(320px,0.82fr)]">
      {/* Cost trend */}
      <section className="fd-shell">
        <div className="fd-core flex flex-col">
          <PanelHead
            title="Cost trend"
            meta="daily API-equivalent value"
            right={
              <div className="flex items-center gap-3 font-mono text-[9px] text-zinc-500">
                <span className="inline-flex items-center gap-1.5">
                  <i className="h-[3px] w-[9px] rounded-sm" style={{ background: "rgb(var(--e-500))" }} />daily
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <i className="h-[3px] w-[9px] rounded-sm" style={{ background: "var(--fdx-environment)" }} />7-day avg
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <i className="w-[9px] border-t border-dashed" style={{ borderColor: "#d97706" }} />reference
                </span>
              </div>
            }
          />
          <div className="flex-1 min-h-0 px-3 py-4">
            {trend.length === 0 ? (
              <div className="grid h-full place-items-center font-mono text-xs text-zinc-500">
                NO DATA in this range
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={trend} margin={{ top: 16, right: 18, left: 4, bottom: 4 }}>
                  <CartesianGrid vertical={false} stroke={chartColors.hair} />
                  <XAxis dataKey="date" tickLine={false} axisLine={false} minTickGap={40}
                         tickFormatter={fmtDay}
                         tick={{ fill: chartColors.faint, fontSize: 10, fontFamily: "IBM Plex Mono" }} />
                  <YAxis tickLine={false} axisLine={false} width={44}
                         domain={[0, (max) => Math.ceil((max * 1.06) / 100) * 100]}
                         tickFormatter={(v) => `$${compact(v)}`}
                         tick={{ fill: chartColors.faint, fontSize: 10, fontFamily: "IBM Plex Mono" }} />
                  <Tooltip
                    cursor={{ fill: "rgba(217,58,24,0.08)" }}
                    formatter={(v, name) => [usd(v), name === "avg7" ? "7-day avg" : "daily"]}
                    labelFormatter={fmtDay}
                    labelStyle={{ color: "var(--fdx-text-muted)", fontSize: 12 }}
                    contentStyle={{
                      background: "var(--fdx-surface-raised)", border: "1px solid var(--fdx-rule)",
                      borderRadius: 14, fontFamily: "IBM Plex Mono", fontSize: 12, color: "var(--fdx-text)",
                    }}
                  />
                  <ReferenceLine y={periodAvg} stroke="#d97706" strokeDasharray="4 3"
                    label={{
                      value: `reference $${compact(periodAvg)}`, position: "insideTopRight",
                      fill: "#d97706", fontSize: 10, fontFamily: "IBM Plex Mono",
                    }} />
                  <Bar dataKey="cost" radius={[4, 4, 1, 1]} maxBarSize={28}>
                    {trend.map((_, i) => (
                      <Cell key={i} fill={i === trend.length - 1 ? chartColors.coral : chartColors.coralLite} />
                    ))}
                  </Bar>
                  <Line type="monotone" dataKey="avg7" stroke={chartColors.sky} strokeWidth={2.2} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </section>

      {/* Operational signals */}
      <section className="fd-shell">
        <div className="fd-core">
          <PanelHead
            title="Operational signals"
            meta="ranked by potential impact"
            right={<SignalChip>24H</SignalChip>}
          />
          <div className="px-4 pb-3.5 pt-1">
            {(() => {
              const ins = USAGE_INSIGHTS[0];
              const findPct = (arr, k) => (arr.find((p) => p.label.toLowerCase().includes(k))?.pct ?? 0);
              const longCtx = findPct(ins.profile, "150k");
              const subagent = findPct(ins.profile, "subagent");
              const chips = [...ins.skills, ...ins.mcp].sort((a, b) => b.pct - a.pct).slice(0, 5);
              return (
                <>
                  <SignalRow mark="#f59e0b" title="Long-context exposure"
                    value={`${longCtx}%`}
                    copy={`${longCtx}% of sessions exceed 150K context. Review retention before reducing it.`} />
                  <SignalRow mark="var(--fdx-signal)" title="Subagent-heavy sessions"
                    value={`${subagent}%`}
                    copy="All recent sessions spawned subagents. Inspect fan-out on high-cost runs." />
                  <SignalRow mark="var(--fdx-environment)" title="Cache performance"
                    value={pct(summary.cache_hit_rate)}
                    copy={`Cache hit remains strong. Uncached input is only ${compact(summary.input_tokens)} tokens.`} />
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {chips.map((c) => (
                      <SignalChip key={c.name}>{c.name} {c.pct}%</SignalChip>
                    ))}
                  </div>
                </>
              );
            })()}
          </div>
        </div>
      </section>
    </div>

    {/* Workload: tokens and agent work-hours */}
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <WorkloadTrend
        title="Token trend"
        meta="daily tokens processed (input, cache, output)"
        data={tokenTrend}
        stacks={[{ key: "tokens", label: "daily", color: chartColors.coralLite, last: chartColors.coral }]}
        rows={[
          { key: "tokens", label: "total" },
          { key: "cache_read", label: "cache read" },
          { key: "cache_write", label: "cache write" },
          { key: "uncached", label: "uncached input" },
          { key: "output", label: "output" },
        ]}
        refValue={meanOf(tokenTrend, "tokens")}
        fmt={compact}
        axisFmt={compact}
        colors={chartColors}
      />
      <WorkloadTrend
        title="Work-hours trend"
        meta="daily agent busy time · every model 1:1"
        data={hoursTrend}
        stacks={[
          // Main and subagent time share one hue (both are agent work); the
          // 7-day line keeps the cost trend's blue.
          { key: "hours_main", label: "main agents", color: chartColors.coralLite },
          { key: "hours_sub", label: "subagents", color: chartColors.coralDeep },
          { key: "hours_est", label: "estimated", color: chartColors.faint },
        ]}
        rows={[
          { key: "agent_hours", label: "total" },
          { key: "hours_main", label: "main agents" },
          { key: "hours_sub", label: "subagents" },
          { key: "hours_est", label: "estimated (transcript gone)" },
        ]}
        refValue={meanOf(hoursTrend, "agent_hours")}
        fmt={hoursFmt}
        axisFmt={(v) => `${Math.round(v)}h`}
        colors={chartColors}
      />
    </div>

    <ThinkingPanel range={range} colors={chartColors} />

    <TasksPanel range={range} />

    {/* Model breakdown: table + cost concentration aside */}
    <section className="fd-shell">
      <div className="fd-core">
        <PanelHead
          title="Model breakdown"
          meta="cost attribution and token flow"
          right={<span className="font-mono text-[10px] text-zinc-500">{byModel.length} models · sorted by cost ↓</span>}
        />
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.65fr)_minmax(300px,0.6fr)]">
          <div className="overflow-x-auto border-b border-[color:var(--fdx-rule)] lg:border-b-0 lg:border-r">
            <table className="w-full">
              <thead>
                <tr className="font-mono text-[9px] uppercase text-zinc-500">
                  <th className="border-b border-[color:var(--fdx-rule)] px-4 py-3 pl-5 text-left font-medium">Model</th>
                  <th className="border-b border-[color:var(--fdx-rule)] px-4 py-3 text-right font-medium">Input raw</th>
                  <th className="border-b border-[color:var(--fdx-rule)] px-4 py-3 text-right font-medium">Input uncached</th>
                  <th className="border-b border-[color:var(--fdx-rule)] px-4 py-3 text-right font-medium">Output</th>
                  <th className="border-b border-[color:var(--fdx-rule)] px-4 py-3 text-right font-medium">Cache read</th>
                  <th className="border-b border-[color:var(--fdx-rule)] px-4 py-3 text-right font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {byModel.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-5 py-10 text-center text-sm text-zinc-500">
                      No model usage in this range.
                    </td>
                  </tr>
                )}
                {byModel.map((m) => (
                  <tr key={m.model}
                      className="border-b border-[color:var(--fdx-rule)] transition-colors last:border-b-0 hover:bg-zinc-500/5">
                    <td className="px-4 py-2.5 pl-5 text-[11px] font-semibold text-zinc-100" title={m.model || ""}>
                      {shortModel(m.model)}
                      {!m.priced && (
                        <span className="ml-2 rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-400">
                          unpriced
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-[11px] text-zinc-300">{compact(m.input_raw)}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-[11px] text-zinc-300">{compact(m.input)}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-[11px] text-zinc-300">{compact(m.output)}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-[11px] text-zinc-300">{compact(m.cache_read)}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-[11px]"
                        style={{ color: "var(--fdx-signal-active)" }}>
                      {m.priced ? usd(m.cost) : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <aside className="p-5">
            <Eyebrow>Cost concentration</Eyebrow>
            <div className="mt-2.5 font-mono text-[24px] tracking-[-0.05em] text-zinc-100">
              {pct(conc.top2Share)}
            </div>
            <div className="mt-1.5 text-[10px] text-zinc-400">
              of API-equivalent value comes from the top two models
            </div>
            <div className="my-4 flex h-[13px] overflow-hidden rounded-lg" style={{ background: "var(--fdx-surface-raised)" }}>
              {conc.segments.map((s, i) => (
                <i key={i} style={{ width: `${s.pct * 100}%`, background: s.color }} />
              ))}
            </div>
            <div className="grid gap-2.5">
              {conc.list.map((s, i) => (
                <div key={i} className="grid grid-cols-[8px_1fr_auto] items-center gap-2 text-[10px] text-zinc-400">
                  <span className="h-[7px] w-[7px] rounded-sm" style={{ background: s.color }} />
                  <span className="truncate">{s.name}</span>
                  <strong className="font-mono text-[10px] font-medium text-zinc-100">{pct(s.pct)}</strong>
                </div>
              ))}
            </div>
            <BurnRate range={range} />
          </aside>
        </div>
      </div>
    </section>

    {/* Spend footnote */}
    <div className="text-[9px] leading-[1.5] text-zinc-500">
      API-equivalent values use list pricing for comparability. Cache read and write multipliers
      follow each model&apos;s applicable rate. Figures shown here are observational estimates,
      not billing records.
    </div>
    </>
  );
}
