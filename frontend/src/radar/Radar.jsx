/**
 * The radar itself, as SVG.
 *
 * One component serves both views because the difference is a frame, not a
 * drawing: the full radar centres the origin and sweeps 360°, the quadrant view
 * puts the origin in the bottom-left corner and sweeps 90°. Everything past that
 * — ring bands, blip placement, the movement arc — is the same math, and keeping
 * it in one place is what stops the two views from drifting apart.
 *
 * Colour lives in the stylesheet, not here. Each blip carries `data-quadrant` and
 * the sheet maps that to `--rdr-q`, so the palette is themeable, is visible to the
 * composition lint, and never needs a second copy in JS.
 */
import React from "react";

import { BlipGlyph } from "./BlipGlyph.jsx";
import {
  QUADRANTS, RINGS, RING_EDGE, RING_LABEL, arcFacing, arcPath, isStale, placeBlips,
  polar, quadrantOf, ringBand, sectorPath, spreadMids,
} from "./geometry.js";

/** Minimum distance between adjacent ring-label anchors, in user units. Sized for
 *  the widest word — CAUTION at the corridor's 14px bold mono with tracking — so
 *  no pair of neighbours can touch whatever the occupancy sizing does to the bands. */
const LABEL_GAP = 78;

/** Blip diameter in user units. Still bigger than a dot on purpose — the blips are
 *  the content and the rings are only the frame — but no longer 30.
 *
 *  Came down from 30/40 once a real radar was loaded. The seed spread 8 Adopt blips
 *  over four quadrants, so nothing collided; FlightDeck's own radar puts FIVE
 *  techniques blips in Adopt, which is the innermost and therefore smallest-area
 *  band, and two of them overlapped. 22 is ~47% of the old area, which clears that
 *  cell without making the numerals unreadable.
 *
 *  The geometry test did not catch it: it requires 6° OR 0.02 radius of separation,
 *  and that is satisfied by two blips that still visually touch at d=30. The
 *  threshold is honest about angular distance and silent about drawn size. */
const D = 22;
const D_SELECTED = 30;

/** The numeral, as a fraction of the blip. It used to be a fixed `0.75rem` in the
 *  stylesheet — 12 user units whatever the blip measured — so shrinking the circle
 *  alone would have pushed the digits past its edge. Tying it to D keeps one number
 *  in charge of the pair. */
const NUM_RATIO = 0.44;

function ringGeometry(mode, width, height) {
  if (mode === "full") {
    const s = Math.min(width, height);
    return { cx: s / 2, cy: s / 2, r: (s - GAP_H) / 2 - 6, vb: `0 0 ${s} ${s}`, s };
  }
  // Quadrant: origin bottom-left, with room under the axis for the ring labels.
  const pad = 20;
  const labels = 30;
  const cy = height - labels;
  const r = Math.min(width - pad, cy - pad);
  return { cx: pad, cy, r, vb: `0 0 ${width} ${height}` };
}

function Blip({ b, cx, cy, r, selected, onSelect }) {
  const p = polar(cx, cy, b.rFrac * r, b.deg);
  const d = selected ? D_SELECTED : D;
  const facing = arcFacing(b.state, b.deg);
  const stale = isStale(b);
  return (
    <g
      className="rdr-blip"
      data-quadrant={b.quadrant}
      data-state={b.state}
      data-stale={stale ? "true" : undefined}
      aria-pressed={selected ? "true" : "false"}
      role="button"
      tabIndex={0}
      onClick={() => onSelect?.(b.num)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect?.(b.num); }
      }}
    >
      <title>{`${b.num}. ${b.name} — ${RING_LABEL[b.ring]}`}</title>
      <BlipGlyph cx={p.x} cy={p.y} d={d} state={b.state} facing={facing} selected={selected} />
      {/* Through a custom property rather than the `font-size` attribute: a CSS
          declaration beats a presentation attribute, so the stylesheet's own value
          would win and the numeral would stay 12 units on a 22-unit blip. */}
      <text className="rdr-blip-num" x={p.x} y={p.y}
            style={{ "--rdr-blip-num-size": `${d * NUM_RATIO}px` }}>{b.num}</text>
    </g>
  );
}

/** The two gaps between quadrants, in user units — kept as separate constants
 *  because they answer different questions.
 *
 *  GAP_H is the horizontal corridor: the LABEL ROW. The full radar is drawn as
 *  two half discs, split along the HORIZONTAL axis, because a single disc leaves
 *  nowhere to put readable ring labels except on top of the drawing. It is sized
 *  to the ring-label line with a little air, nothing more — the ring names read
 *  left-to-right, so they only ever need a horizontal row that fits them.
 *
 *  GAP_V is the vertical gap between the left and right quadrants. It carries no
 *  text — the ring names never run top-to-bottom — so it is only a SEAM: wide
 *  enough to mark where one quadrant ends and the next begins, far too narrow
 *  for a label. That is why it is a second, much smaller constant rather than
 *  the same GAP reused on both axes. */
const GAP_H = 36;
const GAP_V = 10;

/** Each quadrant's own centre: the inner corner of its half. `deg` stays GLOBAL
 *  (turn 1 still sweeps 90–180°), so this is still only a translation of centres —
 *  none of the placement math changes. Both offsets apply now: y opens the
 *  horizontal corridor (turns 0/1 are the top half, turns 2/3 the bottom half),
 *  and x opens the vertical seam (turns 0/3 are the right panels, turns 1/2 the
 *  left). */
