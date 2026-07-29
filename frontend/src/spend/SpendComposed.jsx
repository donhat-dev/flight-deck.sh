/**
 * Spend, re-composed — stage 6 of docs/flightdeck-composition-and-radio.md.
 *
 * Same data as the live Spend view, composed against the contract instead of as
 * 12 near-equal panels. The region-to-rule mapping lives in spend-composed.css;
 * the one argument worth repeating here is the demotion: API-equivalent value and
 * avoided value are the biggest numbers on the page and they go to the FOOTER,
 * because one is explicitly "not the amount charged" and the other is a
 * comparison. The anchor is Burn — the only region that answers "is this normal,
 * and how much room is left".
 *
 * This renders as its own page (`/spend-concept.html`) rather than replacing the
 * live view, following the repo's proposal pattern. Adopting it into `view ===
 * "usage"` is one swap in App.jsx, deliberately left as a separate decision.
 */
import React, { useEffect, useMemo, useState } from "react";
import {
  Bar, CartesianGrid, Cell, ComposedChart, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import { get } from "../api.js";
import { compact, pct, shortModel, usd } from "../format.js";

const RANGES = [
  { k: "today", label: "Today" },
  { k: "7d", label: "7 days" },
  { k: "30d", label: "30 days" },
  { k: "all", label: "All" },
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtDay = (d) => {
  const parts = String(d || "").split("-");
  if (parts.length < 3) return String(d || "");
  return `${MONTHS[Number(parts[1]) - 1] || ""} ${Number(parts[2])}`;
};

/** Recharts writes colours as SVG presentation attributes, where var() does not
 *  resolve — so the concrete values are read from the computed style. */
function useTokens(names) {
  const key = names.join(",");
  const [vals, setVals] = useState({});
  useEffect(() => {
    const read = () => {
      const cs = getComputedStyle(document.documentElement);
      setVals(Object.fromEntries(names.map((n) => [n, cs.getPropertyValue(n).trim()])));
    };
    read();
    const mo = new MutationObserver(read);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return vals;
}

function useSpendData(range) {
  const [data, setData] = useState({ loading: true, error: null });
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [summary, daily, byModel, windows, quota] = await Promise.all([
          get(`/api/summary?range=${range}`),
          get(`/api/daily?range=${range}`),
          get(`/api/by-model?range=${range}`),
          get("/api/usage-windows"),
          get("/api/quota"),
        ]);
        if (alive) {
          setData({
            loading: false, error: null, summary, quota,
            daily: Array.isArray(daily) ? daily : [],
            byModel: Array.isArray(byModel) ? byModel : [],
            window: windows?.active || null,
          });
        }
      } catch (e) {
        if (alive) setData({ loading: false, error: String(e), daily: [], byModel: [] });
      }
    })();
    return () => {
      alive = false;
    };
  }, [range]);
  return data;
}

/* ---------------------------------------------------------------- anchor */

