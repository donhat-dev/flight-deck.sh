import React, { useEffect, useMemo, useRef, useState } from "react";
import { get } from "./api.js";

// One route PER user instruction (a "clearance"): its turns, grouped into
// role-classified units, laid out from the human input to the agent's closing
// summary. Lanes are roles, not phases.
const UNIT_BUDGETS = [6, 9, 12];

const LANE_META = {
  lead:     { label: "Lead",     color: "#CAC9C4", hint: "Human direction and agent framing" },
  scout:    { label: "Scout",    color: "#4E93CC", hint: "Read, search, inspect, delegate" },
  builder:  { label: "Builder",  color: "#FF5133", hint: "Create or change artifacts" },
  reviewer: { label: "Reviewer", color: "#61B5A1", hint: "Test, lint, inspect output" },
  gate:     { label: "Gate",     color: "#D09B5A", hint: "Ship, operate, hand off" },
};

const ROW_H = 104;
const CARD_W = 168;
const CANVAS_PAD = 96;

const compact = (value) => {
  const n = Math.round(value || 0);
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return `${n}`;
};

const safeText = (value, fallback = "Untitled") =>
  String(value || fallback).replace(/[—–]/g, "-");

const shortPath = (path) => {
  if (!path) return "Unknown project";
  const parts = path.split("/").filter(Boolean);
  return parts.slice(-2).join("/") || path;
};

const durationText = (start, end) => {
  const a = start ? new Date(start) : null;
  const b = end ? new Date(end) : null;
  if (!a || !b) return "-";
  const mins = Math.max(0, Math.round((b - a) / 60000));
  if (mins < 1) return "<1 min";
  if (mins < 60) return `${mins} min`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
};

function LoadingRoute() {
  return (
    <div className="loom-shell animate-pulse rounded-2xl border-2" style={{ minHeight: ROW_H * 5 }}>
      <div className="grid grid-cols-[110px_minmax(0,1fr)]">
        <div className="border-r border-zinc-800/70 px-3 py-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center" style={{ height: ROW_H }}>
              <span className="h-2.5 w-14 rounded bg-zinc-800" />
            </div>
          ))}
        </div>
        <div className="relative" style={{ height: ROW_H * 5 }}>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="border-b border-zinc-800/40" style={{ height: ROW_H }} />
          ))}
        </div>
      </div>
    </div>
  );
}

function EmptyRoute({ message }) {
  return (
    <div className="loom-shell grid min-h-[420px] place-items-center rounded-2xl border-2 px-6 text-center">
      <div className="max-w-sm">
        <div className="mx-auto h-10 w-px bg-zinc-700" />
        <div className="mx-auto h-3 w-3 -translate-y-px rotate-45 border border-zinc-600 bg-zinc-950" />
        <h2 className="mt-5 text-base font-semibold text-zinc-200">No route available</h2>
        <p className="mt-1 text-sm leading-relaxed text-zinc-500">{message}</p>
      </div>
    </div>
  );
}

// Node anchors: X by sequence (even), Y by lane. Adjacent units are almost
// always different lanes (a unit boundary IS a lane change), so cards on
// different rows do not collide even when close on X.
function unitPoints(units, lanes, width) {
  const n = units.length;
  const laneIndex = (id) => Math.max(0, lanes.findIndex((l) => l.id === id));
  const usable = Math.max(CARD_W, width - CANVAS_PAD * 2);
  return units.map((unit, index) => {
    const x = n <= 1 ? width / 2 : CANVAS_PAD + (index / (n - 1)) * usable;
    const y = laneIndex(unit.lane) * ROW_H + ROW_H / 2;
    return { x, y, unit, index };
  });
}

