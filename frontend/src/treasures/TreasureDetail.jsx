import React, { useEffect, useRef, useState } from "react";
import { subscribe } from "../api.js";
import CmsPanel from "./detail/CmsPanel.jsx";
import DetailHeader from "./detail/DetailHeader.jsx";
import { IconBack } from "../ui/icons.jsx";
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
 * This file is now the page's STATE and data access only. Everything that draws
 * chrome moved out: `detail/DetailHeader.jsx` (one compact bar) and
 * `detail/CmsPanel.jsx` (the document's properties, collapsible). The old build
 * spent about a third of the viewport on stacked header rows before the preview —
 * the thing the page exists to show — began.
 *
 * The metadata atoms that used to live here went with them, and the duplicated
 * `relTime`/`kb` are now one implementation in `../format.js`; the two copies had
 * drifted to disagree about unparseable dates.
 */

// The Edit pane's own font-family, so it matches whichever a treasure has
// picked instead of always showing Space Grotesk.
const FONT_STACK = {
  "space-grotesk": "'Space Grotesk Variable', system-ui, sans-serif",
  "jetbrains-mono": "'JetBrains Mono', ui-monospace, monospace",
  "default": "system-ui, -apple-system, 'Segoe UI', sans-serif",
};

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

// Re-reads the artifact's ORIGIN document into a new version. Different verb
// from rerender, which re-runs the pipeline over the already-stored source.
async function refreshFromOrigin(id) {
  const path = `/api/treasures/${encodeURIComponent(id)}/refresh`;
  const r = await fetch(path, { method: "POST" });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `${path}: ${r.status}`);
  }
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
  box-shadow: 0 20px 50px -30px rgba(22, 104, 227, .18);
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

/* Styles the artifact applies with no syntax at all, mirrored so the editor
   shows them too — Milkdown emits real h2/p/ol nodes, so these all land. */
.treasure-artifact-skin .ProseMirror > h2 + p {
  font-size: 17px; font-weight: 500; color: #334860; max-width: 78ch; margin-bottom: 1.6rem;
}
.treasure-artifact-skin .ProseMirror > ol { list-style: none; counter-reset: fd-step; padding-left: 0; }
.treasure-artifact-skin .ProseMirror > ol > li {
  counter-increment: fd-step; position: relative; padding-left: 44px; margin: .9rem 0;
}
.treasure-artifact-skin .ProseMirror > ol > li::before {
  content: counter(fd-step); position: absolute; left: 0; top: 1px;
  width: 28px; height: 28px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 13.5px; font-weight: 700; background: #e3edf9; color: #0d4fb8;
}

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
    <div className="treasure-editor-viewport h-[55vh] w-full overflow-auto rounded-lg border border-[color:var(--fdx-rule)] focus-within:border-emerald-500/40">
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

function EditModeToggle({ mode, onChange }) {
  const label = { wysiwyg: "wysiwyg", raw: "raw markdown" };
  return (
    <div className="mb-2 flex items-center gap-2">
      {["wysiwyg", "raw"].map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => onChange(m)}
          title={m === "raw"
            ? "Plain textarea — pasted markdown is stored byte-for-byte"
            : "Rich editor — a paste is reparsed through the editor's schema"}
          className={`rounded-md px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide transition-colors ${
            mode === m ? "bg-emerald-500/15 text-emerald-400"
                       : "text-zinc-500 hover:bg-zinc-500/5"
          }`}
        >
          {label[m]}
        </button>
      ))}
      {mode === "raw" && (
        <span className="font-mono text-[10px] text-zinc-500">
          nothing is reparsed — safe for pasting a whole document
        </span>
      )}
    </div>
  );
}

/* The Save BUTTON lives in the header — it is the view's one primary action, and
   the design puts that at the top right. What stays here is the outcome, next to
   the text it describes. */
