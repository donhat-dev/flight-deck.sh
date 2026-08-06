/**
 * Blip index — every blip as a table.
 *
 * A separate surface rather than a panel beside the radar, because it answers a
 * different question. The radar answers "where does this stand"; the index answers
 * "which ones need attention", and that is a filtering job: facets on the left,
 * rows on the right, sortable. Squeezed into a sidebar it became a legend, which
 * is a list you read once and then ignore.
 *
 * The facet counts are DERIVED from the rows, never typed in. A hand-written count
 * that drifts from the table is worse than no count: it teaches the reader not to
 * trust the numbers on the page.
 */
import React, { useMemo, useState } from "react";

import { QUADRANTS, RINGS, RING_LABEL, isStale } from "./geometry.js";
import { BLIPS, RADARS } from "./seed.js";

const COLUMNS = [
  { k: "num", label: "#" },
  { k: "name", label: "Blip" },
  { k: "quadrant", label: "Quadrant" },
  { k: "ring", label: "Ring" },
  { k: "lastMove", label: "Last move" },
  { k: "why", label: "Why" },
  { k: "evidenceAgeDays", label: "Fresh" },
];

const count = (pred) => BLIPS.filter(pred).length;

export default function BlipIndex({ onOpen }) {
  const [ring, setRing] = useState(null);
  const [quadrant, setQuadrant] = useState(null);
  const [staleOnly, setStaleOnly] = useState(false);

  const rows = useMemo(() => BLIPS.filter((b) =>
    (!ring || b.ring === ring)
    && (!quadrant || b.quadrant === quadrant)
    && (!staleOnly || isStale(b))), [ring, quadrant, staleOnly]);

  const facet = (title, items, active, onPick) => (
    <section className="rdr-facet">
      <h3 className="rdr-eyebrow">{title}</h3>
      <ul className="rdr-facet-list">
        {items.map((it) => (
          <li key={it.k ?? "all"}>
            <button type="button" className="rdr-facet-key"
                    aria-pressed={active === it.k}
                    onClick={() => onPick(active === it.k ? null : it.k)}>
              {it.dot && <span className="rdr-dot" data-ring={it.dot} aria-hidden="true" />}
              <span className="rdr-facet-name">{it.label}</span>
              <span className="rdr-facet-count">{it.n}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );

  return (
    <main className="rdr-index">
      <div className="rdr-index-head">
        <div className="rdr-focus-title">
          <p className="rdr-eyebrow">Index</p>
          <h1 className="rdr-h1">Blip index</h1>
          <p className="rdr-sub">every blip, with the move that put it there</p>
        </div>
        <button type="button" className="rdr-btn" data-variant="primary">New blip</button>
      </div>

      <div className="rdr-index-body">
        <aside className="rdr-facets" aria-label="Filters">
          {facet("Radar", RADARS.map((r) => ({ k: r.slug, label: r.title, n: r.blipCount })),
                 RADARS.find((r) => r.open)?.slug, () => {})}
          {facet("Quadrant", QUADRANTS.map((q) => ({
            k: q.k, label: q.label, n: count((b) => b.quadrant === q.k),
          })), quadrant, setQuadrant)}
          {facet("Ring", [...RINGS].reverse().map((r) => ({
            k: r, label: RING_LABEL[r], dot: r, n: count((b) => b.ring === r),
          })), ring, setRing)}
          <section className="rdr-facet">
            <h3 className="rdr-eyebrow">Flags</h3>
            <ul className="rdr-facet-list">
              <li>
                <button type="button" className="rdr-facet-key" aria-pressed={staleOnly}
                        onClick={() => setStaleOnly((v) => !v)}>
                  <span className="rdr-dot" data-flag="stale" aria-hidden="true" />
                  <span className="rdr-facet-name">Evidence over 60d</span>
                  <span className="rdr-facet-count">{count(isStale)}</span>
                </button>
              </li>
              <li>
                <button type="button" className="rdr-facet-key" aria-pressed={false} disabled>
                  <span className="rdr-dot" data-flag="moved" aria-hidden="true" />
                  <span className="rdr-facet-name">Moved this quarter</span>
                  <span className="rdr-facet-count">{count((b) => b.state !== "held")}</span>
                </button>
              </li>
            </ul>
          </section>
        </aside>

        <section className="rdr-table-panel">
          <div className="rdr-table-bar">
            <span className="rdr-chrome-meta">
              {rows.length} of {BLIPS.length} blips
            </span>
            <span className="rdr-chrome-fill" />
            {(ring || quadrant || staleOnly) && (
              <button type="button" className="rdr-btn" onClick={() => {
                setRing(null); setQuadrant(null); setStaleOnly(false);
              }}>Clear filters</button>
            )}
          </div>
          <table className="rdr-table">
            <thead>
              <tr>{COLUMNS.map((c) => <th key={c.k} data-col={c.k}>{c.label}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((b) => (
                <tr key={b.num} className="rdr-row" onClick={() => onOpen?.(b.num)}
                    data-stale={isStale(b) ? "true" : undefined}>
                  <td data-col="num">{b.num}</td>
                  <td data-col="name">{b.name}</td>
                  <td data-col="quadrant">{QUADRANTS.find((q) => q.k === b.quadrant)?.label}</td>
                  <td data-col="ring">
                    <span className="rdr-ring-badge" data-ring={b.ring}>{RING_LABEL[b.ring]}</span>
                  </td>
                  <td data-col="lastMove">{b.lastMove}</td>
                  <td data-col="why">{b.why}</td>
                  <td data-col="evidenceAgeDays">
                    <span className="rdr-fresh" data-stale={isStale(b) ? "true" : undefined}>
                      {b.evidenceAgeDays}d
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && (
            <p className="rdr-empty-line">No blip matches those filters.</p>
          )}
        </section>
      </div>
    </main>
  );
}
