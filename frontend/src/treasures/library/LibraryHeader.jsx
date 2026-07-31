import React from "react";

import { IconRefresh } from "../../ui/icons.jsx";

/**
 * Library header: what the library is, then what you can do to it.
 *
 * Replaces a five-cell statistics block that cost most of a mobile viewport
 * before the first document appeared. The same facts fit on one line, because a
 * count is a caption, not a panel.
 *
 * `Refresh` is gone as a button. The live indicator IS the control: it reports
 * the real SSE state and clicking it refetches, so the technical detail earns its
 * space instead of sitting beside a status it duplicates.
 *
 * One primary action — New treasure. Scan sources is a different workflow and
 * reads as one.
 */
export default function LibraryHeader({
  total, published, live, onRefresh, onNew, creating, onScan, scanning,
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="space-y-1.5">
        <h2 className="text-[26px] font-bold leading-none tracking-tight text-zinc-100">
          Treasures
        </h2>
        <p className="flex flex-wrap items-center gap-2 text-[13px] text-zinc-400">
          <span>
            <span className="font-mono text-zinc-200">{total}</span> artifacts
          </span>
          <span className="text-zinc-700">·</span>
          <span>
            <span className="font-mono text-zinc-200">{published}</span> published
          </span>
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {/* The status doubles as the refresh control, so the label is honest about
            both what is happening and what clicking will do. */}
        <button
          type="button"
          onClick={onRefresh}
          title={live ? "Live over SSE — click to refetch now" : "Not connected — click to refetch"}
          className="inline-flex min-h-[44px] items-center gap-2 rounded-lg px-3 text-[13px] text-zinc-400 transition-colors hover:bg-zinc-500/5 hover:text-zinc-200"
        >
          <span
            className={`h-[7px] w-[7px] rounded-full ${
              live ? "animate-live-pulse bg-emerald-400" : "bg-zinc-600"
            }`}
          />
          {live ? "Live" : "Offline"}
          <IconRefresh />
        </button>

        <button
          type="button"
          onClick={onScan}
          disabled={scanning}
          className="fdx-button"
          data-variant="secondary"
          data-size="sm"
        >
          <span>{scanning ? "Scanning…" : "Scan sources"}</span>
        </button>

        <button
          type="button"
          onClick={onNew}
          aria-expanded={creating}
          className="fdx-button"
          data-variant={creating ? "secondary" : "primary"}
          data-size="sm"
        >
          <span>{creating ? "Close" : "New treasure"}</span>
        </button>
      </div>
    </header>
  );
}
