import React, { useEffect, useState } from "react";

import { kb, relTime } from "../format.js";

/**
 * The artifact's retained versions.
 *
 * Every save writes a new `v<N>/` and keeps the old one, so a history existed on
 * disk long before anything surfaced it — `GET /api/treasures/{id}/versions`
 * derives it from the directory listing. Nothing here is stored state.
 *
 * Two honesty constraints this component keeps:
 *
 * - `written_at` is the artifact file's mtime, not a recorded creation event, so
 *   it is labelled "written" and never "created".
 * - Selecting a version only changes what the PREVIEW renders. There is no
 *   restore: the API has no endpoint that makes an old version current, and a
 *   button that silently re-saved old bytes as a new version would be inventing
 *   a rollback the store does not implement.
 */
/**
 * The list itself, given its rows.
 *
 * Split from the fetching wrapper so the claims below — "no restore", the
 * disabled row for a missing render — are assertable without a network stub or a
 * DOM: a component that fetches in an effect renders only its skeleton under
 * server-side rendering, which is how these tests run.
 */
export function VersionList({ rows, currentVersion, selected, onSelect }) {
  // An artifact under active editing reaches 20+ versions (one is at 21 today), and
  // an uncapped list pushed Advanced and Danger zone several screens down — the
  // history was crowding out the sections below it. Scroll the list, not the panel.
  return (
    <>
      <div className="max-h-[260px] space-y-1 overflow-y-auto pr-1">
      {rows.map((v) => {
        const active = (selected ?? currentVersion) === v.version;
        return (
          <button
            key={v.version}
            type="button"
            aria-current={active}
            disabled={!v.has_artifact}
            onClick={() => onSelect(v.is_current ? null : v.version)}
            title={v.has_artifact
              ? `Preview v${v.version}`
              : "This version's render was removed — only its source is on disk"}
            className={`flex w-full items-baseline justify-between gap-2 rounded-lg px-2 py-1.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              active ? "bg-[color:var(--fdx-signal)]/10" : "hover:bg-zinc-500/5"
            }`}
          >
            <span className="flex items-baseline gap-2">
              <span className={`font-mono text-[12px] ${active ? "text-zinc-100" : "text-zinc-300"}`}>
                v{v.version}
              </span>
              {v.is_current && (
                <span className="font-mono text-[10px] uppercase tracking-wide text-emerald-400">
                  current
                </span>
              )}
            </span>
            <span className="flex items-baseline gap-2 text-[11px] text-zinc-500">
              <span className="font-mono">{kb(v.render_bytes)}</span>
              <span className="text-zinc-700">·</span>
              <span title={v.written_at || undefined}>{relTime(v.written_at)}</span>
            </span>
          </button>
        );
      })}
      </div>
      {/* Outside the scroll box: a caveat that scrolls out of sight is a caveat
          the reader never sees. */}
      <p className="px-2 pt-2 text-[11px] leading-snug text-zinc-600">
        Selecting a version previews it. There is no restore — the store has no verb
        that makes an old version current.
      </p>
    </>
  );
}

export default function VersionHistory({ id, currentVersion, selected, onSelect }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);

  // Refetches when the version changes, which is what makes a save show up here
  // without a reload.
  useEffect(() => {
    let live = true;
    setRows(null);
    setError(null);
    fetch(`/api/treasures/${encodeURIComponent(id)}/versions`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((body) => { if (live) setRows(body.versions || []); })
      .catch((e) => { if (live) setError(String(e.message || e)); });
    return () => { live = false; };
  }, [id, currentVersion]);

  if (error) return <p className="text-[12px] text-rose-400">{error}</p>;
  if (!rows) return <div className="h-16 animate-pulse rounded-lg bg-zinc-500/10" />;

  return (
    <VersionList
      rows={rows}
      currentVersion={currentVersion}
      selected={selected}
      onSelect={onSelect}
    />
  );
}
