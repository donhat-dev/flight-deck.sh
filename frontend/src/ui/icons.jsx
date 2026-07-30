import React from "react";

/**
 * Line icons for control labels.
 *
 * A label alone made every button look the same at a glance, which is the
 * complaint these answer: in a bank of same-sized keys the word is the only
 * differentiator, and words read slower than shapes. Stroke-based and drawn in
 * `currentColor` so they inherit the control's label colour in both themes and
 * need no per-theme variants.
 *
 * 16×16 viewBox, 1.6 stroke — the kit already sizes `.fdx-button svg` to 1rem.
 */
const base = {
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
  focusable: false,
};

export const IconTranscript = () => (
  <svg {...base}>
    <path d="M3.5 2.5h6l3 3v8h-9z" />
    <path d="M9.5 2.5v3h3M5.5 8.5h5M5.5 11h3.5" />
  </svg>
);

export const IconBack = () => (
  <svg {...base}>
    <path d="M9.5 3.5 5 8l4.5 4.5" />
    <path d="M5 8h7" />
  </svg>
);

export const IconForward = () => (
  <svg {...base}>
    <path d="M6.5 3.5 11 8l-4.5 4.5" />
    <path d="M11 8H4" />
  </svg>
);

export const IconRefresh = () => (
  <svg {...base}>
    <path d="M13 8a5 5 0 1 1-1.6-3.7" />
    <path d="M13 2.5V5h-2.5" />
  </svg>
);

export const IconOnAir = () => (
  <svg {...base}>
    <circle cx="8" cy="8" r="2" />
    <path d="M4.6 4.6a4.8 4.8 0 0 0 0 6.8M11.4 11.4a4.8 4.8 0 0 0 0-6.8" />
  </svg>
);

export const IconSpend = () => (
  <svg {...base}>
    <path d="M2.5 13.5h11" />
    <path d="M4.5 11V7M8 11V3.5M11.5 11V8.5" />
  </svg>
);

export const IconMute = () => (
  <svg {...base}>
    <path d="M3 6.5h2L8 4v8L5 9.5H3z" />
    <path d="M10.5 6.5l3 3M13.5 6.5l-3 3" />
  </svg>
);
