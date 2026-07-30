import React from "react";

/**
 * A plane tab drawn as an old radio preset key.
 *
 * The form is a cuboid in oblique (3/4) projection with the front-top edge
 * chamfered — a cube with one quadrant sliced in half, which is what a preset key
 * looks like when you look down at a radio deck. An earlier pass drew a symmetric
 * front-facing bevel; that reads as a keystone rather than a key, because a key
 * bank is never seen head-on.
 *
 * Four faces are visible, and all four are needed for the solid to read: the
 * FRONT lip (vertical), the CHAMFER (rising back), the TOP (receding up-right,
 * where the label is printed), and the RIGHT side, whose stepped profile is the
 * only place the chamfer's depth is legible.
 *
 * Proportions are measured off the reference rather than guessed. There the three
 * faces stand at 145 : 265 : 162 (top : chamfer : lip), so the CHAMFER is the
 * largest — a first pass gave all three the same depth and the key read as a set
 * of equal steps. Scaled to this box: 14 : 22 : 13.
 *
 * Geometry derived rather than eyeballed: from the front-bottom-left corner, with
 * a depth vector of (46,−26) and the chamfer ending at 46% of the depth and 43% of
 * the height, every vertex is that corner plus some combination of width, height
 * and depth. All faces therefore stay parallel and the solid cannot look broken.
 *
 *     A(2,58)   B(84,58)     front-bottom edge
 *     C(2,45)   D(84,45)     the front lip is 13 tall
 *     E(23,23)  F(105,23)    where the chamfer meets the top face
 *     H(48,9)   G(130,9)     back-top edge
 *     B'(130,32)             back-bottom-right; B'→G is the FULL height (23),
 *                            taller than the front lip because the chamfer only
 *                            removes material at the front
 *
 * Faces are filled with the canvas colour rather than left transparent. On the
 * page that looks identical, but it makes the keys **occlude** each other, which
 * is what lets them overlap into a bank instead of floating apart: each key's
 * right face is covered by the next key, exactly as on the hardware.
 *
 * None of this touches the depth budget. The three-dimensionality is drawn
 * facets, which §3's thesis frees as print-like layering, and no key carries a
 * depth *material* (`Xpx Ypx 0`). State is which key is pressed, not which key has
 * a shadow.
 *
 * The art is fixed at 132×60 to match the CSS box exactly, so a label must stay
 * short (≤ 8 characters) — which is what a preset label is anyway.
 */

const SILHOUETTE = "M2,58 H84 L130,32 V9 H48 L23,23 L2,45 Z";

const FACES = {
  front: "2,58 84,58 84,45 2,45",
  chamfer: "2,45 84,45 105,23 23,23",
  side: "84,58 130,32 130,9 105,23 84,45",
  top: "23,23 105,23 130,9 48,9",
};

export default function RadioKey({ label, selected, onSelect }) {
  return (
    <button
      type="button"
      role="tab"
      className="radio-key"
      aria-selected={selected}
      onClick={onSelect}
    >
      <svg className="radio-key-art" viewBox="0 0 132 60" aria-hidden="true">
        {/* Opaque faces first — they occlude the key behind; the line work draws
            over them. */}
        <polygon className="radio-key-face" points={FACES.front} />
        <polygon className="radio-key-face" points={FACES.chamfer} />
        <polygon className="radio-key-face" points={FACES.side} />
        <polygon className="radio-key-face radio-key-top" points={FACES.top} />

        <path className="radio-key-edge" d={SILHOUETTE} />
        {/* The profile seam: front → chamfer → top, read off the right side. */}
        <polyline className="radio-key-seam" points="84,58 84,45 105,23 130,9" />
        <line className="radio-key-seam" x1="2" y1="45" x2="84" y2="45" />
        <line className="radio-key-seam" x1="23" y1="23" x2="105" y2="23" />
      </svg>
      <span className="radio-key-label">{label}</span>
    </button>
  );
}
