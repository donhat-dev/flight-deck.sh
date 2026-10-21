import React, { useEffect, useState } from "react";

import PixelMark from "./PixelMark.jsx";

/**
 * Day/Night switch for the parallel planes.
 *
 * Stage 7 of docs/flightdeck-composition-and-radio.md exists to test one claim:
 * the art direction lives in the composition, not in the paper colour. That is
 * only falsifiable if the same page can be seen in both palettes, so the toggle
 * is the test instrument rather than a preference control.
 *
 * Every colour on Radio and Spend-re-composed comes from a --fdx-* token that
 * both themes define, so flipping the attribute is the whole port.
 *
 * Two variants, one mechanism. `flat` is the original instrument on Radio and
 * the radar — a bare mono label, unchanged. `rail` is the dashboard's footer
 * control: a pixel mark in a chip, the name of the mode that is running, and a
 * two-cell track showing which side is on. Adding a second theme mechanism for
 * the dashboard was the alternative, and two things writing
 * `documentElement.dataset.theme` is how a theme starts flickering on load.
 *
 * `persistKey` names a localStorage key to remember the choice in. Without it
 * the component behaves as it always has: `initial` decides, nothing is stored.
 */
export default function PaletteToggle({ initial = "day", variant = "flat", persistKey }) {
  const [theme, setTheme] = useState(() => {
    if (!persistKey) return initial;
    try { return localStorage.getItem(persistKey) || initial; } catch { return initial; }
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    if (!persistKey) return;
    try { localStorage.setItem(persistKey, theme); } catch { /* private window */ }
  }, [theme, persistKey]);

  const night = theme === "night";
  const flip = () => setTheme((t) => (t === "day" ? "night" : "day"));

  if (variant === "rail") {
    return (
      <button
        type="button"
        className="fdx-palette-switch"
        role="switch"
        aria-checked={night}
        aria-label="Night mode"
        title={night ? "Night mode" : "Day mode"}
        onClick={flip}
      >
        <span className="fdx-nav-chip">
          <PixelMark name={night ? "moon" : "sun"} />
        </span>
        <span className="fdx-nav-text">{night ? "Night mode" : "Day mode"}</span>
        <span className="fdx-palette-track" aria-hidden="true">
          <i />
          <i />
        </span>
      </button>
    );
  }

  return (
    <button
      type="button"
      className="fdx-palette-toggle"
      aria-pressed={night}
      onClick={flip}
      title="Switch palette — the composition should not depend on it"
    >
      {night ? "Night" : "Day"}
    </button>
  );
}
