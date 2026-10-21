/**
 * Radar geometry lab — the band-clip construction, made visible and adjustable.
 *
 * Exists because the radar's shape is the product of two numbers that are not
 * obvious from looking at it: the width of the horizontal strip (the label
 * corridor) and the width of the vertical strip (the seam). Reading the code
 * tells you what they do; moving them tells you why they are set where they are.
 *
 * It renders the REAL `Radar` component, driven through its `gapH`/`gapV` props,
 * and draws the annotations in a second SVG on exactly the same viewBox. There is
 * deliberately NO second copy of the drawing here: a lab that reimplements what
 * it documents starts lying the first time the original changes.
 */
import React, { useMemo, useState } from "react";

import Radar, {
  GAP_H, GAP_V, ringGeometry,
} from "./Radar.jsx";
import {
  QUADRANTS, RINGS, RING_LABEL, arcPath, ringBand, ringEdges,
} from "./geometry.js";

/** The lab's canvas, in user units. Same frame the app asks for, so every number
 *  read off the panel is the number the real radar works with. */
const S = 720;

const DEFAULTS = { gapH: GAP_H, gapV: GAP_V };

/** Named states worth one click. The four-leaf reading the old presets built
 *  towards is no longer reachable from here — it took four translated centres,
 *  and this construction only ever has one. These four instead show what the
 *  two strip widths do on their own: the app's own numbers, a wide label
 *  corridor, a symmetric cross, and no gaps at all. */
const PRESETS = [
  { k: "app", label: "App default", vals: { gapH: 36, gapV: 10 } },
  { k: "wide", label: "Wide corridor", vals: { gapH: 90, gapV: 10 } },
  { k: "cross", label: "Even cross", vals: { gapH: 60, gapV: 60 } },
  { k: "none", label: "No gaps", vals: { gapH: 0, gapV: 0 } },
];

const fmt = (n, d = 1) => (Number.isFinite(n) ? n.toFixed(d) : "—");

/**
 * Blips from ring counts, so the occupancy sizing has something to size for.
 *
 * Spread round-robin across quadrants: the lab is about radii, and an even angular
 * spread keeps a band's blips from piling into one sector where they would say more
 * about `placeBlips` than about the bands.
 */
function synthBlips(counts) {
  const out = [];
  let num = 1;
  RINGS.forEach((ring) => {
    for (let i = 0; i < (counts[ring] || 0); i += 1) {
      out.push({
        num: num++, name: `${RING_LABEL[ring]} ${i + 1}`, ring,
        quadrant: QUADRANTS[i % 4].k, state: "held", evidenceAgeDays: 0,
      });
    }
  });
  return out;
}

/**
 * The band-clip construction, read back as numbers.
 *
 * The radar's shape is now a CLIP: one disc, minus a horizontal strip and a
 * vertical strip. There is no bulge to measure and no drawn radius to compare
 * against a true one — every arc IS on the true circle by construction. What is
 * left worth marking is where a ring's arc runs into each strip (H and V, both
 * exactly `rr` from the centre) and how much angle the strip leaves open at that
 * radius, which is the thing a reader actually needs when placing a label.
 */
function bandFacts({ turn, rr, gapH, gapV }) {
  const gh = gapH / 2;
  const gv = gapV / 2;
  const P = { x: S / 2, y: S / 2 };                 // the centre never moves
  const [ex, ey] = [[1, 1], [-1, 1], [-1, -1], [1, -1]][turn];
  const atH = Math.sqrt(Math.max(0, rr * rr - gh * gh));
  const atV = Math.sqrt(Math.max(0, rr * rr - gv * gv));
  // The two points where this ring's arc runs into the two strips. Both are exactly
  // rr from the centre, which is the whole property the model buys.
  const H = { x: P.x + ex * atH, y: P.y - ey * gh };
  const V = { x: P.x + ex * gv, y: P.y - ey * atV };
  const corner = Math.hypot(gv, gh);
  // Odd turns sweep from the vertical axis, so the strips swap which end they bound.
  const [first, second] = turn % 2 === 0 ? [gh, gv] : [gv, gh];
  const lo = (Math.asin(Math.min(1, first / rr)) * 180) / Math.PI;
  const hi = (Math.acos(Math.min(1, second / rr)) * 180) / Math.PI;
  return { P, H, V, corner, gh, gv, lo, hi, span: Math.max(0, hi - lo),
           rH: Math.hypot(H.x - P.x, H.y - P.y), rV: Math.hypot(V.x - P.x, V.y - P.y),
           swallowed: rr <= corner };
}

