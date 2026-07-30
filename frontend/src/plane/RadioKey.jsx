import React from "react";

/**
 * A plane tab drawn as an old radio preset key.
 *
 * Line art, not shading: the reference is a wireframe slab with three visible
 * faces — a flat top, a chamfer, and a front lip — so the key is drawn as one
 * silhouette path plus the two internal edges where the faces meet. Fill is
 * reserved for state.
 *
 * Why this is not a C3 depth violation even though every key looks three
 * dimensional: the form comes from drawn facets, which §3's thesis frees as
 * "print-like layering". No key carries a depth *material* (no `Xpx Ypx 0`
 * offset), so the depth budget is untouched. State is carried the way a key bank
 * carries it — the selected key is DOWN in its socket, the others stand up.
 *
 * Geometry is fixed at 120×48 to match the CSS box exactly, so nothing stretches.
 * That fixes a real constraint: a key label has to stay short (≤ 8 characters),
 * which is what preset labels are anyway.
 */

/* Proportions follow the reference: the TOP face carries most of the height (25 of
   48), the chamfer is a narrow band, the front lip is thinner still. Two passes to
   get here: splitting the height evenly read as a ramp, and a strong taper read as
   a monument. The taper is gentle (back edge 100 wide against a 118 base) because
   a key is seen from only slightly above.
   Silhouette: back edge → right chamfer → right lip → bottom → left lip → left
   chamfer. The two <line>s are the top/chamfer and chamfer/lip seams. */
const SILHOUETTE = "M10,5 H110 L116,28 L119,34 V46 H1 V34 L4,28 Z";
const TOP_FACE = "10,5 110,5 116,28 4,28";

export default function RadioKey({ label, selected, onSelect }) {
  return (
    <button
      type="button"
      role="tab"
      className="radio-key"
      aria-selected={selected}
      onClick={onSelect}
    >
      <svg className="radio-key-art" viewBox="0 0 120 48" aria-hidden="true">
        {/* Painted first so the seams draw over it. */}
        <polygon className="radio-key-top" points={TOP_FACE} />
        <path className="radio-key-edge" d={SILHOUETTE} />
        <line className="radio-key-seam" x1="4" y1="28" x2="116" y2="28" />
        <line className="radio-key-seam" x1="1" y1="34" x2="119" y2="34" />
      </svg>
      <span className="radio-key-label">{label}</span>
    </button>
  );
}
