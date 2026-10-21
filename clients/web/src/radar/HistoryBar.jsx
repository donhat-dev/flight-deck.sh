/**
 * The history scrubber, in two variants.
 *
 * `granularity` — the full radar page. That page is nothing but radar and history,
 * so the scrubber is the only control on it and can afford to carry the whole time
 * axis: a day/week/month/quarter switch, and a two-tier ruler where the labelled
 * tier is the unit you picked and the unlabelled one below is the next finer unit.
 * Without that finer tier a quarter scrubber gives no sense of where inside a
 * quarter the handle sits, and "as of" becomes a guess.
 *
 * `quarters` — the blip-focus view. Here the anchor is the summary panel, and a
 * four-way switch plus a two-tier ruler would be four more small controls
 * competing with it. The view keeps the quarter stops, which are what a reader
 * actually scrubs, and drops everything else.
 *
 * The variant is a prop rather than two components because the stops, the move
 * counts and the current quarter are identical: only how much of the time axis is
 * exposed differs.
 */
import React from "react";

/** The four units the switch offers. Not from the API: it is a control, not data. */
const GRANULARITY = ["day", "week", "month", "quarter"];

/** Minor ticks per major stop, per granularity. A quarter holds three months; a
 *  month holds roughly four weeks; a week holds seven days. */
const MINOR = { quarter: 3, month: 4, week: 7, day: 4 };

function Ruler({ minor, handleAt, stops }) {
  return (
    <div className="rdr-ruler-ticks">
      {stops.flatMap((t, ti) =>
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
  );
}

function Stops({ marked, stops }) {
  return (
    <div className="rdr-ruler-labels">
      {stops.map((t) => (
        <span key={t.key} className="rdr-ruler-stop"
              aria-current={t.current ? "true" : undefined}>
          {marked && <span className="rdr-stop-mark" aria-hidden="true" />}
          <span className="rdr-ruler-key">{t.key}</span>
          <span className="rdr-ruler-moves">{t.moves} moves</span>
        </span>
      ))}
    </div>
  );
}

export default function HistoryBar({
  variant = "granularity",
  granularity = "quarter",
  onGranularity,
  periods = [],
  asOf,
}) {
  const full = variant === "granularity";
  // Derived from the moves themselves, server-side, so a quarter with no moves is
  // simply not a stop. A hand-kept list showed quarters that never happened.
  const stops = periods;
  const minor = MINOR[granularity] ?? 3;
  const currentIndex = Math.max(0, stops.findIndex((t) => t.current));
  // The handle sits on the second minor tick of the current stop rather than at
  // its edge: an edge handle reads as "the boundary", which is a different claim.
  const handleAt = currentIndex * minor + Math.min(minor - 1, 1);

  // A scrubber with no stops is not a smaller scrubber, it is a different thing.
  if (stops.length === 0) return null;

  return (
    <div className="rdr-history" data-variant={variant}>
      <div className="rdr-history-lead">
        <span className="rdr-eyebrow">{full ? "History" : "Radar history"}</span>
        {full ? (
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
        ) : (
          <span className="rdr-history-hint">scrub to replay the radar as of any quarter</span>
        )}
      </div>

      <div className="rdr-ruler">
        {full && <Ruler minor={minor} handleAt={handleAt} stops={stops} />}
        <Stops marked={!full} stops={stops} />
      </div>

      <div className="rdr-history-tail">
        {full && asOf && <span className="rdr-eyebrow">As of {asOf}</span>}
        <div className="rdr-history-controls">
          {full ? (
            <>
              <button type="button" className="rdr-icon-btn" aria-label="Previous">‹</button>
              <button type="button" className="rdr-icon-btn" aria-label="Next">›</button>
              <button type="button" className="rdr-btn" data-variant="primary">Replay</button>
            </>
          ) : (
            <>
              <button type="button" className="rdr-btn">⏮ {stops[0]?.key.split(" ")[0] ?? "start"}</button>
              <button type="button" className="rdr-btn">▷ Replay</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
