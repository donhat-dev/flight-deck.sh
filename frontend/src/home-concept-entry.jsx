import React from "react";
import { createRoot } from "react-dom/client";
import { init as initAppearance } from "./ui/appearance.js";
import "./fonts.css";          // Satoshi, self-hosted
import "@fontsource-variable/outfit";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/600.css";
import "@fontsource/ibm-plex-mono/700.css";
import HomeConcept from "./HomeConcept.jsx";
import "./home-concept.css";

// The saved font choice is applied before the first paint, so the page never
// renders in one face and then swaps (see ui/appearance.js).
initAppearance();

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <HomeConcept />
  </React.StrictMode>,
);
