import React from "react";
import { createRoot } from "react-dom/client";
import { init as initAppearance } from "./ui/appearance.js";
import "./fonts.css";          // Satoshi, self-hosted
import "@fontsource-variable/outfit";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/600.css";
import "@fontsource/ibm-plex-mono/700.css";
import RadarPage from "./radar/RadarPage.jsx";
import "./index.css";
import "./radar.css";
import "./radar-full.css";
import "./radar-blip.css";
import "./radar-index.css";
import "./radar-list.css";

// The radar opens in Night. The blips are the only saturated thing on the page and
// they read strongest against the dark canvas; the palette switch owns it after
// that. This is the one place the radar differs from the plane, which opens Day.
document.documentElement.dataset.theme = "night";

// The saved font choice is applied before the first paint, so the page never
// renders in one face and then swaps (see ui/appearance.js).
initAppearance();

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <RadarPage />
  </React.StrictMode>,
);
