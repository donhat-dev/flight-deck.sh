import React from "react";

import { IconRefresh } from "../../ui/icons.jsx";

/**
 * Library action bar.
 *
 * It carries NO title. The app shell's Header already renders "Treasures" plus a
 * subtitle for this view, so a second heading here was the same word twice on one
 * screen — the duplication was visible the moment both were on screen together.
 *
 * Actions sit on the left in the order they are reached for: New treasure first
 * as the single primary action, Scan sources second as the separate workflow.
 * State sits on the right, so "what I can do" and "what is happening" do not
 * interleave.
 *
 * `Refresh` is not a button of its own. The live indicator IS the control: it
 * reports the real SSE state and clicking it refetches, so the technical detail
 * earns its space instead of sitting beside a status it duplicates.
 */
export default function LibraryHeader({
  total, published, live, onRefresh, onNew, creating, onScan, scanning,
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-2">
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
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <p className="flex items-center gap-2 text-[13px] text-zinc-400">
          <span>
            <span className="font-mono text-zinc-200">{total}</span> artifacts
          </span>
          <span className="text-zinc-700">·</span>
          <span>
            <span className="font-mono text-zinc-200">{published}</span> published
          </span>
        </p>
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
      </div>
    </div>
  );
}
