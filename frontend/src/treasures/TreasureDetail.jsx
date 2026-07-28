import React, { useEffect, useRef, useState } from "react";
import { Editor, rootCtx, defaultValueCtx } from "@milkdown/core";
import { commonmark } from "@milkdown/preset-commonmark";
import { gfm } from "@milkdown/preset-gfm";
import { getMarkdown } from "@milkdown/utils";
import { Milkdown, MilkdownProvider, useEditor } from "@milkdown/react";
// Structural editor mechanics only (selection and editable-node handling).
// The artifact skin below restores rendered-document whitespace rules; no
// Milkdown theme is imported because it would bring a second visual system.
import "@milkdown/prose/view/style/prosemirror.css";
// The same two fonts Treasures' own tokens.css can embed into an artifact
// (backend/flightdeck/treasures/templates/fonts/*.woff2), loaded here too so
// the Edit tab can render the source in the artifact's real typeface instead
// of approximating it — true WYSIWYG, not "close enough".
//
// Import the FULL faces, never a single subset. fontsource's `vietnamese-*.css`
// files declare `font-family:'JetBrains Mono'` with NO `unicode-range`, so that
// face claims the whole family while its file only carries Vietnamese glyphs:
// every Latin character then falls back to the system monospace. Measured, the
// bug was invisible-but-real — ASCII advance was 11.12px in the editor vs
// 9.895px in the artifact, and because the `0` glyph was missing the CSS `ch`
// unit degraded to its 0.5em spec fallback, making the 68ch prose measure
// 561px instead of 672.85px. `400.css`/`700.css` declare all six subsets with
// proper unicode-ranges, so the browser still downloads only what the text needs.
import "@fontsource-variable/space-grotesk/wght.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/700.css";

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

// vscode's registered protocol handler opens a local file straight from a
// browser link — no server round-trip, works because these paths are on the
// same machine the browser (and VS Code) run on.
function vscodeUri(path) {
  return path ? `vscode://file${path}` : undefined;
}

function FilePathLink({ path, label }) {
  if (!path) return null;
  return (
    <a
      href={vscodeUri(path)}
      title={`Open in VS Code: ${path}`}
      className="inline-flex items-center gap-1 font-mono text-[10px] text-zinc-500 transition-colors hover:text-emerald-300"
    >
      ↗ {label}
    </a>
  );
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

// Mirrors render.FONTS in the backend (backend/flightdeck/treasures/render.py)
// — keep the two lists in sync by hand, there is no shared source yet.
const FONT_OPTIONS = [
  { value: "space-grotesk", label: "Space Grotesk" },
  { value: "jetbrains-mono", label: "JetBrains Mono" },
  { value: "default", label: "Default (system)" },
];
// The Edit pane's own font-family, so it matches whichever a treasure has
// picked instead of always showing Space Grotesk.
const FONT_STACK = {
  "space-grotesk": "'Space Grotesk Variable', system-ui, sans-serif",
  "jetbrains-mono": "'JetBrains Mono', ui-monospace, monospace",
  "default": "system-ui, -apple-system, 'Segoe UI', sans-serif",
};

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

// PATCH is metadata-only (matches service.update_meta) — the caller must
// follow with rerenderTreasure for the change to reach artifact.html on disk.
async function patchTreasure(id, body) {
  const path = `/api/treasures/${encodeURIComponent(id)}`;
  const r = await fetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail || `${path}: ${r.status}`);
  }
  return r.json();
}

