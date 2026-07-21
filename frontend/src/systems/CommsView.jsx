import React, { useEffect, useState, useCallback, useRef } from "react";
import { get, post } from "../api.js";

/* ---- Comms: MCP server registry + usage + live health ------------------- */
// Data: GET /api/systems/mcp?range= — registry (global ~/.claude.json +
// per-project .mcp.json) joined with real usage from the ledger's tool_calls
// table. Live connection state comes from `claude mcp list` (POST .../reprobe),
// which actually handshakes every server — so it is slow, cached server-side,
// and only run on an explicit re-probe (or once automatically on first open).
// All color flows through FlightDeck tokens / the theme-bound zinc + emerald
// ramps, so every element renders in Night AND Day automatically.

const RANGES = [
  { key: "today", label: "Today" },
  { key: "7d", label: "7d" },
  { key: "30d", label: "30d" },
  { key: "all", label: "All" },
];

// Live-state → presentation. Keyed by the backend's `live.state`.
const LIVE = {
  connected: { label: "Connected", dot: "bg-emerald-400", text: "text-emerald-300", ring: "bg-emerald-500/10" },
  failed: { label: "Failed", dot: "bg-rose-400", text: "text-rose-300", ring: "bg-rose-500/10" },
  needs_auth: { label: "Needs auth", dot: "bg-amber-400", text: "text-amber-300", ring: "bg-amber-500/10" },
  unknown: { label: "Unknown", dot: "bg-zinc-500", text: "text-zinc-400", ring: "bg-zinc-500/10" },
};

// Compact "how long ago" from an ISO timestamp, plus the short date on hover.
function fmtWhen(iso) {
  if (!iso) return { text: "never", title: "" };
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return { text: iso, title: iso };
  const secs = Math.max(0, (Date.now() - t.getTime()) / 1000);
  const day = 86400;
  let text;
  if (secs < 3600) text = `${Math.max(1, Math.round(secs / 60))}m ago`;
  else if (secs < day) text = `${Math.round(secs / 3600)}h ago`;
  else if (secs < 30 * day) text = `${Math.round(secs / day)}d ago`;
  else text = t.toISOString().slice(0, 10);
  return { text, title: t.toLocaleString() };
}

function fmtMtime(iso) {
  if (!iso) return "unknown";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return iso;
  return t.toLocaleString();
}