function Burn({ summary, daily, window: win, quota, colors }) {
  const trend = useMemo(
    () =>
      daily.map((d, i) => {
        const w = daily.slice(Math.max(0, i - 6), i + 1);
        return { ...d, avg7: w.reduce((s, x) => s + (x.cost || 0), 0) / (w.length || 1) };
      }),
    [daily],
  );
  const periodAvg = useMemo(
    () => (daily.length ? daily.reduce((s, x) => s + (x.cost || 0), 0) / daily.length : 0),
    [daily],
  );

  const used = quota?.five_hour?.used_percentage;
  const headroom = used == null ? null : Math.max(0, 100 - used);
  const rate = win?.burnRate?.costPerHour;

  return (
    /* The anchor: rate, room and trend as ONE region. */
    <section className="sc-burn" aria-labelledby="sc-burn-title">
      <div className="sc-burn-read">
        <p className="sc-eyebrow" id="sc-burn-title">
          Burn · is this normal, and how much room is left
        </p>

        <p className="sc-rate">
          <b data-num>{rate == null ? "—" : usd(rate)}</b>
          <span>per hour now</span>
        </p>

        <div className="sc-headroom">
          <p className="sc-eyebrow" data-quiet="true">
            Quota headroom · 5h window
          </p>
          <p className="sc-total-value" data-num>
            {headroom == null ? "—" : `${Math.round(headroom)}%`}
          </p>
          <div className="sc-headroom-bar">
            <i style={{ width: `${Math.min(100, Math.max(0, headroom ?? 0))}%` }} />
          </div>
        </div>

        <dl className="sc-burn-facts">
          <div className="sc-fact">
            <dt>Window spend</dt>
            <dd data-num>{win ? usd(win.costUSD) : "—"}</dd>
          </div>
          <div className="sc-fact">
            <dt>Window left</dt>
            <dd data-num>
              {win?.projection?.remainingMinutes == null ? "—" : `${win.projection.remainingMinutes}m`}
            </dd>
          </div>
          <div className="sc-fact">
            <dt>Avg / day</dt>
            <dd data-num>{usd(periodAvg)}</dd>
          </div>
          <div className="sc-fact">
            <dt>Weekly quota</dt>
            <dd data-num>
              {quota?.seven_day?.used_percentage == null
                ? "—"
                : `${Math.round(quota.seven_day.used_percentage)}%`}
            </dd>
          </div>
          <div className="sc-fact">
            <dt>Sessions</dt>
            <dd data-num>{summary?.session_count ?? "—"}</dd>
          </div>
          <div className="sc-fact">
            <dt>Days</dt>
            <dd data-num>{daily.length}</dd>
          </div>
        </dl>
      </div>

      <div className="sc-chart">
        {trend.length === 0 ? (
          <p className="sc-chart-empty">NO DATA in this range</p>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={trend} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke={colors.rule} />
              <XAxis
                dataKey="date" tickLine={false} axisLine={false} minTickGap={40}
                tickFormatter={fmtDay}
                tick={{ fill: colors.muted, fontSize: 10, fontFamily: "IBM Plex Mono" }}
              />
              <YAxis
                tickLine={false} axisLine={false} width={46}
                tickFormatter={(v) => `$${compact(v)}`}
                tick={{ fill: colors.muted, fontSize: 10, fontFamily: "IBM Plex Mono" }}
              />
              <Tooltip
                formatter={(v, name) => [usd(v), name === "avg7" ? "7-day avg" : "daily"]}
                labelFormatter={fmtDay}
                contentStyle={{
                  background: colors.surface, border: `1px solid ${colors.ruleStrong}`,
                  fontFamily: "IBM Plex Mono", fontSize: 11, color: colors.text,
                }}
              />
              <ReferenceLine
                y={periodAvg} stroke={colors.warning} strokeDasharray="4 3"
                label={{
                  value: `avg $${compact(periodAvg)}`, position: "insideTopRight",
                  fill: colors.warning, fontSize: 10, fontFamily: "IBM Plex Mono",
                }}
              />
              <Bar dataKey="cost" maxBarSize={26}>
                {trend.map((_, i) => (
                  <Cell key={i} fill={i === trend.length - 1 ? colors.signal : colors.barMuted} />
                ))}
              </Bar>
              <Line type="monotone" dataKey="avg7" stroke={colors.text} strokeWidth={1.6} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- support */

function Models({ byModel }) {
  return (
    <section aria-labelledby="sc-models-head">
      <div className="sc-head">
        <p className="sc-eyebrow" data-quiet="true" id="sc-models-head">
          Model breakdown
        </p>
        <span className="sc-meta" data-num>
          {byModel.length} models · by cost
        </span>
      </div>
      <table className="sc-table">
        <thead>
          <tr>
            <th>Model</th>
            <th>Input raw</th>
            <th>Uncached</th>
            <th>Output</th>
            <th>Cache read</th>
            <th>Cost</th>
          </tr>
        </thead>
        <tbody>
          {byModel.length === 0 && (
            <tr>
              <td colSpan={6}>No model usage in this range.</td>
            </tr>
          )}
          {byModel.map((m) => (
            <tr key={m.model}>
              <td title={m.model || ""}>
                {shortModel(m.model)}
                {!m.priced && <span className="sc-unpriced">unpriced</span>}
              </td>
              <td data-num>{compact(m.input_raw)}</td>
              <td data-num>{compact(m.input)}</td>
              <td data-num>{compact(m.output)}</td>
              <td data-num>{compact(m.cache_read)}</td>
              <td className="sc-cost" data-num>
                {m.priced ? usd(m.cost) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Signals({ summary, byModel }) {
  const [selected, setSelected] = useState(null);

  const rows = useMemo(() => {
    const unpriced = byModel.filter((m) => !m.priced);
    const list = [
      {
        id: "cache",
        mark: (summary?.cache_hit_rate ?? 0) > 0.9 ? "good" : "caution",
        title: "Cache performance",
        value: pct(summary?.cache_hit_rate),
        copy: `Uncached input is ${compact(summary?.input_tokens)} tokens against ${compact(
          summary?.input_tokens_raw,
        )} raw.`,
      },
      {
        id: "context",
        mark: "signal",
        title: "Context per turn",
        value: compact(summary?.avg_context_per_turn),
        copy: "Average context carried into each turn — the lever behind cache cost.",
      },
    ];
    // Only reported when it is true, so the slot always carries a distinct fact
    // rather than a word that repeats (C5).
    if (unpriced.length) {
      list.unshift({
        id: "unpriced",
        mark: "caution",
        title: "Unpriced models",
        value: `${unpriced.length}`,
        copy: `${unpriced
          .map((m) => shortModel(m.model))
          .join(", ")} have no price row, so their cost reads as zero.`,
      });
    }
    return list;
  }, [summary, byModel]);

  return (
    <section aria-labelledby="sc-signals-head">
      <div className="sc-head">
        <p className="sc-eyebrow" data-quiet="true" id="sc-signals-head">
          Signals
        </p>
        <span className="sc-meta" data-num>
          {rows.length} ranked
        </span>
      </div>
      {rows.map((r) => (
        <button
          key={r.id}
          type="button"
          className="sc-signal"
          data-mark={r.mark}
          data-selected={selected === r.id ? "true" : "false"}
          aria-pressed={selected === r.id}
          onClick={() => setSelected((s) => (s === r.id ? null : r.id))}
        >
          <i />
          <span>
            <span className="sc-signal-title">{r.title}</span>
            <span className="sc-signal-copy">{r.copy}</span>
          </span>
          <span className="sc-signal-value">{r.value}</span>
        </button>
      ))}
    </section>
  );
}

/** Three meters, not four numbers and a ring. Output tokens was dropped: it is
 *  volume, not efficiency, and the region has to be able to say what it is
 *  about in three readings. */
function Efficiency({ summary }) {
  const hit = summary?.cache_hit_rate ?? 0;
  const ctx = summary?.avg_context_per_turn ?? 0;
  const uncached = summary?.input_tokens ?? 0;
  const raw = summary?.input_tokens_raw || 1;
  return (
    <section className="sc-efficiency" aria-label="Efficiency">
      <dl className="sc-meter" data-tone={hit > 0.9 ? "good" : "default"}>
        <dt>Cache hit</dt>
        <dd data-num>{pct(hit)}</dd>
        <div className="sc-meter-bar">
          <i style={{ width: `${Math.min(100, hit * 100)}%` }} />
        </div>
      </dl>
      {/* No bar: avg context per turn has no natural ceiling — it exceeds the
          200K window because it counts cache reads — and a bar pinned at 100%
          against an invented denominator would read as "at the limit". */}
      <dl className="sc-meter">
        <dt>Avg context / turn</dt>
        <dd data-num>{compact(ctx)}</dd>
      </dl>
      <dl className="sc-meter">
        <dt>Uncached input</dt>
        <dd data-num>{compact(uncached)}</dd>
        <div className="sc-meter-bar">
          <i style={{ width: `${Math.min(100, (uncached / raw) * 100)}%` }} />
        </div>
      </dl>
    </section>
  );
}

/* ---------------------------------------------------------------- footer */

function Footer({ summary, byModel }) {
  const conc = useMemo(() => {
    const total = byModel.reduce((s, m) => s + (m.cost || 0), 0);
    const share = (m) => (total ? (m?.cost || 0) / total : 0);
    const other = byModel.slice(2).reduce((s, m) => s + share(m), 0);
    return {
      top2: share(byModel[0]) + share(byModel[1]),
      segments: byModel.slice(0, 5).map((m) => share(m)),
      list: [
        { name: shortModel(byModel[0]?.model), pct: share(byModel[0]) },
        { name: shortModel(byModel[1]?.model), pct: share(byModel[1]) },
        { name: "Other models", pct: other },
      ],
    };
  }, [byModel]);

  return (
    /* Reference figures, wide and short. These two totals were the old page's
       headline; they are the largest numbers here and deliberately not the
       anchor. */
    <section className="sc-footer" aria-label="Reference totals">
      <div className="sc-total">
        <p className="sc-eyebrow" data-quiet="true">
          API-equivalent value
        </p>
        <p className="sc-total-value" data-num>
          {usd(summary?.total_cost)}
        </p>
        <p className="sc-total-note">
          Comparable list-price value across captured usage. Not the amount charged.
        </p>
      </div>

      <div className="sc-total">
        <p className="sc-eyebrow" data-quiet="true">
          Avoided value
        </p>
        <p className="sc-total-value" data-num>
          {usd(summary?.cache_savings)}
        </p>
        <p className="sc-total-note">
          Cache read and write value. Versus a flat plan: {usd(summary?.saved_vs_subscription)}.
        </p>
      </div>

      <div className="sc-total">
        <p className="sc-eyebrow" data-quiet="true">
          Cost concentration
        </p>
        <p className="sc-total-value" data-num>
          {pct(conc.top2)}
        </p>
        <div className="sc-conc-bar">
          {conc.segments.map((s, i) => (
            <i
              key={i}
              style={{
                width: `${s * 100}%`,
                background: i === 0 ? "var(--fdx-signal)" : "var(--fdx-rule-strong)",
                opacity: 1 - i * 0.16,
              }}
            />
          ))}
        </div>
        <div className="sc-conc-list">
          {conc.list.map((s, i) => (
            <div className="sc-conc-row" key={i}>
              <i
                style={{
                  height: 7,
                  background: i === 0 ? "var(--fdx-signal)" : "var(--fdx-rule-strong)",
                }}
              />
              <span>{s.name}</span>
              <b data-num>{pct(s.pct)}</b>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- page */

export default function SpendComposed() {
  const [range, setRange] = useState("30d");
  const { summary, daily = [], byModel = [], window: win, quota, loading, error } =
    useSpendData(range);

  const tk = useTokens(
    useMemo(
      () => [
        "--fdx-signal", "--fdx-text", "--fdx-text-muted", "--fdx-rule",
        "--fdx-rule-strong", "--fdx-surface", "--fdx-warning",
      ],
      [],
    ),
  );
  const colors = {
    signal: tk["--fdx-signal"] || "#d94625",
    // History reads as ink on paper; only the current day is lit (one coral per
    // region, C6). rule-strong at 54% was too washed to read as data.
    barMuted: "color-mix(in srgb, " + (tk["--fdx-text"] || "#201d18") + " 62%, transparent)",
    text: tk["--fdx-text"] || "#201d18",
    muted: tk["--fdx-text-muted"] || "#6d655a",
    rule: tk["--fdx-rule"] || "rgba(32,29,24,.24)",
    ruleStrong: tk["--fdx-rule-strong"] || "rgba(32,29,24,.54)",
    surface: tk["--fdx-surface"] || "#f8f3e8",
    warning: tk["--fdx-warning"] || "#865d0c",
  };

  return (
    <div className="sc-shell">
      <header className="sc-masthead">
        <p className="sc-wordmark">
          Flight<b>Deck</b> Spend · re-composed
        </p>
        {/* A segmented control: only the selected segment carries depth. The
            first pass used four kit buttons and the lint flagged it — four
            raised segments is uniform depth, which is no depth at all. */}
        <div className="sc-range" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <button
              key={r.k}
              type="button"
              className="sc-range-seg"
              aria-pressed={range === r.k}
              onClick={() => setRange(r.k)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </header>

      <Burn summary={summary} daily={daily} window={win} quota={quota} colors={colors} />

      <div className="sc-dense">
        <Models byModel={byModel} />
        <Signals summary={summary} byModel={byModel} />
      </div>

      <Efficiency summary={summary} />
      <Footer summary={summary} byModel={byModel} />

      <p className="sc-footnote">
        API-equivalent values use list pricing for comparability. Figures are observational
        estimates, not billing records.
        {loading && " Loading…"}
        {error && ` API unreachable: ${error}`}
      </p>
    </div>
  );
}