async function rerenderTreasure(id) {
  const path = `/api/treasures/${encodeURIComponent(id)}/rerender`;
  const r = await fetch(path, { method: "POST" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

/* ---- Milkdown WYSIWYG pane (markdown sources only) ------------------------
 * Deliberately skips importing a Milkdown theme package (e.g. theme-nord):
 * its bundled CSS ships its own light-leaning defaults (`prefers-color-scheme`
 * only, not our app's manual Night/Day toggle) and would fight our tokens.
 *
 * Do NOT put `.md` on this mount. That class is the independently-designed
 * FlightDeck transcript theme used by SessionDetail, so borrowing it makes a
 * second, drifting source of truth for type scale and spacing. The skin below
 * mirrors tokens.css for this editor alone; `prosemirror.css` remains only for
 * editor mechanics (whitespace handling and selected-node affordances).
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

// Scoped so it never leaks onto the `.md` transcript renderer in SessionDetail.
// Keep this in lockstep with backend/.../templates/tokens.css: both contain the
// artifact's complete prose rules rather than a handful of corrective overrides.
// Milkdown inserts `.milkdown > .ProseMirror`, hence the content-root selector.
const ARTIFACT_SKIN_CSS = `
.treasure-editor-viewport { background: #f5f8fb; container-type: inline-size; }
.treasure-artifact-skin {
  box-sizing: border-box;
  min-height: 100%;
  max-width: min(1200px, 80cqw);
  margin: 0 auto;
  padding: clamp(2rem, 5cqw, 4rem) clamp(1.1rem, 4cqw, 2.6rem) 6rem;
  background: #f5f8fb;
  color: #0d1a29;
  font-size: 16.5px;
  font-weight: 400;
  line-height: 1.62;
  -webkit-font-smoothing: antialiased;
}
/* Milkdown's structural stylesheet enables editor-oriented break-spaces and
 * disables ligatures. Restore the artifact defaults for document content;
 * its selected-node outline remains available as an editor-only affordance. */
.treasure-artifact-skin .ProseMirror {
  overflow-wrap: normal;
  word-wrap: normal;
  white-space: normal;
  font-variant-ligatures: normal;
  font-feature-settings: normal;
}
.treasure-artifact-skin .ProseMirror > h1:first-child {
  margin: 0 0 1.4rem;
  font-size: clamp(30px, 4.4cqw, 44px);
  font-weight: 700;
  line-height: 1.16;
  letter-spacing: -0.015em;
}
.treasure-artifact-skin h1, .treasure-artifact-skin h2,
.treasure-artifact-skin h3, .treasure-artifact-skin h4 {
  font-weight: 700;
  line-height: 1.25;
  color: #0d1a29;
}
/* Tailwind's preflight removes the browser defaults which tokens.css retains
 * for non-leading h1s, so restore them inside this isolated skin. */
.treasure-artifact-skin h1 { font-size: 2em; margin: .67em 0; }
.treasure-artifact-skin h2 {
  margin: 3rem 0 1rem;
  padding-bottom: .45rem;
  font-size: 25px;
  border-bottom: 1px solid #d9e2ec;
}
.treasure-artifact-skin h3 { margin: 2.2rem 0 .7rem; font-size: 19.5px; }
.treasure-artifact-skin h4 { margin: 1.8rem 0 .5rem; font-size: 17px; color: #334860; }
.treasure-artifact-skin h2 + h3 { margin-top: 1.2rem; }
.treasure-artifact-skin p, .treasure-artifact-skin ul,
.treasure-artifact-skin ol, .treasure-artifact-skin blockquote,
.treasure-artifact-skin pre, .treasure-artifact-skin table,
.treasure-artifact-skin figure { margin: 0 0 1.15rem; }
.treasure-artifact-skin .ProseMirror > p,
.treasure-artifact-skin .ProseMirror > ul,
.treasure-artifact-skin .ProseMirror > ol,
.treasure-artifact-skin .ProseMirror > blockquote { max-width: 68ch; }
.treasure-artifact-skin ul, .treasure-artifact-skin ol { padding-left: 1.4rem; }
.treasure-artifact-skin li { margin: .3rem 0; }
.treasure-artifact-skin li > ul, .treasure-artifact-skin li > ol { margin: .3rem 0; }
.treasure-artifact-skin strong { font-weight: 700; }
.treasure-artifact-skin hr { height: 1px; margin: 2.5rem 0; border: 0; background: #d9e2ec; }
.treasure-artifact-skin a { color: #1668e3; text-decoration-color: rgba(22,104,227,.35); }
.treasure-artifact-skin a:hover { color: #0d4fb8; text-decoration-color: currentColor; }
.treasure-artifact-skin code, .treasure-artifact-skin kbd,
.treasure-artifact-skin samp, .treasure-artifact-skin pre {
  font-family: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, monospace;
}
.treasure-artifact-skin code {
  padding: .12em .38em;
  font-size: .88em;
  background: #eef3f8;
  border: 1px solid #d9e2ec;
  border-radius: 6px;
}
.treasure-artifact-skin pre {
  overflow-x: auto;
  white-space: pre;
  padding: 1rem 1.1rem;
  font-size: 13.5px;
  line-height: 1.55;
  background: #ffffff;
  border: 1px solid #d9e2ec;
  border-radius: 14px;
}
.treasure-artifact-skin pre code {
  padding: 0;
  font-size: inherit;
  background: none;
  border: 0;
}
.treasure-artifact-skin blockquote {
  padding: .1rem 0 .1rem 1.1rem;
  color: #334860;
  font-weight: 600;
  border-left: 3px solid #1668e3;
}
.treasure-artifact-skin blockquote > :last-child { margin-bottom: 0; }
.treasure-artifact-skin table {
  width: 100%;
  overflow: hidden;
  font-size: 14.5px;
  background: #ffffff;
  border: 1px solid #d9e2ec;
  border-collapse: collapse;
  border-radius: 14px;
}
.treasure-artifact-skin th, .treasure-artifact-skin td {
  padding: .55rem .7rem;
  overflow-wrap: anywhere;
  text-align: left;
  vertical-align: top;
  border-bottom: 1px solid #d9e2ec;
}
.treasure-artifact-skin th { font-size: 15px; font-weight: 700; background: #eef3f8; }
.treasure-artifact-skin tbody tr:last-child td { border-bottom: 0; }
.treasure-artifact-skin img, .treasure-artifact-skin svg,
.treasure-artifact-skin video { max-width: 100%; height: auto; }
.treasure-artifact-skin figure { margin-inline: 0; }
.treasure-artifact-skin figcaption { margin-top: .4rem; font-size: 13px; color: #334860; }

/* --- components (docs/treasures-components.md) ---------------------------
 * Milkdown keeps a component's tags as non-editable inline html chips rather
 * than nesting the content inside them, so the editor cannot show a real
 * hero/card box. What it CAN match is the inline badge, plus the hero accent's
 * <em>. Everything else stays plain here on purpose: pretending to render a
 * card the editor does not actually nest would be worse than showing none. */
.treasure-artifact-skin [data-component="badge"] {
  display: inline-block; font-size: 12.5px; font-weight: 700; line-height: 1.5;
  border-radius: 999px; padding: 3px 12px; white-space: nowrap;
  background: #eef3f8; color: #334860; border: 1px solid #d9e2ec;
}
.treasure-artifact-skin [data-component="badge"][data-tone="good"] { color: #0b6b4a; background: #dcf3e8; border-color: #a7dfc6; }
.treasure-artifact-skin [data-component="badge"][data-tone="mid"]  { color: #7a5800; background: #fbf0cf; border-color: #ecd9a0; }
.treasure-artifact-skin [data-component="badge"][data-tone="weak"] { color: #9c2f1f; background: #fbe4de; border-color: #f0bfb4; }
/* The html chips Milkdown renders for a component's open/close tag: make them
   read as markers rather than as body copy the author might try to edit. */
.treasure-artifact-skin [data-type="html"] {
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 11.5px; color: #7a5800;
  background: #fbf0cf; border: 1px solid #ecd9a0; border-radius: 6px;
  padding: 1px 6px;
}
`;

function MarkdownEditor({ defaultValue, apiRef, onReady, font }) {
  return (
    <div className="treasure-editor-viewport h-[55vh] w-full overflow-auto rounded-lg border border-[color:var(--fd-hair-2)] focus-within:border-emerald-500/40">
      <div
        className="treasure-artifact-skin"
        style={{ fontFamily: FONT_STACK[font] || FONT_STACK["space-grotesk"] }}
      >
        <style>{ARTIFACT_SKIN_CSS}</style>
        <MilkdownProvider>
          <MilkdownPane defaultValue={defaultValue} apiRef={apiRef} onReady={onReady} />
        </MilkdownProvider>
      </div>
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
  const [fontSaving, setFontSaving] = useState(false);
  const [fontError, setFontError] = useState(null);
  const [headOpen, setHeadOpen] = useState(false);
  const [headDraft, setHeadDraft] = useState("");
  const [headSaving, setHeadSaving] = useState(false);
  const [headError, setHeadError] = useState(null);
  const [headSaved, setHeadSaved] = useState(false);

  // font/custom_head are render inputs (service.update_meta doesn't touch
  // artifact.html), so every change here is PATCH-then-rerender, then a
  // fresh GET so the header (render_bytes/updated_at) and the iframe agree.
  const applyRenderInput = async (patch) => {
    await patchTreasure(id, patch);
    await rerenderTreasure(id);
    const fresh = await fetchTreasure(id);
    setDetail(fresh);
    setPreviewNonce((n) => n + 1);
  };

  const changeFont = async (value) => {
    setFontSaving(true);
    setFontError(null);
    try {
      await applyRenderInput({ font: value });
    } catch (e) {
      setFontError(String(e.message || e));
    } finally {
      setFontSaving(false);
    }
  };

  const saveCustomHead = async () => {
    setHeadSaving(true);
    setHeadError(null);
    setHeadSaved(false);
    try {
      await applyRenderInput({ custom_head: headDraft });
      setHeadSaved(true);
    } catch (e) {
      setHeadError(String(e.message || e));
    } finally {
      setHeadSaving(false);
    }
  };

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
        setHeadDraft(d.custom_head || "");
        setHeadSaved(false);
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
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
          <FilePathLink path={detail.source_path} label="source.md" />
          <FilePathLink path={detail.artifact_path} label="artifact.html" />
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <label className="font-mono text-[10px] uppercase tracking-wide text-zinc-500">Font</label>
          <select
            value={detail.font || "space-grotesk"}
            disabled={fontSaving || published}
            onChange={(e) => changeFont(e.target.value)}
            title={published ? "Published artifacts are read-only from the dashboard" : "Change the body font and rerender"}
            className="rounded-md border border-[color:var(--fd-hair-2)] bg-zinc-950/40 px-2 py-0.5 font-mono text-[10px] text-zinc-300 disabled:opacity-50"
          >
            {FONT_OPTIONS.map((f) => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </select>
          {fontSaving && <span className="font-mono text-[10px] text-zinc-500">rerendering…</span>}
          {fontError && <span className="font-mono text-[10px] text-rose-400">{fontError}</span>}
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
              font={detail.font || "space-grotesk"}
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

        {!published && tab === "edit" && (
          <div className="mt-5 border-t border-[color:var(--fd-hair-2)] pt-4">
            <button
              type="button"
              onClick={() => setHeadOpen((o) => !o)}
              className="font-mono text-[10px] uppercase tracking-wide text-zinc-500 hover:text-zinc-300"
            >
              {headOpen ? "▾" : "▸"} Custom &lt;head&gt;
            </button>
            {headOpen && (
              <div className="mt-2">
                <p className="mb-2 text-[10px] text-zinc-500">
                  Raw HTML spliced in right before &lt;/head&gt; — extra meta/style/link tags. Not
                  escaped; only paste HTML you trust.
                </p>
                <textarea
                  value={headDraft}
                  onChange={(e) => { setHeadDraft(e.target.value); setHeadSaved(false); }}
                  spellCheck={false}
                  placeholder='<meta name="robots" content="noindex">'
                  className="h-24 w-full rounded-lg border border-[color:var(--fd-hair-2)] bg-zinc-950/40 p-3 font-mono text-[12px] leading-relaxed text-zinc-200 focus:border-emerald-500/40 focus:outline-none"
                />
                <div className="mt-2 flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    onClick={saveCustomHead}
                    disabled={headSaving}
                    className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 font-mono text-[10px] uppercase tracking-wide text-emerald-300 transition-colors hover:bg-emerald-500/15 disabled:pointer-events-none disabled:opacity-50"
                  >
                    {headSaving ? "Saving…" : "Save & rerender"}
                  </button>
                  {headSaved && <span className="font-mono text-[10px] text-emerald-400">saved, preview reloaded</span>}
                  {headError && <span className="font-mono text-[10px] text-rose-400">{headError}</span>}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
