import React, { useEffect, useMemo, useState } from "react";
import { get } from "../api.js";

/* ---- Manuals: skill inventory + usage -----------------------------------
 * Data: GET /api/systems/skills — every skill scanned from the three sources
 * (personal ~/.claude/skills, the plugins cache, workspace agent/skills)
 * joined with real Skill-tool usage from the ledger. Self-fetching; renders
 * inside a `contained` Shell so it emits page content only. All colors flow
 * through the zinc (neutral) / emerald (signal) ramps + FlightDeck tokens, so
 * both Night and Day themes read correctly.
 */

/* ---- small presentational atoms (SpendView visual language) ------------- */
function Eyebrow({ children }) {
  return (
    <div className="font-mono text-[10px] uppercase leading-tight tracking-[0.17em] text-zinc-500">
      {children}
    </div>
  );
}

function StatCell({ label, value, tone = "text-zinc-100", sub }) {
  return (
    <div className="px-5 py-4">
      <Eyebrow>{label}</Eyebrow>
      <div className={`mt-2 font-mono text-[26px] leading-none tracking-[-0.02em] ${tone}`}>
        {value}
      </div>
      {sub && <div className="mt-1.5 text-[10px] text-zinc-500">{sub}</div>}
    </div>
  );
}

// Source badge — emerald for personal, sky for plugin, zinc for workspace.
function SourceBadge({ source }) {
  const map = {
    personal: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
    plugin: "border-sky-500/30 bg-sky-500/10 text-sky-400",
    workspace: "border-zinc-500/30 bg-zinc-500/10 text-zinc-400",
  };
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-[9px] uppercase tracking-wide ${map[source] || map.workspace}`}>
      {source}
    </span>
  );
}

// Amber "flag" pill for duplicates / ghosts with a short reason.
function Flag({ children, tone = "amber" }) {
  const cls = tone === "rose"
    ? "border-rose-500/30 bg-rose-500/10 text-rose-400"
    : "border-amber-500/30 bg-amber-500/10 text-amber-400";
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[9px] font-medium uppercase tracking-wide ${cls}`}>
      {children}
    </span>
  );
}

function Chip({ active, onClick, children, count }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 font-mono text-[10px] uppercase tracking-wide transition-colors ${
        active
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
          : "border-[color:var(--fd-hair-2)] text-zinc-400 hover:bg-zinc-500/5"
      }`}
    >
      {children}
      {count != null && <span className="text-zinc-500">{count}</span>}
    </button>
  );
}

function relTime(ts) {
  if (!ts) return "—";
  const t = Date.parse(ts);
  if (Number.isNaN(t)) return ts;
  const diff = Date.now() - t;
  const day = 86400000;
  if (diff < day) return "today";
  const d = Math.floor(diff / day);
  if (d < 30) return `${d}d ago`;
  if (d < 365) return `${Math.floor(d / 30)}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
}

/* ---- loading skeleton --------------------------------------------------- */
function Skeleton() {
  return (
    <div className="space-y-4">
      <div className="fd-shell">
        <div className="fd-core grid grid-cols-2 divide-x divide-y divide-zinc-800/80 lg:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="px-5 py-4">
              <div className="h-2.5 w-16 rounded bg-zinc-800" />
              <div className="mt-3 h-6 w-14 rounded bg-zinc-800/70" />
            </div>
          ))}
        </div>
      </div>
      <div className="fd-shell">
        <div className="fd-core p-5">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 py-3">
              <div className="h-3 w-40 rounded bg-zinc-800" />
              <div className="h-3 w-16 rounded bg-zinc-800/70" />
              <div className="h-3 flex-1 rounded bg-zinc-800/40" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ---- Manuals view ------------------------------------------------------- */
