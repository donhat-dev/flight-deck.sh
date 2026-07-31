import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { get, post, subscribe } from "../api.js";
import LibraryHeader from "./library/LibraryHeader.jsx";
import LibrarySearch from "./library/LibrarySearch.jsx";
import { TreasureListRow, TreasureMobileCard, kb } from "./library/TreasureRow.jsx";

/**
 * Treasures library.
 *
 * Data: GET /api/treasures (status/language/tag/query filters), GET
 * /api/treasure-tags, POST /api/treasures (create), POST
 * /api/treasures/discover. Row detail lives at #/treasure/<id>.
 *
 * Two fetch shapes, deliberately separate:
 *  - the FILTERED list, which every filter and (debounced) keystroke re-requests;
 *  - the BASELINE counts and the tag list, which only change when the library
 *    itself changes.
 * They used to be one call, so typing a five-letter query pulled the 1000-row
 * baseline and the tag list five times over. Nothing in the summary line depends
 * on the query, so nothing in it should be refetched by typing.
 */

const SAFETY_POLL_MS = 60000; // fallback only — SSE drives the real refresh
const DISCOVER_MAX_FILES = 400;
const QUERY_DEBOUNCE_MS = 250;

function Skeleton() {
  return (
    <div className="space-y-4">
      <div className="h-9 w-48 rounded bg-zinc-800/70" />
      <div className="h-11 w-full rounded-lg bg-zinc-800/40" />
      <div className="overflow-hidden rounded-xl border border-[color:var(--fd-hair-2)]">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="space-y-2 border-b border-[color:var(--fd-hair-2)] px-5 py-4 last:border-b-0">
            <div className="h-3.5 w-1/2 rounded bg-zinc-800/70" />
            <div className="h-2.5 w-1/3 rounded bg-zinc-800/40" />
          </div>
        ))}
      </div>
    </div>
  );
}

function ErrorPanel({ message, onRetry }) {
  return (
    <div className="space-y-3 rounded-xl border border-rose-500/30 bg-rose-500/5 p-5">
      <p className="text-sm text-rose-300">Could not load the library: {message}</p>
      <button type="button" onClick={onRetry} className="fdx-button" data-variant="secondary" data-size="sm">
        <span>Try again</span>
      </button>
    </div>
  );
}

function EmptyState({ filtered, onClear }) {
  return (
    <div className="space-y-3 px-5 py-16 text-center">
      <p className="text-[15px] text-zinc-300">
        {filtered ? "No artifacts match these filters." : "No artifacts yet."}
      </p>
      <p className="text-[13px] text-zinc-500">
        {filtered
          ? "Clear the filters, or widen the search."
          : "Create one from a file on this machine, or scan your sessions for drafts."}
      </p>
      {filtered && (
        <button type="button" onClick={onClear} className="fdx-button" data-variant="secondary" data-size="sm">
          <span>Clear filters</span>
        </button>
      )}
    </div>
  );
}

/**
 * Create a treasure. Two intake paths, and the file path is the default because
 * they are not equivalent: with a path the server reads the file itself, so the
 * stored checksum describes what is on disk and the row stays refreshable.
 * Pasted text hashes whatever arrived and can never gain that property.
 */
