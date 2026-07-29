import React, { useEffect, useState } from "react";

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
 */
export default function PaletteToggle({ initial = "day" }) {
  const [theme, setTheme] = useState(initial);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <button
      type="button"
      className="fdx-palette-toggle"
      aria-pressed={theme === "night"}
      onClick={() => setTheme((t) => (t === "day" ? "night" : "day"))}
      title="Switch palette — the composition should not depend on it"
    >
      {theme === "day" ? "Day" : "Night"}
    </button>
  );
}
