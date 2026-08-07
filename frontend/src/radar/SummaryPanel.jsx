/**
 * The blip summary, shown beside the radar rather than instead of it.
 *
 * This replaces a round trip. Reading one blip used to mean leaving the full radar for
 * the detail page, then coming back, then finding the blip again — three navigations to
 * answer "what is that one?", and the radar was gone for all of them. So the question is
 * answered in place: the radar stays on screen, keeps its selection, and the reader
 * compares neighbours by clicking along.
 *
 * It makes NO REQUEST. Every field here is already on the board — `/radars` returns the
 * definition, the ring, the newest reason and the related blips with THEIR derived
 * rings, for every blip. What it deliberately does not carry is the move HISTORY, which
 * is the one thing worth a second request and the reason `Open history` still exists.
 *
 * The content follows the Thoughtworks blip anatomy, because that shape is what makes a
 * radar readable a year later rather than a picture of opinions:
 *
 *   what it is        a definition, independent of any ring
 *   why it is there   the newest move's reason, plus what it costs to adopt
 *   when it moved     the transition, with a way into the full history
 *   related blips     the choices this one was weighed against
 *
 * The position TRACK and the evidence LIST were both here and were both removed: they
 * spent height on facts the prose already carries (the ring is named twice in words
 * above) and on a list that belongs with the history. The height went into type size
 * instead, which is what makes a panel read as prose rather than as a form.
 *
 * It is not the anchor of this view. The radar is — it holds the positions and the
 * question the page exists to answer — so the panel takes a border and a raised surface
 * and deliberately no block lift, which the blip-focus panel does take because there it
 * IS the anchor.
 */
import React from "react";

import BlipMark from "./BlipGlyph.jsx";
import { RING_LABEL, quadrantOf } from "./geometry.js";

/** `Q3 2026 → Trial` split back into its two halves, so the transition can be shown as
 *  one. `lastMove` is built server-side and is the only place the pair travels together. */
function transition(blip) {
  if (!blip.ring) return "Entered the radar";
  return `${blip.period ?? ""}  ·  ${RING_LABEL[blip.ring]}`;
}

export default function SummaryPanel({ blip, onOpenDetail, onClose }) {
  const quadrant = quadrantOf(blip.quadrant).label;
  const ring = blip.ring ? RING_LABEL[blip.ring] : "not yet placed";
  const related = blip.related ?? [];
  return (
    <aside className="rdr-side" aria-label={`Summary of blip ${blip.num}`}>
      <header className="rdr-side-head">
        <div className="rdr-side-top">
          <p className="rdr-eyebrow">Blip {blip.num}</p>
          <span className="rdr-chrome-fill" />
          {/* Only rendered when there is somewhere to go. An always-present link that is
              sometimes dead is worse than an absent one. */}
          {blip.ref && (
            <a className="rdr-side-ref" href={blip.ref} target="_blank" rel="noreferrer">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M14 5h5v5M19 5l-8 8M17 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h4" />
              </svg>
              Ref
            </a>
          )}
          <button type="button" className="rdr-icon-btn" aria-label="Close the summary"
                  onClick={onClose}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        <h2 className="rdr-side-name">
          <BlipMark blip={blip} />
          {blip.name}
        </h2>
        <p className="rdr-side-meta">
          {quadrant}  ·  currently {ring}  ·  {blip.moveCount}{" "}
          {blip.moveCount === 1 ? "move" : "moves"} on record
        </p>
      </header>

      <div className="rdr-side-body">
        {/* A definition, and it comes from the BLIP rather than from a move. A definition
            is not a decision: on a move it would change every time the ring changed and
            repeat in every row of the history. */}
        {blip.description && (
          <section className="rdr-side-block">
            <h3 className="rdr-eyebrow">What it is</h3>
            <p className="rdr-side-lede">{blip.description}</p>
          </section>
        )}

        <section className="rdr-side-block">
          <h3 className="rdr-eyebrow">
            {blip.ring ? `Why it is in ${RING_LABEL[blip.ring]}` : "Why it is on the radar"}
          </h3>
          <p className="rdr-side-lede" data-weight="argument">{blip.why}</p>
        </section>

      {/* The move line and the related list scroll WITH the prose rather than sitting
          pinned below it. Pinned, they squeezed the reason into whatever height was
          left and cut it mid-sentence in the middle of the panel — a truncation with
          nothing to say it was one. Inside the scroller the cut lands at the panel's
          bottom edge, where a reader already expects more. Only the head and the way
          out stay fixed. */}
      <div className="rdr-side-moved">
        <span className="rdr-side-when">{transition(blip)}</span>
        <span className="rdr-chrome-fill" />
        {/* The way to the history, named for what it opens. The primary key below opens
            the same page; this one is here because a reader scanning the move line is
            already asking the history question. */}
        <button type="button" className="rdr-side-link" onClick={onOpenDetail}>
          View blip history
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5l7 7-7 7" /></svg>
        </button>
      </div>

      {related.length > 0 && (
        <section className="rdr-side-block" data-region="related">
          <h3 className="rdr-eyebrow">Related blips</h3>
          <ul className="rdr-side-related">
            {related.map((r) => (
              <li key={r.num}>
                {/* Each card carries the other blip's OWN derived ring, resolved
                    server-side from its newest move — so a card can never claim a
                    position the radar disagrees with. */}
                <span className="rdr-side-rel-name">{r.name}</span>
                <span className="rdr-side-rel-ring" data-ring={r.ring ?? undefined}>
                  {r.ring ? RING_LABEL[r.ring] : "Entered"}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      </div>

      <footer className="rdr-side-foot">
        <span className="rdr-chrome-fill" />
        <button type="button" className="rdr-btn" data-variant="primary"
                onClick={onOpenDetail}>
          Open history
        </button>
      </footer>
    </aside>
  );
}
