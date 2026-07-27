import React, { useEffect, useRef, useState } from "react";
import { Editor, rootCtx, defaultValueCtx } from "@milkdown/core";
import { commonmark } from "@milkdown/preset-commonmark";
import { gfm } from "@milkdown/preset-gfm";
import { getMarkdown } from "@milkdown/utils";
import { Milkdown, MilkdownProvider, useEditor } from "@milkdown/react";
// Structural editor reset only (whitespace handling, selected-node outline) —
// no colors, so it is safe under both Night and Day. See the design note
// above MarkdownEditor for why we don't also pull in a Milkdown theme.
import "@milkdown/prose/view/style/prosemirror.css";

/* ---- Treasure detail: its own #/treasure/<id> page ----------------------
 * Was the bottom half of TreasuresView's combined list+detail pane; moved to
 * a real route (App.jsx `route.name === "treasure"`) so a treasure gets a
 * shareable, back-button-friendly URL instead of scroll position in a list.
 *
 * Fetches its own GET /api/treasures/{id}?include_source=true. The editor is
 * Milkdown (WYSIWYG over markdown) for markdown sources; an HTML-fragment
 * source keeps the plain textarea, and so does a markdown source if this
 * component is ever loaded in an environment where Milkdown didn't install.
 *
 * Small presentational atoms (StatusBadge/LanguageBadge/relTime/kb) are
 * intentionally duplicated from TreasuresView.jsx rather than imported —
 * list and detail are now separate routes/files and shouldn't reach into
 * each other's internals.
 */

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

function ProvenanceLink({ originId, onOpenSession }) {
  if (!originId) return null;
  return (
    <button
      type="button"
      onClick={() => onOpenSession?.(originId)}
      title={`Open originating session ${originId}`}
      className="rounded-md border border-[color:var(--fd-hair-2)] px-2 py-0.5 font-mono text-[9px] uppercase tracking-wide text-zinc-400 transition-colors hover:border-emerald-500/40 hover:text-emerald-300"
    >
      → session
    </button>
  );
}

/* ---- raw fetch helpers ----------------------------------------------------
 * api.js's `get`/`post` throw a generic Error on !ok without exposing the
 * status code, so a 404 (treasure not found / stale link) can't be told apart
 * from any other failure. These two mirror TreasuresView's `putSource` style
 * (plain fetch, throw on !ok) but `fetchTreasure` also tags 404s.
 */
async function fetchTreasure(id) {
  const path = `/api/treasures/${encodeURIComponent(id)}?include_source=true`;
  const r = await fetch(path);
  if (r.status === 404) {
    const e = new Error("treasure not found");
    e.status = 404;
    throw e;
  }
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

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

/* ---- Milkdown WYSIWYG pane (markdown sources only) ------------------------
 * Deliberately skips importing a Milkdown theme package (e.g. theme-nord):
 * its bundled CSS ships its own light-leaning defaults (`prefers-color-scheme`
 * only, not our app's manual Night/Day toggle) and would fight our tokens.
 * Instead the mount point wears `.md` — the same theme-aware markdown-prose
 * class SessionDetail's renderer uses (src/index.css) — so headings, lists,
 * code, blockquotes and tables already read correctly in both themes without
 * any new global CSS. Only `prosemirror.css` is imported above, for
 * structural behavior (whitespace handling), not color.
 */
function MilkdownPane({ defaultValue, apiRef, onReady }) {
  const { get, loading } = useEditor(
    (root) =>
      Editor.make()
        .config((ctx) => {
          ctx.set(rootCtx, root);
          ctx.set(defaultValueCtx, defaultValue);
        })
        .use(commonmark)
        .use(gfm),
    []
  );
  useEffect(() => {
    if (loading) return;
    apiRef.current = () => get()?.action(getMarkdown()) ?? "";
    onReady?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading]);
  return <Milkdown />;
}

function MarkdownEditor({ defaultValue, apiRef, onReady }) {
  return (
    <div className="md h-[55vh] w-full overflow-auto rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-950/40 p-4 text-[13px] leading-relaxed text-zinc-200 focus-within:border-emerald-500/40">
      <MilkdownProvider>
        <MilkdownPane defaultValue={defaultValue} apiRef={apiRef} onReady={onReady} />
      </MilkdownProvider>
    </div>
  );
}

function SaveRow({ onSave, saving, disabled, savedVersion, saveError }) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={onSave}
        disabled={disabled}
        className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide text-emerald-300 transition-colors hover:bg-emerald-500/15 disabled:pointer-events-none disabled:opacity-50"
      >
        {saving ? "Saving…" : "Save"}
      </button>
      {savedVersion != null && (
        <span className="font-mono text-[10px] text-emerald-400">
          saved → v{savedVersion}, preview reloaded
        </span>
      )}
      {saveError && <span className="font-mono text-[10px] text-rose-400">{saveError}</span>}
    </div>
  );
}

