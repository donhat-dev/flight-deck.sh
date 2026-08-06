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

import {
  QUADRANTS, RINGS, RING_LABEL, arcFacing, isStale, placeBlips, polar,
  quadrantOf, ringBand, sectorPath,
} from "./geometry.js";

/** Degrees of blank left on each side of a quadrant seam, in the full view. */
const SEAM = 1.1;

/** Blip diameter in user units. Bigger than a dot on purpose: the blips are the
 *  content, and the rings are only the frame that gives them a position. */
const D = 30;
const D_SELECTED = 40;

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
      {selected && (
        <circle className="rdr-blip-halo" cx={p.x} cy={p.y} r={d / 2 + 9} />
      )}
      {b.state === "new" && (
        <circle className="rdr-blip-entered" cx={p.x} cy={p.y} r={d / 2 + 5} />
      )}
      {facing !== null && (
        <path
          className="rdr-blip-arc"
          d={sectorPath(p.x, p.y, d / 2 + 3, d / 2 + 8, facing - 56, facing + 56)}
        />
      )}
      <circle className="rdr-blip-body" cx={p.x} cy={p.y} r={d / 2} />
      <text className="rdr-blip-num" x={p.x} y={p.y}>{b.num}</text>
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
          edges rather than under them. */}
      {[...RINGS].reverse().map((ring) => {
        const [lo, hi] = ringBand(ring);
        return turns.map((turn) => {
          const gap = mode === "full" ? SEAM : 0;
          return (
            <path
              key={`${ring}-${turn}`}
              className="rdr-ring"
              data-ring={ring}
              d={sectorPath(cx, cy, lo * r, hi * r, turn * 90 + gap, turn * 90 + 90 - gap)}
            />
          );
        });
      })}

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
