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
 * Every view is fed from `/api/radar/*`. Ring, movement direction and evidence age
 * are derived SERVER-SIDE and never recomputed here — the same derivation living in
 * two languages is the shape of bug where the table and the drawing disagree.
 */
import React, { useCallback, useEffect, useState } from "react";

import PaletteToggle from "../ui/PaletteToggle.jsx";
import BlipIndex from "./BlipIndex.jsx";
import BlipPanel from "./BlipPanel.jsx";
import HistoryBar from "./HistoryBar.jsx";
import Radar from "./Radar.jsx";
import RadarList from "./RadarList.jsx";
import { OPEN_RADAR, useBlip, useRadar, useRadars } from "./data.js";
import { QUADRANTS, quadrantOf } from "./geometry.js";

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

/**
 * The states a data-driven surface has to be able to show.
 *
 * Loading, failure and emptiness are three separate renders on purpose. "Nothing
 * here yet" and "we could not ask" look identical to a reader and mean opposite
 * things, and only one of them is worth a retry button.
 */
function Status({ loading, error, empty, onRetry }) {
  if (loading) {
    return <div className="rdr-status"><p className="rdr-status-line">Loading the radar…</p></div>;
  }
  if (error) {
    return (
      <div className="rdr-status">
        <p className="rdr-status-line" data-tone="error">
          Could not load the radar. {error.message}
        </p>
        <button type="button" className="rdr-btn" onClick={onRetry}>Try again</button>
      </div>
    );
  }
  if (empty) {
    return (
      <div className="rdr-status">
        <p className="rdr-status-line">This radar has no blips yet.</p>
        <p className="rdr-status-hint">
          A blip appears when something enters the radar with a reason.
        </p>
      </div>
    );
  }
  return null;
}

function Chrome({ active, board }) {
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
      <span className="rdr-chrome-title">{board?.title ?? "Radar"}</span>
      <span className="rdr-chrome-fill" />
      {board && <span className="rdr-chrome-meta">{board.blipCount} blips</span>}
      {/* `initial` has to be given: the toggle writes the attribute on mount, so
          leaving it at its "day" default silently overwrites the Night the entry
          set before first paint and the page opens in the wrong palette. */}
      <PaletteToggle initial="night" />
    </header>
  );
}

function FullView({ board, granularity, onGranularity }) {
  return (
    <main className="rdr-full">
      <div className="rdr-full-stage">
        <Radar mode="full" blips={board.blips} onSelect={(num) => go(`#/blip/${num}`)} />
      </div>
      <HistoryBar granularity={granularity} onGranularity={onGranularity}
                  periods={board.periods} />
    </main>
  );
}

function BlipView({ board, num }) {
  // The detail is a SECOND request. The board already carries every blip's position,
  // which is all the drawing needs; one blip's whole move history is only wanted when
  // a reader opens it, and fetching 34 histories to draw one circle would pull most of
  // the ledger down for nothing.
  const { blip, error, loading, reload } = useBlip(board.slug, num);
  const onBoard = board.blips.find((b) => b.num === num);

  if (!onBoard) {
    return (
      <main className="rdr-status">
        <p className="rdr-status-line">No blip numbered {num} on this radar.</p>
        <button type="button" className="rdr-btn" onClick={() => go("#/")}>
          Back to the radar
        </button>
      </main>
    );
  }

  const quadrant = quadrantOf(onBoard.quadrant);
  const siblings = board.blips.filter((b) => b.quadrant === onBoard.quadrant);

  return (
    <main className="rdr-focus">
      <div className="rdr-focus-head">
        {/* A full-height slab flush against the left edge, not a button floating in
            a gutter. It is the only way out of a view that carries no chrome, so it
            gets the weight of an edge rather than the weight of a control — and no
            hard offset, because a lifted key here would read as an action on the
            radar rather than as leaving it. */}
        <button type="button" className="rdr-back" aria-label="Back to the full radar"
                onClick={() => go("#/")}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M14 5l-7 7 7 7" />
          </svg>
        </button>
        <div className="rdr-focus-title">
          {/* The title alone. The eyebrow and the migration subtitle were both
              removed: a reader who clicked a blip already knows which radar they
              are on, so both were repeating context to buy nothing. */}
          <h1 className="rdr-h1">{board.title}</h1>
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
            {/* Stepping between quadrants is a move along a ring of four, so the
                control is a pair of steppers rather than four named chips: the names
                are already the four corners of the full radar. */}
            <div className="rdr-quadrant-nav">
              {[["prev", -1, "M14 5l-7 7 7 7"], ["next", 1, "M10 5l7 7-7 7"]].map(([k, step, d]) => (
                <button key={k} type="button" className="rdr-icon-btn" data-size="lg"
                        aria-label={`${k === "prev" ? "Previous" : "Next"} quadrant`}
                        onClick={() => {
                          const i = QUADRANTS.findIndex((q) => q.k === onBoard.quadrant);
                          const to = QUADRANTS[(i + step + QUADRANTS.length) % QUADRANTS.length];
                          const first = board.blips.find((b) => b.quadrant === to.k);
                          if (first) go(`#/blip/${first.num}`);
                        }}>
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d={d} /></svg>
                </button>
              ))}
            </div>
          </div>
          <div className="rdr-quadrant-stage">
            <Radar mode="quadrant" blips={siblings} quadrant={onBoard.quadrant}
                   selectedNum={num} width={530} height={640}
                   onSelect={(n) => go(`#/blip/${n}`)} />
          </div>
        </section>

        {blip ? (
          <BlipPanel blip={blip} />
        ) : (
          <article className="rdr-panel">
            <Status loading={loading} error={error} onRetry={reload} />
          </article>
        )}
      </div>

      <HistoryBar variant="quarters" periods={board.periods} />
    </main>
  );
}

export default function RadarPage() {
  const route = useRoute();
  const [granularity, setGranularity] = useState("quarter");
  const onGranularity = useCallback((g) => setGranularity(g), []);
  const active = route.name === "blip" ? "radar" : route.name;

  const { board, error, loading, reload } = useRadar(OPEN_RADAR);
  const { radars } = useRadars();

  const blocked = loading || error || !board || board.blips.length === 0;

  return (
    <div className="rdr-page">
      {/* No chrome on the blip route. The anchor there is the summary panel, and a
          wordmark plus four nav keys plus a palette switch is five small elements
          competing with it. The radar title returns to the full radar, so the view
          is not a dead end. */}
      {route.name !== "blip" && <Chrome active={active} board={board} />}
      {blocked ? (
        <main className="rdr-status-wrap">
          <Status loading={loading} error={error} onRetry={reload}
                  empty={!loading && !error && !!board && board.blips.length === 0} />
        </main>
      ) : route.name === "index" ? (
        <BlipIndex blips={board.blips} radars={radars ?? [board]}
                   currentPeriod={board.periods.find((p) => p.current)?.key}
                   onOpen={(num) => go(`#/blip/${num}`)} />
      ) : route.name === "radars" ? (
        <RadarList radars={radars ?? [board]} openSlug={board.slug}
                   onOpen={() => go("#/")} />
      ) : route.name === "blip" ? (
        <BlipView board={board} num={route.num} />
      ) : (
        <FullView board={board} granularity={granularity} onGranularity={onGranularity} />
      )}
    </div>
  );
}
