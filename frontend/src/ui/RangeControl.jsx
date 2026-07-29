import React from "react";

/**
 * Segmented time-range control.
 *
 * Shared because two surfaces render it and the composition rule it obeys is
 * easy to lose: only the SELECTED segment carries depth. The first version used
 * four kit buttons, which put offset depth on all four at once — uniform depth,
 * which is no depth. Keeping one implementation keeps that fix in one place.
 */
export const RANGES = [
  { k: "today", label: "Today" },
  { k: "7d", label: "7 days" },
  { k: "30d", label: "30 days" },
  { k: "all", label: "All" },
];

export default function RangeControl({ range, onChange }) {
  return (
    <div className="fdx-segmented" role="group" aria-label="Time range">
      {RANGES.map((r) => (
        <button
          key={r.k}
          type="button"
          className="fdx-segmented-seg"
          aria-pressed={range === r.k}
          onClick={() => onChange(r.k)}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}
