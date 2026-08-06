/**
 * The radar page — its own surface, like the plane.
 *
 * Entered through `radar.html`, not through the deck's App, so nothing here
 * inherits the dashboard's Shell, header or spacing. That isolation is the point:
 * the radar is a poster you read, and the deck is a console you scan, and a page
 * trying to be both ends up neither.
 *
 * Four routes, all hash-based so a view is linkable and survives a reload:
 *
 *   #/            the full radar — radar and the history bar, nothing else
 *   #/blip/<num>  one blip: its quadrant, and the panel that carries the anchor
 *   #/index       every blip as a table, with facets
 *   #/radars      every radar in the install
 *
 * The full route holds NO header and no side panel on purpose. It is the only view
 * whose job is "where does everything stand", and a masthead over it would take
 * attention the blips are supposed to have.
 */
import React, { useCallback, useEffect, useState } from "react";

import PaletteToggle from "../ui/PaletteToggle.jsx";
import BlipIndex from "./BlipIndex.jsx";
import BlipPanel from "./BlipPanel.jsx";
import HistoryBar from "./HistoryBar.jsx";
import Radar from "./Radar.jsx";
import RadarList from "./RadarList.jsx";
import { quadrantOf } from "./geometry.js";
import { BLIPS, RADARS, blipByNum } from "./seed.js";

const NAV = [
  { k: "radar", label: "Radar", hash: "#/" },
  { k: "index", label: "Blip index", hash: "#/index" },
  { k: "radars", label: "Radars", hash: "#/radars" },
];

function parseRoute(hash) {
  const b = (hash || "").match(/^#\/blip\/(\d+)\/?$/);
  if (b) return { name: "blip", num: Number(b[1]) };
  if (/^#\/index\/?$/.test(hash || "")) return { name: "index" };
  if (/^#\/radars\/?$/.test(hash || "")) return { name: "radars" };
  return { name: "radar" };
}

function useRoute() {
  const read = () => parseRoute(typeof window === "undefined" ? "" : window.location.hash);
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

const go = (hash) => { window.location.hash = hash; window.scrollTo(0, 0); };

/** The open radar. One install can hold several; only one is ever on screen. */
const openRadar = RADARS.find((r) => r.open) || RADARS[0];

function Chrome({ active }) {
  return (
    <header className="rdr-chrome">
      <a className="rdr-mark" href="/" aria-label="FlightDeck">
        <span className="rdr-roundel" aria-hidden="true" />
        <span className="rdr-wordmark">FlightDeck</span>
      </a>
      <nav className="rdr-nav" aria-label="Radar views">
        {NAV.map((n) => (
          <button key={n.k} type="button" className="rdr-nav-key"
                  aria-current={n.k === active ? "page" : undefined}
                  onClick={() => go(n.hash)}>
            {n.label}
          </button>
        ))}
      </nav>
      <span className="rdr-chrome-title">{openRadar.title}</span>
      <span className="rdr-chrome-fill" />
      <span className="rdr-chrome-meta">{openRadar.blipCount} blips</span>
      {/* `initial` has to be given: the toggle writes the attribute on mount, so
          leaving it at its "day" default silently overwrites the Night the entry
          set before first paint and the page opens in the wrong palette. */}
      <PaletteToggle initial="night" />
    </header>
  );
}

function FullView({ granularity, onGranularity }) {
  return (
    <main className="rdr-full">
      <div className="rdr-full-stage">
        <Radar mode="full" blips={BLIPS} onSelect={(num) => go(`#/blip/${num}`)} />
      </div>
      <HistoryBar granularity={granularity} onGranularity={onGranularity} />
    </main>
  );
}

function BlipView({ num }) {
  const blip = blipByNum(num);
  if (!blip) {
    return (
      <main className="rdr-empty">
        <p className="rdr-empty-line">No blip numbered {num} on this radar.</p>
        <button type="button" className="rdr-btn" onClick={() => go("#/")}>Back to the radar</button>
      </main>
    );
  }
  const quadrant = quadrantOf(blip.quadrant);
  const siblings = BLIPS.filter((b) => b.quadrant === blip.quadrant);
  return (
    <main className="rdr-focus">
      <div className="rdr-focus-head">
        <div className="rdr-focus-title">
          {/* The title alone. The eyebrow and the migration subtitle were both
              removed: a reader who clicked a blip already knows which radar they
              are on, so both were repeating context to buy nothing. The title is
              the way back, since this view has no chrome. */}
          <h1 className="rdr-h1">
            <button type="button" className="rdr-title-btn" onClick={() => go("#/")}>
              {openRadar.title}
            </button>
          </h1>
        </div>
        <button type="button" className="rdr-btn">Export</button>
        <button type="button" className="rdr-btn" data-variant="primary">Move blip</button>
      </div>

      <div className="rdr-focus-body">
        <section className="rdr-quadrant" aria-label={`${quadrant.label} quadrant`}>
          <div className="rdr-quadrant-head">
            <h2 className="rdr-h2">{quadrant.label}</h2>
            <span className="rdr-chrome-meta">{siblings.length} blips</span>
            <span className="rdr-chrome-fill" />
          </div>
          <div className="rdr-quadrant-stage">
            <Radar mode="quadrant" blips={siblings} quadrant={blip.quadrant}
                   selectedNum={blip.num} width={530} height={640}
                   onSelect={(n) => go(`#/blip/${n}`)} />
          </div>
        </section>

        <BlipPanel blip={blip} />
      </div>

      <HistoryBar variant="quarters" />
    </main>
  );
}

export default function RadarPage() {
  const route = useRoute();
  const [granularity, setGranularity] = useState("quarter");
  const onGranularity = useCallback((g) => setGranularity(g), []);
  const active = route.name === "blip" ? "radar" : route.name;

  return (
    <div className="rdr-page">
      {/* No chrome on the blip route. The anchor there is the summary panel, and a
          wordmark plus four nav keys plus a palette switch is five small elements
          competing with it. The radar title returns to the full radar, so the view
          is not a dead end. */}
      {route.name !== "blip" && <Chrome active={active} />}
      {route.name === "index" ? (
        <BlipIndex onOpen={(num) => go(`#/blip/${num}`)} />
      ) : route.name === "radars" ? (
        <RadarList onOpen={() => go("#/")} />
      ) : route.name === "blip" ? (
        <BlipView num={route.num} />
      ) : (
        <FullView granularity={granularity} onGranularity={onGranularity} />
      )}
    </div>
  );
}
