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
 * of equal steps. Scaled to this box: 14 : 24 : 14.
 *
 * The projection is close to perpendicular to the viewer. An earlier depth vector
 * of (46,−26) leaned about 52° off vertical, which sheared the whole solid: the
 * back of it appeared to slide up and to the right, so the key read as lying on a
 * diagonal plane rather than facing you. The depth is now (16,−26) — roughly 23°
 * off vertical — which keeps a side face narrow but present. Some horizontal shift
 * is required: at zero the side face collapses, and with it the only view of how
 * deep the chamfer cuts.
 *
 * Geometry derived rather than eyeballed: from the front-bottom-left corner, with
 * the chamfer ending at 46% of the depth and 46% of the height, every vertex is
 * that corner plus some combination of width, height and depth. All faces
 * therefore stay parallel and the solid cannot look broken.
 *
 *     A(2,70)   B(110,70)    front-bottom edge
 *     C(2,56)   D(110,56)    the front lip is 14 tall
 *     E(9,32)   F(117,32)    where the chamfer meets the top face
 *     H(18,18)  G(126,18)    back-top edge
 *     B'(126,44)             back-bottom-right; B'→G is the FULL height (26),
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
 * The art is fixed at 128×72 to match the CSS box exactly, so a label must stay
 * short (≤ 8 characters) — which is what a preset label is anyway.
 */

const SILHOUETTE = "M2,70 H110 L126,44 V18 H18 L9,32 L2,56 Z";

const FACES = {
  front: "2,70 110,70 110,56 2,56",
  chamfer: "2,56 110,56 117,32 9,32",
  side: "110,70 126,44 126,18 117,32 110,56",
  top: "9,32 117,32 126,18 18,18",
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
      <svg className="radio-key-art" viewBox="0 0 128 72" aria-hidden="true">
        {/* Opaque faces first — they occlude the key behind; the line work draws
            over them. */}
        <polygon className="radio-key-face" points={FACES.front} />
        <polygon className="radio-key-face" points={FACES.chamfer} />
        <polygon className="radio-key-face" points={FACES.side} />
        <polygon className="radio-key-face radio-key-top" points={FACES.top} />

        <path className="radio-key-edge" d={SILHOUETTE} />
        {/* The profile seam: front → chamfer → top, read off the right side. */}
        <polyline className="radio-key-seam" points="110,70 110,56 117,32 126,18" />
        <line className="radio-key-seam" x1="2" y1="56" x2="110" y2="56" />
        <line className="radio-key-seam" x1="9" y1="32" x2="117" y2="32" />
      </svg>
      <span className="radio-key-label">{label}</span>
    </button>
  );
}
