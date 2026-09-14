import React, { Suspense, lazy, useEffect, useState, useCallback, useMemo, useRef } from "react";
import { get, post, subscribe } from "./api.js";
import Ring from "./ui/Ring.jsx";
import { usd, compact, pct, day, hm, shortModel } from "./format.js";
import SpendView from "./spend/SpendView.jsx";
import GraphView from "./GraphView.jsx";
import Hub from "./hub/Hub.jsx";
// import Pulse from "./Pulse.jsx"; // SUSPENDED 2026-07-14: Pulse paused (was a load amplifier; re-enable with the NAV entry + view branch below)
import RepoDiff from "./RepoDiff.jsx";
import RouteLoom from "./RouteLoom.jsx";
import SessionDetail from "./SessionDetail.jsx";
import Landing from "./Landing.jsx";
import { Shell, Header } from "./ui/Shell.jsx";
import { Roundel, Wordmark } from "./brand.jsx";
import { IconBack, IconForward } from "./ui/icons.jsx";
import CommsView from "./systems/CommsView.jsx";
import ManualsView from "./systems/ManualsView.jsx";
import HangarView from "./systems/HangarView.jsx";
import MemoryView from "./memory/MemoryView.jsx";
import RelayView from "./agui/RelayView.jsx";
import MissionsView from "./systems/MissionsView.jsx";
import TicketsView from "./tickets/TicketsView.jsx";
import TicketForm from "./tickets/TicketForm.jsx";
import AppearanceView from "./systems/AppearanceView.jsx";
import TreasuresView from "./treasures/TreasuresView.jsx";
import TreasureConfig from "./treasures/TreasureConfig.jsx";
import NavGroup from "./ui/NavGroup.jsx";
import PixelMark from "./ui/PixelMark.jsx";
import PaletteToggle from "./ui/PaletteToggle.jsx";
import { StatusBadge } from "./ui/FlightComponents.jsx";
// Lazy: TreasureDetail pulls in Milkdown (WYSIWYG editor), a sizeable
// dependency only ever needed on the single #/treasure/<id> route — keeping
// it out of the main bundle instead of growing every other view's load.
const TreasureDetail = React.lazy(() => import("./treasures/TreasureDetail.jsx"));

const ComponentLab = lazy(() => import("./ui/ComponentLab.jsx"));

