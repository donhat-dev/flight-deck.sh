import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/outfit";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/600.css";
import SpendComposed from "./spend/SpendComposed.jsx";
import "./index.css";
import "./spend-composed.css";

// Day palette, matching Radio. Stage 7 ports both to Night.
document.documentElement.dataset.theme = "day";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <SpendComposed />
  </React.StrictMode>,
);