// "probed X ago" from the server-computed age in seconds. Using age_s (not the
// timestamp) sidesteps the container-UTC vs browser-local skew that would make
// a naive ISO read hours off.
function fmtAge(secs) {
  if (secs == null) return "";
  if (secs < 5) return "just now";
  if (secs < 90) return `${Math.round(secs)}s ago`;
  if (secs < 5400) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

/* ---- primitives (mirrors SpendView's Eyebrow / PanelHead / chips) ------- */
function Eyebrow({ children, className = "" }) {
  return (
    <div className={`font-mono text-[10px] uppercase leading-tight tracking-[0.17em] text-zinc-500 ${className}`}>
      {children}
    </div>
  );
}

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

function RangeControl({ value, onChange }) {
  return (
    <div className="inline-flex overflow-hidden rounded-lg border border-[color:var(--fd-hair-2)]">
      {RANGES.map((r) => (
        <button key={r.key} type="button" onClick={() => onChange(r.key)}
          className={`px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide transition-colors ${
            value === r.key
              ? "bg-emerald-500/15 text-emerald-300"
              : "text-zinc-500 hover:bg-zinc-500/10 hover:text-zinc-300"
          }`}>
          {r.label}
        </button>
      ))}
    </div>
  );
}

// Scope badge: global vs a project/workspace name vs connector vs unregistered.
function ScopeBadge({ scope, registered }) {
  if (!registered) {
    return (
      <span className="rounded bg-amber-500/10 px-1.5 py-0.5 font-mono text-[9px] font-medium uppercase tracking-wide text-amber-400">
        unregistered
      </span>
    );
  }
  const isGlobal = scope === "global";
  const isConnector = scope === "connector";
  return (
    <span className={`rounded px-1.5 py-0.5 font-mono text-[9px] font-medium uppercase tracking-wide ${
      isGlobal ? "bg-emerald-500/10 text-emerald-300"
        : isConnector ? "bg-sky-500/10 text-sky-300"
        : "bg-zinc-500/10 text-zinc-400"
    }`}>
      {scope}
    </span>
  );
}

// Live connection badge — only shown once a probe has run and the server was
// present in it. A steady dot (connected) vs a static dot for other states.
function LiveBadge({ live }) {
  if (!live) return null;
  const v = LIVE[live.state] || LIVE.unknown;
  return (
    <span title={live.status_text}
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 font-mono text-[9px] font-medium ${v.ring} ${v.text}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${v.dot} ${live.state === "connected" ? "animate-live-pulse" : ""}`} />
      {v.label}
    </span>
  );
}

function ToolChip({ tool, calls }) {
  return (
    <span className="inline-flex min-h-[20px] items-center gap-1 rounded-full border border-[color:var(--fd-hair-2)] bg-zinc-500/5 px-2 font-mono text-[9px] text-zinc-400">
      {tool}
      <span className="text-zinc-500">{calls}</span>
    </span>
  );
}

// Re-probe button: fires the (safe, read-only) live health check. Spins while
// the probe is in flight; the label carries a short explanation on hover.
function ReprobeButton({ probing, onClick }) {
  return (
    <button type="button" onClick={onClick} disabled={probing}
      title="Re-check live connection status (runs `claude mcp list`; read-only, nothing is killed or restarted)"
      className="inline-flex items-center gap-1.5 rounded-lg border border-[color:var(--fd-hair-2)] px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-zinc-400 transition-colors hover:bg-zinc-500/10 hover:text-zinc-200 disabled:opacity-60">
      <span className={probing ? "inline-block animate-spin" : ""}>↻</span>
      {probing ? "probing" : "re-probe"}
    </button>
  );
}

/* ---- local processes panel ---------------------------------------------- */
// GET /api/systems/mcp/processes — read-only /proc scan of MCP server child
// processes. Only works when host PIDs are visible (pid:host / demo.sh); it
// degrades to an instruction panel otherwise. No process is ever signalled.
function ProcessPanel() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    get("/api/systems/mcp/processes")
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message || String(e)));
  }, []);

  useEffect(() => { load(); }, [load]);

  if (error) return null;              // stay quiet on a transient fetch error
  if (!data) return null;              // nothing until the first response

  const available = data.available;
  const procs = data.processes || [];

  return (
    <section className="fd-shell">
      <div className="fd-core">
        <PanelHead
          title="Local processes"
          meta={
            available
              ? `${data.process_count} MCP process(es) · read-only`
              : "stdio server processes on this host"
          }
          right={
            <button type="button" onClick={load}
              className="inline-flex items-center gap-1.5 rounded-lg border border-[color:var(--fd-hair-2)] px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-zinc-400 transition-colors hover:bg-zinc-500/10 hover:text-zinc-200">
              ↻ refresh
            </button>
          }
        />
        {!available ? (
          <div className="px-5 py-5">
            <div className="flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
              <span className="font-mono text-[11px] text-zinc-300">
                process visibility not enabled
              </span>
              <span className="font-mono text-[9px] uppercase tracking-wide text-zinc-600">
                pid 1: {data.pid1}
              </span>
            </div>
            <div className="mt-2 max-w-2xl text-[11px] leading-[1.6] text-zinc-500">
              {data.detail}
            </div>
          </div>
        ) : procs.length === 0 ? (
          <div className="grid place-items-center px-5 py-8 font-mono text-xs text-zinc-500">
            No MCP server processes running right now.
          </div>
        ) : (
          procs.map((p) => (
            <div key={p.pid}
              className="grid grid-cols-1 gap-2 border-b border-[color:var(--fd-hair-2)] px-5 py-3 last:border-b-0 md:grid-cols-[minmax(0,1fr)_auto]">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[13px] font-semibold text-zinc-100">
                    {p.server || "unattributed"}
                  </span>
                  <span className="font-mono text-[9px] uppercase tracking-wide text-zinc-500">
                    pid {p.pid}
                  </span>
                </div>
                <div className="mt-1 truncate font-mono text-[10px] text-zinc-500" title={p.cmd}>
                  {p.cmd}
                </div>
              </div>
              <div className="flex items-center gap-5 md:justify-end">
                <div className="text-right">
                  <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-zinc-500">Uptime</div>
                  <div className="mt-0.5 font-mono text-xs text-zinc-300">{p.uptime}</div>
                </div>
                <div className="text-right">
                  <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-zinc-500">Memory</div>
                  <div className="mt-0.5 font-mono text-xs text-zinc-300">{p.rss_mb} MB</div>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

/* ---- loading skeleton --------------------------------------------------- */
function CommsSkeleton() {
  return (
    <section className="fd-shell">
      <div className="fd-core animate-pulse">
        <div className="flex min-h-[42px] items-center border-b border-[color:var(--fd-hair-2)] px-5">
          <div className="h-2.5 w-40 rounded bg-zinc-800" />
        </div>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="border-b border-[color:var(--fd-hair-2)] px-5 py-4 last:border-b-0">
            <div className="h-3 w-48 rounded bg-zinc-800" />
            <div className="mt-2.5 h-2.5 w-72 rounded bg-zinc-800/60" />
          </div>
        ))}
      </div>
    </section>
  );
}

/* ---- server row --------------------------------------------------------- */
function ServerRow({ s }) {
  const used = s.calls > 0;
  // Dim only when idle AND not live-connected (a connected-but-unused server
  // still matters — it is up right now).
  const liveUp = s.live && s.live.state === "connected";
  const dim = s.registered && !used && !liveUp;
  const when = fmtWhen(s.last_used);
  return (
    <div className={`grid grid-cols-1 gap-3 border-b border-[color:var(--fd-hair-2)] px-5 py-3.5 transition-colors last:border-b-0 hover:bg-zinc-500/5 md:grid-cols-[minmax(0,1.5fr)_auto] ${
      dim ? "opacity-55" : ""
    }`}>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-[13px] font-semibold text-zinc-100">{s.name}</span>
          <LiveBadge live={s.live} />
          <ScopeBadge scope={s.scope} registered={s.registered} />
          <span className="font-mono text-[9px] uppercase tracking-wide text-zinc-500">{s.transport}</span>
          {s.registered && s.scopes && s.scopes.length > 1 && (
            <span className="font-mono text-[9px] text-zinc-600" title={s.scopes.join(", ")}>
              +{s.scopes.length - 1} scope
            </span>
          )}
        </div>
        {(s.command || s.url) && (
          <div className="mt-1 truncate font-mono text-[10px] text-zinc-500" title={s.command || s.url}>
            {s.command || s.url}
          </div>
        )}
        {!s.registered && (
          <div className="mt-1 text-[10px] text-amber-400/80">
            used but not in any registry (configured elsewhere / removed)
          </div>
        )}
        {s.top_tools && s.top_tools.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {s.top_tools.map((t) => (
              <ToolChip key={t.tool} tool={t.tool} calls={t.calls} />
            ))}
          </div>
        )}
      </div>
      <div className="flex items-center gap-5 md:justify-end">
        <div className="text-right">
          <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-zinc-500">Calls</div>
          <div className="mt-0.5 font-mono text-sm text-zinc-100">{s.calls.toLocaleString()}</div>
        </div>
        <div className="text-right">
          <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-zinc-500">Sessions</div>
          <div className="mt-0.5 font-mono text-sm text-zinc-100">{s.sessions.toLocaleString()}</div>
        </div>
        <div className="text-right">
          <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-zinc-500">Last used</div>
          <div className="mt-0.5 font-mono text-xs text-zinc-300" title={when.title}>{when.text}</div>
        </div>
      </div>
    </div>
  );
}

/* ---- Comms view --------------------------------------------------------- */
export default function CommsView() {
  const [range, setRange] = useState("all");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [probing, setProbing] = useState(false);
  // Guards a single automatic first-open probe (manual re-probe is unlimited).
  const autoProbed = useRef(false);

  const reprobe = useCallback((rng) => {
    setProbing(true);
    return post(`/api/systems/mcp/reprobe?range=${rng}`)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message || String(e)))
      .finally(() => setProbing(false));
  }, []);

  const load = useCallback((rng) => {
    setLoading(true);
    setError(null);
    get(`/api/systems/mcp?range=${rng}`)
      .then((d) => {
        setData(d);
        // First time the view opens with no cached probe: kick one probe so
        // live badges populate without the user having to ask.
        if (!autoProbed.current && d?.health && !d.health.probed) {
          autoProbed.current = true;
          reprobe(rng);
        }
      })
      .catch((e) => setError(e.message || String(e)))
      .finally(() => setLoading(false));
  }, [reprobe]);

  useEffect(() => { load(range); }, [range, load]);

  if (loading && !data) return <CommsSkeleton />;

  if (error && !data) {
    return (
      <section className="fd-shell">
        <div className="fd-core p-6">
          <Eyebrow>Comms unavailable</Eyebrow>
          <div className="mt-2 text-sm text-zinc-300">Could not load the MCP registry.</div>
          <div className="mt-1 font-mono text-[11px] text-amber-400">{error}</div>
          <button type="button" onClick={() => load(range)}
            className="mt-4 rounded-lg border border-[color:var(--fd-hair-2)] px-3 py-1.5 font-mono text-[11px] text-zinc-300 hover:bg-zinc-500/10">
            Retry
          </button>
        </div>
      </section>
    );
  }

  const servers = data?.servers || [];
  const totals = data?.totals || {};
  const reg = data?.registry || {};
  const warnings = data?.warnings || [];
  const health = data?.health || {};

  return (
    <div className="flex flex-col gap-4">
      {/* Totals + live health rollup */}
      <section className="fd-shell">
        <div className="fd-core grid grid-cols-2 divide-x divide-y divide-[color:var(--fd-hair-2)] sm:grid-cols-4 sm:divide-y-0">
          {[
            { label: "Servers", value: totals.servers ?? 0 },
            {
              label: "Connected now",
              value: health.probed ? (totals.connected ?? 0) : "—",
              tone: "text-emerald-300",
            },
            {
              label: "Failed / Needs auth",
              value: health.probed ? `${totals.failed ?? 0} / ${totals.needs_auth ?? 0}` : "—",
              tone: (totals.failed ?? 0) > 0 ? "text-rose-300" : "text-zinc-100",
            },
            { label: "Total calls", value: (totals.calls ?? 0).toLocaleString() },
          ].map((k) => (
            <div key={k.label} className="px-5 py-4">
              <Eyebrow>{k.label}</Eyebrow>
              <div className={`mt-2 font-mono text-2xl tracking-[-0.02em] ${k.tone || "text-zinc-100"}`}>{k.value}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Server list */}
      <section className="fd-shell">
        <div className="fd-core">
          <PanelHead
            title="MCP servers"
            meta={
              health.probed
                ? `live status probed ${fmtAge(health.age_s)}`
                : "registry + usage · live status not yet probed"
            }
            right={
              <div className="flex items-center gap-2">
                <ReprobeButton probing={probing} onClick={() => reprobe(range)} />
                <RangeControl value={range} onChange={setRange} />
              </div>
            }
          />
          {health.error && (
            <div className="border-b border-[color:var(--fd-hair-2)] bg-amber-500/5 px-5 py-2 font-mono text-[10px] text-amber-400/90">
              live probe: {health.error}
            </div>
          )}
          {servers.length === 0 ? (
            <div className="grid place-items-center px-5 py-12 font-mono text-xs text-zinc-500">
              No MCP servers registered or used in this range.
            </div>
          ) : (
            servers.map((s) => <ServerRow key={s.name} s={s} />)
          )}
        </div>
      </section>

      {/* Local MCP server processes (read-only) */}
      <ProcessPanel />

      {/* Registry freshness + warnings */}
      <div className="flex flex-col gap-1.5 text-[10px] leading-[1.5] text-zinc-500">
        <div>
          Registry as of{" "}
          <span className="font-mono text-zinc-400">{fmtMtime(reg.global_mtime_iso)}</span>
          {reg.global_path && (
            <span className="text-zinc-600"> · {reg.global_path}</span>
          )}
          {totals.unregistered_used > 0 && (
            <span className="text-amber-400/80">
              {" "}· {totals.unregistered_used} used server(s) not in any registry
            </span>
          )}
        </div>
        {reg.project_files && reg.project_files.length > 0 && (
          <div className="text-zinc-600">
            Project registries:{" "}
            {reg.project_files.map((p) => p.scope).join(", ")}
          </div>
        )}
        {warnings.map((w, i) => (
          <div key={i} className="text-amber-400/70">⚠ {w}</div>
        ))}
      </div>
    </div>
  );
}