/* ---- hash routing ------------------------------------------------------ */
// Hash routing keeps deep links working under the static file mount without a
// server rewrite: #/session/<id> opens the read-only transcript page.
function parseRoute(hash) {
  const m = (hash || "").match(/^#\/session\/([^?]+)(?:\?(.*))?$/);
  if (m) {
    const params = new URLSearchParams(m[2] || "");
    return { name: "session", id: decodeURIComponent(m[1]), view: params.get("view") || null };
  }
  const t = (hash || "").match(/^#\/treasure\/([^?]+)$/);
  if (t) return { name: "treasure", id: decodeURIComponent(t[1]) };
  const k = (hash || "").match(/^#\/ticket\/([^?]+)$/);
  if (k) return { name: "ticket", key: decodeURIComponent(k[1]) };
  if ((hash || "").match(/^#\/loom\/?$/)) return { name: "loom" };
  if ((hash || "").match(/^#\/welcome\/?$/)) return { name: "welcome" };
  return { name: "home" };
}
function useHash() {
  const [hash, setHash] = useState(() => (typeof window !== "undefined" ? window.location.hash : ""));
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}
const goSession = (id, view) => {
  window.location.hash = `#/session/${encodeURIComponent(id)}${view ? `?view=${view}` : ""}`;
};
const goTreasure = (id) => {
  window.location.hash = `#/treasure/${encodeURIComponent(id)}`;
  window.scrollTo(0, 0);
};
const goLoom = () => { window.location.hash = "#/loom"; window.scrollTo(0, 0); };
const goTicket = (key) => {
  window.location.hash = `#/ticket/${encodeURIComponent(key)}`;
  window.scrollTo(0, 0);
};
// Session detail intentionally lands at the bottom (latest turn) on open —
// but the window scroll position otherwise persists across the hash change
// (single-page app, no browser scroll restoration), so without this, going
// back from a bottom-scrolled detail page left the list ALSO scrolled to the
// bottom. Reset explicitly whenever navigating home.
const goHome = () => { window.location.hash = "#/"; window.scrollTo(0, 0); };

/* ---- brand: roundel + stylized-i wordmark ------------------------------ */
// Roundel + Wordmark now live in ./brand.jsx (shared with the marketing
// Landing) and are imported at the top of this file.

/* ---- day/night theme toggle ------------------------------------------- */
// FlightDeck is Night by default (the app is a dark instrument panel), and the
// switch lives at the foot of the side menu. There used to be a second theme
// mechanism here — a two-segment control with its own localStorage read — and it
// is gone: `ui/PaletteToggle.jsx` already owned `<html data-theme>` for Radio
// and the radar, so the dashboard passes it `variant="rail"` and the same
// `fd-theme` key rather than writing the attribute from a second place.

/* ---- scroll-to-top/bottom (floating) ----------------------------------- */
// Long transcripts + the sessions list both scroll the window; these fixed
// buttons jump to either end. Each hides (fades out) once you're already there.
function ScrollButtons() {
  const [atTop, setAtTop] = useState(true);
  const [atBottom, setAtBottom] = useState(false);
  useEffect(() => {
    const onScroll = () => {
      const doc = document.documentElement;
      setAtTop(window.scrollY < 120);
      setAtBottom(window.innerHeight + window.scrollY >= doc.scrollHeight - 120);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);
  const btn =
    "flex h-9 w-9 items-center justify-center rounded-full border border-zinc-700 " +
    "bg-zinc-900/90 text-base text-zinc-300 shadow-lg backdrop-blur transition-all " +
    "hover:border-emerald-500/50 hover:text-emerald-400 " +
    "disabled:pointer-events-none disabled:translate-y-1 disabled:opacity-0";
  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 print:hidden">
      <button type="button" aria-label="Scroll to top" title="Scroll to top" disabled={atTop}
        onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} className={btn}>↑</button>
      <button type="button" aria-label="Scroll to bottom" title="Scroll to bottom" disabled={atBottom}
        onClick={() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" })} className={btn}>↓</button>
    </div>
  );
}

// Instrument vocabulary: Quota remains fixed in the sidebar; Route Loom is a
// spatial workspace, separate from the transcript-oriented Logbook.
// `mark` names an 8x8 pixel grid in ui/PixelMark.jsx. It replaced a single
// Unicode glyph per row, which had two problems the marks do not: the shapes
// available in a text font are not a set anybody designed together, and at 16px
// several of them were the same lozenge. The mark also survives the rail
// collapsing to 76px, where it is the only thing left of the row.
const NAV = [
  // { k: "pulse", label: "Pulse", mark: "…" }, // SUSPENDED 2026-07-14: Pulse paused
  { k: "usage", label: "Spend", mark: "spend" },
  { k: "sessions", label: "Logbook", mark: "logbook" },
  { k: "loom", label: "Route Loom", mark: "routeloom" },
  { k: "graph", label: "Charts", mark: "charts" },
  { k: "diff", label: "Diff", mark: "diff" },
  { k: "tickets", label: "Tickets", mark: "tickets" },
  { k: "hub", label: "Hub", mark: "hub" },
  // The first nav entry with children. Treasures stays a real view — the row
  // still opens the library — and Config is a page under it rather than three
  // separate entries for three text fields, which is how a settings panel is
  // shaped everywhere it works.
  { k: "treasures", label: "Treasures", mark: "treasures", children: [
    { k: "treasure-config", label: "Config", mark: "config" },
  ] },
];
// Systems section: environment management (read-only v1) as opposed to the
// usage-analytics views above. Comms = MCP servers, Manuals = skills,
// Hangar = Docker containers.
const SYS_NAV = [
  { k: "components", label: "Components", mark: "components" },
  { k: "comms", label: "Comms", mark: "comms" },
  { k: "manuals", label: "Manuals", mark: "manuals" },
  // Next to Comms and Manuals because it belongs with them: those three are what
  // an agent brings to a session — its servers, its skills, and what it remembers.
  { k: "memory", label: "Memory", mark: "memory" },
  { k: "hangar", label: "Hangar", mark: "hangar" },
  { k: "relay", label: "Relay", mark: "relay" },
  { k: "missions", label: "Missions", mark: "missions" },
  { k: "appearance", label: "Appearance", mark: "appearance" },
];
const RANGES = [
  { k: "today", label: "Today" },
  { k: "7d", label: "7 days" },
  { k: "30d", label: "30 days" },
  { k: "all", label: "All" },
];
const SESSIONS_PAGE_SIZE = 10;

/* ---- sorting ----------------------------------------------------------- */
function useSort(rows, initial) {
  const [sort, setSort] = useState(initial); // { key, dir } | null
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const { key, dir } = sort;
    const out = [...rows].sort((a, b) => {
      const av = a[key], bv = b[key];
      const c =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av ?? "").localeCompare(String(bv ?? ""));
      return dir === "asc" ? c : -c;
    });
    return out;
  }, [rows, sort]);
  const toggle = (key, defaultDir = "desc") =>
    setSort((p) =>
      p && p.key === key
        ? { key, dir: p.dir === "asc" ? "desc" : "asc" }
        : { key, dir: defaultDir });
  return { sorted, sort, toggle };
}

function SortTh({ label, k, sort, toggle, align = "right", defaultDir = "desc" }) {
  const active = sort?.key === k;
  return (
    <th className={`px-5 py-2.5 font-medium ${align === "right" ? "text-right" : "text-left"}`}>
      <button
        type="button"
        onClick={() => toggle(k, defaultDir)}
        className={`inline-flex items-center gap-1 transition-colors hover:text-zinc-200 ${
          align === "right" ? "flex-row-reverse" : ""
        } ${active ? "text-emerald-400" : ""}`}
      >
        {label}
        <span className="w-2 text-[10px] leading-none">
          {active ? (sort.dir === "asc" ? "↑" : "↓") : ""}
        </span>
      </button>
    </th>
  );
}

/* ---- primitives -------------------------------------------------------- */
// Ring (the shared percentage gauge) now lives in ./ui/Ring.jsx — used here by
// the sidebar QuotaBar and by the Spend Efficiency gauge.

function Panel({ title, right, children, className = "" }) {
  return (
    <section className={`fd-shell ${className}`}>
      <div className="fd-core">
        {title && (
          <header className="flex items-center justify-between border-b border-zinc-800/80 px-5 py-3.5">
            <h2 className="text-sm font-semibold tracking-tight text-zinc-200">{title}</h2>
            {right}
          </header>
        )}
        {children}
      </div>
    </section>
  );
}

const ago = (s) => {
  if (s == null) return "";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};
const resetTime = (epoch) =>
  epoch ? new Date(epoch * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "-";

// The Quota instrument, as a Ring: amber >70, rose >90, else coral (a state
// separate from the accent — approaching-limit is real, meaningful state).
// Sized for the sidebar column (~190px usable).
function QuotaBar({ label, win }) {
  const p = win?.used_percentage ?? null;
  const color = p == null ? "var(--fdx-text-muted)" : p > 90 ? "#fb7185" : p > 70 ? "#fbbf24" : "var(--fdx-signal-hover)";
  const reset = win?.resets_at ? resetTime(win.resets_at) : (win?.resets_text || "-");
  return (
    <div className="flex items-center gap-3">
      <Ring pct={p} size={40} stroke={4} color={color} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2 text-xs">
          <span className="text-zinc-400">{label}</span>
          <span className="font-mono text-zinc-200">{p == null ? "-" : `${Math.round(p)}%`}</span>
        </div>
        <div className="mt-0.5 text-[11px] text-zinc-600">resets {reset}</div>
      </div>
    </div>
  );
}

// Official subscription quota (%), merged from `claude -p /usage` polls and the
// statusLine capture (freshest source wins). Lives as a fixed widget in the
// sidebar (was an inline "Usage" panel) so it stays visible on every view.
function SidebarQuota({ data, onRefresh }) {
  const [busy, setBusy] = useState(false);
  const refresh = async () => {
    if (busy) return;
    setBusy(true);
    try { await onRefresh?.(); } finally { setBusy(false); }
  };
  const stale = data?.available && data.age_seconds != null && data.age_seconds > 1800;
  const src = data?.sources || {};
  const srcParts = [];
  if (src.report_age != null) srcParts.push(`/usage poll ${ago(src.report_age)}`);
  if (src.statusline_age != null) srcParts.push(`statusline ${ago(src.statusline_age)}`);
  const freshness = data?.available
    ? srcParts.join(" · ") || `as of ${ago(data.age_seconds)}`
    : "unavailable";

  return (
    <div className="mx-3 mb-3 shrink-0 rounded-xl border border-zinc-800/70 bg-zinc-900/40 px-3 py-3">
      <div className="flex items-center justify-between" title={`official · ${freshness}`}>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">Quota</span>
        <button
          type="button"
          onClick={refresh}
          disabled={busy}
          aria-label="Refresh usage"
          title="Fetch latest usage now"
          className="text-zinc-500 transition-colors hover:text-emerald-400 disabled:opacity-50"
        >
          <span className={busy ? "inline-block animate-spin" : "inline-block"}>↻</span>
        </button>
      </div>
      {!data ? (
        <p className="mt-2 text-[11px] text-zinc-600">loading…</p>
      ) : !data.available ? (
        <p className="mt-2 text-[11px] text-zinc-600">
          no official quota yet - run <span className="font-mono">usage_poll</span> on the host
        </p>
      ) : (
        <div className="mt-2.5 space-y-3">
          <QuotaBar label="Session (5h)" win={data.five_hour} />
          <QuotaBar label="Weekly" win={data.seven_day} />
          {stale && <p className="text-[11px] text-amber-400/80">may be outdated ({ago(data.age_seconds)})</p>}
        </div>
      )}
    </div>
  );
}

// Rolling 5-hour block + weekly, sourced from the ccusage CLI.
function UsageWindows({ data }) {
  if (!data) return null;
  if (!data.available) {
    return (
      <Panel title="Usage windows" right={<span className="text-xs text-zinc-600">via ccusage</span>}>
        <p className="px-5 py-6 text-sm text-zinc-500">
          ccusage not available in this environment. It powers the 5-hour block and burn-rate view
          (install Node + <span className="font-mono text-zinc-400">ccusage</span>, or run on the host).
        </p>
      </Panel>
    );
  }
  const b = data.active;
  const week = data.weekly?.[data.weekly.length - 1];
  const weekCtx = week
    ? (week.inputTokens || 0) + (week.outputTokens || 0) +
      (week.cacheReadTokens || 0) + (week.cacheCreationTokens || 0)
    : 0;

  let elapsedPct = 0;
  if (b) {
    const start = new Date(b.startTime).getTime();
    const end = new Date(b.endTime).getTime();
    elapsedPct = Math.min(100, Math.max(0, ((Date.now() - start) / (end - start)) * 100));
  }

  return (
    <Panel title="Usage windows" right={<span className="text-xs text-zinc-600">5-hour block · via ccusage</span>}>
      <div className="grid gap-px bg-zinc-800/80 md:grid-cols-3">
        {/* active 5h block spans 2 cols */}
        <div className="bg-zinc-900/40 p-5 md:col-span-2">
          <div className="text-[11.5px] font-medium uppercase tracking-[0.14em] text-zinc-500">
            Current 5-hour block
          </div>
          {b ? (
            <>
              <div className="mt-2 flex items-baseline gap-3">
                <span className="font-mono text-3xl leading-none tracking-tight text-emerald-400">
                  {usd(b.costUSD)}
                </span>
                <span className="font-mono text-sm text-zinc-500">
                  projected {usd(b.projection?.totalCost)}
                </span>
              </div>
              <div className="mt-4 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
                <div className="h-full rounded-full bg-emerald-400/80" style={{ width: `${elapsedPct}%` }} />
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-zinc-400">
                <span>{elapsedPct.toFixed(0)}% elapsed</span>
                <span className="text-zinc-600">·</span>
                <span>resets {hm(b.endTime)}</span>
                <span className="text-zinc-600">·</span>
                <span>{b.projection?.remainingMinutes ?? 0}m left</span>
                <span className="text-zinc-600">·</span>
                <span>{compact(b.burnRate?.tokensPerMinuteForIndicator)} tok/min</span>
                <span className="text-zinc-600">·</span>
                <span>{compact(b.entries)} msgs</span>
              </div>
              <div className="mt-2 text-xs text-zinc-500">
                {(b.models || []).map(shortModel).join(", ")}
              </div>
            </>
          ) : (
            <p className="mt-3 text-sm text-zinc-500">No active block right now.</p>
          )}
        </div>
        {/* this week */}
        <div className="bg-zinc-900/40 p-5">
          <div className="text-[11.5px] font-medium uppercase tracking-[0.14em] text-zinc-500">
            This week (rolling)
          </div>
          <div className="mt-2 font-mono text-2xl leading-none tracking-tight text-zinc-100">
            {week ? usd(week.totalCost) : "-"}
          </div>
          <div className="mt-2 font-mono text-xs text-zinc-400">
            {compact(weekCtx)} context tokens
          </div>
          <div className="mt-1 text-xs text-zinc-600">7-day rolling, all models</div>
        </div>
      </div>
    </Panel>
  );
}

/* ---- subagent rows (in the sessions table) ----------------------------- */
// A subagent counts as "running" if its transcript was written within this
// window (a live subagent appends continuously; a finished one goes quiet).
const RUNNING_MS = 90_000;

function SubRow({ parent, sub, running }) {
  const go = () => goSession(`${parent.session_id}~${sub.agent_id}`);
  return (
    <tr
      role="link"
      tabIndex={0}
      onClick={go}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } }}
      title="Open subagent transcript (opens the parent, focused on this subagent)"
      className="cursor-pointer bg-amber-500/[0.03] align-top transition-colors hover:bg-amber-500/[0.09] focus:bg-amber-500/[0.09] focus:outline-none"
    >
      <td className="py-2 pl-10 pr-5">
        <div className="flex items-center gap-2">
          {running
            ? <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-emerald-400 border-t-transparent" title="running" />
            : <span className="text-amber-500/60">⤷</span>}
          <span className="rounded bg-amber-500/10 px-1.5 text-[11px] font-semibold uppercase tracking-wide text-amber-400">
            {sub.agent_type || "subagent"}
          </span>
          {running && (
            <span className="rounded bg-emerald-500/15 px-1.5 text-[11px] font-medium uppercase tracking-wide text-emerald-400">running</span>
          )}
          <span className="max-w-[16rem] truncate text-xs text-zinc-500">{sub.dispatch}</span>
        </div>
      </td>
      <td className="whitespace-nowrap px-5 py-2 font-mono text-[11px] text-zinc-600"
          title={`started ${day(sub.first_ts)} · last activity ${day(sub.last_ts)}`}>
        {day(sub.last_ts || sub.first_ts)}
      </td>
      <td className="px-5 py-2 text-[11px] text-zinc-500">{shortModel(sub.model)}</td>
      <td className="px-5 py-2 text-right font-mono text-zinc-400">{compact(sub.turns)}</td>
      <td className="px-5 py-2 text-right font-mono text-amber-200/80">{compact(sub.input_raw)}</td>
      <td className="px-5 py-2 text-right font-mono text-zinc-400">{compact(sub.output)}</td>
      <td className="px-5 py-2 text-right font-mono text-zinc-400">{compact(sub.cache_read)}</td>
      <td className="px-5 py-2 text-right font-mono text-zinc-700">-</td>
      <td className="px-5 py-2 text-right font-mono text-zinc-700">-</td>
      <td className="px-5 py-2 text-right font-mono text-zinc-400">{usd(sub.cost)}</td>
    </tr>
  );
}

// Shows running subagents (spinner) always; collapses the finished ones into a
// single "N subagents…" toggle. Sorted by last activity, newest first.
function SubagentRows({ parent, now }) {
  const [expanded, setExpanded] = useState(false);
  const subs = useMemo(
    () => [...(parent.subagents || [])].sort((a, b) => (b.last_ts || "").localeCompare(a.last_ts || "")),
    [parent.subagents]);
  if (!subs.length) return null;
  const isRunning = (s) => s.last_ts && (now - Date.parse(s.last_ts)) < RUNNING_MS;
  const running = subs.filter(isRunning);
  const finished = subs.filter((s) => !isRunning(s));
  const toggle = (
    <tr className="bg-amber-500/[0.02]">
      <td colSpan={10} className="py-1.5 pl-10 pr-5">
        <button type="button" onClick={() => setExpanded((v) => !v)}
          className="text-xs text-zinc-500 transition-colors hover:text-zinc-300">
          {expanded
            ? "▾ hide finished subagents"
            : `▸ ${finished.length} subagent${finished.length > 1 ? "s" : ""}…`}
        </button>
      </td>
    </tr>
  );
  return (
    <>
      {running.map((sub) => <SubRow key={sub.agent_id} parent={parent} sub={sub} running />)}
      {finished.length > 0 && toggle}
      {expanded && finished.map((sub) => <SubRow key={sub.agent_id} parent={parent} sub={sub} running={false} />)}
    </>
  );
}

/* ---- app --------------------------------------------------------------- */
export default function App() {
  const [summary, setSummary] = useState(null);
  const [daily, setDaily] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [byModel, setByModel] = useState([]);
  const [windows, setWindows] = useState(null);
  const [quota, setQuota] = useState(null);
  const [live, setLive] = useState(false);
  const [err, setErr] = useState(null);
  const [range, setRange] = useState("all");
  const [view, setView] = useState("usage");
  const [navOpen, setNavOpen] = useState(false); // mobile off-canvas sidebar
  // Icon-only rail. A reading page wants its width back, but hiding the nav
  // entirely costs the reader their bearings - the icons keep the map.
  const [navRail, setNavRail] = useState(() => {
    try { return localStorage.getItem("fd.navRail") === "1"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("fd.navRail", navRail ? "1" : "0"); } catch { /* private window */ }
  }, [navRail]);
  // ticks so subagent "running" flags recompute even without a live update
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 5000); return () => clearInterval(id); }, []);
  const route = parseRoute(useHash());
  // The SSE callback (registered once) reads the latest range via a ref.
  const rangeRef = useRef(range);
  useEffect(() => { rangeRef.current = range; }, [range]);

  const load = useCallback(async () => {
    const q = rangeRef.current;
    try {
      const [s, d, se, bm, win, qt] = await Promise.all([
        get(`/api/summary?range=${q}`), get(`/api/daily?range=${q}`),
        get(`/api/sessions?limit=100&range=${q}`), get(`/api/by-model?range=${q}`),
        get(`/api/usage-windows`),  // rolling windows, range-independent
        get(`/api/quota`),          // official statusLine rate_limits
      ]);
      // Drop stale responses: if the range changed while this was in flight
      // (e.g. a concurrent live-update fetch), don't clobber the current view.
      if (rangeRef.current !== q) return;
      setSummary(s); setDaily(d); setSessions(se); setByModel(bm);
      setWindows(win); setQuota(qt); setErr(null);
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, []);

  // Manual "refresh usage": force a /usage poll (host) or re-read (container).
  const refreshQuota = useCallback(async () => {
    setQuota(await post("/api/quota/refresh"));
  }, []);

  // Refetch when the range changes; subscribe to live updates once.
  useEffect(() => { load(); }, [range, load]);
  useEffect(() => subscribe(load, setLive), [load]);

  const sess = useSort(sessions, { key: "last_ts", dir: "desc" });
  const models = useSort(byModel, { key: "cost", dir: "desc" });

  // Sessions table pagination — top-level sessions only (a session's subagent
  // rows stay attached to it, uncounted). Reset to page 1 when the sort order
  // or range changes, since "page 3" would otherwise point at different rows.
  const [sessPage, setSessPage] = useState(0);
  useEffect(() => { setSessPage(0); }, [range, sess.sort]);
  const sessTotalPages = Math.max(1, Math.ceil(sess.sorted.length / SESSIONS_PAGE_SIZE));
  const sessCurPage = Math.min(sessPage, sessTotalPages - 1);
  const sessPageRows = sess.sorted.slice(
    sessCurPage * SESSIONS_PAGE_SIZE, sessCurPage * SESSIONS_PAGE_SIZE + SESSIONS_PAGE_SIZE);

  const totalSpan = daily.length ? `${daily[0].date} to ${daily[daily.length - 1].date}` : "";

  const NAV_ACTIVE = route.name === "session" ? null
    : route.name === "loom" ? "loom"
    : route.name === "treasure" ? "treasures"
    : route.name === "ticket" ? "tickets"
    : view;

  // One renderer for both nav sections so the row styling can't drift. Every
  // difference between the two rail widths is a CSS rule hanging off
  // `data-collapsed` on the rail, not a branch here — including the label, which
  // stays in the accessibility tree while it is visually clipped, so the row
  // keeps its accessible name from its own text at both widths.
  const navBtn = (n) => (
    <button
      key={n.k}
      type="button"
      className="fdx-nav-button"
      aria-pressed={NAV_ACTIVE === n.k}
      onClick={() => {
        setView(n.k);
        if (n.k === "loom") goLoom(); else goHome();
        setNavOpen(false);
      }}
      title={n.label}
    >
      <span className="fdx-nav-chip">
        <PixelMark name={n.mark} />
      </span>
      <span className="fdx-nav-text">{n.label}</span>
    </button>
  );

  // Shared Header slots for the Spend/Logbook contained pages: the range control
  // (Spend/Logbook-only) goes in `actions`, and the summary meta line becomes the
  // Header subtitle. Both views reuse this identical header.
  const rangeControl = (
    <div role="group" aria-label="Time range"
         className="fdx-segmented shrink-0">
      {RANGES.map((r) => (
        <button key={r.k} type="button" aria-pressed={range === r.k}
          onClick={() => setRange(r.k)}
          className="fdx-segmented-seg">
          {r.label}
        </button>
      ))}
    </div>
  );
  const spendSubtitle = summary ? (
    <>
      <span className="font-mono text-zinc-300">{compact(summary.session_count)}</span> sessions
      <span className="px-2 text-zinc-700">/</span>
      <span className="font-mono text-zinc-300">{compact(summary.message_count)}</span> turns
      {summary.unknown_model_tokens > 0 && (
        <>
          <span className="px-2 text-zinc-700">/</span>
          <span className="font-mono text-amber-400">{compact(summary.unknown_model_tokens)}</span> unpriced tokens
        </>
      )}
      {totalSpan && (
        <>
          <span className="px-2 text-zinc-700">/</span>
          <span className="text-zinc-400">{totalSpan}</span>
        </>
      )}
    </>
  ) : null;

  // Marketing landing lives at #/welcome and takes over the full screen. Placed
  // after every hook above so hook order stays stable (rules of hooks); the
  // default view is untouched — the app still opens to Spend at #/.
  if (route.name === "welcome") return <Landing />;

  // Reading a treasure is a focus mode: the artifact is a full document and the
  // nav is not part of reading it, so the sidebar goes off-canvas on desktop too
  // and the content reclaims its 224px. This reuses the existing off-canvas
  // machinery rather than adding a second hide mechanism — the only change is that
  // `lg` stops forcing the rail open, so `navOpen` governs every width.
  const navHidden = route.name === "treasure";

  return (
    // Same zoom correction as the bleed Shell: with `body { zoom: 1.12 }` a
    // plain 100dvh floor is 12% taller than the viewport, so every page
    // carried a scrollbar with nothing under it.
    <div className="min-h-[calc(100dvh/var(--app-zoom,1))]">
      {/* atmosphere: mesh orbs behind everything, film grain on top (both inert) */}
      <div className="fd-mesh motion-safe:animate-mesh-drift" aria-hidden="true" />
      <div className="fd-grain" aria-hidden="true" />
      {/* backdrop behind the off-canvas sidebar — on desktop too while the nav is
          hidden, since there it is a real overlay and needs a click-away. */}
      {navOpen && (
        <button type="button" aria-label="Close menu" onClick={() => setNavOpen(false)}
          className={`fixed inset-0 z-40 bg-black/60 backdrop-blur-sm ${navHidden ? "" : "lg:hidden"}`} />
      )}
      {/* the way back INTO the nav while it is hidden. Fixed and 36px, so it costs
          no layout — a top bar here would take back the vertical space the focus
          mode just freed. Bottom-left, not top-left: the detail's own back button
          occupies the top-left of the reclaimed area, and two controls stacked
          there would collide. Below `lg` the mobile top bar already has a
          hamburger. */}
      {navHidden && !navOpen && (
        <button
          type="button"
          onClick={() => setNavOpen(true)}
          aria-label="Show menu"
          aria-expanded={false}
          className="fixed bottom-3 left-2 z-40 hidden h-9 w-9 items-center justify-center rounded-lg border border-zinc-800/80 bg-zinc-950/80 text-zinc-400 backdrop-blur-xl transition-colors hover:text-zinc-100 lg:flex"
        >
          <span aria-hidden="true" className="text-[15px] leading-none">☰</span>
        </button>
      )}
      {/* fixed left sidebar (off-canvas below lg, and on every width while a
          treasure is open) */}
      {/* Two widths, one attribute. `data-collapsed` is the whole switch: the
          272px menu and the 76px chip rail differ only in CSS rules hanging off
          it, so there is no second set of class names here to fall out of step.
          Tailwind still owns the off-canvas position and slide, which is the one
          job it was already doing correctly. */}
      <aside
        id="fd-side-menu"
        data-collapsed={navRail ? "true" : "false"}
        className={`fdx-rail fixed inset-y-0 left-0 z-50 flex flex-col lg:z-30 ${navHidden ? "" : "lg:translate-x-0"} ${navOpen ? "translate-x-0" : "-translate-x-full"}`}
      >
        <button type="button" className="fdx-rail-brand" aria-label="FlightDeck home"
          onClick={() => { goHome(); setNavOpen(false); }}>
          <Roundel className="h-[22px] w-[22px]" />
          <span className="fdx-nav-text"><Wordmark className="text-[15px]" /></span>
        </button>
        {/* The nav is the ONLY part allowed to grow, and it scrolls once it runs
            out of room. Without `min-height: 0` a flex child refuses to shrink
            below its content, so a nav this long pushed Quota and the live/theme
            row past the bottom edge — the two things that must stay reachable were
            the two the overflow ate. */}
        <nav className="fdx-nav" aria-label="Views">
          {/* Each group label is a real word expanded and a short rule collapsed,
              at one height either way so the rows below it do not shift. The word
              stays in the accessibility tree at both widths. */}
          <div className="fdx-nav-label"><span>Views</span></div>
          {/* NavGroup renders a childless entry as `renderLeaf(item)` and nothing
              else, so every flat entry here is exactly the button it was before. */}
          {NAV.map((n) => (
            <NavGroup key={n.k} item={n} view={NAV_ACTIVE} renderLeaf={navBtn}
                      onSelect={(k) => { setView(k); goHome(); setNavOpen(false); }} />
          ))}
          <div className="fdx-nav-label"><span>Systems</span></div>
          {SYS_NAV.map(navBtn)}
          {/* Radar belongs to Systems, but it cannot be a SYS_NAV entry: it is its
              own Vite entry rather than a view in this SPA, so it has to be an
              anchor. Styled exactly like navBtn on purpose — a link that looked
              different would read as broken — with the trailing mark as the only
              signal that it leaves the dashboard, the same contract Radio uses
              under Planes. */}
          <a href="/radar.html" className="fdx-nav-button" title="Radar"
             onClick={() => setNavOpen(false)}>
            <span className="fdx-nav-chip"><PixelMark name="radar" /></span>
            <span className="fdx-nav-text">Radar</span>
            <PixelMark name="leavesApp" className="fdx-nav-away" />
          </a>
          {/* Radio is a separate Vite entry, not a view in this SPA, so it is an
              anchor rather than a setView button. */}
          <div className="fdx-nav-label"><span>Planes</span></div>
          <a href="/radio.html" className="fdx-nav-button" title="Radio"
             onClick={() => setNavOpen(false)}>
            <span className="fdx-nav-chip"><PixelMark name="radio" /></span>
            <span className="fdx-nav-text">Radio</span>
            <PixelMark name="leavesApp" className="fdx-nav-away" />
          </a>
        </nav>
        {!navRail && <SidebarQuota data={quota} onRefresh={refreshQuota} />}
        {/* `mt-auto` is gone on purpose: the nav's `flex: 1` now takes the slack,
            so keeping both would be two rules competing to place the same row. */}
        <div className="fdx-rail-footer">
          <div className="fdx-rail-live">
            <StatusBadge tone={live ? "live" : "neutral"} pulse={live}>
              <span className="fdx-nav-text">{live ? "Live" : "Offline"}</span>
            </StatusBadge>
          </div>
          <PaletteToggle variant="rail" persistKey="fd-theme" initial="night" />
        </div>
        {/* The width switch. At the foot of the rail, where a control that changes
            the frame belongs - not among the views it would compete with. */}
        <button type="button" className="fdx-rail-collapse"
          onClick={() => setNavRail((r) => !r)}
          aria-expanded={!navRail} aria-controls="fd-side-menu"
          aria-label={navRail ? "Expand the menu" : "Collapse the menu"}
          title={navRail ? "Expand the menu" : "Collapse the menu"}>
          <span className="fdx-nav-chip"><PixelMark name="chevron" flip={navRail} /></span>
          <span className="fdx-nav-text">{navRail ? "Expand" : "Collapse"}</span>
        </button>
      </aside>

      {/* content (offset by sidebar width on desktop, except in focus mode where
          the sidebar is an overlay; sits above the mesh) */}
      {/* The offset comes from the same two width tokens the rail is sized from,
          so the content and the menu cannot disagree about how wide it is. */}
      <div className="fdx-rail-offset relative z-10"
           data-rail={navHidden ? "hidden" : navRail ? "collapsed" : "expanded"}>
      {/* mobile top bar: hamburger + wordmark (hidden on desktop) */}
      <div className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-zinc-800/80 bg-zinc-950/80 px-4 backdrop-blur-xl lg:hidden">
        <button type="button" onClick={() => setNavOpen((o) => !o)} aria-label="Menu" aria-expanded={navOpen}
          className="relative h-11 w-11 shrink-0 rounded-full border border-zinc-800 bg-white/5">
          <span className={`absolute left-3 right-3 h-px bg-zinc-100 transition-all duration-500 ease-[cubic-bezier(.32,.72,0,1)] ${navOpen ? "top-1/2 -translate-y-1/2 rotate-45" : "top-[17px]"}`} />
          <span className={`absolute left-3 right-3 h-px bg-zinc-100 transition-all duration-500 ease-[cubic-bezier(.32,.72,0,1)] ${navOpen ? "top-1/2 -translate-y-1/2 -rotate-45" : "top-[25px]"}`} />
        </button>
        <Roundel className="h-5 w-5" />
        <Wordmark className="text-sm" />
      </div>
      {route.name === "session" ? (
        // Session detail owns its sticky "← Logbook" back-nav + real session
        // title, so it renders inside the shared contained Shell but WITHOUT a
        // shared Header (its own is the single, richer title bar). Bleed: the
        // transcript owns the viewport - its header and rails stay put and only
        // the stream scrolls, which is what makes a 38k-turn session readable.
        <Shell variant="bleed">
          <SessionDetail sessionId={route.id} initialView={route.view} onBack={goHome} />
        </Shell>
      ) : route.name === "treasure" ? (
        // Same pattern as the session branch above: a full-page detail reached
        // by a hash route, owning its own sticky back-nav, rendered inside the
        // contained Shell WITHOUT a shared Header. `key` forces a clean remount
        // (fresh fetch, fresh Milkdown instance) when navigating treasure -> treasure.
        // Uncapped width, since this is the view that hides the nav to gain room:
        // under the shared 1440px cap the freed space became margin, measured at
        // +12px of actual preview.
        <Shell variant="contained" maxWidthClassName="max-w-none">
          <React.Suspense fallback={<div className="p-6 text-sm text-zinc-500">Loading…</div>}>
            <TreasureDetail key={route.id} id={route.id}
                            onBack={() => { setView("treasures"); goHome(); }}
                            onOpenSession={(sid) => { setView("sessions"); goSession(sid); }} />
          </React.Suspense>
        </Shell>
      ) : route.name === "ticket" ? (
        <Shell variant="contained" maxWidthClassName="max-w-none"
               header={<Header title="Ticket" subtitle="Dev-side phase, links and plan" />}>
          <TicketForm key={route.key} ticketKey={route.key}
                      onBack={() => { setView("tickets"); goHome(); }} />
        </Shell>
      ) : view === "tickets" ? (
        <Shell variant="contained" maxWidthClassName="max-w-none"
               header={<Header title="Tickets" subtitle="Open to closed — the baton says who has it, the reason says what holds it" />}>
          <TicketsView onOpen={goTicket} />
        </Shell>
      ) : route.name === "loom" || view === "loom" ? (
        <Shell variant="bleed" header={<Header title="Route Loom" />}>
          <RouteLoom onOpenSession={(id) => { setView("loom"); goSession(id, "clearance"); }} />
        </Shell>
      ) : view === "graph" ? (
        <Shell variant="bleed" header={<Header title="Charts" />}>
          <GraphView />
        </Shell>
      ) : view === "diff" ? (
        <Shell variant="bleed" header={<Header title="Diff" />}>
          <RepoDiff />
        </Shell>
      ) : view === "hub" ? (
        <Shell variant="bleed" header={<Header title="Hub" />}>
          <Hub />
        </Shell>
      ) : view === "comms" ? (
        <Shell variant="contained" header={<Header title="Comms" subtitle="MCP servers — registry & usage" />}>
          <CommsView />
        </Shell>
      ) : view === "manuals" ? (
        <Shell variant="contained" header={<Header title="Manuals" subtitle="Skills — inventory & usage" />}>
          <ManualsView />
        </Shell>
      ) : view === "memory" ? (
        <Shell variant="contained" header={<Header title="Memory"
          subtitle="Auto-memory store — what drifted, what is in there, how it connects" />}>
          <MemoryView />
        </Shell>
      ) : view === "hangar" ? (
        <Shell variant="contained" header={<Header title="Hangar" subtitle="Compose stacks — read-only board" />}>
          <HangarView />
        </Shell>
      ) : view === "relay" ? (
        <Shell variant="contained" header={<Header title="Relay" subtitle="AG-UI event flow — live agent run" />}>
          <RelayView />
        </Shell>
      ) : view === "missions" ? (
        <Shell variant="contained" header={<Header title="Missions" subtitle="Personal TODO and notes, shared across agent sessions" />}>
          <MissionsView />
        </Shell>
      ) : view === "appearance" ? (
        <Shell variant="contained" header={<Header title="Appearance" subtitle="Face, weight and size for each kind of text — saved for the whole install" />}>
          <AppearanceView />
        </Shell>
      ) : view === "treasure-config" ? (
        <Shell variant="contained" header={<Header title="Treasure config"
          subtitle="Agent notes, header and footer applied to every artifact generated from now on" />}>
          <TreasureConfig />
        </Shell>
      ) : view === "treasures" ? (
        <Shell variant="contained" header={<Header title="Treasures" subtitle="Artifact library — wrap, preview, provenance" />}>
          <TreasuresView onOpenSession={(id) => { setView("sessions"); goSession(id); }}
                         onOpenTreasure={goTreasure} />
        </Shell>
      ) : view === "components" ? (
        <Shell
          variant="contained"
          contentClassName="p-0"
          header={<Header title="Components" subtitle="Token-driven interface kit — states, patterns, and accessibility" />}
        >
          <Suspense
            fallback={
              <div className="grid min-h-[55vh] place-items-center px-6 text-center">
                <div>
                  <div className="mx-auto h-5 w-5 animate-spin rounded-full border-2 border-zinc-700 border-r-emerald-400" />
                  <p className="mt-4 font-mono text-[11px] uppercase tracking-[0.16em] text-zinc-500">
                    Loading interface kit
                  </p>
                </div>
              </div>
            }
          >
            <ComponentLab />
          </Suspense>
        </Shell>
      ) : (
      // Spend + Logbook share one contained Shell: identical Header (title +
      // summary subtitle + range control), body switches on `view`.
      <Shell
        variant="contained"
        contentClassName="space-y-3 px-5 pt-4 pb-5 md:px-8"
        header={
          <Header
            title={view === "sessions" ? "Logbook" : "Spend"}
            subtitle={spendSubtitle}
            actions={rangeControl}
          />
        }
      >
        {err && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
            Could not reach the API ({err}). Retrying on the next update.
          </div>
        )}

        {view === "sessions" ? null : (
          <SpendView summary={summary} daily={daily} byModel={byModel} />
        )}

        {view === "sessions" && (
        <Panel title="Logbook" right={<span className="font-mono text-xs text-zinc-500">{sessions.length}</span>}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] uppercase tracking-wider text-zinc-500">
                  <SortTh label="Session" k="title" align="left" defaultDir="asc" sort={sess.sort} toggle={sess.toggle} />
                  <SortTh label="When" k="last_ts" align="left" sort={sess.sort} toggle={sess.toggle} />
                  <th className="px-5 py-2.5 text-left font-medium">Models</th>
                  <SortTh label="Turns" k="turns" sort={sess.sort} toggle={sess.toggle} />
                  <SortTh label="In (raw)" k="input_raw" sort={sess.sort} toggle={sess.toggle} />
                  <SortTh label="Output" k="output" sort={sess.sort} toggle={sess.toggle} />
                  <SortTh label="Cache read" k="cache_read" sort={sess.sort} toggle={sess.toggle} />
                  <SortTh label="Avg ctx" k="avg_context" sort={sess.sort} toggle={sess.toggle} />
                  <SortTh label="Cache" k="cache_ratio" sort={sess.sort} toggle={sess.toggle} />
                  <SortTh label="Cost" k="cost" sort={sess.sort} toggle={sess.toggle} />
                </tr>
              </thead>
              <tbody>
                {sess.sorted.length === 0 && (
                  <tr className="border-t border-zinc-800/60">
                    <td colSpan={10} className="px-5 py-10 text-center text-sm text-zinc-500">
                      No sessions in this range.
                    </td>
                  </tr>
                )}
                {sessPageRows.map((s) => (
                  <React.Fragment key={s.session_id}>
                  <tr
                    role="link"
                    tabIndex={0}
                    onClick={() => goSession(s.session_id)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goSession(s.session_id); } }}
                    title="Open transcript"
                    className="cursor-pointer border-t border-zinc-800/60 align-top transition-colors hover:bg-emerald-500/[0.06] focus:bg-emerald-500/[0.06] focus:outline-none"
                  >
                    <td className="px-5 py-3">
                      <div className="max-w-[22rem] truncate text-zinc-200 group-hover:text-emerald-300">
                        {s.title || <span className="text-zinc-600">untitled</span>}
                      </div>
                      <div className="font-mono text-[11px] text-zinc-600">{(s.session_id || "").slice(0, 8)} →</div>
                    </td>
                    <td className="whitespace-nowrap px-5 py-3 font-mono text-xs text-zinc-500"
                        title={`started ${day(s.first_ts)} · last activity ${day(s.last_ts)}`}>
                      {day(s.last_ts || s.first_ts)}
                    </td>
                    <td className="px-5 py-3 text-xs text-zinc-400">{(s.models || []).map(shortModel).join(", ")}</td>
                    <td className="px-5 py-3 text-right font-mono text-zinc-300">{compact(s.turns)}</td>
                    <td className="px-5 py-3 text-right font-mono text-zinc-100">{compact(s.input_raw)}</td>
                    <td className="px-5 py-3 text-right font-mono text-zinc-300">{compact(s.output)}</td>
                    <td className="px-5 py-3 text-right font-mono text-zinc-300">{compact(s.cache_read)}</td>
                    <td className="px-5 py-3 text-right font-mono text-zinc-300">{compact(s.avg_context)}</td>
                    <td className="px-5 py-3 text-right">
                      <div className="ml-auto flex w-16 flex-col items-end gap-1">
                        <span className="font-mono text-xs text-zinc-300">{pct(s.cache_ratio)}</span>
                        <span className="h-[3px] w-full overflow-hidden rounded-full">
                          <span className="block h-full rounded-full bg-emerald-400/70"
                                style={{ width: `${Math.round((s.cache_ratio || 0) * 100)}%` }} />
                        </span>
                      </div>
                    </td>
                    <td className="px-5 py-3 text-right font-mono text-zinc-100">{usd(s.cost)}</td>
                  </tr>
                  <SubagentRows parent={s} now={now} />
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
          {sess.sorted.length > 0 && (
            <div className="flex items-center justify-between border-t border-zinc-800/80 px-5 py-3">
              <span className="font-mono text-xs text-zinc-500">
                {sessCurPage * SESSIONS_PAGE_SIZE + 1}-{Math.min(sess.sorted.length, (sessCurPage + 1) * SESSIONS_PAGE_SIZE)} of {sess.sorted.length}
              </span>
              <div className="flex items-center gap-2">
                <button type="button" disabled={sessCurPage === 0}
                  onClick={() => setSessPage((p) => Math.max(0, p - 1))}
                  className="fdx-button" data-variant="secondary" data-size="sm">
                  <IconBack />
                  <span>Prev</span>
                </button>
                <span className="font-mono text-xs text-zinc-400">{sessCurPage + 1} / {sessTotalPages}</span>
                <button type="button" disabled={sessCurPage >= sessTotalPages - 1}
                  onClick={() => setSessPage((p) => Math.min(sessTotalPages - 1, p + 1))}
                  className="fdx-button" data-variant="secondary" data-size="sm">
                  <span>Next</span>
                  <IconForward />
                </button>
              </div>
            </div>
          )}
        </Panel>
        )}

        {view === "sessions" && (
        <footer className="pt-2 text-xs text-zinc-600">
          Costs are API list-equivalent (Opus $5/$25, Sonnet $3/$15, Haiku $1/$5, Fable $10/$50 per 1M;
          cache read 0.1x, write 1.25x/2x). No long-context or tier surcharges applied.
        </footer>
        )}
      </Shell>
      )}
      </div>
      <ScrollButtons />
    </div>
  );
}
