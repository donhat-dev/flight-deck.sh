import React from "react";
import { createRoot } from "react-dom/client";
import { init as initAppearance } from "./ui/appearance.js";
import "./fonts.css";          // Satoshi, self-hosted
import "@fontsource-variable/outfit";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/600.css";
import "@fontsource/ibm-plex-mono/700.css";
import GeometryLab from "./radar/GeometryLab.jsx";
import "./index.css";
import "./radar.css";
import "./radar-lab.css";

// Night, like the radar it dissects: the lab draws the real component, and the
// component's colours were chosen against the dark canvas.
document.documentElement.dataset.theme = "night";

initAppearance();

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <GeometryLab />
  </React.StrictMode>,
);
