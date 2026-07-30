import React from "react";
import { createRoot } from "react-dom/client";
// Self-hosted fonts (offline-safe, no external <link>), per FlightDeck Night:
// Outfit for display + UI, IBM Plex Mono for all instrument labels & numbers.
import "./fonts.css";          // Satoshi, self-hosted
import "@fontsource-variable/outfit";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/600.css";
import App from "./App.jsx";
import "./index.css";
createRoot(document.getElementById("root")).render(<App />);
