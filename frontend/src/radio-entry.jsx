import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/outfit";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/600.css";
import Radio from "./Radio.jsx";
import "./index.css";
import "./radio.css";

// Day palette first, per decision 4 of docs/flightdeck-composition-and-radio.md:
// the reference material IS warm paper and ink, so Day is where this art
// direction is provable. Night is the port (stage 7), not the original.
document.documentElement.dataset.theme = "day";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Radio />
  </React.StrictMode>,
);
