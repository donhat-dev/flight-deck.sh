import React, { useState } from "react";

import VersionHistory from "./VersionHistory.jsx";
import { kb, relTime } from "../format.js";

/**
 * The document's controls, in one collapsible column.
 *
 * Everything here used to be a permanent header row, which meant the preview — the
 * reason the page exists — started a third of the way down the viewport. A CMS
 * panel is the right shape for it: these are properties of the document, they are
 * consulted occasionally, and they are edited one at a time.
 *
 * Collapsed it becomes a rail of section buttons rather than disappearing, so the
 * panel's contents stay discoverable and reopening lands on the section you meant.
 *
 * Only Source, Appearance, Advanced and Danger zone can write. Publish shows state
 * and can archive, but cannot publish: there is no publish endpoint — that runs
 * through the MCP tool — and a button that looked like it published would be
 * lying about what the dashboard can do.
 */

// Mirrors render.FONTS in backend/flightdeck/treasures/render.py — the renderer
// rejects anything else, so this list cannot be invented here. No shared source
// exists yet; keep the two in sync by hand.
const FONT_OPTIONS = [
  { value: "space-grotesk", label: "Space Grotesk" },
  { value: "jetbrains-mono", label: "JetBrains Mono" },
  { value: "default", label: "Default (system)" },
];

const ORIGIN_LABEL = {
  doc_file: "Tracked file",
  claude_session: "Claude session",
  "artifact-port": "Ported artifact",
  ui: "Pasted into the dashboard",
  discover: "Discovered in a transcript",
};

// The third entry is the rail label. Collapsed is the DEFAULT state, so the rail is
// the first thing seen — single letters (P, S, V, !) would have made every section
// a guess. Set vertically, the full word fits in a 30px column.
const SECTIONS = [
  ["publish", "Publish", "Publish"],
  ["source", "Source", "Source"],
  ["content", "Content", "Content"],
  ["tags", "Tags", "Tags"],
  ["appearance", "Appearance", "Appearance"],
  ["versions", "Version history", "Versions"],
  ["advanced", "Advanced", "Advanced"],
  ["danger", "Danger zone", "Danger"],
];

function vscodeUri(path) {
  return path ? `vscode://file${path.startsWith("/") ? "" : "/"}${path}` : null;
}

function PathLink({ path, label }) {
  if (!path) return null;
  return (
    <a
      href={vscodeUri(path)}
      title={path}
      className="block truncate font-mono text-[11px] text-zinc-400 underline decoration-dotted underline-offset-2 hover:text-[color:var(--fdx-signal)]"
    >
      {label}
    </a>
  );
}

function Row({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="shrink-0 text-[12px] text-zinc-500">{label}</span>
      <span className="min-w-0 text-right text-[12px] text-zinc-200">{children}</span>
    </div>
  );
}

function Section({ id, title, open, onToggle, children }) {
  return (
    <section className="border-b border-[color:var(--fdx-rule)] last:border-b-0">
      <h3>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={`panel-${id}`}
          onClick={onToggle}
          className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left transition-colors hover:bg-zinc-500/[0.03]"
        >
          <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-400">
            {title}
          </span>
          <span aria-hidden="true" className="text-[10px] text-zinc-600">{open ? "▾" : "▸"}</span>
        </button>
      </h3>
      {open && <div id={`panel-${id}`} className="px-3 pb-3.5">{children}</div>}
    </section>
  );
}

function Note({ children }) {
  return <p className="mt-2 text-[11px] leading-snug text-zinc-600">{children}</p>;
}

