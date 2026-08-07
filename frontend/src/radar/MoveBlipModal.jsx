/**
 * Moving a blip — the only way a position changes.
 *
 * There is no "set ring" call in the API and there is no ring column in the store:
 * a blip's position IS its newest move. So this form is not an editor for a field,
 * it is the record of a decision, and its shape is dictated by what the store
 * refuses rather than by what is convenient to type. Ring, period, reason and at
 * least one piece of evidence are exactly the four things
 * `POST /api/radar/{slug}/blips/{num}/moves` will not work without, which is what
 * keeps the form from drifting away from what the server accepts.
 *
 * The REASON is the anchor — the largest field, the only one with a rule printed
 * under it. That is the whole point of the feature: a year from now the radar shows
 * this sentence, not the ring.
 *
 * Refusal is checked in three places on purpose, all of them fail-closed:
 * `refusal()` disables the key, the submit handler re-checks before it fetches, and
 * the server checks again. The first two can be bypassed by a reader with devtools;
 * the third cannot, and it is the one that matters.
 */
import React, { useCallback, useEffect, useState } from "react";

import BlipMark from "./BlipGlyph.jsx";
import { moveBlip } from "./data.js";
import { RINGS, RING_LABEL, directionTo, quadrantOf } from "./geometry.js";
import { periodOptions } from "./periods.js";

/** The kinds the store already holds. Not a free text field: an evidence kind the
 *  panel has no icon for renders as a blank square, so the set is closed here. */
const KINDS = [
  { k: "treasure", label: "Treasure" },
  { k: "trace", label: "Trace" },
  { k: "jira", label: "Jira" },
  { k: "note", label: "Note" },
];

/** Outer to inner, the way the radar is read. Same order the panel's position track
 *  uses, so a reader moving between the two never has to re-orient. */
const RING_CHOICES = [...RINGS].reverse();

const DIRECTION_NOTE = { in: "inward", out: "outward", held: "now", new: "" };

const emptyEvidence = () => ({ kind: "treasure", title: "", dated: "" });

/**
 * Why this move cannot be recorded yet, or null.
 *
 * Exported because it is the guard, and a guard nobody can call is a guard nobody
 * can test. The wording is the reader's, not the server's — the server's messages
 * are written for an API caller.
 */
export function refusal(draft) {
  if (!draft.ring) return "Pick the ring this blip moves to.";
  if (!draft.period) return "Pick the period this move belongs to.";
  if (!draft.why.trim()) return "A move with no reason is not recordable.";
  // Evidence is NOT here any more. It is recommended, not required — see `advice`. A
  // citation typed to get past a gate supports nothing, and the store now agrees.
  return null;
}

/**
 * What the form should SUGGEST, having refused nothing.
 *
 * Returns null when there is nothing worth saying. The distinction from `refusal` is the
 * point: advice never blocks, so it has to earn its place by only appearing when it would
 * change what a careful person does.
 *
 * It appears in two cases, and stays quiet otherwise:
 *
 *   the ring CHANGES        the position moves, and a year from now the question will be
 *                           what moved it
 *   Adopt or Caution        the two consequential landings — one is what later work gets
 *                           built on, the other is what it gets steered away from
 *
 * A hold at Trial with no citation gets no nag. Nagging on every move is how a hint stops
 * being read.
 */
export function advice(draft, blip) {
  if (draft.evidence.some((e) => e.title.trim())) return null;
  const moved = draft.ring !== blip.ring;
  const consequential = draft.ring === "adopt" || draft.ring === "caution";
  if (!moved && !consequential) return null;
  const why = moved
    ? `This changes the position from ${RING_LABEL[blip.ring] ?? "unplaced"} to ${RING_LABEL[draft.ring]}.`
    : `${RING_LABEL[draft.ring]} is what other work gets built on or steered away from.`;
  return `${why} Worth citing something, though you can record it without.`;
}

/** The draft as the API wants it: blank evidence rows dropped, blank dates nulled. */
export function payload(draft) {
  return {
    ring: draft.ring,
    period: draft.period,
    why: draft.why.trim(),
    evidence: draft.evidence
      .filter((e) => e.title.trim())
      .map((e) => ({ kind: e.kind, title: e.title.trim(), dated: e.dated || null })),
  };
}

