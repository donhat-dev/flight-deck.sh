import React from "react";

import { IconBack, IconPanel } from "../../ui/icons.jsx";

/**
 * One compact bar instead of the old stacked header.
 *
 * The previous build spent roughly a third of the viewport before the preview
 * started: a back strip, a title block, six lines of metadata, a font row, a
 * source-change banner, then the tabs. Everything that was a permanent row moved
 * into the CMS panel, and what is left here is the identity of the document, the
 * view switch, and the one action the current view can take.
 *
 * Tabs are `role="tablist"` because they switch a view in place. The source-change
 * indicator stays in the header — it is the one piece of state that must be seen
 * without opening a panel, since it says the document you are reading is behind
 * its origin.
 */

const STATUS_TONE = {
  draft: "text-zinc-300",
  published: "text-emerald-400",
  archived: "text-zinc-500",
};

const TABS = [
  ["preview", "Preview"],
  ["source", "Source"],
  ["edit", "Edit"],
];

export default function DetailHeader({
  detail, tab, onTab, published, viewingVersion, onBack,
  panelOpen, onTogglePanel, staleClick, action,
}) {
  return (
    <div className="sticky top-0 z-20 -mt-6 border-b border-[color:var(--fdx-rule)] bg-zinc-950/95 pt-6 backdrop-blur-xl md:-mt-8 md:pt-8">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pb-3">
        <button
          type="button"
          onClick={onBack}
          title="Back to Treasures"
          className="inline-flex min-h-[36px] items-center gap-1.5 rounded-lg px-2 text-[13px] text-zinc-400 transition-colors hover:bg-zinc-500/5 hover:text-zinc-200"
        >
          <IconBack />
          <span className="hidden sm:inline">Treasures</span>
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-baseline gap-2">
            <h2 className="truncate text-[15px] font-semibold text-zinc-100" title={detail.title}>
              {detail.title}
            </h2>
            <span className={`text-[12px] font-semibold capitalize ${STATUS_TONE[detail.status] || "text-zinc-400"}`}>
              {detail.status}
            </span>
            <span className="font-mono text-[12px] text-zinc-500">v{detail.version}</span>
            {viewingVersion != null && (
              <span className="rounded-full bg-[color:var(--fdx-signal)]/15 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[color:var(--fdx-signal)]">
                viewing v{viewingVersion}
              </span>
            )}
          </div>
        </div>

        {/* Only when the server says the origin moved on. It is a button, not a
            badge: the point is to get to the panel section that can act on it. */}
        {detail.origin_stale?.stale === true && (
          <button
            type="button"
            onClick={staleClick}
            className="inline-flex min-h-[36px] items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 text-[12px] font-semibold text-amber-300 transition-colors hover:bg-amber-500/20"
          >
            Source changed
          </button>
        )}

        <div role="tablist" aria-label="View" className="flex items-center gap-1">
          {TABS.map(([key, label]) => {
            const disabled = key === "edit" && (published || viewingVersion != null);
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                disabled={disabled}
                onClick={() => onTab(key)}
                title={disabled
                  ? published
                    ? "Published artifacts are read-only from the dashboard — claude.ai has no update API"
                    : "Editing applies to the current version; leave the history preview first"
                  : undefined}
                className={`min-h-[36px] rounded-lg px-2.5 text-[13px] font-semibold transition-colors ${
                  tab === key
                    ? "bg-zinc-500/10 text-zinc-100"
                    : disabled
                      ? "cursor-not-allowed text-zinc-700"
                      : "text-zinc-400 hover:bg-zinc-500/5 hover:text-zinc-200"
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>

        {action}

        <button
          type="button"
          onClick={onTogglePanel}
          aria-expanded={panelOpen}
          aria-controls="treasure-panel"
          title={panelOpen ? "Collapse panel" : "Expand panel"}
          className="inline-flex min-h-[36px] items-center rounded-lg px-2 text-zinc-400 transition-colors hover:bg-zinc-500/5 hover:text-zinc-200"
        >
          <IconPanel />
          <span className="sr-only">{panelOpen ? "Collapse panel" : "Expand panel"}</span>
        </button>
      </div>
    </div>
  );
}