/* ---- page --------------------------------------------------------------- */
export default function TreasureDetail({ id, onBack, onOpenSession }) {
  const [detail, setDetail] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [detailError, setDetailError] = useState(null);
  const [tab, setTab] = useState("preview"); // preview | source | edit
  const [draft, setDraft] = useState(""); // textarea fallback (html source, or Milkdown unready)
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [savedVersion, setSavedVersion] = useState(null);
  const [previewNonce, setPreviewNonce] = useState(0);
  const [milkdownReady, setMilkdownReady] = useState(false);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  // Permanent delete. Armed by a first click (see the back-nav), and the API
  // still requires ?confirm=true, so both layers must agree before anything on
  // disk is touched. On success we leave for the list — the page's subject no
  // longer exists.
  const doDelete = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      const res = await fetch(
        `/api/treasures/${encodeURIComponent(id)}?confirm=true`,
        { method: "DELETE" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `delete failed: ${res.status}`);
      }
      onBack?.();
    } catch (e) {
      setDeleteError(String(e.message || e));
      setDeleteArmed(false);
    } finally {
      setDeleting(false);
    }
  };
  const apiRef = useRef(null); // () => current markdown, set once Milkdown reports ready

  useEffect(() => {
    let live = true;
    setDetail(null);
    setNotFound(false);
    setDetailError(null);
    setDraft("");
    setSavedVersion(null);
    setSaveError(null);
    setTab("preview");
    setMilkdownReady(false);
    apiRef.current = null;
    fetchTreasure(id)
      .then((d) => {
        if (!live) return;
        setDetail(d);
        setDraft(d.source || "");
      })
      .catch((e) => {
        if (!live) return;
        if (e.status === 404) setNotFound(true);
        else setDetailError(String(e.message || e));
      });
    return () => { live = false; };
  }, [id]);

  const published = detail?.status === "published";
  const isMarkdown = detail?.source_format === "markdown";
  const version = detail?.version;

  const save = async () => {
    if (saving || published) return;
    const content = isMarkdown ? (apiRef.current ? apiRef.current() : draft) : draft;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await putSource(id, content);
      setDetail((d) => (d ? { ...d, ...updated, source: content } : d));
      setSavedVersion(updated.version);
      // Force the preview iframe to refetch /raw rather than trusting a cache.
      setPreviewNonce((n) => n + 1);
    } catch (e) {
      setSaveError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  // Sticky back-nav, same treatment as SessionDetail's "← Logbook" strip:
  // a negative margin + top padding lets it sit flush against the Shell's own
  // padding while staying pinned as the page scrolls.
  const backNav = (
    <div className="sticky top-0 z-20 -mt-6 bg-zinc-950/95 pt-6 backdrop-blur-xl md:-mt-8 md:pt-8">
      <div className="mb-4 flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1.5 text-sm text-zinc-400 transition-colors hover:text-emerald-400"
        >
          ← Treasures
        </button>
        {/* Two-step delete: the first click only arms it, so a stray click can
            never destroy an artifact. The API is fail-closed too (it needs
            ?confirm=true), and the server refuses any path outside the
            filestore. Archiving is offered as the non-destructive option. */}
        {detail && (
          <div className="flex items-center gap-2">
            {deleteArmed && (
              <span className="font-mono text-[10px] text-rose-300">
                deletes files + index row — permanent
              </span>
            )}
            {deleteArmed && (
              <button
                type="button"
                onClick={() => setDeleteArmed(false)}
                className="rounded-lg border border-[color:var(--fd-hair-2)] px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-zinc-400 hover:bg-zinc-500/10"
              >
                Cancel
              </button>
            )}
            <button
              type="button"
              disabled={deleting}
              onClick={() => (deleteArmed ? doDelete() : setDeleteArmed(true))}
              title={deleteArmed
                ? "Permanently delete this artifact"
                : "Delete permanently (asks for confirmation)"}
              className={`rounded-lg border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide transition-colors disabled:opacity-50 ${
                deleteArmed
                  ? "border-rose-500/50 bg-rose-500/15 text-rose-300 hover:bg-rose-500/25"
                  : "border-[color:var(--fd-hair-2)] text-zinc-500 hover:text-rose-300"
              }`}
            >
              {deleting ? "Deleting…" : deleteArmed ? "Confirm delete" : "Delete"}
            </button>
          </div>
        )}
      </div>
      {deleteError && (
        <div className="mb-3 rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 font-mono text-[11px] text-rose-300">
          {deleteError}
        </div>
      )}
    </div>
  );

  if (notFound) {
    return (
      <div className="w-full">
        {backNav}
        <div className="fd-shell">
          <div className="fd-core p-6 text-sm">
            <div className="font-mono text-[11px] uppercase tracking-wide text-zinc-500">Not found</div>
            <p className="mt-2 text-zinc-400">
              No treasure with id <span className="font-mono text-zinc-300">{id}</span>. It may have
              been removed, or the link is stale.
            </p>
            <button
              type="button"
              onClick={onBack}
              className="mt-4 rounded-lg border border-[color:var(--fd-hair-2)] px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide text-zinc-300 hover:bg-zinc-500/5"
            >
              ← Back to Treasures
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (detailError) {
    return (
      <div className="w-full">
        {backNav}
        <div className="fd-shell">
          <div className="fd-core p-6 text-sm">
            <div className="font-mono text-[11px] uppercase tracking-wide text-rose-400">Failed to load</div>
            <p className="mt-2 text-zinc-400">{detailError}</p>
            <button
              type="button"
              onClick={onBack}
              className="mt-4 rounded-lg border border-[color:var(--fd-hair-2)] px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide text-zinc-300 hover:bg-zinc-500/5"
            >
              ← Back to Treasures
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="w-full">
        {backNav}
        <div className="fd-shell">
          <div className="fd-core p-6">
            <div className="h-40 animate-pulse rounded-lg bg-zinc-800/40" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full">
      {backNav}

      <header className="border-b border-[color:var(--fd-hair-2)] pb-5">
        <h1 className="text-xl font-semibold tracking-tight text-zinc-100">{detail.title}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-zinc-500">
          <span className="font-mono text-zinc-400">{detail.slug}</span>
          <span className="text-zinc-700">·</span>
          <span>{detail.kind}</span>
          <span className="text-zinc-700">·</span>
          <StatusBadge status={detail.status} />
          <span className="text-zinc-700">·</span>
          <LanguageBadge language={detail.language} />
          <span className="text-zinc-700">·</span>
          <span className="font-mono">v{version}</span>
          <span className="text-zinc-700">·</span>
          <span className="font-mono">{kb(detail.render_bytes)}</span>
          <span className="text-zinc-700">·</span>
          <span title={detail.updated_at}>{relTime(detail.updated_at)}</span>
          {detail.origin_id && (
            <>
              <span className="text-zinc-700">·</span>
              <ProvenanceLink originId={detail.origin_id} onOpenSession={onOpenSession} />
            </>
          )}
        </div>
        {detail.published_url && (
          <p className="mt-2.5 text-[11px] text-amber-300/90">
            Published —{" "}
            <a
              href={detail.published_url}
              target="_blank"
              rel="noreferrer"
              className="underline decoration-dotted underline-offset-2 hover:text-amber-200"
            >
              {detail.published_url}
            </a>
            <span className="text-amber-300/60"> · the published copy can't be updated from here — claude.ai has no update API.</span>
          </p>
        )}
      </header>

      <div className="mt-4 flex items-center gap-2">
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
      </div>

      <div className="mt-4">
        {tab === "preview" && (
          <div>
            <p className="mb-2 text-[10px] text-zinc-500">
              Rendered in an isolated sandbox — bare <span className="font-mono">sandbox=""</span> forces
              an opaque origin and blocks scripts.
            </p>
            <iframe
              key={`${id}-${version}-${previewNonce}`}
              src={`/api/treasures/${encodeURIComponent(id)}/raw`}
              sandbox=""
              className="h-[78vh] w-full rounded-lg border border-[color:var(--fd-hair-2)] bg-white"
              title={detail.title}
            />
          </div>
        )}

        {tab === "source" && (
          <pre className="max-h-[78vh] overflow-auto rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-950/40 p-4 font-mono text-[11px] leading-relaxed text-zinc-300">
            {detail.source}
          </pre>
        )}

        {tab === "edit" && published && (
          <p className="text-[11px] text-zinc-500">
            Published artifacts are read-only from the dashboard — claude.ai has no update API.
          </p>
        )}

        {/* The edit surface stays MOUNTED (hidden via CSS, not unmounted) once
           the source is loaded, regardless of which tab is active. Milkdown
           owns its document internally — remounting it on every tab switch
           would discard whatever the user had typed. */}
        {!published && isMarkdown && (
          <div className={tab === "edit" ? "" : "hidden"}>
            <MarkdownEditor
              defaultValue={detail.source}
              apiRef={apiRef}
              onReady={() => setMilkdownReady(true)}
            />
            <SaveRow
              onSave={save}
              saving={saving}
              disabled={saving || !milkdownReady}
              savedVersion={savedVersion}
              saveError={saveError}
            />
          </div>
        )}
        {!published && !isMarkdown && (
          <div className={tab === "edit" ? "" : "hidden"}>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              className="h-[55vh] w-full rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-950/40 p-3 font-mono text-[12px] leading-relaxed text-zinc-200 focus:border-emerald-500/40 focus:outline-none"
            />
            <SaveRow
              onSave={save}
              saving={saving}
              disabled={saving}
              savedVersion={savedVersion}
              saveError={saveError}
            />
          </div>
        )}
      </div>
    </div>
  );
}