function Slider({ label, value, min, max, step, onChange, suffix = "" }) {
  return (
    <label className="rlab-slider">
      <span className="rlab-slider-top">
        <span className="rlab-slider-name">{label}</span>
        <span className="rlab-slider-val">{value}{suffix}</span>
      </span>
      <input type="range" min={min} max={max} step={step} value={value}
             onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

function Toggle({ on, onChange, children }) {
  return (
    <button type="button" className="rlab-toggle" aria-pressed={on}
            onClick={() => onChange(!on)}>
      {children}
    </button>
  );
}

/**
 * The three constructions the radar does NOT use, kept as the decision record.
 *
 * Each was tried, each measured, each rejected — the numbers are from a controlled
 * comparison at radius 200 with a 26-unit gap, not a live function of the sliders,
 * so they are captions here rather than table rows. Only the outer ring edge of
 * all four quadrants is drawn against a dashed reference circle: enough to show
 * the outline shape, nothing to read numbers off.
 */
function AlternativesFigure() {
  const CX = 120;
  const CY = 120;
  const R = 100;
  const OFFSET = 13;
  const turns = [0, 1, 2, 3];

  const inset = ((13 / 100) * 180) / Math.PI / 2;
  const rotated = turns.map((turn) => (
    <path key={turn} className="rlab-arc-live"
          d={arcPath(CX, CY, R, turn * 90 + inset, turn * 90 + 90 - inset)} />
  ));

  const slidCentres = turns.map((turn) => {
    const bis = ((turn * 90 + 45) * Math.PI) / 180;
    return { turn, cx: CX + OFFSET * Math.cos(bis), cy: CY - OFFSET * Math.sin(bis) };
  });
  const slid = slidCentres.map(({ turn, cx, cy }) => (
    <path key={turn} className="rlab-arc-live"
          d={arcPath(cx, cy, R, turn * 90, turn * 90 + 90)} />
  ));
  const slidAndCompensated = slidCentres.map(({ turn, cx, cy }) => (
    <path key={turn} className="rlab-arc-live"
          d={arcPath(cx, cy, R, turn * 90, turn * 90 + 90, 1.05)} />
  ));

  const alts = [
    {
      key: "rotated", title: "Rotated apart, one centre", dev: "0.03", arcs: rotated,
      why: "a wedge-shaped gap cannot carry a label row of constant height.",
    },
    {
      key: "slid", title: "Slid outward, four centres", dev: "4.99", arcs: slid,
      why: "it is an exploded pie.",
    },
    {
      key: "comp", title: "Slid + arcs flattened 5%", dev: "1.17", arcs: slidAndCompensated,
      why: "it compensates the symptom and leaves the arcs off true.",
    },
  ];

  return (
    <div>
      <p className="rlab-alts-head">
        Constructions the radar does NOT use — deviations measured at radius 200
        with a 26-unit gap.
      </p>
      <div className="rlab-alts">
        {alts.map((a) => (
          <figure key={a.key} className="rlab-alt">
            <svg className="rlab-diagram" viewBox="0 0 240 240" aria-label={a.title}>
              <circle className="rlab-ghost" cx={CX} cy={CY} r={R} />
              {a.arcs}
            </svg>
            <figcaption>
              <b>{a.title}</b>
              <span className="dev">deviation {a.dev}</span> — rejected: {a.why}
            </figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}

export default function GeometryLab() {
  const [gapH, setGapH] = useState(DEFAULTS.gapH);
  const [gapV, setGapV] = useState(DEFAULTS.gapV);
  const [counts, setCounts] = useState({ adopt: 9, trial: 4, assess: 3, caution: 3 });
  const [turn, setTurn] = useState(0);
  const [ring, setRing] = useState("caution");
  const [show, setShow] = useState({
    strips: true, centre: true, arcEnds: true, blips: true,
  });

  const blips = useMemo(() => synthBlips(counts), [counts]);
  const edges = useMemo(() => ringEdges(counts), [counts]);
  const { r } = ringGeometry("full", S, S);
  const rr = ringBand(ring, edges)[1] * r;
  const f = bandFacts({ turn, rr, gapH, gapV });
  const q = QUADRANTS[turn];
  const flip = (k) => setShow((s) => ({ ...s, [k]: !s[k] }));
  const apply = (v) => { setGapH(v.gapH); setGapV(v.gapV); };

  return (
    <div className="rdr-page rlab">
      <header className="rlab-head">
        <h1 className="rlab-title">Radar geometry</h1>
        <p className="rlab-lede">
          The real radar component, driven by the two numbers that decide its shape.
          The radar is one disc with a horizontal strip and a vertical strip cut out
          of it — never four discs around four centres — so every arc it draws sits
          on the same circle and both corridors stay a constant width at any radius.
          The annotations below mark where a ring's arc meets a strip edge, and how
          much angular room the strip leaves open at that radius.
        </p>
      </header>

      <div className="rlab-body">
        <aside className="rlab-controls">
          <div className="rlab-presets">
            {PRESETS.map((p) => (
              <button key={p.k} type="button" className="rlab-preset"
                      onClick={() => apply(p.vals)}>{p.label}</button>
            ))}
          </div>

          <Slider label="GAP_H · label corridor" value={gapH} min={0} max={140} step={2}
                  onChange={setGapH} />
          <Slider label="GAP_V · vertical seam" value={gapV} min={0} max={140} step={2}
                  onChange={setGapV} />

          <div className="rlab-group">
            <span className="rlab-group-name">Blips per ring</span>
            <div className="rlab-counts">
              {RINGS.map((k) => (
                <label key={k} className="rlab-count">
                  <span>{RING_LABEL[k]}</span>
                  <input type="number" min={0} max={40} value={counts[k]}
                         onChange={(e) => setCounts((c) => ({
                           ...c, [k]: Math.max(0, Number(e.target.value) || 0) }))} />
                </label>
              ))}
            </div>
          </div>

          <div className="rlab-group">
            <span className="rlab-group-name">Annotate</span>
            <div className="rlab-chips">
              {QUADRANTS.map((qq) => (
                <button key={qq.k} type="button" className="rlab-chip"
                        aria-pressed={qq.turn === turn} onClick={() => setTurn(qq.turn)}>
                  {qq.label.split(" ")[0]}
                </button>
              ))}
            </div>
            <div className="rlab-chips">
              {RINGS.map((k) => (
                <button key={k} type="button" className="rlab-chip"
                        aria-pressed={k === ring} onClick={() => setRing(k)}>
                  {RING_LABEL[k]}
                </button>
              ))}
            </div>
          </div>

          <div className="rlab-group">
            <span className="rlab-group-name">Overlays</span>
            <div className="rlab-chips">
              <Toggle on={show.strips} onChange={() => flip("strips")}>Strips</Toggle>
              <Toggle on={show.centre} onChange={() => flip("centre")}>Centre</Toggle>
              <Toggle on={show.arcEnds} onChange={() => flip("arcEnds")}>Arc ends</Toggle>
              <Toggle on={show.blips} onChange={() => flip("blips")}>Blips</Toggle>
            </div>
          </div>
        </aside>

        <div className="rlab-stage-col">
          <div className="rlab-stage">
            <Radar mode="full" blips={show.blips ? blips : []} edges={edges}
                   gapH={gapH} gapV={gapV} width={S} height={S} />

            {/* Same viewBox, same box, so a coordinate here is a coordinate there. */}
            <svg className="rlab-overlay" viewBox={`0 0 ${S} ${S}`} aria-hidden="true">
              {show.strips && (
                <>
                  <rect className="rlab-strip" x={0} y={S / 2 - f.gh} width={S} height={2 * f.gh} />
                  <rect className="rlab-strip" x={S / 2 - f.gv} y={0} width={2 * f.gv} height={S} />
                </>
              )}

              {show.arcEnds && (
                <>
                  {RINGS.map((k) => (
                    <circle key={k} className="rlab-ghost" cx={S / 2} cy={S / 2}
                            r={ringBand(k, edges)[1] * r} />
                  ))}
                  <circle className="rlab-pt" cx={f.H.x} cy={f.H.y} r={4} />
                  <text className="rlab-pt-label" x={f.H.x + 9} y={f.H.y - 9}>H</text>
                  <circle className="rlab-pt" cx={f.V.x} cy={f.V.y} r={4} />
                  <text className="rlab-pt-label" x={f.V.x + 9} y={f.V.y - 9}>V</text>
                </>
              )}

              {show.centre && (
                <>
                  <g className="rlab-centre" data-true="true">
                    <line x1={S / 2 - 11} y1={S / 2} x2={S / 2 + 11} y2={S / 2} />
                    <line x1={S / 2} y1={S / 2 - 11} x2={S / 2} y2={S / 2 + 11} />
                  </g>
                  <text className="rlab-pt-label" x={S / 2 + 15} y={S / 2 + 22}>O</text>
                </>
              )}
            </svg>
          </div>

          <p className="rlab-caption">
            Annotating the <strong>{q.label}</strong> quadrant at the outer edge of
            <strong> {RING_LABEL[ring]}</strong>. H and V mark where this ring's arc
            runs into the horizontal and vertical strips — both sit exactly rr from
            O, which is the property the single-centre construction buys.
          </p>
          {show.arcEnds && (
            <p className="rlab-caption">
              The dashed reference circle and the drawn ring edge land on top of
              each other here — under this construction they are not two curves to
              compare, they are the same curve.
            </p>
          )}
        </div>

        <div className="rlab-facts">
          <AlternativesFigure />

          <table className="rlab-table">
            <tbody>
              <tr><th>ring radius rr</th><td>{fmt(rr)}</td><td>outer edge, {RING_LABEL[ring]}</td></tr>
              <tr>
                <th>strip half-widths</th>
                <td>{fmt(f.gh)} / {fmt(f.gv)}</td>
                <td>GAP_H/2 and GAP_V/2</td>
              </tr>
              <tr><th>arc end H</th><td>{fmt(f.rH)}</td><td>distance from O — equals rr</td></tr>
              <tr><th>arc end V</th><td>{fmt(f.rV)}</td><td>distance from O — equals rr</td></tr>
              <tr>
                <th>angular window</th>
                <td>{fmt(f.lo)}° → {fmt(f.hi)}°</td>
                <td>what the strips leave at this radius</td>
              </tr>
              <tr className="rlab-row-key">
                <th>window width</th>
                <td>{fmt(f.span)}°</td>
                <td>shrinks as the radius shrinks</td>
              </tr>
              <tr>
                <th>cross corner</th>
                <td>{fmt(f.corner)}</td>
                <td>hypot(gv, gh) — a ring inside this vanishes</td>
              </tr>
              <tr className="rlab-row-key">
                <th>outline deviation</th>
                <td>0.00</td>
                <td>one centre: every arc on one circle</td>
              </tr>
              <tr>
                <th>ring edges</th>
                <td colSpan={2}>
                  {RINGS.map((k) => `${RING_LABEL[k]} ${(edges[k] * 100).toFixed(0)}%`).join(" · ")}
                </td>
              </tr>
              {f.swallowed && (
                <tr>
                  <th>swallowed</th>
                  <td colSpan={2}>This ring lies entirely inside the cross the two strips form.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
