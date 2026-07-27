import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { get, post } from "../api.js";

/* ---- Treasures: the artifact library ------------------------------------
 * Data: GET /api/treasures (+ status/language/query filters), GET
 * /api/treasures/{id}?include_source=true, GET /api/treasures/{id}/raw (the
 * rendered artifact, only ever shown inside a bare `sandbox=""` iframe),
 * POST /api/treasures/discover (dry run, then an explicit import), PUT
 * /api/treasures/{id}/source (edit -> new version -> re-render).
 *
 * Presentational atoms deliberately mirror ManualsView (Eyebrow, StatCell,
 * Chip, relTime, Skeleton) so this view reads as a sibling, not a one-off.
 * All color flows through the zinc (neutral) / emerald (signal) / amber
 * (Vietnamese-language flag) ramps + FlightDeck tokens — no hard-coded
 * black/white except the iframe's own `bg-white` (the artifact carries its
 * own theme).
 */

const POLL_MS = 15000;
const DISCOVER_MAX_FILES = 400;

/* ---- small presentational atoms (SpendView / ManualsView language) ------- */
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
  const minute = 60000, hour = 3600000, day = 86400000;
  if (diff < minute) return "just now";
  if (diff < hour) return `${Math.round(diff / minute)}m ago`;
  if (diff < day) return `${Math.round(diff / hour)}h ago`;
  const d = Math.floor(diff / day);
  if (d < 30) return `${d}d ago`;
  if (d < 365) return `${Math.floor(d / 30)}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
}

function kb(bytes) {
  if (!bytes && bytes !== 0) return "—";
  return `${(bytes / 1024).toFixed(1)} KB`;
}

const STATUS_TONE = {
  draft: "border-zinc-500/30 bg-zinc-500/10 text-zinc-400",
  published: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
  archived: "border-zinc-700/40 bg-zinc-800/20 text-zinc-600",
};
function StatusBadge({ status }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-[9px] uppercase tracking-wide ${STATUS_TONE[status] || STATUS_TONE.draft}`}>
      {status}
    </span>
  );
}