export default function CmsPanel({
  detail, id, open, onOpen, openSection, onOpenSection, onOpenSession,
  viewingVersion, onSelectVersion,
  onUpdateFromOrigin, updating, updateError,
  onChangeFont, fontSaving, fontError,
  onChangeStatus, statusSaving, statusError,
  headDraft, onHeadDraft, onSaveHead, headSaving, headSaved, headError,
  onDelete, deleteArmed, onArmDelete, deleting, deleteError,
}) {
  const published = detail.status === "published";
  const [expanded, setExpanded] = useState(() => new Set(["source", "versions"]));
  const isOpen = (key) => expanded.has(key) || openSection === key;
  const toggle = (key) => {
    if (openSection === key) onOpenSection(null);
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  if (!open) {
    return (
      <div className="hidden shrink-0 flex-col items-center gap-1 self-start border-l border-[color:var(--fdx-rule)] pl-1 lg:flex">
        {SECTIONS.map(([key, title, label]) => (
          <button
            key={key}
            type="button"
            title={`${title} — click to open`}
            onClick={() => { onOpenSection(key); onOpen(); }}
            className="w-[26px] rounded-lg py-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-zinc-500 transition-colors [writing-mode:vertical-rl] hover:bg-zinc-500/5 hover:text-zinc-200"
          >
            {label}
            {/* The rail label is shortened ("Versions", "Danger"); the full section
                name still reaches a screen reader. */}
            <span className="sr-only">{title}</span>
          </button>
        ))}
      </div>
    );
  }

  return (
    <aside
      id="treasure-panel"
      className="w-full shrink-0 self-start rounded-xl border border-[color:var(--fdx-rule)] bg-zinc-500/[0.02] lg:w-[300px]"
    >
      <Section id="publish" title="Publish" open={isOpen("publish")} onToggle={() => toggle("publish")}>
        <Row label="Status"><span className="capitalize">{detail.status}</span></Row>
        {detail.published_url && (
          <Row label="Live at">
            <a
              href={detail.published_url}
              target="_blank"
              rel="noreferrer"
              className="truncate underline decoration-dotted underline-offset-2 hover:text-[color:var(--fdx-signal)]"
            >
              claude.ai
            </a>
          </Row>
        )}
        {!published && (
          <button
            type="button"
            onClick={() => onChangeStatus(detail.status === "archived" ? "draft" : "archived")}
            disabled={statusSaving}
            className="fdx-button mt-2 w-full"
            data-variant="secondary"
            data-size="sm"
          >
            <span>
              {statusSaving
                ? "Saving…"
                : detail.status === "archived" ? "Restore to draft" : "Archive"}
            </span>
          </button>
        )}
        {statusError && <p className="mt-2 text-[11px] text-rose-400">{statusError}</p>}
        <Note>
          {published
            ? "The published copy cannot be updated from here — claude.ai has no update API."
            : "Publishing runs through the MCP tool, not this panel. Archiving is reversible and touches no files."}
        </Note>
      </Section>

      <Section id="source" title="Source" open={isOpen("source")} onToggle={() => toggle("source")}>
        <Row label="Origin">{ORIGIN_LABEL[detail.origin_kind] || detail.origin_kind || "—"}</Row>
        {detail.origin_id && (
          <Row label="Session">
            <button
              type="button"
              onClick={() => onOpenSession?.(detail.origin_id)}
              className="font-mono underline decoration-dotted underline-offset-2 hover:text-[color:var(--fdx-signal)]"
            >
              {String(detail.origin_id).slice(0, 8)}
            </button>
          </Row>
        )}
        {detail.origin_path && (
          <div className="pt-1">
            <PathLink path={detail.origin_path} label={detail.origin_path.split("/").pop()} />
          </div>
        )}

        {detail.origin_stale?.stale === true && (
          <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] p-2.5">
            <p className="text-[12px] font-semibold text-amber-300">Source changed on disk</p>
            <p className="mt-1 text-[11px] leading-snug text-amber-300/70">
              The origin document moved on. Updating re-reads it into a new version;
              it is never automatic, or every editor flush would mint one.
            </p>
            <button
              type="button"
              onClick={onUpdateFromOrigin}
              disabled={updating}
              className="fdx-button mt-2 w-full"
              data-variant="secondary"
              data-size="sm"
            >
              <span>{updating ? "Updating…" : "Update from source"}</span>
            </button>
            {updateError && <p className="mt-2 text-[11px] text-rose-400">{updateError}</p>}
          </div>
        )}
        {detail.origin_stale?.stale === false && (
          <Note>Matches the origin document as of this read.</Note>
        )}
        {detail.origin_stale?.refreshable === false && (
          <Note>{detail.origin_stale.reason || "This origin cannot be re-read."}</Note>
        )}
      </Section>

      <Section id="content" title="Content" open={isOpen("content")} onToggle={() => toggle("content")}>
        <Row label="Format"><span className="font-mono">{detail.source_format}</span></Row>
        <Row label="Kind">{detail.kind}</Row>
        <Row label="Language"><span className="font-mono uppercase">{detail.language}</span></Row>
        <Row label="Rendered"><span className="font-mono">{kb(detail.render_bytes)}</span></Row>
        <Row label="Updated">
          <span title={detail.updated_at}>{relTime(detail.updated_at)}</span>
        </Row>
        <div className="mt-2 space-y-0.5">
          <PathLink path={detail.source_path} label="source.md" />
          <PathLink path={detail.artifact_path} label="artifact.html" />
        </div>
      </Section>

      <Section id="tags" title="Tags" open={isOpen("tags")} onToggle={() => toggle("tags")}>
        {(detail.tags || []).length === 0
          ? <p className="text-[12px] text-zinc-500">None</p>
          : (
            <div className="flex flex-wrap gap-1.5">
              {(detail.tags || []).map((t) => (
                <span
                  key={t}
                  className="rounded-full border border-[color:var(--fdx-rule)] px-2 py-0.5 font-mono text-[11px] text-zinc-400"
                >
                  #{t}
                </span>
              ))}
            </div>
          )}
        <Note>Set by an agent (treasure_tag) or from the library's filter chips.</Note>
      </Section>

      <Section id="appearance" title="Appearance" open={isOpen("appearance")} onToggle={() => toggle("appearance")}>
        <label className="block text-[12px] text-zinc-500" htmlFor="treasure-font">Body font</label>
        <select
          id="treasure-font"
          value={detail.font || "space-grotesk"}
          disabled={fontSaving || published}
          onChange={(e) => onChangeFont(e.target.value)}
          title={published ? "Published artifacts are read-only from the dashboard" : undefined}
          className="mt-1 min-h-[36px] w-full rounded-lg border border-[color:var(--fdx-rule)] bg-transparent px-2 text-[13px] text-zinc-200 disabled:opacity-50"
        >
          {FONT_OPTIONS.map((f) => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
        {fontSaving && <p className="mt-2 text-[11px] text-zinc-500">Rerendering…</p>}
        {fontError && <p className="mt-2 text-[11px] text-rose-400">{fontError}</p>}
        <Note>A font change rerenders the artifact — the preview reloads, no new version.</Note>
      </Section>

      <Section id="versions" title="Version history" open={isOpen("versions")} onToggle={() => toggle("versions")}>
        <VersionHistory
          id={id}
          currentVersion={detail.version}
          selected={viewingVersion}
          onSelect={onSelectVersion}
        />
      </Section>

      <Section id="advanced" title="Advanced" open={isOpen("advanced")} onToggle={() => toggle("advanced")}>
        <label className="block text-[12px] text-zinc-500" htmlFor="treasure-head">
          Custom &lt;head&gt;
        </label>
        <textarea
          id="treasure-head"
          value={headDraft}
          onChange={(e) => onHeadDraft(e.target.value)}
          disabled={published}
          spellCheck={false}
          placeholder='<meta name="robots" content="noindex">'
          className="mt-1 h-24 w-full rounded-lg border border-[color:var(--fdx-rule)] bg-zinc-950/40 p-2 font-mono text-[11px] leading-relaxed text-zinc-200 focus:border-[color:var(--fdx-signal)]/50 focus:outline-none disabled:opacity-50"
        />
        <button
          type="button"
          onClick={onSaveHead}
          disabled={headSaving || published}
          className="fdx-button mt-2 w-full"
          data-variant="secondary"
          data-size="sm"
        >
          <span>{headSaving ? "Saving…" : "Save & rerender"}</span>
        </button>
        {headSaved && <p className="mt-2 text-[11px] text-emerald-400">Saved, preview reloaded.</p>}
        {headError && <p className="mt-2 text-[11px] text-rose-400">{headError}</p>}
        <Note>Spliced in raw before &lt;/head&gt; and never escaped — only paste HTML you trust.</Note>
      </Section>

      <Section id="danger" title="Danger zone" open={isOpen("danger")} onToggle={() => toggle("danger")}>
        <button
          type="button"
          onClick={() => (deleteArmed ? onDelete() : onArmDelete(true))}
          disabled={deleting}
          className={`min-h-[36px] w-full rounded-lg border px-2.5 text-[13px] font-semibold transition-colors disabled:opacity-50 ${
            deleteArmed
              ? "border-rose-500/50 bg-rose-500/15 text-rose-300 hover:bg-rose-500/25"
              : "border-[color:var(--fdx-rule)] text-zinc-400 hover:border-rose-500/40 hover:text-rose-300"
          }`}
        >
          {deleting ? "Deleting…" : deleteArmed ? "Confirm permanent delete" : "Delete permanently"}
        </button>
        {deleteArmed && (
          <>
            <p className="mt-2 text-[11px] leading-snug text-rose-300/80">
              Removes every version's files and the index row. Not recoverable.
            </p>
            <button
              type="button"
              onClick={() => onArmDelete(false)}
              className="mt-1.5 text-[12px] text-zinc-400 underline decoration-dotted underline-offset-2 hover:text-zinc-200"
            >
              Cancel
            </button>
          </>
        )}
        {deleteError && <p className="mt-2 text-[11px] text-rose-400">{deleteError}</p>}
        <Note>Two clicks here, and the API still requires ?confirm=true. Archive instead if you only want it out of the way.</Note>
      </Section>
    </aside>
  );
}
