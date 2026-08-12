/**
 * The radar itself, as SVG.
 *
 * One component serves both views because the difference is a frame, not a
 * drawing: the full radar sweeps 360° around a single centre with two straight
 * strips cut out of the disc (the label corridor and the vertical seam), the
 * quadrant view puts the origin in the bottom-left corner and sweeps 90° with
 * nothing cut out at all. Everything past that — ring bands, blip placement,
 * the movement arc — is the same math, and keeping it in one place is what
 * stops the two views from drifting apart.
 *
 * Colour lives in the stylesheet, not here. Each blip carries `data-quadrant` and
 * the sheet maps that to `--rdr-q`, so the palette is themeable, is visible to the
 * composition lint, and never needs a second copy in JS.
 */
import React from "react";

import { BlipGlyph } from "./BlipGlyph.jsx";
import {
  QUADRANTS, RINGS, RING_EDGE, RING_LABEL, arcFacing, arcPath, bandArcPath, bandSectorPath,
  isStale, placeBlips, polar, quadrantOf, ringBand, sectorPath, spreadMids,
} from "./geometry.js";

/** Minimum distance between adjacent ring-label anchors, in user units. Sized for
 *  the widest word — CAUTION at the corridor's 14px bold mono with tracking — so
 *  no pair of neighbours can touch whatever the occupancy sizing does to the bands. */
export const LABEL_GAP = 78;

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

export function ringGeometry(mode, width, height) {
  if (mode === "full") {
    const s = Math.min(width, height);
    return { cx: s / 2, cy: s / 2, r: s / 2 - 6, vb: `0 0 ${s} ${s}`, s };
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

/** The two strips cut out of the single disc, in user units — kept as separate
 *  constants because they answer different questions.
 *
 *  GAP_H is the horizontal corridor: the LABEL ROW. It is a strip removed along
 *  the horizontal axis because a single unbroken disc leaves nowhere to put
 *  readable ring labels except on top of the drawing. It is sized to the
 *  ring-label line with a little air, nothing more — the ring names read
 *  left-to-right, so they only ever need a horizontal row that fits them.
 *
 *  GAP_V is the vertical strip between the left and right quadrants. It carries no
 *  text — the ring names never run top-to-bottom — so it is only a SEAM: wide
 *  enough to mark where one quadrant ends and the next begins, far too narrow
 *  for a label. That is why it is a second, much smaller constant rather than
 *  the same width reused on both axes. */
export const GAP_H = 36;
export const GAP_V = 10;

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
  // The three layout numbers, as props defaulting to the module constants. They are
  // props ONLY so the geometry lab can drive the real component with its own values
  // instead of a second copy of this drawing — every caller in the app takes the
  // defaults, so the radar has one layout and the lab cannot drift from it.
  gapH = GAP_H,
  gapV = GAP_V,
}) {
  const { cx, cy, r, vb, s } = ringGeometry(mode, width, height);
  const turns = mode === "full" ? QUADRANTS.map((q) => q.turn) : [0];
  const gh = gapH / 2;
  const gv = gapV / 2;
  const bands = mode === "full"
    ? { h: gh / r, v: gv / r, pad: (D / 2 + 2) / r }
    : null;
  const placed = placeBlips(blips, { quadrant, edges, bands });

  return (
    <svg className="rdr-canvas" viewBox={vb} role="img"
         aria-label={mode === "full" ? "Radar, four quadrants"
           : `Radar, ${(quadrants.find((q) => q.k === quadrant) ?? quadrantOf(quadrant)).label}`}>
      {/* Rings outer-first so the inner ones paint on top of their neighbours'
          edges rather than under them. FILL only — the boundary is drawn below as
          an open arc, because stroking a closed sector also strokes its two radial
          cuts, and those read as a chamfer where the band meets the strip edge. */}
      {[...RINGS].reverse().map((ring) => {
        const [lo, hi] = ringBand(ring, edges);
        return turns.map((turn) => {
          const d = mode === "full"
            ? bandSectorPath(cx, cy, lo * r, hi * r, turn, gh, gv)
            : sectorPath(cx, cy, lo * r, hi * r, 0, 90);
          if (!d) return null;
          return (
            <path key={`${ring}-${turn}`} className="rdr-ring" data-ring={ring} d={d} />
          );
        });
      })}

      {/* Ring boundaries: one open arc per ring per quadrant, concentric only. */}
      {RINGS.map((ring) => {
        const [, hi] = ringBand(ring, edges);
        return turns.map((turn) => {
          const d = mode === "full"
            ? bandArcPath(cx, cy, hi * r, turn, gh, gv)
            : arcPath(cx, cy, hi * r, 0, 90);
          if (!d) return null;
          return (
            <path key={`edge-${ring}-${turn}`} className="rdr-ring-arc" data-ring={ring} d={d} />
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
          // The anchor is textAnchor="middle" and sits s/2+mid (full) or cx+mid
          // (quadrant) from the viewBox edge, so the bound must leave half of the
          // widest word (CAUTION, ~80 units) inside the drawing, or that half gets
          // clipped by the SVG edge — r-44 keeps the whole word on canvas in both modes.
          { gap: LABEL_GAP, max: r - 44 },
        );
        if (mode === "full") {
          return RINGS.map((ring, i) => [-1, 1].map((side) => (
            <text key={`${ring}-${side}`} className="rdr-ring-label" data-ring={ring}
                  x={s / 2 + side * mids[i]} y={s / 2} textAnchor="middle"
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

      {placed.map((b) => (
        <Blip key={b.num} b={b} cx={cx} cy={cy} r={r}
              selected={b.num === selectedNum} onSelect={onSelect} />
      ))}
    </svg>
  );
}
