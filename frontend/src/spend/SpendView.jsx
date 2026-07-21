import React, { useEffect, useState, useMemo } from "react";
import {
  Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell,
  ComposedChart, Line, ReferenceLine,
} from "recharts";
import Ring from "../ui/Ring.jsx";
import { usd, compact, pct, shortModel } from "../lib/format.js";

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
      className="grid h-[17px] w-[17px] shrink-0 cursor-help place-items-center rounded-full border border-[color:var(--fd-hair)] font-serif text-[10px] text-zinc-500">
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
    <div className="grid grid-cols-[9px_1fr_auto] items-start gap-2.5 border-b border-[color:var(--fd-hair-2)] py-2.5 last:border-b-0">
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
    <span className="inline-flex min-h-[22px] items-center rounded-full border border-[color:var(--fd-hair-2)] bg-zinc-500/5 px-2 font-mono text-[9px] text-zinc-400">
      {children}
    </span>
  );
}

// Panel head shared by Cost trend / Operational signals / Model breakdown:
// title + mono meta line on the left, an arbitrary `right` node on the right.
function PanelHead({ title, meta, right }) {
  return (
    <div className="flex min-h-[42px] items-center justify-between gap-4 border-b border-[color:var(--fd-hair-2)] px-5">
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

/* ---- Spend view -------------------------------------------------------- */
// The full Spend dashboard body. Computes its own derived state from the
// summary / daily / by-model props and renders the KPI skeleton while summary
// is still loading. Extracted verbatim from App.jsx (no behavior change).
export default function SpendView({ summary, daily, byModel }) {
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

  // Concrete chart colors, theme-resolved (see useTokens).
  const tk = useTokens(useMemo(
    () => ["--fd-coral", "--e-300", "--fd-sky", "--fd-faint", "--fd-hair-2"], []));
  const chartColors = {
    coral: tk["--fd-coral"] || "#D93A18",
    coralLite: tk["--e-300"] ? `rgb(${tk["--e-300"]})` : "#FF6A4D",
    sky: tk["--fd-sky"] || "#4E93CC",
    faint: tk["--fd-faint"] || "rgba(244,243,239,0.38)",
    hair: tk["--fd-hair-2"] || "rgba(255,255,255,0.07)",
  };

  // Cost concentration: top-two models' share of total API-equivalent value.
  const conc = useMemo(() => {
    const total = byModel.reduce((s, m) => s + (m.cost || 0), 0);
    const share = (m) => (total ? (m?.cost || 0) / total : 0);
    const top2Share = share(byModel[0]) + share(byModel[1]);
    const otherShare = byModel.slice(2).reduce((s, m) => s + share(m), 0);
    const palette = [
      "rgb(var(--e-500))", "rgb(var(--e-300))", "var(--fd-sky)",
      "var(--fd-sky-deep)", "var(--fd-faint)",
    ];
    return {
      total,
      top2Share,
      otherShare,
      segments: byModel.slice(0, 5).map((m, i) => ({
        pct: share(m), color: palette[i] || "var(--fd-faint)",
      })),
      list: [
        { name: shortModel(byModel[0]?.model), pct: share(byModel[0]), color: palette[0] },
        { name: shortModel(byModel[1]?.model), pct: share(byModel[1]), color: palette[1] },
        { name: "Other models", pct: otherShare, color: "var(--fd-faint)" },
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
               style={{ color: "var(--fd-coral-deep)" }}>
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
        <div className="border-t border-[color:var(--fd-hair)] p-4 min-[900px]:border-l min-[900px]:border-t-0">
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
            <div className="h-px bg-[color:var(--fd-hair-2)]" />
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
        <div className="border-t border-[color:var(--fd-hair)] p-4 min-[900px]:border-l min-[900px]:border-t-0">
          <div className="flex items-center justify-between gap-3">
            <Eyebrow>Efficiency</Eyebrow>
            <InfoDot title="Input-token cache performance" />
          </div>
          <div className="mt-4 grid grid-cols-[82px_1fr] items-center gap-4">
            <div className="relative grid place-items-center" style={{ width: 78, height: 78 }}>
              <Ring pct={(summary.cache_hit_rate ?? 0) * 100} size={78} stroke={6} color="var(--fd-coral)" />
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
                  <i className="h-[3px] w-[9px] rounded-sm" style={{ background: "var(--fd-sky)" }} />7-day avg
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
                    labelStyle={{ color: "var(--fd-dim)", fontSize: 12 }}
                    contentStyle={{
                      background: "var(--fd-raise-2)", border: "1px solid var(--fd-hair)",
                      borderRadius: 14, fontFamily: "IBM Plex Mono", fontSize: 12, color: "var(--fd-text)",
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
                  <SignalRow mark="var(--fd-coral)" title="Subagent-heavy sessions"
                    value={`${subagent}%`}
                    copy="All recent sessions spawned subagents. Inspect fan-out on high-cost runs." />
                  <SignalRow mark="var(--fd-sky)" title="Cache performance"
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

    {/* Model breakdown: table + cost concentration aside */}
    <section className="fd-shell">
      <div className="fd-core">
        <PanelHead
          title="Model breakdown"
          meta="cost attribution and token flow"
          right={<span className="font-mono text-[10px] text-zinc-500">{byModel.length} models · sorted by cost ↓</span>}
        />
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.65fr)_minmax(300px,0.6fr)]">
          <div className="overflow-x-auto border-b border-[color:var(--fd-hair-2)] lg:border-b-0 lg:border-r">
            <table className="w-full">
              <thead>
                <tr className="font-mono text-[9px] uppercase text-zinc-500">
                  <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 pl-5 text-left font-medium">Model</th>
                  <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Input raw</th>
                  <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Input uncached</th>
                  <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Output</th>
                  <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Cache read</th>
                  <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Cost</th>
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
                      className="border-b border-[color:var(--fd-hair-2)] transition-colors last:border-b-0 hover:bg-zinc-500/5">
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
                        style={{ color: "var(--fd-coral-deep)" }}>
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
            <div className="my-4 flex h-[13px] overflow-hidden rounded-lg" style={{ background: "var(--fd-raise-2)" }}>
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