function Head({ blip, onClose }) {
  const quadrant = quadrantOf(blip.quadrant).label;
  const ring = blip.ring ? RING_LABEL[blip.ring] : "not yet placed";
  return (
    <header className="rdr-modal-head">
      <BlipMark blip={blip} />
      <div className="rdr-modal-titles">
        <p className="rdr-eyebrow">Move blip</p>
        <h2 className="rdr-modal-title" id="rdr-move-title">{blip.name}</h2>
        <p className="rdr-modal-meta">
          {quadrant} · currently {ring} · {blip.moveCount}{" "}
          {blip.moveCount === 1 ? "move" : "moves"} on record
        </p>
      </div>
      <button type="button" className="rdr-icon-btn" aria-label="Close without recording"
              onClick={onClose}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>
    </header>
  );
}

function RingField({ blip, draft, set, periods }) {
  const options = periodOptions(periods);
  const picked = options.find((o) => o.key === draft.period);
  return (
    <section className="rdr-field">
      <h3 className="rdr-eyebrow">Move to</h3>
      <div className="rdr-seg" data-variant="rings" role="group" aria-label="Ring">
        {RING_CHOICES.map((r) => (
          <button key={r} type="button" className="rdr-seg-key"
                  aria-pressed={r === draft.ring}
                  onClick={() => set({ ring: r })}>
            <span className="rdr-ring-key-label">{RING_LABEL[r]}</span>
            <span className="rdr-ring-key-note">
              {DIRECTION_NOTE[directionTo(blip.ring, r)]}
            </span>
          </button>
        ))}
      </div>
      {/* Re-selecting the current ring is allowed and labelled `now`. Holding a
          position with fresh evidence is a real move, and hiding it would make the
          only way to refresh a stale blip a demotion that never happened. */}
      {draft.ring === blip.ring && (
        <p className="rdr-field-hint">
          Re-selecting {RING_LABEL[draft.ring]} records the position being held, with
          fresh evidence.
        </p>
      )}
      <label className="rdr-inline-field">
        <span className="rdr-eyebrow">Period</span>
        <select className="rdr-select" value={draft.period}
                onChange={(e) => set({ period: e.target.value })}>
          {options.map((o) => (
            <option key={o.key} value={o.key}>{o.key}</option>
          ))}
        </select>
        {picked?.isNew && (
          <span className="rdr-field-hint">first move in this quarter</span>
        )}
      </label>
    </section>
  );
}

function WhyField({ draft, set }) {
  return (
    // The modal's anchor — declared as such in radar-move.css, where the lint can
    // read it. It gets the height, the reading-size type and the only accent border
    // here, because every other field records a fact that could be reconstructed
    // from the repo and this one could not.
    <section className="rdr-field" data-anchor="why">
      <h3 className="rdr-eyebrow">
        Why it moved <span className="rdr-required">required</span>
      </h3>
      <textarea
        className="rdr-why-input"
        // The field a reader came to fill in. Focus starts here rather than on the
        // first control, because the ring already carries a sensible default and
        // the reason never can.
        autoFocus
        rows={4}
        // Mirrors the server's own bound rather than inventing a friendlier one: a
        // client limit looser than the server's turns a long reason into a 422 the
        // reader cannot act on.
        maxLength={2000}
        value={draft.why}
        onChange={(e) => set({ why: e.target.value })}
        placeholder="What changed, and what it means for this choice."
        aria-describedby="rdr-why-rule"
      />
      <p className="rdr-field-rule" id="rdr-why-rule">
        A move with no reason is not recordable. This sentence is what the radar shows
        a year from now, not the ring.
      </p>
    </section>
  );
}

