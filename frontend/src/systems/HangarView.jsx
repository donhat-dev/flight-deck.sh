import React, { useEffect, useState, useCallback, useRef } from "react";
import { get } from "../api.js";

/* ---- Hangar: compose stack board (read-only) ---------------------------- */
// Data: GET /api/systems/stacks — compose stacks joined from two sources, the
// containers the daemon reports and the compose files found on disk, so a stack
// that is fully DOWN is still listed with the path that would bring it back.
// Still strictly READ-ONLY: no start/stop/restart action anywhere. Lazy
// per-container stats on row expand (GET /api/systems/containers/{id}/stats).
//
// Logs come from Dozzle in an IFRAME, and the distinction matters: the browser
// fetches it directly, so a visitor who reaches this page from the LAN gets an
// iframe pointing at THEIR OWN loopback, not ours. Proxying Dozzle through this
// origin instead would hand that visitor a shell in every container, because
// Dozzle here runs with DOZZLE_ENABLE_SHELL and no auth. Never do that.

const REFRESH_MS = 10000;

// Served by Caddy with `bind 127.0.0.1` — a name, still loopback-only.
const DOZZLE_BASE = "http://dozzle.localhost";

/* ---- state → color mark ------------------------------------------------- */
// emerald = running, amber = restarting / unhealthy, zinc = exited / other.
function stateMark(state, health) {
  if (health === "unhealthy" || state === "restarting" || state === "paused") {
    return { dot: "bg-amber-400", ring: "shadow-[0_0_0_3px_rgba(251,191,36,0.15)]", text: "text-amber-400" };
  }
  if (state === "running") {
    return { dot: "bg-emerald-400", ring: "shadow-[0_0_0_3px_rgba(52,211,153,0.15)]", text: "text-emerald-400" };
  }
  return { dot: "bg-zinc-600", ring: "", text: "text-zinc-500" };
}

