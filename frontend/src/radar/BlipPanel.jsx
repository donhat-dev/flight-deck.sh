/**
 * The blip panel — the anchor of the blip-focus view.
 *
 * Two tabs at the same level, one visible at a time. `Blip summary` answers "what
 * is this and where does it stand"; `Why it moved` answers "how did it get there".
 * They are siblings rather than stacked sections because a reader is doing one of
 * those two things, never both, and stacking them made the panel a scroll.
 *
 * The panel deliberately runs taller than its content. The instruction was to
 * accept intentional emptiness rather than fill it with small UI: the room is held
 * open for what lands here later, and a panel that shrinks to fit would keep
 * changing size as blips differ.
 */
import React, { useState } from "react";

import BlipMark from "./BlipGlyph.jsx";
import { RINGS, RING_LABEL, isStale } from "./geometry.js";
import { EVIDENCE, MOVES, SUMMARY } from "./seed.js";

const TABS = [
  { k: "summary", label: "Blip summary" },
  { k: "why", label: "Why it moved" },
];

/** One icon per evidence kind, so the list scans without reading the labels. */
function EvidenceMark({ kind }) {
  const d = {
    treasure: "M6 2h8l4 5-8 11L2 7z",
    trace: "M2 6h4l2-3h6l2 3h4v12H2z M12 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
    jira: "M3 5h14v10H3z M6 9h8 M6 12h5",
  }[kind] || "M4 4h12v12H4z";
  return (
    <svg className="rdr-ev-mark" viewBox="0 0 20 20" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

function Position({ ring }) {
  return (
    <section className="rdr-block">
      <h3 className="rdr-eyebrow">Position</h3>
      <div className="rdr-track">
        {[...RINGS].reverse().map((r) => (
          <div key={r} className="rdr-track-seg" aria-current={r === ring ? "true" : undefined}>
            <span className="rdr-track-bar" />
            <span className="rdr-track-label">{RING_LABEL[r]}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function Summary({ blip }) {
  const evidence = EVIDENCE[blip.num] || [];
  return (
    <>
      <Position ring={blip.ring} />
      <section className="rdr-block rdr-lede-block">
        <p className="rdr-lede">{SUMMARY[blip.num] || blip.why}</p>
      </section>
      <section className="rdr-block">
        <h3 className="rdr-eyebrow">
          Evidence <span className="rdr-count">{evidence.length}</span>
        </h3>
        <ul className="rdr-ev-list">
          {evidence.map((e) => (
            <li key={e.title} className="rdr-ev">
              <EvidenceMark kind={e.kind} />
              <span className="rdr-ev-title">{e.title}</span>
              <span className="rdr-ev-kind">{e.kind}</span>
              <span className="rdr-ev-date">{e.date}</span>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

function Why({ blip }) {
  const moves = MOVES[blip.num] || [];
  return (
    <section className="rdr-block">
      <ol className="rdr-moves">
        {moves.map((m, i) => (
          <li key={`${m.quarter}-${m.ring ?? "entered"}`} className="rdr-move"
              data-current={i === 0 ? "true" : undefined}>
            <span className="rdr-move-rail" aria-hidden="true">
              <span className="rdr-move-dot" />
              {i < moves.length - 1 && <span className="rdr-move-line" />}
            </span>
            <div className="rdr-move-body">
              <p className="rdr-move-head">
                <span className="rdr-move-when">{m.quarter}</span>
                <span className="rdr-move-arrow" aria-hidden="true">→</span>
                <span className="rdr-move-ring" data-ring={m.ring || undefined}>
                  {m.ring ? RING_LABEL[m.ring] : "Entered"}
                </span>
              </p>
              <p className="rdr-move-why">{m.why}</p>
              {m.evidence.length > 0 && (
                <p className="rdr-move-ev">
                  {m.evidence.map((e) => (
                    <span key={e} className="rdr-chip">{e}</span>
                  ))}
                </p>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function BlipPanel({ blip }) {
  const [tab, setTab] = useState("summary");
  const moves = MOVES[blip.num] || [];
  return (
    <article className="rdr-panel">
      <div className="rdr-tabs" role="tablist" aria-label="Blip detail">
        {TABS.map((t) => (
          <button
            key={t.k}
            type="button"
            role="tab"
            className="rdr-tab"
            aria-selected={t.k === tab}
            onClick={() => setTab(t.k)}
          >
            {t.label}
            {t.k === "why" && moves.length > 0 && (
              <span className="rdr-count">{moves.length}</span>
            )}
          </button>
        ))}
      </div>

      <header className="rdr-panel-head">
        <p className="rdr-eyebrow">Blip</p>
        <h2 className="rdr-blip-name">
          <BlipMark blip={blip} />
          {blip.name}
          {isStale(blip) && <span className="rdr-stale">evidence {blip.evidenceAgeDays}d old</span>}
        </h2>
      </header>

      {tab === "summary" ? <Summary blip={blip} /> : <Why blip={blip} />}
    </article>
  );
}
