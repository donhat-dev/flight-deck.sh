/**
 * The blip glyph — one shape vocabulary, used everywhere a blip is drawn.
 *
 * It lives on its own because it was duplicated once and immediately drifted: the
 * radar drew a circle with a facing arc while the detail panel drew a triangle, so
 * the panel contradicted the radar's own language about what a shape means. Any
 * second place that draws a blip has to call this, not describe it again.
 *
 * The vocabulary:
 *
 *   circle          the blip
 *   arc on its edge the direction of the last move
 *   full ring       entered the radar this period
 *   nothing         held its position
 *
 * `facing` is a bearing in the same convention as the rest of the geometry —
 * degrees counter-clockwise from the +x axis. On the radar it is derived from the
 * blip's own polar angle so "inward" always points at the centre. In a standalone
 * mark there is no centre to point at, so the caller supplies the panel's own axis
 * instead: the POSITION track runs Caution on the left to Adopt on the right, which
 * makes inward rightward, i.e. 0 degrees.
 */
import React from "react";

import { sectorPath } from "./geometry.js";

/**
 * The decorations sit OUTSIDE the circle, so how far out has to scale with it.
 *
 * These were fixed pixel offsets, tuned for the radar's 30px blip. Reused at the
 * panel mark's smaller diameter the arc reached past the viewBox and collided with
 * the heading beside it — the same absolute number is a thin ring on a big blip and
 * a halo on a small one. Fractions of `d` hold the proportion at any size.
 */
const GAP = 0.1;   // clear space between the body and the arc
const BAND = 0.17; // the arc's own thickness
const HALO = 0.3;
const RING = 0.17;

export function BlipGlyph({ cx, cy, d, state, facing, selected = false }) {
  return (
    <>
      {selected && <circle className="rdr-blip-halo" cx={cx} cy={cy} r={d / 2 + d * HALO} />}
      {state === "new" && (
        <circle className="rdr-blip-entered" cx={cx} cy={cy} r={d / 2 + d * RING} />
      )}
      {facing !== null && facing !== undefined && (
        <path
          className="rdr-blip-arc"
          d={sectorPath(cx, cy, d / 2 + d * GAP, d / 2 + d * (GAP + BAND),
                        facing - 56, facing + 56)}
        />
      )}
      <circle className="rdr-blip-body" cx={cx} cy={cy} r={d / 2} />
    </>
  );
}

/**
 * A standalone mark, for use outside the radar canvas.
 *
 * Sized in ems so it tracks the text it sits beside rather than fixing a pixel size
 * that goes wrong the moment the appearance config changes the type scale.
 */
/**
 * Bearing for a standalone mark, in the panel's own axis.
 *
 * Deliberately NOT `arcFacing(state, 0)`. That helper answers "which way is the
 * centre" for a blip at a given polar angle, and feeding it a fake angle of zero
 * put an inward move's arc on the LEFT — the opposite of what the panel says
 * underneath it, where the POSITION track runs Caution on the left to Adopt on the
 * right. The mock drew it on the right and the code drew it on the left, which is
 * the drift this file exists to prevent, so the mapping is stated here instead of
 * borrowed.
 */
function markFacing(state) {
  if (state === "in") return 0;    // rightward, toward Adopt
  if (state === "out") return 180; // leftward, toward Caution
  return null;
}

export default function BlipMark({ blip, size = 34 }) {
  const c = size / 2;
  // Leaves room for the widest decoration: the halo at d*0.3 past the body.
  const d = size / (1 + 2 * 0.3);
  const facing = markFacing(blip.state);
  return (
    <svg className="rdr-blip-mark" data-quadrant={blip.quadrant}
         viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <BlipGlyph cx={c} cy={c} d={d} state={blip.state} facing={facing} />
      <text className="rdr-blip-num" x={c} y={c}>{blip.num}</text>
    </svg>
  );
}
