/**
 * Radar geometry lab — the arc construction, made visible and adjustable.
 *
 * Exists because the radar's shape is the product of three numbers that are not
 * obvious from looking at it: the corridor width, the seam width, and the factor
 * the drawn arcs are flattened by. Reading the code tells you what they do; moving
 * them tells you why they are set where they are. The clover the flattening fixes
 * is one preset away, which is faster than any explanation of it.
 *
 * It renders the REAL `Radar` component, driven through its `gapH`/`gapV`/`flat`
 * props, and draws the annotations in a second SVG on exactly the same viewBox.
 * There is deliberately NO second copy of the drawing here: a lab that reimplements
 * what it documents starts lying the first time the original changes.
 */
import React, { useMemo, useState } from "react";

import Radar, {
  ARC_FLAT, GAP_H, GAP_V, panelCenter, ringGeometry,
} from "./Radar.jsx";
import {
  QUADRANTS, RINGS, RING_LABEL, arcPath, polar, ringBand, ringEdges,
} from "./geometry.js";

/** The lab's canvas, in user units. Same frame the app asks for, so every number
 *  read off the panel is the number the real radar works with. */
const S = 720;

const DEFAULTS = { gapH: GAP_H, gapV: GAP_V, flat: ARC_FLAT };

/** Named states worth one click. `clover` is the bug the flattening was added for:
 *  wide gaps and no flattening, which is where the four-leaf reading came from. */
const PRESETS = [
  { k: "app", label: "App default", vals: { gapH: 36, gapV: 10, flat: 1.05 } },
  { k: "one", label: "One disc", vals: { gapH: 0, gapV: 0, flat: 1 } },
  { k: "clover", label: "Clover (the bug)", vals: { gapH: 72, gapV: 72, flat: 1 } },
  { k: "over", label: "Over-flattened", vals: { gapH: 36, gapV: 10, flat: 1.35 } },
];

const fmt = (n, d = 1) => (Number.isFinite(n) ? n.toFixed(d) : "—");
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

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
 * Every point the `A` command implies, for one quarter arc.
 *
 * This is the whole geometry of the flattening in one function. An SVG arc is given
 * a radius and an END POINT, never a centre — so for two fixed endpoints, choosing a
 * radius chooses which circle passes through both, and the centre follows from
 * Pythagoras on the chord. A bigger radius puts the centre further behind the chord
 * (`h` grows) and leaves less of the circle in front of it (`sag` shrinks), which is
 * flattening without moving an endpoint.
 *
 * `sag` is the sagitta: chord to arc at its deepest. `drift` measures the thing the
 * eye actually complains about — how far this quarter's apex sits from where one
 * single circle of the same radius would have put it.
 */