function laneStroke(laneId, alpha) {
  const hex = (LANE_META[laneId] || {}).color || "#9C9B96";
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function routePath(a, b) {
  // exit right edge of a, enter left edge of b (cards sit on top of the line)
  const x1 = a.x + CARD_W / 2 - 8;
  const x2 = b.x - CARD_W / 2 + 8;
  const curve = Math.max(24, (x2 - x1) * 0.45);
  return `M ${x1} ${a.y} C ${x1 + curve} ${a.y}, ${x2 - curve} ${b.y}, ${x2} ${b.y}`;
}

function RouteCanvas({ route, lanes, selectedUnit, onSelectUnit }) {
  const wrapRef = useRef(null);
  const nodeRefs = useRef([]);
  const [width, setWidth] = useState(1000);
  const units = route.units || [];

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  // Fill the container when a clearance has few units; scroll horizontally when
  // it has many, so cards keep a clickable slot instead of overlapping.
  const contentWidth = Math.max(width, units.length * 180);
  const points = useMemo(() => unitPoints(units, lanes, contentWidth), [units, lanes, contentWidth]);
  const height = lanes.length * ROW_H;

  const choose = (index, focus = false) => {
    onSelectUnit(index);
    if (focus) nodeRefs.current[index]?.focus();
  };

  return (
    <div className="grid grid-cols-[110px_minmax(0,1fr)]">
      {/* lane labels */}
      <div className="border-r border-zinc-800/70">
        {lanes.map((lane) => {
          const meta = LANE_META[lane.id] || { label: lane.label, color: "#9C9B96", hint: "" };
          return (
            <div key={lane.id} className="flex items-center gap-2 px-3" style={{ height: ROW_H }} title={meta.hint}>
              <span className="h-6 w-[3px] rounded-full" style={{ background: meta.color }} />
              <span className="text-[11px] font-semibold uppercase tracking-wide text-zinc-400">{meta.label}</span>
            </div>
          );
        })}
      </div>

      {/* canvas (scrolls horizontally when a clearance has many units) */}
      <div ref={wrapRef} className="overflow-x-auto">
      <div className="relative" style={{ height, width: contentWidth }}>
        {lanes.map((lane, i) => (
          <div
            key={lane.id}
            className="absolute inset-x-0 border-b border-zinc-800/45"
            style={{ top: i * ROW_H, height: ROW_H }}
          >
            <span className="absolute inset-x-0 top-1/2 border-t border-dashed border-zinc-800/50" />
          </div>
        ))}

        <svg className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
          {points.slice(0, -1).map((point, index) => {
            const next = points[index + 1];
            const active = index === selectedUnit || index + 1 === selectedUnit;
            return (
              <path
                key={point.unit.id + next.unit.id}
                d={routePath(point, next)}
                fill="none"
                stroke={active ? "#FF5133" : laneStroke(next.unit.lane, 0.5)}
                strokeWidth={active ? 2.4 : 1.5}
                strokeDasharray={active ? "0" : "4 5"}
                className="loom-route-segment"
              />
            );
          })}
        </svg>

        {points.map(({ x, y, unit, index }) => {
          const meta = LANE_META[unit.lane] || { label: unit.lane, color: "#9C9B96" };
          const selected = index === selectedUnit;
          return (
            <button
              key={unit.id}
              ref={(el) => { nodeRefs.current[index] = el; }}
              type="button"
              aria-current={selected ? "step" : undefined}
              aria-label={`Unit ${index + 1}, ${meta.label}: ${safeText(unit.label)}`}
              onClick={() => choose(index)}
              onKeyDown={(event) => {
                if (event.key === "ArrowRight" && index < units.length - 1) { event.preventDefault(); choose(index + 1, true); }
                if (event.key === "ArrowLeft" && index > 0) { event.preventDefault(); choose(index - 1, true); }
              }}
              className={`loom-waypoint absolute -translate-x-1/2 -translate-y-1/2 rounded-2xl border-2 px-3 py-2 text-left transition-[transform,border-color,background-color] duration-300 ${
                selected ? "z-[3] scale-[1.03]" : "z-[2] hover:scale-[1.02]"
              }`}
              style={{
                left: x, top: y, width: CARD_W,
                borderColor: selected ? "#FF5133" : "rgba(255,255,255,0.10)",
                background: selected ? "rgba(255,81,51,0.10)" : "#0B0C10",
              }}
            >
              <span className="absolute left-0 top-3 h-[calc(100%-24px)] w-[3px] rounded-full" style={{ background: meta.color }} />
              <span className="ml-1.5 block truncate text-[12.5px] font-semibold leading-tight text-zinc-100" title={safeText(unit.label)}>
                {safeText(unit.label)}
              </span>
              <span className="ml-1.5 mt-1 flex items-center gap-1.5 font-mono text-[9.5px] uppercase tracking-wide text-zinc-500">
                <span style={{ color: meta.color }}>{meta.label}</span>
                <span className="text-zinc-700">·</span>
                <span>{compact(unit.turn_count)}t</span>
                {unit.tool_count > 0 && <><span className="text-zinc-700">·</span><span>{compact(unit.tool_count)}tc</span></>}
                {unit.error_count > 0 && (
                  <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-rose-400" title={`${unit.error_count} errors`} />
                )}
              </span>
            </button>
          );
        })}
      </div>
      </div>
    </div>
  );
}

function ClearanceList({ clearances, lanes, selectedIndex, onSelect }) {
  const laneColor = (id) => (LANE_META[id] || {}).color || "#9C9B96";
  // newest instruction first
  const ordered = clearances.map((c, i) => ({ c, i })).slice().reverse();
  return (
    <div className="loom-shell flex h-full flex-col overflow-hidden rounded-2xl border">
      <div className="flex items-baseline justify-between border-b border-zinc-800/70 px-3 py-2.5 font-mono text-[10px] uppercase tracking-[0.12em] text-zinc-500">
        <span>Clearances</span><span>{clearances.length}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {ordered.map(({ c, i }) => {
          const on = i === selectedIndex;
          const dominant = Object.entries(c.lane_counts || {}).sort((a, b) => b[1] - a[1]);
          return (
            <button
              key={i}
              type="button"
              aria-current={on ? "true" : undefined}
              onClick={() => onSelect(i)}
              className={`flex w-full items-start gap-2 border-t border-l-2 border-t-zinc-800/60 px-3 py-2.5 text-left transition-colors first:border-t-0 ${
                on ? "border-l-emerald-400 bg-emerald-500/[0.08]" : "border-l-transparent hover:bg-zinc-800/40"
              }`}
            >
              <span className="mt-0.5 shrink-0 font-mono text-[10px] text-zinc-600">C{i + 1}</span>
              <span className="min-w-0 flex-1">
                <span className={`block truncate text-xs ${on ? "text-emerald-300" : "text-zinc-300"}`}>{safeText(c.label)}</span>
                <span className="mt-1 flex items-center gap-1">
                  {dominant.slice(0, 4).map(([lane]) => (
                    <span key={lane} className="h-1.5 w-4 rounded-full" style={{ background: laneColor(lane) }} title={lane} />
                  ))}
                  <span className="ml-1 font-mono text-[10px] text-zinc-600">
                    {c.unit_count} units{c.error_count ? ` · ${c.error_count} err` : ""}
                  </span>
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function UnitFooter({ clearance, unit, sessionId, onOpenSession }) {
  const meta = unit ? (LANE_META[unit.lane] || { label: unit.lane, color: "#9C9B96" }) : null;
  return (
    <div className="loom-inspector grid grid-cols-1 gap-5 border-t border-zinc-800/80 px-5 py-4 md:grid-cols-[minmax(0,1fr)_auto_auto_auto_auto]">
      <div className="min-w-0">
        {unit && (
          <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold" style={{ color: meta.color }}>
            <span className="h-px w-5" style={{ background: meta.color }} />
            {meta.label}
            <span className="font-mono text-[10px] font-normal text-zinc-600">unit {unit.sequence} / {clearance?.unit_count}</span>
          </div>
        )}
        <h3 className="truncate text-[17px] font-semibold tracking-tight text-zinc-100" title={unit ? safeText(unit.label) : ""}>
          {unit ? safeText(unit.label) : "Select a unit"}
        </h3>
        <p className="mt-0.5 truncate text-xs text-zinc-500">
          {clearance ? `Clearance ${clearance.index + 1}: ${safeText(clearance.label)}` : ""}
        </p>
      </div>
      {unit && (
        <>
          <div className="md:border-l md:border-zinc-800/70 md:pl-5">
            <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-zinc-600">Turns</div>
            <div className="mt-1 font-mono text-base text-zinc-200">{unit.start_turn}-{unit.end_turn}</div>
          </div>
          <div className="md:border-l md:border-zinc-800/70 md:pl-5">
            <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-zinc-600">Duration</div>
            <div className="mt-1 font-mono text-base text-zinc-200">{durationText(unit.first_ts, unit.last_ts)}</div>
          </div>
          <div className="md:border-l md:border-zinc-800/70 md:pl-5">
            <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-zinc-600">Tools</div>
            <div className={`mt-1 font-mono text-base ${unit.error_count ? "text-rose-400" : "text-zinc-200"}`}>
              {unit.tool_count}{unit.error_count ? ` / ${unit.error_count}e` : ""}
            </div>
          </div>
          <div className="flex items-end md:border-l md:border-zinc-800/70 md:pl-5">
            <button
              type="button"
              onClick={() => onOpenSession(sessionId)}
              className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-zinc-950 transition-[background-color,transform] hover:bg-emerald-400 active:translate-y-px"
            >
              Open transcript
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export default function RouteLoom({ onOpenSession }) {
  const [sessions, setSessions] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [budget, setBudget] = useState(9);
  const [data, setData] = useState(null);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [routeLoading, setRouteLoading] = useState(false);
  const [error, setError] = useState(null);
  const [clearanceIndex, setClearanceIndex] = useState(0);
  const [unitIndex, setUnitIndex] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let active = true;
    setSessionsLoading(true);
    get("/api/sessions?limit=100&range=all")
      .then((rows) => {
        if (!active) return;
        const sorted = [...rows].sort((a, b) => (b.last_ts || "").localeCompare(a.last_ts || ""));
        setSessions(sorted);
        let remembered = "";
        try { remembered = window.localStorage.getItem("flightdeck-route-session") || ""; } catch { /* no-op */ }
        const next = sorted.find((s) => s.session_id === remembered)?.session_id
          || sorted.find((s) => (s.turns || 0) >= 20)?.session_id
          || sorted[0]?.session_id || "";
        setSelectedId((current) => current || next);
        setError(null);
      })
      .catch((reason) => { if (active) setError(`Could not load sessions (${reason.message || reason})`); })
      .finally(() => { if (active) setSessionsLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    setRouteLoading(true);
    setError(null);
    get(`/api/session/${encodeURIComponent(selectedId)}/clearance-routes?max_units=${budget}`)
      .then((result) => {
        if (!active) return;
        setData(result);
        setClearanceIndex((current) => Math.min(current, Math.max(0, (result.clearances?.length || 1) - 1)));
        setUnitIndex(0);
        try { window.localStorage.setItem("flightdeck-route-session", selectedId); } catch { /* no-op */ }
      })
      .catch((reason) => { if (active) setError(`Could not build route (${reason.message || reason})`); })
      .finally(() => { if (active) setRouteLoading(false); });
    return () => { active = false; };
  }, [selectedId, budget, refreshKey]);

  const clearances = data?.clearances || [];
  const lanes = data?.lanes || [];
  const clearance = clearances[clearanceIndex] || null;
  const units = clearance?.units || [];
  const unit = units[unitIndex] || null;

  const selectClearance = (index) => { setClearanceIndex(index); setUnitIndex(0); };

  return (
    <main className="mx-auto h-full max-w-[1600px] overflow-y-auto px-4 py-5 md:px-7 md:py-7">
      <header className="flex flex-col gap-5 border-b border-zinc-800/80 pb-5 xl:flex-row xl:items-end xl:justify-between">
        <div className="max-w-xl">
          <h1 className="text-xl font-semibold tracking-tight text-zinc-100">Route Loom</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Each instruction becomes one route: its turns, grouped into role units, from your input to the closing summary.
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-[minmax(240px,420px)_auto_auto] sm:items-end">
          <label className="min-w-0">
            <span className="mb-1.5 block text-[10px] font-medium uppercase tracking-[0.12em] text-zinc-500">Session</span>
            <select
              value={selectedId}
              disabled={sessionsLoading || sessions.length === 0}
              onChange={(event) => { setSelectedId(event.target.value); setClearanceIndex(0); setUnitIndex(0); }}
              className="h-10 w-full rounded-lg border border-zinc-800 bg-zinc-900/80 px-3 text-sm text-zinc-200 outline-none transition-colors hover:border-zinc-700 focus:border-emerald-500 disabled:opacity-50"
            >
              {sessions.length === 0 && <option value="">No sessions</option>}
              {sessions.map((s) => (
                <option key={s.session_id} value={s.session_id}>
                  {safeText(s.title)} [{s.session_id.slice(0, 8)}]
                </option>
              ))}
            </select>
          </label>
          <div>
            <span className="mb-1.5 block text-[10px] font-medium uppercase tracking-[0.12em] text-zinc-500">Units</span>
            <div className="flex h-10 rounded-lg border border-zinc-800 bg-zinc-900/70 p-0.5" role="group" aria-label="Unit budget">
              {UNIT_BUDGETS.map((value) => (
                <button
                  key={value} type="button" aria-pressed={budget === value} onClick={() => setBudget(value)}
                  className={`min-w-10 rounded-md px-2 font-mono text-xs transition-colors ${
                    budget === value ? "bg-emerald-500/18 text-emerald-400" : "text-zinc-500 hover:text-zinc-200"}`}
                >
                  {value}
                </button>
              ))}
            </div>
          </div>
          <button
            type="button" disabled={!selectedId || routeLoading}
            onClick={() => setRefreshKey((v) => v + 1)}
            className="h-10 whitespace-nowrap rounded-lg border border-zinc-800 px-3 text-xs font-semibold text-zinc-400 transition-[border-color,color,transform] hover:border-zinc-700 hover:text-zinc-100 active:translate-y-px disabled:pointer-events-none disabled:opacity-40"
          >
            {routeLoading ? "Building..." : "Refresh"}
          </button>
        </div>
      </header>

      {data && (
        <div className="mt-4 flex min-w-0 flex-col gap-1 md:flex-row md:items-end md:justify-between">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-zinc-200" title={safeText(data.title)}>{safeText(data.title)}</h2>
            <p className="mt-0.5 truncate font-mono text-[10px] text-zinc-600">
              {shortPath(data.project)} / {(selectedId || "").slice(0, 8)} / {compact(data.source?.turn_count)} turns / {data.source?.clearance_count} clearances
            </p>
          </div>
          <div className="mt-2 font-mono text-[10px] text-zinc-600 md:mt-0">deterministic rules / {data.rule_version}</div>
        </div>
      )}

      {error && (
        <div className="mt-5 flex flex-col gap-3 rounded-xl border border-rose-500/30 bg-rose-500/[0.06] px-4 py-3 text-sm text-rose-300 sm:flex-row sm:items-center sm:justify-between">
          <span>{error}</span>
          <button type="button" onClick={() => setRefreshKey((v) => v + 1)} className="shrink-0 font-semibold text-rose-200 hover:text-rose-100">Try again</button>
        </div>
      )}

      {sessionsLoading && !data ? (
        <div className="mt-5"><LoadingRoute /></div>
      ) : !selectedId ? (
        <div className="mt-5"><EmptyRoute message="No sessions were found in the local ledger." /></div>
      ) : routeLoading && !data ? (
        <div className="mt-5"><LoadingRoute /></div>
      ) : clearances.length ? (
        <div className={`mt-5 grid items-start gap-5 xl:grid-cols-[300px_minmax(0,1fr)] ${routeLoading ? "opacity-55" : "opacity-100"} transition-opacity`} aria-busy={routeLoading}>
          <div className="h-[600px] xl:h-[calc(100dvh-230px)]">
            <ClearanceList clearances={clearances} lanes={lanes} selectedIndex={clearanceIndex} onSelect={selectClearance} />
          </div>

          <section className="loom-shell min-w-0 overflow-hidden rounded-2xl border-2">
            <div className="flex items-center justify-between border-b border-zinc-800/70 px-4 py-3">
              <div className="min-w-0">
                <h2 className="truncate text-sm font-semibold text-zinc-200">
                  Clearance {clearance ? clearance.index + 1 : "-"} route
                </h2>
                <p className="mt-0.5 truncate text-[10px] text-zinc-600" title={safeText(clearance?.label)}>
                  {safeText(clearance?.label)}
                </p>
              </div>
              <div className="hidden shrink-0 items-center gap-4 pl-4 font-mono text-[9px] uppercase tracking-wide text-zinc-600 sm:flex">
                <span>lanes = roles</span>
                <span>left = start · right = summary</span>
              </div>
            </div>

            {units.length ? (
              <RouteCanvas route={clearance} lanes={lanes} selectedUnit={unitIndex} onSelectUnit={setUnitIndex} />
            ) : (
              <div className="grid place-items-center py-16 text-sm text-zinc-500">This clearance has no renderable units.</div>
            )}

            <UnitFooter clearance={clearance} unit={unit} sessionId={selectedId} onOpenSession={onOpenSession} />
          </section>
        </div>
      ) : data ? (
        <div className="mt-5"><EmptyRoute message="The session contains no user instructions." /></div>
      ) : null}
    </main>
  );
}