export default function ManualsView() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [range, setRange] = useState("all");
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    get(`/api/systems/skills?range=${range}`)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(String(e.message || e)));
    return () => { live = false; };
  }, [range]);

  const skills = data?.skills || [];
  const ghosts = data?.ghosts || [];

  const filtered = useMemo(() => {
    switch (filter) {
      case "personal":
      case "plugin":
      case "workspace":
        return skills.filter((s) => s.source === filter);
      case "broken":
        return skills.filter((s) => s.broken);
      case "never":
        return skills.filter((s) => !s.calls && !s.broken);
      default:
        return skills;
    }
  }, [skills, filter]);

  if (error) {
    return (
      <div className="fd-shell rounded-2xl">
        <div className="fd-core p-6 text-sm">
          <div className="font-mono text-[11px] uppercase tracking-wide text-rose-400">
            Failed to load skill inventory
          </div>
          <div className="mt-2 text-zinc-400">{error}</div>
          <button
            type="button"
            onClick={() => setRange((r) => r)}
            className="mt-4 rounded-lg border border-[color:var(--fd-hair-2)] px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide text-zinc-300 hover:bg-zinc-500/5"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!data) return <Skeleton />;

  const s = data.summary;
  const bySource = s.by_source || {};

  return (
    <div className="space-y-4">
      {/* Summary strip */}
      <section className="fd-shell">
        <div className="fd-core grid grid-cols-2 divide-x divide-y divide-[color:var(--fd-hair-2)] sm:grid-cols-3 lg:grid-cols-5">
          <StatCell
            label="Skills"
            value={s.total}
            sub={`${bySource.personal || 0} personal · ${bySource.plugin || 0} plugin · ${bySource.workspace || 0} workspace`}
          />
          <StatCell label="Personal" value={bySource.personal || 0} tone="text-emerald-400" />
          <StatCell label="Plugin" value={bySource.plugin || 0} tone="text-sky-400" />
          <StatCell
            label="Broken"
            value={s.broken}
            tone={s.broken ? "text-rose-400" : "text-zinc-100"}
          />
          <StatCell
            label="Never used"
            value={s.never_used}
            tone={s.never_used ? "text-amber-400" : "text-zinc-100"}
            sub={s.ghosts ? `${s.ghosts} ghost${s.ghosts === 1 ? "" : "s"}` : null}
          />
        </div>
      </section>

      {/* Controls: filter chips + range */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <Chip active={filter === "all"} onClick={() => setFilter("all")} count={skills.length}>All</Chip>
          <Chip active={filter === "personal"} onClick={() => setFilter("personal")} count={bySource.personal || 0}>Personal</Chip>
          <Chip active={filter === "plugin"} onClick={() => setFilter("plugin")} count={bySource.plugin || 0}>Plugin</Chip>
          <Chip active={filter === "workspace"} onClick={() => setFilter("workspace")} count={bySource.workspace || 0}>Workspace</Chip>
          <Chip active={filter === "broken"} onClick={() => setFilter("broken")} count={s.broken}>Broken</Chip>
          <Chip active={filter === "never"} onClick={() => setFilter("never")} count={s.never_used}>Never used</Chip>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[9px] uppercase tracking-wide text-zinc-500">usage</span>
          {["today", "7d", "30d", "all"].map((r) => (
            <Chip key={r} active={range === r} onClick={() => setRange(r)}>{r}</Chip>
          ))}
        </div>
      </div>

      {/* Warnings */}
      {data.warnings?.length > 0 && (
        <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-2.5">
          <div className="font-mono text-[9px] uppercase tracking-wide text-amber-400">Scan warnings</div>
          <ul className="mt-1 space-y-0.5">
            {data.warnings.map((w, i) => (
              <li key={i} className="text-[10px] text-zinc-400">{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Main table */}
      <section className="fd-shell">
        <div className="fd-core overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="font-mono text-[9px] uppercase text-zinc-500">
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 pl-5 text-left font-medium">Skill</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-left font-medium">Source</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-left font-medium">Description</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Calls</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Sessions</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Last used</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-5 py-10 text-center text-sm text-zinc-500">
                    No skills match this filter.
                  </td>
                </tr>
              )}
              {filtered.map((sk) => {
                const dim = !sk.calls && !sk.broken;
                const rowBg = sk.broken ? "bg-rose-500/[0.04]" : "";
                return (
                  <tr
                    key={`${sk.source}:${sk.name}:${sk.path}`}
                    className={`border-b border-[color:var(--fd-hair-2)] transition-colors last:border-b-0 hover:bg-zinc-500/5 ${rowBg}`}
                  >
                    <td className="px-4 py-2.5 pl-5 align-top">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`text-[11px] font-semibold ${sk.broken ? "text-rose-300" : dim ? "text-zinc-400" : "text-zinc-100"}`}>
                          {sk.name}
                        </span>
                        {sk.version && (
                          <span className="font-mono text-[9px] text-zinc-500">v{sk.version}</span>
                        )}
                        {sk.broken && <Flag tone="rose">broken</Flag>}
                        {sk.duplicate && <Flag>dup of {sk.duplicate_of}</Flag>}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 align-top"><SourceBadge source={sk.source} /></td>
                    <td className="max-w-[420px] px-4 py-2.5 align-top">
                      <span
                        className={`block truncate text-[11px] ${dim ? "text-zinc-500" : "text-zinc-400"}`}
                        title={sk.description || ""}
                      >
                        {sk.description || <span className="italic text-zinc-600">no description</span>}
                      </span>
                    </td>
                    <td className={`px-4 py-2.5 text-right align-top font-mono text-[11px] ${sk.calls ? "text-zinc-200" : "text-zinc-600"}`}>
                      {sk.calls || "—"}
                    </td>
                    <td className={`px-4 py-2.5 text-right align-top font-mono text-[11px] ${sk.sessions ? "text-zinc-300" : "text-zinc-600"}`}>
                      {sk.sessions || "—"}
                    </td>
                    <td className="px-4 py-2.5 text-right align-top font-mono text-[11px] text-zinc-400">
                      {relTime(sk.last_used)}
                    </td>
                  </tr>
                );
              })}

              {/* Ghosts: invoked names not found on disk (only in All view) */}
              {filter === "all" && ghosts.map((g) => (
                <tr key={`ghost:${g.name}`} className="border-b border-[color:var(--fd-hair-2)] bg-amber-500/[0.04] last:border-b-0">
                  <td className="px-4 py-2.5 pl-5 align-top">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[11px] font-semibold text-amber-300">{g.name}</span>
                      <Flag>ghost · not on disk</Flag>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 align-top">
                    <span className="font-mono text-[9px] uppercase tracking-wide text-zinc-500">—</span>
                  </td>
                  <td className="px-4 py-2.5 align-top text-[11px] italic text-zinc-500">
                    Invoked but deleted/renamed — no matching skill found.
                  </td>
                  <td className="px-4 py-2.5 text-right align-top font-mono text-[11px] text-zinc-300">{g.calls}</td>
                  <td className="px-4 py-2.5 text-right align-top font-mono text-[11px] text-zinc-300">{g.sessions}</td>
                  <td className="px-4 py-2.5 text-right align-top font-mono text-[11px] text-zinc-400">{relTime(g.last_used)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="text-[9px] leading-[1.5] text-zinc-500">
        Skills scanned from ~/.claude/skills (personal), the plugins cache, and workspace
        agent/skills. Usage counts join the ledger&apos;s Skill tool calls over the selected range;
        broken = missing SKILL.md, ghost = invoked but no longer on disk.
      </div>
    </div>
  );
}
