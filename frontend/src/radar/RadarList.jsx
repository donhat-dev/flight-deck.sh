/**
 * Radar list — one card per initiative.
 *
 * The card leads with a miniature of the radar rather than a name alone, because
 * the shape carries information a title cannot: a radar whose blips all sit in the
 * outer rings is a question, and one clustered inside is a decision already made.
 *
 * The ring distribution is drawn as four bars whose counts are read off the same
 * numbers the radar uses, so the card cannot disagree with the page it links to.
 */
import React from "react";

import { RINGS, RING_LABEL } from "./geometry.js";


/** The miniature. Four rings, four dots — one per quadrant, placed so the glyph
 *  reads as a radar at 74px rather than as a target. */
function Mini() {
  const rings = [1, 0.72, 0.48, 0.27];
  const dots = [
    { x: 25, y: 21, q: "platforms" },
    { x: 51, y: 27, q: "techniques" },
    { x: 30, y: 53, q: "tools" },
    { x: 52, y: 51, q: "lang" },
  ];
  return (
    <svg className="rdr-mini" viewBox="0 0 74 74" aria-hidden="true">
      {rings.map((f, i) => (
        <circle key={f} className="rdr-mini-ring" data-inner={i === rings.length - 1 ? "true" : undefined}
                cx={37} cy={37} r={36 * f} />
      ))}
      {dots.map((d) => (
        <circle key={d.q} className="rdr-mini-dot" data-quadrant={d.q} cx={d.x} cy={d.y} r={3.6} />
      ))}
    </svg>
  );
}

export default function RadarList({ radars, openSlug, onOpen }) {
  return (
    <main className="rdr-list">
      <div className="rdr-index-head">
        <div className="rdr-focus-title">
          <p className="rdr-eyebrow">Radars</p>
          <h1 className="rdr-h1">Radars</h1>
          <p className="rdr-sub">one radar per initiative · a blip moves only with a reason and evidence</p>
        </div>
        <button type="button" className="rdr-btn" data-variant="primary">New radar</button>
      </div>

      <ul className="rdr-cards">
        {radars.map((r) => ((r = { ...r, open: r.slug === openSlug }),
          <li key={r.slug}>
            <button type="button" className="rdr-card" aria-current={r.open ? "true" : undefined}
                    onClick={() => r.open && onOpen?.(r.slug)}>
              <span className="rdr-card-top">
                <Mini />
                <span className="rdr-card-title">
                  <span className="rdr-card-name">
                    {r.title}
                    {r.open && <span className="rdr-card-open">open</span>}
                  </span>
                  <span className="rdr-card-sub">{r.subtitle}</span>
                </span>
              </span>

              <span className="rdr-card-dist">
                {RINGS.map((ring) => (
                  <span key={ring} className="rdr-card-seg" data-ring={ring}
                        data-empty={r.rings[ring] ? undefined : "true"}>
                    <span className="rdr-card-bar" />
                    <span className="rdr-card-count">{r.rings[ring]}</span>
                    <span className="rdr-card-ring">{RING_LABEL[ring]}</span>
                  </span>
                ))}
              </span>

              <span className="rdr-card-foot">
                <span className="rdr-card-meta">{r.blipCount} blips · {r.moveCount} moves</span>
                <span className="rdr-chrome-fill" />
                {r.stale > 0 && (
                  <span className="rdr-card-stale">{r.stale} stale</span>
                )}
                <span className="rdr-card-meta">{r.updated}</span>
              </span>
            </button>
          </li>
        ))}
        <li>
          <button type="button" className="rdr-card" data-new="true">
            <span className="rdr-card-plus" aria-hidden="true">+</span>
            <span className="rdr-card-name">New radar</span>
            <span className="rdr-card-sub">four rings, four quadrants, one initiative</span>
          </button>
        </li>
      </ul>
    </main>
  );
}
