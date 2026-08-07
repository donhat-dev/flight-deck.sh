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
  QUADRANTS, RINGS, RING_LABEL, arcFacing, arcPath, isStale, placeBlips, polar,
  quadrantOf, ringBand, sectorPath,
} from "./geometry.js";

/** Width of the quadrant seam, in user units.
 *
 * A CONSTANT WIDTH, drawn as an overlay, rather than an angular gap cut out of each
 * sector. An angular gap is a wedge: 1.1 degrees is 17px of missing ring at the
 * outer radius and 6px at the inner one, so every quadrant loses a visible bite out
 * of its outer corner and the four corners never reach their axis. Overlaying a
 * line in the page colour cuts the same corridor at the same width everywhere and
 * leaves the corners square, which is what the reference radar shows. */
const SEAM_W = 3;

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
    return { cx: s / 2, cy: s / 2, r: s / 2 - 4, vb: `0 0 ${s} ${s}` };
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

export default function Radar({
  mode = "full",
  blips,
  quadrant = null,
  selectedNum = null,
  onSelect,
  width = 880,
  height = 880,
}) {
  const { cx, cy, r, vb } = ringGeometry(mode, width, height);
  const turns = mode === "full" ? QUADRANTS.map((q) => q.turn) : [0];
  const placed = placeBlips(blips, { quadrant });

  return (
    <svg className="rdr-canvas" viewBox={vb} role="img"
         aria-label={mode === "full" ? "Radar, four quadrants" : `Radar, ${quadrantOf(quadrant).label}`}>
      {/* Rings outer-first so the inner ones paint on top of their neighbours'
          edges rather than under them. FILL only — the boundary is drawn below as
          an open arc, because stroking a closed sector also strokes its two radial
          cuts, and those read as a chamfer at every quadrant seam. */}
      {[...RINGS].reverse().map((ring) => {
        const [lo, hi] = ringBand(ring);
        return turns.map((turn) => (
          <path
            key={`${ring}-${turn}`}
            className="rdr-ring"
            data-ring={ring}
            d={sectorPath(cx, cy, lo * r, hi * r, turn * 90, turn * 90 + 90)}
          />
        ));
      })}

      {/* Ring boundaries: one open arc per ring per quadrant, concentric only. */}
      {RINGS.map((ring) => {
        const [, hi] = ringBand(ring);
        return turns.map((turn) => (
          <path
            key={`edge-${ring}-${turn}`}
            className="rdr-ring-arc"
            data-ring={ring}
            d={arcPath(cx, cy, hi * r, turn * 90, turn * 90 + 90)}
          />
        ));
      })}

      {/* The seams, drawn OVER the rings in the page colour so the corridor is the
          same width at every radius. Below the blips: a blip is never placed on a
          seam, so nothing it could hide is ever there. */}
      {mode === "full" && (
        <g className="rdr-seams">
          <rect x={cx - SEAM_W / 2} y={cy - r} width={SEAM_W} height={r * 2} />
          <rect x={cx - r} y={cy - SEAM_W / 2} width={r * 2} height={SEAM_W} />
        </g>
      )}

      {/* Ring labels ride the seam in the full view and the axis in the quadrant
          view — both are places no blip is allowed to sit, so a label can never
          land on top of one. */}
      {RINGS.map((ring) => {
        const [lo, hi] = ringBand(ring);
        const mid = ((lo + hi) / 2) * r;
        const at = mode === "full"
          ? { x: cx, y: cy - mid, anchor: "middle" }
          : { x: cx + mid, y: cy + 20, anchor: "middle" };
        return (
          <text key={ring} className="rdr-ring-label" data-ring={ring}
                x={at.x} y={at.y} textAnchor={at.anchor}>
            {RING_LABEL[ring].toUpperCase()}
          </text>
        );
      })}

      {mode === "full" && QUADRANTS.map((q) => {
        const right = q.turn === 0 || q.turn === 3;
        const bottom = q.turn === 2 || q.turn === 3;
        return (
          <text key={q.k} className="rdr-quadrant-label" data-quadrant={q.k}
                x={right ? cx * 2 - 8 : 8}
                y={bottom ? cy * 2 - 8 : 18}
                textAnchor={right ? "end" : "start"}>
            {q.label.toUpperCase()}
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