function LanguageBadge({ language }) {
  const vi = language === "vi";
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wide ${
      vi ? "border-amber-500/30 bg-amber-500/10 text-amber-400" : "border-zinc-500/30 bg-zinc-500/10 text-zinc-400"
    }`}>
      {language}
    </span>
  );
}

function ProvenanceButton({ originId, onOpenSession }) {
  if (!originId) {
    return <span className="text-[10px] italic text-zinc-600">no source</span>;
  }
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onOpenSession?.(originId); }}
      title={`Open originating session ${originId}`}
      className="rounded-md border border-[color:var(--fd-hair-2)] px-2 py-0.5 font-mono text-[9px] uppercase tracking-wide text-zinc-400 transition-colors hover:border-emerald-500/40 hover:text-emerald-300"
    >
      → session
    </button>
  );
}

/* ---- loading skeleton (ManualsView pattern) ------------------------------ */
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
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 py-3">
              <div className="h-3 w-48 rounded bg-zinc-800" />
              <div className="h-3 w-16 rounded bg-zinc-800/70" />
              <div className="h-3 flex-1 rounded bg-zinc-800/40" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ErrorPanel({ message, onRetry }) {
  return (
    <div className="fd-shell rounded-2xl">
      <div className="fd-core p-6 text-sm">
        <div className="font-mono text-[11px] uppercase tracking-wide text-rose-400">
          Failed to load Treasures
        </div>
        <div className="mt-2 text-zinc-400">{message}</div>
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-lg border border-[color:var(--fd-hair-2)] px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide text-zinc-300 hover:bg-zinc-500/5"
        >
          Retry
        </button>
      </div>
    </div>
  );
}

/* ---- PUT helper — api.js only has get/post/del ---------------------------
 * Kept in the same style: relative fetch, throw on !ok, return parsed JSON.
 */
async function putSource(id, content) {
  const path = `/api/treasures/${encodeURIComponent(id)}/source`;
  const r = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

/* ---- detail pane: Preview / Source / Edit -------------------------------- */
function DetailPane({ row, onClose, onSaved }) {
  const [tab, setTab] = useState("preview");
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [savedVersion, setSavedVersion] = useState(null);
  const [previewNonce, setPreviewNonce] = useState(0);

  const published = row.status === "published";

  useEffect(() => {
    let live = true;
    setDetail(null);
    setDetailError(null);
    setSavedVersion(null);
    setSaveError(null);
    setTab("preview");
    get(`/api/treasures/${encodeURIComponent(row.id)}?include_source=true`)
      .then((d) => {
        if (!live) return;
        setDetail(d);
        setDraft(d.source || "");
      })
      .catch((e) => live && setDetailError(String(e.message || e)));
    return () => { live = false; };
  }, [row.id]);

  const version = detail?.version ?? row.version;

  const save = async () => {
    if (saving || published) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await putSource(row.id, draft);
      setDetail((d) => (d ? { ...d, ...updated, source: draft } : d));
      setSavedVersion(updated.version);
      // Force the preview iframe to refetch /raw rather than trusting a cache.
      setPreviewNonce((n) => n + 1);
      onSaved?.(updated);
    } catch (e) {
      setSaveError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="fd-shell">
      <div className="fd-core">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[color:var(--fd-hair-2)] px-5 py-3.5">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-zinc-100">{row.title}</div>
            <div className="mt-0.5 flex items-center gap-2 font-mono text-[10px] text-zinc-500">
              <span>{row.slug}</span>
              <span className="text-zinc-700">·</span>
              <span>v{version}</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {["preview", "source", "edit"].map((t) => {
              const disabled = t === "edit" && published;
              return (
                <button
                  key={t}
                  type="button"
                  disabled={disabled}
                  onClick={() => setTab(t)}
                  title={disabled ? "Published artifacts are read-only from the dashboard — claude.ai has no update API" : undefined}
                  className={`rounded-md px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide transition-colors ${
                    tab === t
                      ? "bg-emerald-500/15 text-emerald-400"
                      : disabled
                        ? "cursor-not-allowed text-zinc-700"
                        : "text-zinc-400 hover:bg-zinc-500/5"
                  }`}
                >
                  {t}
                </button>
              );
            })}
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-[color:var(--fd-hair-2)] px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-zinc-400 hover:bg-zinc-500/5"
            >
              Close
            </button>
          </div>
        </header>

        {published && (
          <div className="border-b border-[color:var(--fd-hair-2)] bg-amber-500/5 px-5 py-2.5 text-[11px] text-amber-300/90">
            Published
            {row.published_url && (
              <>
                {" — "}
                <a href={row.published_url} target="_blank" rel="noreferrer"
                   className="underline decoration-dotted underline-offset-2 hover:text-amber-200">
                  {row.published_url}
                </a>
              </>
            )}
            . Read-only from here — claude.ai has no update API.
          </div>
        )}

        <div className="p-5">
          {detailError && (
            <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
              {detailError}
            </div>
          )}

          {!detail && !detailError && (
            <div className="h-40 animate-pulse rounded-lg bg-zinc-800/40" />
          )}

          {detail && tab === "preview" && (
            <div>
              <p className="mb-2 text-[10px] text-zinc-500">
                Rendered in an isolated sandbox — bare <span className="font-mono">sandbox=""</span> forces
                an opaque origin and blocks scripts.
              </p>
              <iframe
                key={`${row.id}-${version}-${previewNonce}`}
                src={`/api/treasures/${encodeURIComponent(row.id)}/raw`}
                sandbox=""
                className="h-[70vh] w-full rounded-lg border border-[color:var(--fd-hair-2)] bg-white"
                title={row.title}
              />
            </div>
          )}

          {detail && tab === "source" && (
            <pre className="max-h-[70vh] overflow-auto rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-950/40 p-4 font-mono text-[11px] leading-relaxed text-zinc-300">
              {detail.source}
            </pre>
          )}

          {detail && tab === "edit" && published && (
            <p className="text-[11px] text-zinc-500">
              Published artifacts are read-only from the dashboard — claude.ai has no update API.
            </p>
          )}

          {detail && tab === "edit" && !published && (
            <div className="space-y-3">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
                className="h-[55vh] w-full rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-950/40 p-3 font-mono text-[12px] leading-relaxed text-zinc-200 focus:border-emerald-500/40 focus:outline-none"
              />
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={save}
                  disabled={saving}
                  className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide text-emerald-300 transition-colors hover:bg-emerald-500/15 disabled:pointer-events-none disabled:opacity-50"
                >
                  {saving ? "Saving…" : "Save"}
                </button>
                {savedVersion != null && (
                  <span className="font-mono text-[10px] text-emerald-400">
                    saved → v{savedVersion}, preview reloaded
                  </span>
                )}
                {saveError && (
                  <span className="font-mono text-[10px] text-rose-400">{saveError}</span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/* ---- Treasures view ------------------------------------------------------ */
export default function TreasuresView({ onOpenSession }) {
  const [allRows, setAllRows] = useState(null); // unfiltered baseline, drives the summary strip
  const [rows, setRows] = useState([]);         // current filtered/searched view
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("all");  // all | draft | published | en | vi
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(null);

  const [discoverBusy, setDiscoverBusy] = useState(false);
  const [discoverResult, setDiscoverResult] = useState(null);
  const [discoverError, setDiscoverError] = useState(null);
  const [importBusy, setImportBusy] = useState(false);

  // The 15s poll timer reads the latest filter/query via a ref, same pattern
  // App.jsx uses for its range-aware SSE callback.
  const stateRef = useRef({ filter, query });
  useEffect(() => { stateRef.current = { filter, query }; }, [filter, query]);

  const load = useCallback(async () => {
    const { filter: f, query: q } = stateRef.current;
    const params = new URLSearchParams();
    if (f === "draft" || f === "published") params.set("status", f);
    if (f === "en" || f === "vi") params.set("language", f);
    if (q) params.set("query", q);
    params.set("limit", "200");
    try {
      const [full, filtered] = await Promise.all([
        get(`/api/treasures?limit=1000`),
        get(`/api/treasures?${params.toString()}`),
      ]);
      setAllRows(full.treasures);
      setRows(filtered.treasures);
      setError(null);
    } catch (e) {
      setError(String(e.message || e));
    }
  }, []);

  useEffect(() => { load(); }, [load, filter, query]);
  useEffect(() => {
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const stats = useMemo(() => {
    const list = allRows || [];
    return {
      total: list.length,
      drafts: list.filter((r) => r.status === "draft").length,
      published: list.filter((r) => r.status === "published").length,
      en: list.filter((r) => r.language === "en").length,
      vi: list.filter((r) => r.language === "vi").length,
    };
  }, [allRows]);

  const runDiscover = async (doImport) => {
    if (doImport) setImportBusy(true); else setDiscoverBusy(true);
    setDiscoverError(null);
    try {
      const result = await post(
        `/api/treasures/discover?do_import=${doImport ? "true" : "false"}&max_files=${DISCOVER_MAX_FILES}`);
      setDiscoverResult(result);
      if (doImport) load(); // newly imported rows should show up immediately
    } catch (e) {
      setDiscoverError(String(e.message || e));
    } finally {
      if (doImport) setImportBusy(false); else setDiscoverBusy(false);
    }
  };

  const newCandidateCount = discoverResult
    ? discoverResult.candidates.filter((c) => !c.already_imported).length
    : 0;

  const selectedRow = selectedId != null
    ? (rows.find((r) => r.id === selectedId) || (allRows || []).find((r) => r.id === selectedId))
    : null;

  if (error && !allRows) {
    return <ErrorPanel message={error} onRetry={load} />;
  }
  if (!allRows) {
    return <Skeleton />;
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
          Could not reach the API ({error}). Showing the last good data; retrying on the next poll.
        </div>
      )}

      {/* Summary strip */}
      <section className="fd-shell">
        <div className="fd-core grid grid-cols-2 divide-x divide-y divide-[color:var(--fd-hair-2)] sm:grid-cols-3 lg:grid-cols-5">
          <StatCell label="Artifacts" value={stats.total} />
          <StatCell label="Draft" value={stats.drafts} tone="text-zinc-300" />
          <StatCell label="Published" value={stats.published} tone="text-emerald-400" />
          <StatCell label="EN" value={stats.en} />
          <StatCell label="VI" value={stats.vi} tone="text-amber-400" />
        </div>
      </section>

      {/* Controls: filter chips + search + refresh + discover */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <Chip active={filter === "all"} onClick={() => setFilter("all")} count={stats.total}>All</Chip>
          <Chip active={filter === "draft"} onClick={() => setFilter("draft")} count={stats.drafts}>Draft</Chip>
          <Chip active={filter === "published"} onClick={() => setFilter("published")} count={stats.published}>Published</Chip>
          <Chip active={filter === "en"} onClick={() => setFilter("en")} count={stats.en}>EN</Chip>
          <Chip active={filter === "vi"} onClick={() => setFilter("vi")} count={stats.vi}>VI</Chip>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="search title / slug…"
            className="rounded-full border border-[color:var(--fd-hair-2)] bg-transparent px-3 py-1 font-mono text-[10px] text-zinc-300 placeholder:text-zinc-600 focus:border-emerald-500/40 focus:outline-none"
          />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[9px] uppercase tracking-wide text-zinc-500">auto 15s</span>
          <button
            type="button"
            onClick={load}
            className="rounded-full border border-[color:var(--fd-hair-2)] px-3 py-1 font-mono text-[10px] uppercase tracking-wide text-zinc-400 transition-colors hover:bg-zinc-500/5"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={() => runDiscover(false)}
            disabled={discoverBusy}
            className="rounded-full border border-[color:var(--fd-hair-2)] px-3 py-1 font-mono text-[10px] uppercase tracking-wide text-zinc-400 transition-colors hover:bg-zinc-500/5 disabled:pointer-events-none disabled:opacity-50"
          >
            {discoverBusy ? "Scanning…" : "Discover drafts"}
          </button>
          {discoverResult && (
            <button
              type="button"
              onClick={() => runDiscover(true)}
              disabled={importBusy || newCandidateCount === 0}
              className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 font-mono text-[10px] uppercase tracking-wide text-emerald-300 transition-colors hover:bg-emerald-500/15 disabled:pointer-events-none disabled:opacity-40"
            >
              {importBusy ? "Importing…" : `Import ${newCandidateCount} new`}
            </button>
          )}
        </div>
      </div>

      {discoverError && (
        <div className="rounded-xl border border-rose-500/20 bg-rose-500/5 px-4 py-2.5 text-[11px] text-rose-300">
          Discover failed: {discoverError}
        </div>
      )}

      {discoverResult && (
        <div className="rounded-xl border border-[color:var(--fd-hair-2)] bg-zinc-500/[0.03] px-4 py-2.5 text-[10px] text-zinc-400">
          <span className="font-mono text-zinc-300">{discoverResult.candidates.length}</span> candidate
          {discoverResult.candidates.length === 1 ? "" : "s"} found
          <span className="px-1.5 text-zinc-700">·</span>
          <span className="font-mono text-zinc-300">
            {discoverResult.candidates.length - newCandidateCount}
          </span> already imported
          <span className="px-1.5 text-zinc-700">·</span>
          scanned <span className="font-mono text-zinc-300">{discoverResult.scanned}</span> files
          <span className="px-1.5 text-zinc-700">·</span>
          bounds: max_files={discoverResult.bounds?.max_files},
          min_bytes={discoverResult.bounds?.min_bytes}
          {discoverResult.bounds?.max_age_days != null && `, max_age_days=${discoverResult.bounds.max_age_days}`}
          {discoverResult.skipped && (
            <>
              <span className="px-1.5 text-zinc-700">·</span>
              skipped: {Object.entries(discoverResult.skipped).map(([k, v]) => `${k}=${v}`).join(", ")}
            </>
          )}
        </div>
      )}

      {/* List */}
      <section className="fd-shell">
        <div className="fd-core overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="font-mono text-[9px] uppercase text-zinc-500">
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 pl-5 text-left font-medium">Title</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-left font-medium">Kind</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-left font-medium">Status</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-left font-medium">Lang</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Ver</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Size</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Updated</th>
                <th className="border-b border-[color:var(--fd-hair-2)] px-4 py-3 text-right font-medium">Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-5 py-10 text-center text-sm text-zinc-500">
                    No artifacts match this filter.
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr
                  key={r.id}
                  role="link"
                  tabIndex={0}
                  onClick={() => setSelectedId(r.id)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelectedId(r.id); } }}
                  className={`cursor-pointer border-b border-[color:var(--fd-hair-2)] transition-colors last:border-b-0 hover:bg-zinc-500/5 ${
                    selectedId === r.id ? "bg-emerald-500/[0.05]" : ""
                  }`}
                >
                  <td className="px-4 py-2.5 pl-5 align-top">
                    <span className="text-[11px] font-semibold text-zinc-100">{r.title}</span>
                    <div className="font-mono text-[9px] text-zinc-600">{r.slug}</div>
                  </td>
                  <td className="px-4 py-2.5 align-top text-[11px] text-zinc-400">{r.kind}</td>
                  <td className="px-4 py-2.5 align-top"><StatusBadge status={r.status} /></td>
                  <td className="px-4 py-2.5 align-top"><LanguageBadge language={r.language} /></td>
                  <td className="px-4 py-2.5 text-right align-top font-mono text-[11px] text-zinc-300">v{r.version}</td>
                  <td className="px-4 py-2.5 text-right align-top font-mono text-[11px] text-zinc-400">{kb(r.render_bytes)}</td>
                  <td className="px-4 py-2.5 text-right align-top font-mono text-[11px] text-zinc-400">{relTime(r.updated_at)}</td>
                  <td className="px-4 py-2.5 text-right align-top">
                    <ProvenanceButton originId={r.origin_id} onOpenSession={onOpenSession} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {selectedRow && (
        <DetailPane row={selectedRow} onClose={() => setSelectedId(null)} onSaved={() => load()} />
      )}

      <div className="text-[9px] leading-[1.5] text-zinc-500">
        Artifacts wrapped via the Treasures MCP or harvested by Discover from ~/.claude/projects.
        Markdown stays the source of truth — editing writes a new version rather than mutating the
        rendered HTML. Published copies are read-only here; claude.ai has no update API.
      </div>
    </div>
  );
}