function panelCenter(turn, s) {
  const offH = GAP_H / 2;
  const offV = GAP_V / 2;
  const x = turn === 0 || turn === 3 ? s / 2 + offV : s / 2 - offV;
  const y = turn <= 1 ? s / 2 - offH : s / 2 + offH;
  return { x, y };
}

export default function Radar({
  mode = "full",
  blips,
  quadrant = null,
  selectedNum = null,
  onSelect,
  width = 880,
  height = 880,
  // The board labels. Geometry (keys, turns) stays the module constant; only the words
  // a radar calls its quadrants come from outside, because they are per-radar prose.
  quadrants = QUADRANTS,
  // Ring edges sized by the board's own occupancy (geometry.ringEdges). The fixed
  // RING_EDGE default keeps standalone renders and old callers working.
  edges = RING_EDGE,
}) {
  const { cx, cy, r, vb, s } = ringGeometry(mode, width, height);
  const turns = mode === "full" ? QUADRANTS.map((q) => q.turn) : [0];
  const placed = placeBlips(blips, { quadrant, edges });
  const centerFor = (turn) => (mode === "full" ? panelCenter(turn, s) : { x: cx, y: cy });

  return (
    <svg className="rdr-canvas" viewBox={vb} role="img"
         aria-label={mode === "full" ? "Radar, four quadrants"
           : `Radar, ${(quadrants.find((q) => q.k === quadrant) ?? quadrantOf(quadrant)).label}`}>
      {/* Rings outer-first so the inner ones paint on top of their neighbours'
          edges rather than under them. FILL only — the boundary is drawn below as
          an open arc, because stroking a closed sector also strokes its two radial
          cuts, and those read as a chamfer at every panel's square corner. */}
      {[...RINGS].reverse().map((ring) => {
        const [lo, hi] = ringBand(ring, edges);
        return turns.map((turn) => {
          const c = centerFor(turn);
          return (
            <path
              key={`${ring}-${turn}`}
              className="rdr-ring"
              data-ring={ring}
              d={sectorPath(c.x, c.y, lo * r, hi * r, turn * 90, turn * 90 + 90)}
            />
          );
        });
      })}

      {/* Ring boundaries: one open arc per ring per quadrant, concentric only. */}
      {RINGS.map((ring) => {
        const [, hi] = ringBand(ring, edges);
        return turns.map((turn) => {
          const c = centerFor(turn);
          return (
            <path
              key={`edge-${ring}-${turn}`}
              className="rdr-ring-arc"
              data-ring={ring}
              d={arcPath(c.x, c.y, hi * r, turn * 90, turn * 90 + 90)}
            />
          );
        });
      })}

      {/* Ring labels. Full view: in the horizontal corridor, both halves, mirrored —
          the reference layout, and the reason the corridor exists. Quadrant view: under
          the axis, as before. Band midpoints move with the occupancy-sized edges, so a
          busy ring's label rides its wider band automatically. */}
      {(() => {
        const mids = spreadMids(
          RINGS.map((ring) => {
            const [lo, hi] = ringBand(ring, edges);
            return ((lo + hi) / 2) * r;
          }),
          // The anchor is textAnchor="middle" and sits GAP_V/2+mid (full) or cx+mid
          // (quadrant) from the viewBox edge, so the bound must leave half of the
          // widest word (CAUTION, ~80 units) inside the drawing, or that half gets
          // clipped by the SVG edge — r-44 keeps the whole word on canvas in both modes.
          { gap: LABEL_GAP, max: r - 44 },
        );
        if (mode === "full") {
          return RINGS.map((ring, i) => [-1, 1].map((side) => (
            <text key={`${ring}-${side}`} className="rdr-ring-label" data-ring={ring}
                  x={s / 2 + side * (GAP_V / 2 + mids[i])} y={s / 2} textAnchor="middle"
                  dominantBaseline="central">
              {RING_LABEL[ring].toUpperCase()}
            </text>
          )));
        }
        return RINGS.map((ring, i) => (
          <text key={ring} className="rdr-ring-label" data-ring={ring}
                x={cx + mids[i]} y={cy + 20} textAnchor="middle">
            {RING_LABEL[ring].toUpperCase()}
          </text>
        ));
      })()}

      {/* Quadrant names at display size, in each panel's OUTER corner — the empty
          space the quarter-disc geometry leaves there is exactly label-sized. */}
      {mode === "full" && quadrants.map((q) => {
        const right = q.turn === 0 || q.turn === 3;
        const bottom = q.turn === 2 || q.turn === 3;
        return (
          <text key={q.k} className="rdr-quadrant-label" data-quadrant={q.k}
                x={right ? s - 4 : 4}
                y={bottom ? s - 12 : 34}
                textAnchor={right ? "end" : "start"}>
            {q.label}
          </text>
        );
      })}

      {placed.map((b) => {
        const c = centerFor(quadrantOf(b.quadrant).turn);
        return (
          <Blip key={b.num} b={b} cx={c.x} cy={c.y} r={r}
                selected={b.num === selectedNum} onSelect={onSelect} />
        );
      })}
    </svg>
  );
}