function arcFacts({ turn, rr, flat, gapH, gapV }) {
  const P = panelCenter(turn, S, gapH, gapV);
  const deg0 = turn * 90;
  const A = polar(P.x, P.y, rr, deg0);
  const B = polar(P.x, P.y, rr, deg0 + 90);
  const M = { x: (A.x + B.x) / 2, y: (A.y + B.y) / 2 };
  const d = dist(A, B);
  const half = d / 2;
  // A radius under half the chord cannot reach both endpoints, and SVG scales it up
  // to exactly half rather than failing: the semicircle is the most curved arc two
  // fixed points admit, and the slider can be pushed into it.
  const asked = rr * flat;
  const R = Math.max(asked, half);
  const h = Math.sqrt(Math.max(0, R * R - half * half));
  const pm = dist(M, P) || 1;
  const u = { x: (M.x - P.x) / pm, y: (M.y - P.y) / pm };
  const C = { x: M.x - u.x * h, y: M.y - u.y * h };
  const sag = R - h;
  const apex = { x: M.x + u.x * sag, y: M.y + u.y * sag };
  // Where one circle of this radius, around the page centre, would put the apex.
  const apexOne = { x: S / 2 + u.x * rr, y: S / 2 + u.y * rr };
  return {
    P, A, B, M, C, u, d, R, h, sag, apex, apexOne,
    clamped: asked < half,
    drift: dist(apex, apexOne),
    sagTrue: rr * (1 - 1 / Math.SQRT2),
    offset: Math.hypot(gapV / 2, gapH / 2),
  };
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
 * The construction drawn on its own, at a size where the labels fit.
 *
 * The radar itself is a bad teacher for this: four arcs at once, each cut by a
 * corridor, and the centre of any one of them sits under a neighbouring panel. Here
 * one quarter gets the whole frame, with the family of arcs the same two endpoints
 * admit drawn faintly behind the current one.
 */
function ArcDiagram({ flat }) {
  const rr = 160;
  const P = { x: 70, y: 250 };
  const A = polar(P.x, P.y, rr, 0);
  const B = polar(P.x, P.y, rr, 90);
  const M = { x: (A.x + B.x) / 2, y: (A.y + B.y) / 2 };
  const half = dist(A, B) / 2;
  const asked = rr * flat;
  const R = Math.max(asked, half);
  const h = Math.sqrt(Math.max(0, R * R - half * half));
  const u = { x: Math.SQRT1_2, y: -Math.SQRT1_2 };
  const C = { x: M.x - u.x * h, y: M.y - u.y * h };
  const apex = { x: M.x + u.x * (R - h), y: M.y + u.y * (R - h) };
  const family = [Math.SQRT1_2, 0.85, 1, 1.2, 1.5];

  return (
    <svg className="rlab-diagram" viewBox="18 46 266 256"
         aria-label="One quarter arc: chord, centre, radius and sagitta">
      {/* The family: same two endpoints, five radii. Curvature is a choice here. */}
      {family.map((f) => (
        <path key={f} className="rlab-family"
              d={arcPath(P.x, P.y, rr, 0, 90, f)} />
      ))}
      <path className="rlab-arc-live" d={arcPath(P.x, P.y, rr, 0, 90, flat)} />

      <line className="rlab-chord" x1={A.x} y1={A.y} x2={B.x} y2={B.y} />
      <line className="rlab-radius" x1={C.x} y1={C.y} x2={A.x} y2={A.y} />
      <line className="rlab-radius" x1={C.x} y1={C.y} x2={B.x} y2={B.y} />
      {/* The bisector: every centre that can reach both endpoints lies on it. */}
      <line className="rlab-bisector"
            x1={C.x - u.x * 40} y1={C.y - u.y * 40}
            x2={apex.x + u.x * 26} y2={apex.y + u.y * 26} />
      <line className="rlab-sagitta" x1={M.x} y1={M.y} x2={apex.x} y2={apex.y} />

      {/* Anchored away from each other by hand: five labelled points in one quarter
          frame collide under any single uniform offset, and the diagram is only worth
          drawing if every letter is readable. */}
      {[[A, "A", 9, 13, "start"], [B, "B", 9, -7, "start"], [M, "M", 9, -7, "start"],
        [C, "C", -9, 15, "end"], [apex, "apex", 9, -8, "start"]].map(
        ([pt, t, dx, dy, anchor]) => (
          <g key={t}>
            <circle className="rlab-pt" cx={pt.x} cy={pt.y} r={4} />
            <text className="rlab-pt-label" x={pt.x + dx} y={pt.y + dy}
                  textAnchor={anchor}>{t}</text>
          </g>
        ))}
      {/* Each measure rides the segment it measures. */}
      <text className="rlab-measure" x={M.x - 18} y={M.y + 22} textAnchor="end">
        d = {fmt(half * 2)}
      </text>
      <text className="rlab-measure" x={(C.x + A.x) / 2} y={(C.y + A.y) / 2 + 18}
            textAnchor="middle">R = {fmt(R)}</text>
      <text className="rlab-measure" x={(C.x + M.x) / 2 - 12} y={(C.y + M.y) / 2 + 16}
            textAnchor="end">h = {fmt(h)}</text>
      <text className="rlab-measure"
            x={M.x + u.x * ((R - h) / 2) + 15} y={M.y + u.y * ((R - h) / 2) + 19}
            textAnchor="start">s = {fmt(R - h)}</text>
    </svg>
  );
}

export default function GeometryLab() {
  const [gapH, setGapH] = useState(DEFAULTS.gapH);
  const [gapV, setGapV] = useState(DEFAULTS.gapV);
  const [flat, setFlat] = useState(DEFAULTS.flat);
  const [counts, setCounts] = useState({ adopt: 9, trial: 4, assess: 3, caution: 3 });
  const [turn, setTurn] = useState(0);
  const [ring, setRing] = useState("caution");
  const [show, setShow] = useState({
    ghost: true, centres: true, build: true, unflat: true, blips: true,
  });

  const blips = useMemo(() => synthBlips(counts), [counts]);
  const edges = useMemo(() => ringEdges(counts), [counts]);
  const { r } = ringGeometry("full", S, S, gapH);
  const rr = ringBand(ring, edges)[1] * r;
  const f = arcFacts({ turn, rr, flat, gapH, gapV });
  const q = QUADRANTS[turn];
  const flip = (k) => setShow((s) => ({ ...s, [k]: !s[k] }));
  const apply = (v) => { setGapH(v.gapH); setGapV(v.gapV); setFlat(v.flat); };

  return (
    <div className="rdr-page rlab">
      <header className="rlab-head">
        <h1 className="rlab-title">Radar geometry</h1>
        <p className="rlab-lede">
          The real radar component, driven by the three numbers that decide its shape.
          The annotations are the construction an SVG arc command implies but never
          states: two fixed endpoints, one radius, and the centre that follows.
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
          <Slider label="ARC_FLAT · drawn radius ×" value={flat} min={0.64} max={1.5}
                  step={0.01} onChange={setFlat} />
          {f.clamped && (
            <p className="rlab-note">
              Radius below half the chord — SVG scales it back up to a semicircle,
              the most curved arc these two endpoints allow.
            </p>
          )}

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
              <Toggle on={show.ghost} onChange={() => flip("ghost")}>One-circle ghost</Toggle>
              <Toggle on={show.centres} onChange={() => flip("centres")}>Panel centres</Toggle>
              <Toggle on={show.build} onChange={() => flip("build")}>Construction</Toggle>
              <Toggle on={show.unflat} onChange={() => flip("unflat")}>Unflattened arc</Toggle>
              <Toggle on={show.blips} onChange={() => flip("blips")}>Blips</Toggle>
            </div>
          </div>
        </aside>

        <div className="rlab-stage-col">
          <div className="rlab-stage">
            <Radar mode="full" blips={show.blips ? blips : []} edges={edges}
                   gapH={gapH} gapV={gapV} flat={flat} width={S} height={S} />

            {/* Same viewBox, same box, so a coordinate here is a coordinate there. */}
            <svg className="rlab-overlay" viewBox={`0 0 ${S} ${S}`} aria-hidden="true">
              {show.ghost && RINGS.map((k) => (
                <circle key={k} className="rlab-ghost" cx={S / 2} cy={S / 2}
                        r={ringBand(k, edges)[1] * r} />
              ))}

              {show.unflat && (
                <path className="rlab-unflat"
                      d={arcPath(f.P.x, f.P.y, rr, turn * 90, turn * 90 + 90, 1)} />
              )}

              {show.centres && (
                <>
                  {QUADRANTS.map((qq) => {
                    const p = panelCenter(qq.turn, S, gapH, gapV);
                    return (
                      <g key={qq.k} className="rlab-centre">
                        <line x1={p.x - 7} y1={p.y} x2={p.x + 7} y2={p.y} />
                        <line x1={p.x} y1={p.y - 7} x2={p.x} y2={p.y + 7} />
                      </g>
                    );
                  })}
                  <g className="rlab-centre" data-true="true">
                    <line x1={S / 2 - 11} y1={S / 2} x2={S / 2 + 11} y2={S / 2} />
                    <line x1={S / 2} y1={S / 2 - 11} x2={S / 2} y2={S / 2 + 11} />
                  </g>
                  <text className="rlab-pt-label" x={S / 2 + 15} y={S / 2 + 22}>O</text>
                </>
              )}

              {show.build && (
                <>
                  <line className="rlab-chord" x1={f.A.x} y1={f.A.y} x2={f.B.x} y2={f.B.y} />
                  <line className="rlab-radius" x1={f.C.x} y1={f.C.y} x2={f.A.x} y2={f.A.y} />
                  <line className="rlab-radius" x1={f.C.x} y1={f.C.y} x2={f.B.x} y2={f.B.y} />
                  <line className="rlab-bisector"
                        x1={f.C.x} y1={f.C.y}
                        x2={f.apex.x + f.u.x * 22} y2={f.apex.y + f.u.y * 22} />
                  <line className="rlab-sagitta" x1={f.M.x} y1={f.M.y}
                        x2={f.apex.x} y2={f.apex.y} />
                  {/* Same letters as the isolated diagram, so the two read as one
                      explanation rather than two drawings. */}
                  {[[f.A, "A"], [f.B, "B"], [f.M, "M"], [f.C, "C"], [f.apex, "apex"]].map(
                    ([pt, t]) => (
                      <g key={t}>
                        <circle className="rlab-pt" cx={pt.x} cy={pt.y} r={t === "C" ? 5 : 4} />
                        <text className="rlab-pt-label" x={pt.x + 9} y={pt.y - 9}>{t}</text>
                      </g>
                    ))}
                  {/* Hollow: where a single circle would have put this apex. */}
                  <circle className="rlab-pt-ghost" cx={f.apexOne.x} cy={f.apexOne.y} r={6} />
                </>
              )}
            </svg>
          </div>

          <p className="rlab-caption">
            Annotating the <strong>{q.label}</strong> quadrant at the outer edge of
            <strong> {RING_LABEL[ring]}</strong>. C is the centre SVG derives for the
            drawn arc; the hollow ring is where one circle around O would have put the
            same apex. The gap between them is the four-leaf reading.
          </p>
        </div>

        <div className="rlab-facts">
          <ArcDiagram flat={flat} />

          <table className="rlab-table">
            <tbody>
              <tr><th>ring radius rr</th><td>{fmt(rr)}</td><td>outer edge, {RING_LABEL[ring]}</td></tr>
              <tr><th>chord d</th><td>{fmt(f.d)}</td><td>rr·√2 — fixed by the endpoints</td></tr>
              <tr><th>drawn radius R</th><td>{fmt(f.R)}</td><td>rr × {flat}</td></tr>
              <tr><th>centre offset h</th><td>{fmt(f.h)}</td><td>√(R² − (d/2)²)</td></tr>
              <tr><th>sagitta s</th><td>{fmt(f.sag)}</td><td>R − h — the bulge</td></tr>
              <tr><th>s at flat = 1</th><td>{fmt(f.sagTrue)}</td><td>rr(1 − 1/√2)</td></tr>
              <tr className="rlab-row-key">
                <th>bulge removed</th>
                <td>{fmt(f.sagTrue - f.sag)}</td>
                <td>{fmt(((f.sagTrue - f.sag) / (rr || 1)) * 100, 2)}% of rr</td>
              </tr>
              <tr><th>panel offset |PO|</th><td>{fmt(f.offset)}</td><td>√((GAP_V/2)² + (GAP_H/2)²)</td></tr>
              <tr className="rlab-row-key">
                <th>apex drift</th><td>{fmt(f.drift)}</td>
                <td>drawn apex vs one circle</td>
              </tr>
              <tr>
                <th>ring edges</th>
                <td colSpan={2}>
                  {RINGS.map((k) => `${RING_LABEL[k]} ${(edges[k] * 100).toFixed(0)}%`).join(" · ")}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