function EvidenceField({ draft, set, hint }) {
  const rows = draft.evidence;
  const change = (i, patch) =>
    set({ evidence: rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  return (
    <section className="rdr-field">
      <h3 className="rdr-eyebrow">
        {/* `optional`, not `at least one`. The badge has to match what the form does,
            or it is the form lying about its own rules — and a reader who trusts a
            "required" badge once and then finds it was not will not read the next one. */}
        Evidence <span className="rdr-optional">optional</span>
      </h3>
      {/* The suggestion, and it appears only when it would change what a careful person
          does: the ring changed, or the landing is Adopt or Caution. A hint shown on
          every move is a hint nobody reads. */}
      {hint && <p className="rdr-field-hint" data-tone="suggest">{hint}</p>}
      <ul className="rdr-ev-rows">
        {rows.map((row, i) => (
          <li key={i} className="rdr-ev-row">
            <select className="rdr-select" value={row.kind}
                    aria-label={`Evidence ${i + 1} kind`}
                    onChange={(e) => change(i, { kind: e.target.value })}>
              {KINDS.map((k) => <option key={k.k} value={k.k}>{k.label}</option>)}
            </select>
            <input className="rdr-input" type="text" value={row.title}
                   aria-label={`Evidence ${i + 1} title`}
                   placeholder="Pick a treasure, trace, or ticket…"
                   onChange={(e) => change(i, { title: e.target.value })} />
            <input className="rdr-input" data-size="date" type="date" value={row.dated}
                   aria-label={`Evidence ${i + 1} date`}
                   onChange={(e) => change(i, { dated: e.target.value })} />
            {/* Disabled on the last remaining row rather than hidden. Hiding it would
                make the row look different from its siblings for a reason the reader
                cannot see; disabled says "not down to zero" out loud. */}
            <button type="button" className="rdr-icon-btn"
                    aria-label={`Remove evidence ${i + 1}`}
                    disabled={rows.length === 1}
                    onClick={() => set({ evidence: rows.filter((_, j) => j !== i) })}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="rdr-btn"
              onClick={() => set({ evidence: [...rows, emptyEvidence()] })}>
        Add evidence
      </button>
    </section>
  );
}

export default function MoveBlipModal({ slug, blip, periods = [], onClose, onRecorded }) {
  const [draft, setDraft] = useState(() => ({
    ring: blip.ring,
    period: periodOptions(periods)[0]?.key ?? "",
    why: "",
    evidence: [emptyEvidence()],
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const set = useCallback((patch) => setDraft((d) => ({ ...d, ...patch })), []);

  // Escape closes. A dialog with no keyboard way out is a trap, and this one can be
  // opened by a keyboard user from the header key.
  useEffect(() => {
    const on = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [busy, onClose]);

  const blocked = refusal(draft);

  async function submit(e) {
    e.preventDefault();
    // Re-checked rather than trusted: the key being disabled is a hint to the
    // reader, not a guarantee to the code. Enter in a text field submits a form.
    if (blocked || busy) return;
    setBusy(true);
    setError(null);
    try {
      const moved = await moveBlip(slug, blip.num, payload(draft));
      onRecorded(moved);
    } catch (err) {
      // The draft is deliberately kept. A failed request that also cleared a
      // paragraph of reasoning would cost more than the failure itself.
      setError(err);
      setBusy(false);
    }
  }

  const arrow = draft.ring === blip.ring
    ? `${RING_LABEL[draft.ring]} held in ${draft.period}`
    : `${blip.ring ? RING_LABEL[blip.ring] : "Unplaced"} → ${RING_LABEL[draft.ring]} in ${draft.period}`;

  return (
    // The scrim is a button so a click outside closes, which is what a reader
    // expects of an overlay — and being a real button means the keyboard reaches it
    // too, rather than it being a div that only a mouse can use.
    <div className="rdr-scrim">
      <button type="button" className="rdr-scrim-hit" tabIndex={-1} aria-hidden="true"
              onClick={() => !busy && onClose()} />
      <div className="rdr-modal" role="dialog" aria-modal="true"
           aria-labelledby="rdr-move-title">
        <Head blip={blip} onClose={onClose} />
        <form className="rdr-modal-body" onSubmit={submit}>
          <RingField blip={blip} draft={draft} set={set} periods={periods} />
          <WhyField draft={draft} set={set} />
          <EvidenceField draft={draft} set={set} hint={advice(draft, blip)} />
          <footer className="rdr-modal-foot">
            <span className="rdr-modal-arrow">{arrow}</span>
            {/* One line, and it says which of the two it is. A refusal is something
                the reader can fix; a failed request is not, and telling them apart
                is the difference between retrying and rewriting. */}
            {error ? (
              <span className="rdr-modal-failed">Not recorded. {error.message}</span>
            ) : blocked ? (
              <span className="rdr-modal-blocked">{blocked}</span>
            ) : null}
            <button type="button" className="rdr-btn" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="rdr-btn" data-variant="primary"
                    disabled={!!blocked || busy}>
              {busy ? "Recording…" : "Record move"}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
