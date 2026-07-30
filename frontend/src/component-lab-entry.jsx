import React from "react";
import { createRoot } from "react-dom/client";
import "./fonts.css";          // Satoshi, self-hosted
import "@fontsource-variable/outfit";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/600.css";
import ComponentLab from "./ui/ComponentLab.jsx";
import "./index.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ComponentLab />
  </React.StrictMode>,
);