function fmtBytes(n) {
  if (!n && n !== 0) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

/* ---- small presentational blocks --------------------------------------- */
function Eyebrow({ children, className = "" }) {
  return (
    <div className={`font-mono text-[10px] uppercase leading-tight tracking-[0.17em] text-zinc-500 ${className}`}>
      {children}
    </div>
  );
}

function Chip({ children, title }) {
  return (
    <span title={title}
      className="inline-flex min-h-[20px] items-center rounded-full border border-[color:var(--fdx-rule)] bg-emerald-500/5 px-2 font-mono text-[10px] text-emerald-300/90">
      {children}
    </span>
  );
}

/* ---- summary strip ------------------------------------------------------ */
function SummaryStrip({ summary, socket, onRefresh, loading, lastUpdated }) {
  const s = summary || {};
  return (
    <section className="fd-shell">
      <div className="fd-core flex flex-wrap items-center gap-x-8 gap-y-3 px-5 py-4">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[26px] leading-none tracking-[-0.02em] text-emerald-400">
            {s.up ?? "—"}
          </span>
          <span className="font-mono text-[15px] leading-none text-zinc-500">/ {s.stacks ?? "—"}</span>
          <Eyebrow className="ml-2">stacks up</Eyebrow>
        </div>
        <div className="hidden h-8 w-px bg-[color:var(--fdx-rule)] sm:block" />
        <div>
          <Eyebrow>partial</Eyebrow>
          <div className="mt-1 font-mono text-sm text-amber-400/90">{s.partial ?? "—"}</div>
        </div>
        <div>
          <Eyebrow>down</Eyebrow>
          <div className="mt-1 font-mono text-sm text-zinc-300">{s.down ?? "—"}</div>
        </div>
        <div>
          <Eyebrow>standalone</Eyebrow>
          <div className="mt-1 font-mono text-sm text-zinc-300">{s.orphan_containers ?? "—"}</div>
        </div>
        <div>
          <Eyebrow>daemon</Eyebrow>
          <div className="mt-1 font-mono text-sm text-zinc-300">{s.docker_version || "—"}</div>
        </div>
        {socket && (
          <div className="min-w-0">
            <Eyebrow>socket</Eyebrow>
            <div className="mt-1 truncate font-mono text-[11px] text-zinc-500" title={socket}>{socket}</div>
          </div>
        )}
        <div className="ml-auto flex items-center gap-3">
          <span className="hidden font-mono text-[10px] text-zinc-600 md:inline">
            {lastUpdated ? `updated ${lastUpdated}` : ""}
            <span className="ml-2 text-zinc-700">· auto 10s</span>
          </span>
          <button
            onClick={onRefresh}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[color:var(--fdx-rule)] px-3 py-1.5 font-mono text-[11px] text-zinc-300 transition-colors hover:bg-zinc-500/10 disabled:opacity-50">
            <span className={loading ? "animate-spin" : ""}>↻</span>
            {loading ? "refreshing" : "refresh"}
          </button>
        </div>
      </div>
    </section>
  );
}

/* ---- lazy per-container stats on expand --------------------------------- */
function StatsRow({ id }) {
  const [stats, setStats] = useState(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    let alive = true;
    setStats(null); setErr(false);
    get(`/api/systems/containers/${id}/stats`)
      .then((d) => { if (alive) (d && d.available ? setStats(d) : setErr(true)); })
      .catch(() => { if (alive) setErr(true); });
    return () => { alive = false; };
  }, [id]);

  if (err) {
    return <div className="px-5 py-2 font-mono text-[10px] text-zinc-600">stats unavailable</div>;
  }
  if (!stats) {
    return (
      <div className="flex gap-4 px-5 py-2">
        <div className="h-2.5 w-20 animate-pulse rounded bg-zinc-800" />
        <div className="h-2.5 w-24 animate-pulse rounded bg-zinc-800" />
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 px-5 py-2 font-mono text-[10px] text-zinc-400">
      <span>cpu <strong className="text-zinc-200">{stats.cpu_pct != null ? `${stats.cpu_pct}%` : "—"}</strong></span>
      <span>mem <strong className="text-zinc-200">{fmtBytes(stats.mem_usage)}</strong>
        {stats.mem_limit ? <span className="text-zinc-600"> / {fmtBytes(stats.mem_limit)}</span> : null}
        {stats.mem_pct != null ? <span className="text-zinc-600"> ({stats.mem_pct}%)</span> : null}
      </span>
    </div>
  );
}

/* ---- one container row -------------------------------------------------- */
function ContainerRow({ c }) {
  const [open, setOpen] = useState(false);
  const mark = stateMark(c.state, c.health);
  const uptime = c.status || (c.uptime ? `Up ${c.uptime}` : c.state);
  return (
    <div className="border-b border-[color:var(--fdx-rule)] last:border-b-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className="grid w-full grid-cols-[14px_minmax(0,1.3fr)_minmax(0,1.6fr)_auto] items-center gap-3 px-5 py-2.5 text-left transition-colors hover:bg-zinc-500/5">
        <span className={`h-[9px] w-[9px] shrink-0 rounded-full ${mark.dot} ${mark.ring}`}
              title={`${c.state}${c.health ? ` · ${c.health}` : ""}`} />
        {/* name + service */}
        <div className="min-w-0">
          <div className="truncate text-[12px] font-semibold text-zinc-100">{c.name}</div>
          <div className="mt-0.5 truncate font-mono text-[10px] text-zinc-500">
            {c.service || (c.project ? "—" : "no compose service")}
            {c.health && <span className={`ml-2 ${mark.text}`}>{c.health}</span>}
          </div>
        </div>
        {/* image (truncated, full in title) */}
        <div className="min-w-0 font-mono text-[11px] text-zinc-400 truncate" title={c.image}>
          {c.image}
        </div>
        {/* uptime/status + ports */}
        <div className="flex items-center justify-end gap-2.5">
          {c.ports && c.ports.length > 0 && (
            <div className="hidden flex-wrap justify-end gap-1 sm:flex">
              {c.ports.map((p) => (
                <Chip key={`${p.host}-${p.container}-${p.proto}`}
                      title={`host ${p.host} → container ${p.container}/${p.proto}`}>
                  {p.label}
                </Chip>
              ))}
            </div>
          )}
          <span className={`whitespace-nowrap font-mono text-[10px] ${mark.text}`} title={c.status || ""}>
            {uptime}
          </span>
          <span className="w-3 font-mono text-[10px] text-zinc-600">{open ? "▾" : "▸"}</span>
        </div>
      </button>
      {open && (
        <div className="border-t border-[color:var(--fdx-rule)] bg-zinc-500/[0.02]">
          <StatsRow id={c.id} />
          {c.state === "running" ? (
            <div className="px-5 pb-4">
              <div className="mb-2 flex items-center justify-between">
                <Eyebrow>logs · dozzle</Eyebrow>
                <a href={`${DOZZLE_BASE}/container/${c.id}`} target="_blank" rel="noreferrer"
                   className="font-mono text-[10px] text-zinc-500 underline-offset-2 hover:text-zinc-300 hover:underline">
                  open full ↗
                </a>
              </div>
              {/* Dozzle has no embed mode — every route renders its own host/container
                  sidebar, which is redundant here because the stack context is the card
                  this iframe sits in. It is cross-origin, so it cannot be styled from
                  here; instead the iframe is over-widened and pulled left inside an
                  overflow-hidden frame so the sidebar falls outside the visible box.
                  The offset is a PERCENTAGE on purpose: Dozzle sizes that sidebar
                  proportionally (measured 133px at 900w and 178px at 1200w, both
                  ≈14.8% of width), so a fixed pixel offset would only be right at one
                  window size. If a Dozzle release changes that ratio the sidebar simply
                  reappears — collapse it once in "open full" and the setting persists in
                  Dozzle's own origin. */}
              <div className="overflow-hidden rounded-lg border border-[color:var(--fdx-rule)] bg-zinc-950">
                <iframe
                  src={`${DOZZLE_BASE}/container/${c.id}`}
                  title={`logs for ${c.name}`}
                  loading="lazy"
                  className="block h-[340px] border-0"
                  style={{ width: "117.4%", marginLeft: "-17.4%" }}
                />
              </div>
            </div>
          ) : (
            <div className="px-5 pb-3 font-mono text-[10px] text-zinc-600">
              container is {c.state} — no log stream
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ---- one compose stack card --------------------------------------------- */
// up = every container running · partial = some · down = none, which includes
// the stack that exists only as files on disk and has nothing on the daemon.
const STACK_MARK = {
  up:      { dot: "bg-emerald-400", text: "text-emerald-400", label: "up" },
  partial: { dot: "bg-amber-400",   text: "text-amber-400",   label: "partial" },
  down:    { dot: "bg-zinc-700",    text: "text-zinc-600",    label: "down" },
  unknown: { dot: "bg-zinc-700",    text: "text-zinc-600",    label: "unknown" },
};

function shortPath(p) {
  if (!p) return "";
  return p.replace(/^\/home\/[^/]+\//, "~/");
}

function StackCard({ stack }) {
  const [open, setOpen] = useState(stack.state !== "down");
  const mark = STACK_MARK[stack.state] || STACK_MARK.unknown;
  return (
    <section className={`fd-shell ${stack.state === "down" ? "opacity-70" : ""}`}>
      <div className="fd-core">
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex min-h-[42px] w-full items-center justify-between gap-4 border-b border-[color:var(--fdx-rule)] px-5 text-left transition-colors hover:bg-zinc-500/5">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className={`h-[9px] w-[9px] shrink-0 rounded-full ${mark.dot}`} title={stack.state} />
            <span className="truncate text-xs font-bold tracking-tight text-zinc-100">{stack.project}</span>
            {stack.on_disk && !stack.declared && (
              <span title="Name guessed from the directory — the compose file declares no top-level `name:`."
                    className="font-mono text-[9px] uppercase tracking-wide text-zinc-600">from-dir</span>
            )}
            {!stack.on_disk && (
              <span title="Running, but no compose file was found under the scanned roots."
                    className="font-mono text-[9px] uppercase tracking-wide text-amber-500/70">no file</span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <span className="font-mono text-[10px] text-zinc-500">
              <span className={mark.text}>{stack.running}</span> / {stack.total} up
            </span>
            <span className={`font-mono text-[9px] uppercase tracking-wide ${mark.text}`}>{mark.label}</span>
            <span className="w-3 font-mono text-[10px] text-zinc-600">{open ? "▾" : "▸"}</span>
          </div>
        </button>

        {open && (
          <>
            {stack.config_files && stack.config_files.length > 0 && (
              <div className="border-b border-[color:var(--fdx-rule)] px-5 py-2.5">
                <Eyebrow>compose files</Eyebrow>
                <div className="mt-1 space-y-0.5">
                  {stack.config_files.map((f) => (
                    <div key={f} className="truncate font-mono text-[10px] text-zinc-500" title={f}>
                      {shortPath(f)}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {stack.containers.length > 0
              ? stack.containers.map((c) => <ContainerRow key={c.id} c={c} />)
              : (
                <div className="px-5 py-3 font-mono text-[10px] text-zinc-600">
                  no containers — stack is down
                  {stack.working_dir && (
                    <span className="ml-2 text-zinc-700">· cwd {shortPath(stack.working_dir)}</span>
                  )}
                </div>
              )}
          </>
        )}
      </div>
    </section>
  );
}

/* ---- containers carrying no compose project ----------------------------- */
// Collapsed by default: these are mostly short-lived `docker run` leftovers, and
// they are not stacks — keeping them out of the stack list is the point.
function OrphanCard({ containers }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="fd-shell">
      <div className="fd-core">
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex min-h-[42px] w-full items-center justify-between gap-4 border-b border-[color:var(--fdx-rule)] px-5 text-left transition-colors hover:bg-zinc-500/5">
          <div className="flex items-baseline gap-2.5">
            <span className="text-xs font-bold tracking-tight text-zinc-100">Standalone</span>
            <span className="font-mono text-[9px] uppercase tracking-wide text-zinc-600">no compose project</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] text-zinc-500">{containers.length}</span>
            <span className="w-3 font-mono text-[10px] text-zinc-600">{open ? "▾" : "▸"}</span>
          </div>
        </button>
        {open && containers.map((c) => <ContainerRow key={c.id} c={c} />)}
      </div>
    </section>
  );
}

/* ---- unavailable (socket not mounted) panel ----------------------------- */
function UnavailablePanel({ data }) {
  return (
    <section className="fd-shell">
      <div className="fd-core px-6 py-8">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-amber-500/30 text-amber-400">!</span>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-zinc-100">Docker socket not reachable</div>
            <p className="mt-1.5 text-[12px] leading-[1.5] text-zinc-400">
              {data?.detail || data?.reason ||
                "The Hangar could not reach the Docker Engine API."}
            </p>
            <p className="mt-3 text-[12px] leading-[1.5] text-zinc-400">
              The container needs the Docker socket mounted. Bring it up with a
              force-recreate so the new mount takes effect:
            </p>
            <pre className="mt-2 overflow-x-auto rounded-lg border border-[color:var(--fdx-rule)] bg-zinc-500/5 px-3 py-2 font-mono text-[11px] text-emerald-300/90">
docker compose up -d --force-recreate
            </pre>
            {data?.checked && (
              <div className="mt-3 font-mono text-[10px] text-zinc-600">
                checked: {data.checked.join(", ")}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

/* ---- loading skeleton --------------------------------------------------- */
function Skeleton() {
  return (
    <>
      <section className="fd-shell">
        <div className="fd-core flex gap-8 px-5 py-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i}>
              <div className="h-2 w-16 rounded bg-zinc-800" />
              <div className="mt-2 h-5 w-12 rounded bg-zinc-800/70" />
            </div>
          ))}
        </div>
      </section>
      {Array.from({ length: 2 }).map((_, i) => (
        <section key={i} className="fd-shell">
          <div className="fd-core">
            <div className="h-[42px] border-b border-[color:var(--fdx-rule)] px-5" />
            {Array.from({ length: 3 }).map((_, j) => (
              <div key={j} className="flex items-center gap-3 border-b border-[color:var(--fdx-rule)] px-5 py-3 last:border-b-0">
                <div className="h-[9px] w-[9px] rounded-full bg-zinc-800" />
                <div className="h-3 w-40 rounded bg-zinc-800" />
                <div className="ml-auto h-3 w-24 rounded bg-zinc-800/70" />
              </div>
            ))}
          </div>
        </section>
      ))}
    </>
  );
}

/* ---- error state -------------------------------------------------------- */
function ErrorPanel({ message, onRetry }) {
  return (
    <section className="fd-shell">
      <div className="fd-core px-6 py-8">
        <div className="text-sm font-semibold text-zinc-100">Could not load the container board</div>
        <p className="mt-1.5 font-mono text-[11px] text-zinc-500">{message}</p>
        <button onClick={onRetry}
          className="mt-4 rounded-lg border border-[color:var(--fdx-rule)] px-3 py-1.5 font-mono text-[11px] text-zinc-300 transition-colors hover:bg-zinc-500/10">
          retry
        </button>
      </div>
    </section>
  );
}

/* ---- Hangar view -------------------------------------------------------- */
export default function HangarView() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await get("/api/systems/stacks");
      if (!mounted.current) return;
      setData(d);
      setError(null);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (e) {
      if (mounted.current) setError(e.message || String(e));
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { mounted.current = false; clearInterval(id); };
  }, [load]);

  // First paint, nothing yet.
  if (loading && !data && !error) return <div className="space-y-4"><Skeleton /></div>;
  if (error && !data) return <ErrorPanel message={error} onRetry={load} />;

  const available = data?.available;
  const stacks = data?.stacks || [];
  const orphans = data?.orphans || [];

  // The daemon being unreachable no longer empties the board: the disk scan
  // still knows which stacks exist, so they render with state `unknown` under
  // the warning rather than disappearing.
  return (
    <div className="space-y-4">
      {available && (
        <SummaryStrip
          summary={data.summary}
          socket={data.socket}
          onRefresh={load}
          loading={loading}
          lastUpdated={lastUpdated}
        />
      )}
      {!available && <UnavailablePanel data={data} />}
      {data?.scan_error && (
        <section className="fd-shell">
          <div className="fd-core px-5 py-3 font-mono text-[11px] text-amber-400/90">
            compose scan failed: {data.scan_error}
          </div>
        </section>
      )}
      {stacks.length === 0 && orphans.length === 0 && (
        <section className="fd-shell">
          <div className="fd-core px-6 py-8 text-center text-sm text-zinc-500">
            No compose stacks or containers found.
          </div>
        </section>
      )}
      {stacks.map((st) => <StackCard key={st.project} stack={st} />)}
      {orphans.length > 0 && <OrphanCard containers={orphans} />}
    </div>
  );
}
