/**
 * The history scrubber.
 *
 * Two tiers on purpose. The labelled tier is the granularity you picked; the
 * unlabelled tier below it is the next finer unit, drawn as plain ticks. Without
 * the finer tier a quarter scrubber gives no sense of where inside a quarter the
 * handle sits, and "as of" becomes a guess.
 *
 * The granularity switch changes what a tick MEANS, not how many there are, so the
 * ruler's width never changes and the handle does not jump when you switch.
 */
import React from "react";

import { GRANULARITY, TIMELINE } from "./seed.js";

/** Minor ticks per major stop, per granularity. A quarter holds three months; a
 *  month holds roughly four weeks; a week holds seven days. */
const MINOR = { quarter: 3, month: 4, week: 7, day: 4 };

export default function HistoryBar({ granularity = "quarter", onGranularity, asOf = "04 Aug 2026" }) {
  const minor = MINOR[granularity] ?? 3;
  const currentIndex = Math.max(0, TIMELINE.findIndex((t) => t.current));
  // The handle sits on the second minor tick of the current stop rather than at
  // its edge: an edge handle reads as "the boundary", which is a different claim.
  const handleAt = currentIndex * minor + Math.min(minor - 1, 1);

  return (
    <div className="rdr-history">
      <div className="rdr-history-lead">
        <span className="rdr-eyebrow">History</span>
        <div className="rdr-seg" role="group" aria-label="History granularity">
          {GRANULARITY.map((g) => (
            <button
              key={g}
              type="button"
              className="rdr-seg-key"
              aria-pressed={g === granularity}
              onClick={() => onGranularity?.(g)}
            >
              {g}
            </button>
          ))}
        </div>
      </div>

      <div className="rdr-ruler">
        <div className="rdr-ruler-ticks">
          {TIMELINE.flatMap((t, ti) =>
            Array.from({ length: minor }, (_, mi) => {
              const idx = ti * minor + mi;
              return (
                <span
                  key={idx}
                  className="rdr-tick"
                  data-major={mi === 0 ? "true" : undefined}
                  data-handle={idx === handleAt ? "true" : undefined}
                />
              );
            }))}
        </div>
        <div className="rdr-ruler-labels">
          {TIMELINE.map((t) => (
            <span key={t.key} className="rdr-ruler-stop" aria-current={t.current ? "true" : undefined}>
              <span className="rdr-ruler-key">{t.key}</span>
              <span className="rdr-ruler-moves">{t.moves} moves</span>
            </span>
          ))}
        </div>
      </div>

      <div className="rdr-history-tail">
        <span className="rdr-eyebrow">As of {asOf}</span>
        <div className="rdr-history-controls">
          <button type="button" className="rdr-icon-btn" aria-label="Previous">‹</button>
          <button type="button" className="rdr-icon-btn" aria-label="Next">›</button>
          <button type="button" className="rdr-btn" data-variant="primary">Replay</button>
        </div>
      </div>
    </div>
  );
}
