import React from "react";

/* ---- FlightDeck brand marks (shared) -----------------------------------
   Lifted out of App.jsx so the dashboard chrome and the marketing Landing
   render the SAME roundel + wordmark from one source. Both are purely
   presentational and depend only on index.css brand classes (.fd-roundel,
   .fd-word, .fdi). Design system §9.
   ----------------------------------------------------------------------- */

// Attitude-indicator roundel: sky over void, bone horizon, coral wings + dot.
export function Roundel({ className = "" }) {
  return (
    <svg viewBox="0 0 48 48" className={`fd-roundel ${className}`} aria-hidden="true">
      <defs>
        <linearGradient id="fd-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#8FC3E8" /><stop offset="1" stopColor="#2E6FB0" />
        </linearGradient>
        <clipPath id="fd-rc"><circle cx="24" cy="24" r="21" /></clipPath>
      </defs>
      <g clipPath="url(#fd-rc)">
        <rect x="0" y="0" width="48" height="24.5" fill="url(#fd-sky)" />
        <rect x="0" y="24.5" width="48" height="24" fill="#050505" />
        <rect x="0" y="23.6" width="48" height="1.6" fill="#F4F3EF" />
      </g>
      <circle cx="24" cy="24" r="21" fill="none" stroke="currentColor" strokeWidth="3" />
      <path d="M10 24h8.5M29.5 24H38" stroke="#FF5133" strokeWidth="3.4" strokeLinecap="round" />
      <circle cx="24" cy="24" r="3" fill="#FF5133" />
    </svg>
  );
}

// The dot of the "i" is a coral delta pointer (see index.css .fdi). At display
// sizes the stem also carries a dashed runway centerline (DS §9).
export function Wordmark({ className = "", runway = false }) {
  return (
    <span className={`fd-word ${runway ? "fd-word-runway" : ""} ${className}`}>
      Fl<span className="fdi" aria-hidden="true"><i className="stem" /><i className="dot" /></span>
      <span className="sr-only">i</span>ghtDeck
    </span>
  );
}
