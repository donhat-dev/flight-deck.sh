/**
 * The blip summary, shown beside the radar rather than instead of it.
 *
 * This replaces a round trip. Reading one blip used to mean leaving the full radar for
 * the detail page, then coming back, then finding the blip again — three navigations
 * to answer "what is that one?", and the radar was gone for all of them. So the
 * question is answered in place: the radar stays on screen, keeps its selection, and
 * the reader compares neighbours by clicking along.
 *
 * It makes NO REQUEST. Every field here is already on the board — `/radars` returns
 * ring, state, period, the newest reason and the evidence counts for every blip,
 * derived server-side. What it deliberately does not carry is the move HISTORY, which
 * is the one thing worth a second request and the reason `Open detail` still exists.
 * That split is the whole design: the cheap answer is instant, the expensive one is
 * asked for.
 *
 * It is not the anchor of this view. The radar is — it holds the positions and the
 * question the page exists to answer — so the panel takes a border and a raised
 * surface and deliberately no block lift, which the blip-focus panel does take
 * because there it IS the anchor.
 */
import React from "react";

import BlipMark from "./BlipGlyph.jsx";
import { Position } from "./BlipPanel.jsx";
import { isStale, quadrantOf } from "./geometry.js";

export default function SummaryPanel({ blip, onOpenDetail, onClose }) {
  const quadrant = quadrantOf(blip.quadrant).label;
  return (
    <aside className="rdr-side" aria-label={`Summary of blip ${blip.num}`}>
      <header className="rdr-side-head">
        <p className="rdr-eyebrow">Blip {blip.num}</p>
        <h2 className="rdr-side-name">
          <BlipMark blip={blip} />
          {blip.name}
        </h2>
        <button type="button" className="rdr-icon-btn" aria-label="Close the summary"
                onClick={onClose}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </header>

      <div className="rdr-side-body">
        {/* No wrapper and no heading of its own: `Position` already IS a
            `.rdr-block` with its own "Position" eyebrow, so adding either produced a
            section inside a section and the word twice. */}
        <Position ring={blip.ring} />

        {/* The reason, at reading size. It is the newest move's `why`, which is what a
            reader is actually after when they click a blip — not the ring, which the
            drawing already told them. */}
        <section className="rdr-block rdr-lede-block">
          <p className="rdr-lede">{blip.why}</p>
        </section>

        <section className="rdr-block">
          <dl className="rdr-side-meta">
            <div><dt>Quadrant</dt><dd>{quadrant}</dd></div>
            <div><dt>Last move</dt><dd>{blip.lastMove ?? "—"}</dd></div>
            <div><dt>Moves</dt><dd>{blip.moveCount}</dd></div>
            <div>
              <dt>Evidence</dt>
              <dd>
                {blip.evidenceCount}
                {/* Staleness is a property of the EVIDENCE, so it is stated here and
                    not next to the ring. A blip does not go stale; its citations do. */}
                {isStale(blip) && (
                  <span className="rdr-stale"> newest {blip.evidenceAgeDays}d old</span>
                )}
              </dd>
            </div>
          </dl>
        </section>
      </div>

      <footer className="rdr-side-foot">
        {/* The ring is deliberately NOT repeated here. The position track above already
            names it, in orange, and a badge saying Trial under a track pointing at
            Trial is the same fact twice — which is how a panel accumulates small
            elements until none of them is read. */}
        <span className="rdr-chrome-fill" />
        {/* The only route out. Named for what it costs the reader — a page — rather
            than for the data it fetches. */}
        <button type="button" className="rdr-btn" data-variant="primary"
                onClick={onOpenDetail}>
          Open detail
        </button>
      </footer>
    </aside>
  );
}