function CreatePanel({ onCreated, onCancel }) {
  const [mode, setMode] = useState("path");
  const [path, setPath] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = mode === "path" ? { source_path: path.trim() } : { title: title.trim(), content };
      const list = tags.split(",").map((t) => t.trim()).filter(Boolean);
      if (list.length) body.tags = list;
      onCreated(await post("/api/treasures", body));
    } catch (e) {
      setError(e.detail || e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const ready = mode === "path"
    ? path.trim().length > 0
    : title.trim().length > 0 && content.trim().length > 0;
  const input =
    "w-full min-h-[44px] rounded-lg border border-[color:var(--fd-hair-2)] bg-transparent px-3.5 text-[14px] text-zinc-100 placeholder:text-zinc-600 focus:border-[color:var(--fd-coral)]/50 focus:outline-none";

  return (
    <section className="space-y-3 rounded-xl border border-[color:var(--fd-hair)] bg-zinc-500/[0.03] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-[15px] font-semibold text-zinc-100">New treasure</h3>
        <div className="fdx-segmented" role="group" aria-label="Source">
          <button type="button" className="fdx-segmented-seg" aria-pressed={mode === "path"} onClick={() => setMode("path")}>
            From file
          </button>
          <button type="button" className="fdx-segmented-seg" aria-pressed={mode === "paste"} onClick={() => setMode("paste")}>
            Paste
          </button>
        </div>
      </div>

      {mode === "path" ? (
        <div className="space-y-2">
          <label htmlFor="new-path" className="block text-[12px] font-semibold text-zinc-400">
            File on this machine
          </label>
          <input id="new-path" className={input} value={path} onChange={(e) => setPath(e.target.value)}
                 placeholder="/home/…/docs/report.md" autoFocus />
          <p className="text-[12px] leading-relaxed text-zinc-500">
            Preferred: the server reads the file, so this stays refreshable and an edit on
            disk is detectable. Paths are limited to the configured read roots.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          <label htmlFor="new-title" className="block text-[12px] font-semibold text-zinc-400">
            Title <span className="font-normal text-zinc-500">— required, there is nothing to derive it from</span>
          </label>
          <input id="new-title" className={input} value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
          <label htmlFor="new-body" className="block pt-1 text-[12px] font-semibold text-zinc-400">Markdown</label>
          <textarea id="new-body" className={`${input} min-h-[10rem] py-3`} value={content}
                    onChange={(e) => setContent(e.target.value)} placeholder="# Heading…" />
        </div>
      )}

      <div className="space-y-2">
        <label htmlFor="new-tags" className="block text-[12px] font-semibold text-zinc-400">
          Tags <span className="font-normal text-zinc-500">— optional, comma separated</span>
        </label>
        <input id="new-tags" className={input} value={tags} onChange={(e) => setTags(e.target.value)} />
      </div>

      {error && (
        <p className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-3.5 py-2.5 text-[13px] text-rose-300">
          {error}
        </p>
      )}

      <div className="flex items-center gap-2">
        <button type="button" className="fdx-button" data-variant="primary" data-size="sm"
                disabled={!ready || busy} onClick={submit}>
          <span>{busy ? "Creating…" : "Create"}</span>
        </button>
        <button type="button" className="fdx-button" data-variant="secondary" data-size="sm" onClick={onCancel}>
          <span>Cancel</span>
        </button>
      </div>
    </section>
  );
}

function DiscoverResult({ result, onImport, importing, newCount }) {
  return (
    <section className="space-y-3 rounded-xl border border-[color:var(--fd-hair)] bg-zinc-500/[0.03] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[15px] text-zinc-100">
          <span className="font-mono">{newCount}</span> new document{newCount === 1 ? "" : "s"} found
          <span className="px-2 text-zinc-700">·</span>
          <span className="text-[13px] text-zinc-500">
            {result.candidates.length - newCount} already imported
          </span>
        </p>
        <button type="button" onClick={onImport} disabled={importing || newCount === 0}
                className="fdx-button" data-variant="primary" data-size="sm">
          <span>{importing ? "Importing…" : `Import all ${newCount}`}</span>
        </button>
      </div>
      {/* Candidates listed by name and path, not by scan parameters. Selecting
          individual ones needs an API that accepts a subset — the endpoint is
          all-or-nothing today. */}
      <ul className="divide-y divide-[color:var(--fd-hair-2)] overflow-hidden rounded-lg border border-[color:var(--fd-hair-2)]">
        {result.candidates.slice(0, 8).map((c) => (
          <li key={c.path} className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2.5">
            <span className="min-w-0 space-y-0.5">
              <span className="block truncate text-[14px] text-zinc-200">{c.title || c.path.split("/").pop()}</span>
              <span className="block truncate font-mono text-[11px] text-zinc-600">{c.path}</span>
            </span>
            <span className="text-[12px] text-zinc-500">
              {c.already_imported ? "imported" : `${kb(c.bytes)} · new`}
            </span>
          </li>
        ))}
      </ul>
      {result.candidates.length > 8 && (
        <p className="text-[12px] text-zinc-500">
          Showing 8 of {result.candidates.length}. Import brings in all new ones.
        </p>
      )}
    </section>
  );
}

export default function TreasuresView({ onOpenSession, onOpenTreasure }) {
  const [rows, setRows] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [tags, setTags] = useState([]);
  const [error, setError] = useState(null);
  const [live, setLive] = useState(false);

  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [status, setStatus] = useState(null);
  const [language, setLanguage] = useState(null);
  const [tag, setTag] = useState(null);
  const [sort, setSort] = useState("updated");

  const [creating, setCreating] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [discoverResult, setDiscoverResult] = useState(null);
  const [discoverError, setDiscoverError] = useState(null);
  const [importing, setImporting] = useState(false);

  // Typing must not fetch on every character. The filtered list follows the
  // debounced value; the raw value drives the input so it stays responsive.
  useEffect(() => {
    const id = setTimeout(() => setDebounced(query), QUERY_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [query]);

  const filterRef = useRef({ status, language, tag, debounced });
  useEffect(() => {
    filterRef.current = { status, language, tag, debounced };
  }, [status, language, tag, debounced]);

  const loadList = useCallback(async () => {
    const { status: s, language: l, tag: t, debounced: q } = filterRef.current;
    const params = new URLSearchParams({ limit: "200" });
    if (s) params.set("status", s);
    if (l) params.set("language", l);
    if (t) params.set("tag", t);
    if (q) params.set("query", q);
    try {
      setRows((await get(`/api/treasures?${params.toString()}`)).treasures);
      setError(null);
    } catch (e) {
      setError(e.detail || e.message || String(e));
    }
  }, []);

  const loadBaseline = useCallback(async () => {
    try {
      const [all, tagList] = await Promise.all([
        get("/api/treasures?limit=1000"),
        get("/api/treasure-tags"),
      ]);
      setBaseline(all.treasures);
      setTags(tagList.tags || []);
    } catch {
      /* the filtered list carries the error; the summary line can wait */
    }
  }, []);

  const refreshAll = useCallback(() => { loadList(); loadBaseline(); }, [loadList, loadBaseline]);

  useEffect(() => { loadList(); }, [loadList, status, language, tag, debounced]);
  useEffect(() => { loadBaseline(); }, [loadBaseline]);
  useEffect(() => subscribe(refreshAll, setLive), [refreshAll]);
  useEffect(() => {
    const id = setInterval(refreshAll, SAFETY_POLL_MS);
    return () => clearInterval(id);
  }, [refreshAll]);

  const summary = useMemo(() => {
    const list = baseline || [];
    return { total: list.length, published: list.filter((r) => r.status === "published").length };
  }, [baseline]);

  const sorted = useMemo(() => {
    const list = [...(rows || [])];
    if (sort === "title") list.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
    else if (sort === "size") list.sort((a, b) => (b.render_bytes || 0) - (a.render_bytes || 0));
    else list.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
    return list;
  }, [rows, sort]);

  const activeCount = [status, language, tag].filter(Boolean).length;
  const clearAll = () => { setStatus(null); setLanguage(null); setTag(null); };
  const newCount = discoverResult
    ? discoverResult.candidates.filter((c) => !c.already_imported).length
    : 0;

  const runDiscover = async (doImport) => {
    if (doImport) setImporting(true); else setScanning(true);
    setDiscoverError(null);
    try {
      const result = await post(
        `/api/treasures/discover?do_import=${doImport ? "true" : "false"}&max_files=${DISCOVER_MAX_FILES}`);
      setDiscoverResult(result);
      if (doImport) refreshAll();
    } catch (e) {
      setDiscoverError(e.detail || e.message || String(e));
    } finally {
      if (doImport) setImporting(false); else setScanning(false);
    }
  };

  if (error && rows === null) return <ErrorPanel message={error} onRetry={refreshAll} />;
  if (rows === null) return <Skeleton />;

  return (
    <div className="space-y-5">
      {error && (
        <p className="rounded-xl border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
          Could not reach the API ({error}). Showing the last good data; retrying on the next update.
        </p>
      )}

      <LibraryHeader
        total={summary.total}
        published={summary.published}
        live={live}
        onRefresh={refreshAll}
        onNew={() => setCreating((v) => !v)}
        creating={creating}
        onScan={() => runDiscover(false)}
        scanning={scanning}
      />

      <LibrarySearch
        query={query} onQuery={setQuery}
        status={status} onStatus={setStatus}
        language={language} onLanguage={setLanguage}
        tag={tag} onTag={setTag} tags={tags}
        sort={sort} onSort={setSort}
        activeCount={activeCount} onClearAll={clearAll}
      />

      {creating && (
        <CreatePanel
          onCancel={() => setCreating(false)}
          onCreated={(row) => { setCreating(false); refreshAll(); onOpenTreasure?.(row.id); }}
        />
      )}

      {discoverError && (
        <p className="rounded-xl border border-rose-500/30 bg-rose-500/5 px-4 py-2.5 text-[13px] text-rose-300">
          Scan failed: {discoverError}
        </p>
      )}
      {discoverResult && (
        <DiscoverResult result={discoverResult} newCount={newCount}
                        importing={importing} onImport={() => runDiscover(true)} />
      )}

      <section className="overflow-hidden rounded-xl border border-[color:var(--fd-hair-2)]">
        <div className="hidden grid-cols-[minmax(0,1fr)_150px_190px_28px] gap-3 border-b border-[color:var(--fd-hair-2)] px-5 py-2.5 text-[12px] font-semibold tracking-[0.04em] text-zinc-500 md:grid">
          <span>Title / source</span>
          <span>Status</span>
          <span>Updated</span>
          <span />
        </div>

        {sorted.length === 0 ? (
          <EmptyState filtered={activeCount > 0 || debounced.length > 0} onClear={clearAll} />
        ) : (
          <>
            <div className="hidden md:block">
              {sorted.map((r) => (
                <TreasureListRow key={r.id} row={r} onOpen={onOpenTreasure} />
              ))}
            </div>
            <div className="md:hidden">
              {sorted.map((r) => (
                <TreasureMobileCard key={r.id} row={r} onOpen={onOpenTreasure} />
              ))}
            </div>
          </>
        )}

        {sorted.length > 0 && (
          <div className="flex items-center justify-between gap-3 border-t border-[color:var(--fd-hair-2)] px-5 py-3">
            <span className="font-mono text-[12px] text-zinc-500">
              {sorted.length} of {summary.total}
            </span>
            {onOpenSession && (
              <span className="text-[12px] text-zinc-600">Open a row to see its provenance</span>
            )}
          </div>
        )}
      </section>

      <p className="text-[12px] leading-relaxed text-zinc-500">
        Markdown stays the source of truth — editing writes a new version rather than mutating the
        rendered HTML. Published copies are read-only here; claude.ai has no update API.
      </p>
    </div>
  );
}