function SaveStatus({ savedVersion, saveError }) {
  if (savedVersion == null && !saveError) return null;
  return (
    <div className="mt-2 text-[12px]">
      {savedVersion != null && (
        <span className="text-emerald-400">Saved → v{savedVersion}, preview reloaded.</span>
      )}
      {saveError && <span className="text-rose-400">{saveError}</span>}
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
  const [updating, setUpdating] = useState(false);
  const [updateError, setUpdateError] = useState(null);
  const [statusSaving, setStatusSaving] = useState(false);
  const [statusError, setStatusError] = useState(null);
  // Panel chrome. `openSection` force-opens ONE section without collapsing what
  // the user already had open — it is how "Source changed" in the header lands on
  // the section that can act on it.
  // Collapsed by default: the page is opened to READ the document, and the
  // properties are consulted only when there is something to change. It collapses
  // to a rail, so nothing about the panel becomes unfindable.
  const [panelOpen, setPanelOpen] = useState(false);
  const [openSection, setOpenSection] = useState(null);
  // Which version the PREVIEW is showing; null means the current one.
  const [viewingVersion, setViewingVersion] = useState(null);
  // "wysiwyg" | "raw". Raw exists because Milkdown PARSES a paste: dropping a
  // whole markdown document into it goes through the ProseMirror schema, which
  // rewrites anything the schema does not model (our component tags become
  // inline html chips, list bullets get normalised) — fine for editing prose,
  // wrong for replacing a document wholesale. Raw is a plain textarea, so
  // pasted markdown is stored byte-for-byte.
  const [editMode, setEditMode] = useState("wysiwyg");
  // Bumping this remounts Milkdown so it reloads from `draft`. Needed because
  // Milkdown owns its document internally: without a remount, raw edits would be
  // invisible to it and the next WYSIWYG save would silently overwrite them.
  const [milkdownKey, setMilkdownKey] = useState(0);

  const switchEditMode = (next) => {
    if (next === editMode) return;
    if (next === "raw") {
      // Seed the textarea from Milkdown's CURRENT text, not the last-loaded
      // source, or switching would discard whatever was just typed.
      if (apiRef.current) setDraft(apiRef.current());
    } else {
      setMilkdownReady(false);
      setMilkdownKey((k) => k + 1);
    }
    setEditMode(next);
  };

  // Pull the origin document's current state into a new version. The badge that
  // reveals this button comes from `origin_stale`, which the server computes on
  // read rather than storing — a stored flag would be wrong the moment the file
  // changed while the service was down.
  const updateFromOrigin = async () => {
    setUpdating(true);
    setUpdateError(null);
    try {
      await refreshFromOrigin(id);
      const fresh = await fetchTreasure(id);
      setDetail(fresh);
      // The stored source just changed underneath the editor, so reload it —
      // otherwise the next save would write the pre-update text back.
      setDraft(fresh.source || "");
      setMilkdownReady(false);
      setMilkdownKey((k) => k + 1);
      setPreviewNonce((n) => n + 1);
    } catch (e) {
      setUpdateError(String(e.message || e));
    } finally {
      setUpdating(false);
    }
  };

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

  // Status is pure metadata — archiving hides a treasure from the default list
  // view and touches no files, so unlike font/custom_head it needs no rerender.
  const changeStatus = async (status) => {
    setStatusSaving(true);
    setStatusError(null);
    try {
      const updated = await patchTreasure(id, { status });
      setDetail((d) => (d ? { ...d, ...updated } : d));
    } catch (e) {
      setStatusError(String(e.message || e));
    } finally {
      setStatusSaving(false);
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
    // A version number belongs to ONE treasure — carrying it across navigation
    // would preview a v5 that the next artifact may not have.
    setViewingVersion(null);
    setOpenSection(null);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // The server watches this artifact's origin document and pings the SSE stream
  // when it changes; refetching here is what makes the "origin changed" badge
  // appear without a manual reload. Only the row is refetched — never the editor
  // content, which would discard whatever the user is typing.
  useEffect(() => {
    return subscribe(() => {
      fetchTreasure(id)
        .then((d) => setDetail((prev) => (prev ? { ...prev, ...d, source: prev.source } : d)))
        .catch(() => {});
    });
  }, [id]);

  const published = detail?.status === "published";
  const isMarkdown = detail?.source_format === "markdown";
  const version = detail?.version;

  const save = async () => {
    if (saving || published) return;
    // Raw mode is the textarea's own text; WYSIWYG serialises back out of
    // Milkdown. An HTML-fragment source only ever has the textarea.
    const useMilkdown = isMarkdown && editMode === "wysiwyg" && apiRef.current;
    const content = useMilkdown ? apiRef.current() : draft;
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
  // Viewing an old version changes the PREVIEW only. The editor holds the current
  // source, so history stays read-only: there is no restore endpoint, and letting
  // Edit run while a v3 preview was on screen would invite saving v3's bytes over
  // v7 without ever saying so.
  const selectVersion = (v) => {
    setViewingVersion(v);
    if (v != null) setTab("preview");
  };

  const shell = (children) => (
    <div className="w-full">
      <div className="sticky top-0 z-20 -mt-6 border-b border-[color:var(--fdx-rule)] bg-zinc-950/95 pb-3 pt-6 backdrop-blur-xl md:-mt-8 md:pt-8">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex min-h-[36px] items-center gap-1.5 rounded-lg px-2 text-[13px] text-zinc-400 transition-colors hover:bg-zinc-500/5 hover:text-zinc-200"
        >
          <IconBack />
          Treasures
        </button>
      </div>
      <div className="mt-4 rounded-xl border border-[color:var(--fdx-rule)] p-6">{children}</div>
    </div>
  );

  if (notFound) {
    return shell(
      <>
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-500">Not found</p>
        <p className="mt-2 text-[14px] text-zinc-400">
          No treasure with id <span className="font-mono text-zinc-300">{id}</span>. It may have been
          removed, or the link is stale.
        </p>
      </>
    );
  }

  if (detailError) {
    return shell(
      <>
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-rose-400">Failed to load</p>
        <p className="mt-2 text-[14px] text-zinc-400">{detailError}</p>
      </>
    );
  }

  if (!detail) {
    return shell(<div className="h-40 animate-pulse rounded-lg bg-zinc-500/10" />);
  }

  // The header's one primary action, which is whatever the current view can do.
  // Preview and Source can do nothing, so they get no button rather than a
  // disabled one that implies a capability.
  const canEdit = !published && tab === "edit" && viewingVersion == null;
  const headerAction = canEdit ? (
    <button
      type="button"
      onClick={save}
      disabled={saving || (isMarkdown && editMode === "wysiwyg" && !milkdownReady)}
      className="fdx-button"
      data-variant="primary"
      data-size="sm"
    >
      <span>{saving ? "Saving…" : "Save"}</span>
    </button>
  ) : null;

  return (
    <div className="w-full">
      <DetailHeader
        detail={detail}
        tab={tab}
        onTab={setTab}
        published={published}
        viewingVersion={viewingVersion}
        onBack={onBack}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOpen((o) => !o)}
        staleClick={() => { setPanelOpen(true); setOpenSection("source"); }}
        action={headerAction}
      />

      {/* The preview is the dominant region and the panel is a column beside it.
          Below `lg` they stack, panel last — on a narrow screen the document
          matters more than its properties. */}
      <div className="mt-4 flex flex-col gap-4 lg:flex-row">
        <div className="min-w-0 flex-1">
          {viewingVersion != null && (
            <div className="mb-2 flex flex-wrap items-center gap-3 rounded-lg border border-[color:var(--fdx-signal)]/30 bg-[color:var(--fdx-signal)]/[0.07] px-3 py-2">
              <p className="text-[12px] text-zinc-200">
                Previewing <span className="font-mono">v{viewingVersion}</span> — read-only.
                Editing applies to v{detail.version}.
              </p>
              <button
                type="button"
                onClick={() => selectVersion(null)}
                className="text-[12px] font-semibold text-[color:var(--fdx-signal)] hover:underline"
              >
                Back to current
              </button>
            </div>
          )}

          {tab === "preview" && (
            <>
              <iframe
                key={`${id}-${version}-${viewingVersion ?? "cur"}-${previewNonce}`}
                src={`/api/treasures/${encodeURIComponent(id)}/raw${
                  viewingVersion != null ? `?version=${viewingVersion}` : ""
                }`}
                sandbox=""
                className="h-[calc(100vh-13rem)] min-h-[420px] w-full rounded-xl border border-[color:var(--fdx-rule)] bg-white"
                title={detail.title}
              />
              <p className="mt-1.5 text-[11px] text-zinc-600">
                Rendered in an isolated sandbox — a bare <span className="font-mono">sandbox=&quot;&quot;</span>{" "}
                forces an opaque origin and blocks scripts.
              </p>
            </>
          )}

          {tab === "source" && (
            <pre className="h-[calc(100vh-13rem)] min-h-[420px] overflow-auto rounded-xl border border-[color:var(--fdx-rule)] bg-zinc-950/40 p-4 font-mono text-[12px] leading-relaxed text-zinc-300">
              {detail.source}
            </pre>
          )}

          {tab === "edit" && published && (
            <p className="rounded-xl border border-[color:var(--fdx-rule)] p-4 text-[13px] text-zinc-400">
              Published artifacts are read-only from the dashboard — claude.ai has no update API.
            </p>
          )}

          {/* The edit surface stays MOUNTED (hidden via CSS, not unmounted) once
              the source is loaded, regardless of which tab is active. Milkdown
              owns its document internally — remounting it on every tab switch
              would discard whatever the user had typed. */}
          {!published && isMarkdown && (
            <div className={tab === "edit" ? "" : "hidden"}>
              <EditModeToggle mode={editMode} onChange={switchEditMode} />
              {/* Raw is a sibling, not a replacement: Milkdown stays mounted so
                  switching back and forth cannot lose its document. */}
              <div className={editMode === "raw" ? "hidden" : ""}>
                <MarkdownEditor
                  key={milkdownKey}
                  defaultValue={draft}
                  apiRef={apiRef}
                  onReady={() => setMilkdownReady(true)}
                  font={detail.font || "space-grotesk"}
                />
              </div>
              {editMode === "raw" && (
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  spellCheck={false}
                  placeholder="Paste markdown here — stored byte-for-byte, nothing is reparsed."
                  className="h-[calc(100vh-19rem)] min-h-[320px] w-full rounded-xl border border-[color:var(--fdx-rule)] bg-zinc-950/40 p-3 font-mono text-[12px] leading-relaxed text-zinc-200 focus:border-[color:var(--fdx-signal)]/50 focus:outline-none"
                />
              )}
              <SaveStatus savedVersion={savedVersion} saveError={saveError} />
            </div>
          )}
          {!published && !isMarkdown && (
            <div className={tab === "edit" ? "" : "hidden"}>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
                className="h-[calc(100vh-16rem)] min-h-[320px] w-full rounded-xl border border-[color:var(--fdx-rule)] bg-zinc-950/40 p-3 font-mono text-[12px] leading-relaxed text-zinc-200 focus:border-[color:var(--fdx-signal)]/50 focus:outline-none"
              />
              <SaveStatus savedVersion={savedVersion} saveError={saveError} />
            </div>
          )}
        </div>

        <CmsPanel
          detail={detail}
          id={id}
          open={panelOpen}
          onOpen={() => setPanelOpen(true)}
          openSection={openSection}
          onOpenSection={setOpenSection}
          onOpenSession={onOpenSession}
          viewingVersion={viewingVersion}
          onSelectVersion={selectVersion}
          onUpdateFromOrigin={updateFromOrigin}
          updating={updating}
          updateError={updateError}
          onChangeFont={changeFont}
          fontSaving={fontSaving}
          fontError={fontError}
          onChangeStatus={changeStatus}
          statusSaving={statusSaving}
          statusError={statusError}
          headDraft={headDraft}
          onHeadDraft={(v) => { setHeadDraft(v); setHeadSaved(false); }}
          onSaveHead={saveCustomHead}
          headSaving={headSaving}
          headSaved={headSaved}
          headError={headError}
          onDelete={doDelete}
          deleteArmed={deleteArmed}
          onArmDelete={setDeleteArmed}
          deleting={deleting}
          deleteError={deleteError}
        />
      </div>
    </div>
  );
}
