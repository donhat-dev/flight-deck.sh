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
import MoveBlipModal from "./MoveBlipModal.jsx";
import Radar from "./Radar.jsx";
import RadarList from "./RadarList.jsx";
import SummaryPanel from "./SummaryPanel.jsx";
import { useBlip, useRadars } from "./data.js";
import { QUADRANTS, boardQuadrants, ringEdges } from "./geometry.js";

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
export function Status({ loading, error, empty, noRadars, onRetry }) {
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
  {/* Three empties, not one. "No radars at all" and "a radar with nothing on it" are
      different situations with different next actions, and both used to be reachable
      only as the ERROR above — the install with no radars got "Could not load the
      radar", a failure message for something that had not failed. */}
  if (noRadars) {
    return (
      <div className="rdr-status">
        <p className="rdr-status-line">No radars yet.</p>
        <p className="rdr-status-hint">
          A radar is one initiative. Create one with the radar MCP
          (<code>radar_create</code>), then put something on it with a reason.
        </p>
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
  const quads = boardQuadrants(board);
  const edges = ringEdges(board.rings);
  // Selection is component state, not a route. Clicking a blip does not navigate at
  // all now, so there is nothing for the URL to preserve and nothing for the back
  // button to walk — a hash per click would put twenty entries in the history for one
  // reading session. The linkable thing is still the detail page.
  const [picked, setPicked] = useState(null);
  const blip = board.blips.find((b) => b.num === picked) ?? null;

  // A radar that changed under the panel must not leave a stale blip open. This is
  // reachable: recording a move refetches the board, and the reindex tool can renumber
  // every blip on it.
  useEffect(() => {
    if (picked !== null && !board.blips.some((b) => b.num === picked)) setPicked(null);
  }, [board, picked]);

  useEffect(() => {
    const on = (e) => { if (e.key === "Escape") setPicked(null); };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  return (
    <main className="rdr-full">
      {/* The stage is a ROW so the panel takes width from the radar rather than
          covering it. The canvas is already sized from its height with
          `max-inline-size: 100%`, so it shrinks to the room left over on its own. */}
      <div className="rdr-full-row">
        <div className="rdr-full-stage">
          <Radar mode="full" blips={board.blips} selectedNum={picked} quadrants={quads}
                 edges={edges}
                 onSelect={(num) => setPicked((cur) => (cur === num ? null : num))} />
        </div>
        {blip && (
          <SummaryPanel blip={blip} quadrants={quads}
                        onClose={() => setPicked(null)}
                        onOpenDetail={() => go(`#/blip/${blip.num}`)} />
        )}
      </div>
      <HistoryBar granularity={granularity} onGranularity={onGranularity}
                  periods={board.periods} />
    </main>
  );
}

function BlipView({ board, num, reloadBoard }) {
  const quads = boardQuadrants(board);
  const edges = ringEdges(board.rings);
  // The detail is a SECOND request. The board already carries every blip's position,
  // which is all the drawing needs; one blip's whole move history is only wanted when
  // a reader opens it, and fetching 34 histories to draw one circle would pull most of
  // the ledger down for nothing.
  const { blip, error, loading, reload } = useBlip(board.slug, num);
  const [moving, setMoving] = useState(false);
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

  const quadrant = quads.find((q) => q.k === onBoard.quadrant) ?? quads[0];
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
        {/* Disabled until the DETAIL has arrived, not just the board. The modal
            needs the move count and the blip's own ring to say what a choice means,
            and opening it against a half-loaded blip would show "0 moves on record"
            for a blip with four. */}
        <button type="button" className="rdr-btn" data-variant="primary"
                disabled={!blip} onClick={() => setMoving(true)}>
          Move blip
        </button>
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
                   selectedNum={num} width={530} height={640} quadrants={quads}
                   edges={edges}
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

      {/* Both surfaces are refetched, not patched. A move changes the blip's own
          history AND its position on the board, and the ring the drawing uses is
          derived server-side — so re-deriving it here to save a request is exactly
          how the table and the circle come to disagree. */}
      {moving && blip && (
        <MoveBlipModal
          slug={board.slug}
          blip={blip}
          quadrants={quads}
          periods={board.periods}
          onClose={() => setMoving(false)}
          onRecorded={() => { setMoving(false); reload(); reloadBoard(); }}
        />
      )}
    </main>
  );
}

export default function RadarPage() {
  const route = useRoute();
  const [granularity, setGranularity] = useState("quarter");
  const onGranularity = useCallback((g) => setGranularity(g), []);
  const active = route.name === "blip" ? "radar" : route.name;

  // ONE request. `/radars` already returns each radar as a complete board, so the open
  // one is picked out of the list rather than fetched a second time by slug.
  const { radars, error, loading, reload } = useRadars();

  // Which radar is open is now state, not a constant. `null` means "whichever the API
  // listed first", which is what the deleted OPEN_RADAR constant only claimed to be.
  const [chosen, setChosen] = useState(null);
  const board = radars?.find((r) => r.slug === chosen) ?? radars?.[0] ?? null;

  // Two different blocks, because they block different things. With no radars there is
  // nothing any view can render. With a radar that simply has no blips yet, the Radars
  // list must stay reachable — it is how a reader gets to a different one, and a single
  // flag used to lock that door too.
  const noRadars = !loading && !error && (radars?.length ?? 0) === 0;
  const dead = loading || error || noRadars || !board;
  const noBlips = !dead && board.blips.length === 0;

  return (
    <div className="rdr-page">
      {/* No chrome on the blip route. The anchor there is the summary panel, and a
          wordmark plus four nav keys plus a palette switch is five small elements
          competing with it. The radar title returns to the full radar, so the view
          is not a dead end. */}
      {route.name !== "blip" && <Chrome active={active} board={board} />}
      {dead ? (
        <main className="rdr-status-wrap">
          <Status loading={loading} error={error} noRadars={noRadars} onRetry={reload} />
        </main>
      ) : route.name === "radars" ? (
        <RadarList radars={radars} openSlug={board.slug}
                   onOpen={(slug) => { setChosen(slug); go("#/"); }} />
      ) : noBlips ? (
        <main className="rdr-status-wrap">
          <Status empty onRetry={reload} />
        </main>
      ) : route.name === "index" ? (
        <BlipIndex blips={board.blips} radars={radars} quadrants={boardQuadrants(board)}
                   currentPeriod={board.periods.find((p) => p.current)?.key}
                   onOpen={(num) => go(`#/blip/${num}`)} />
      ) : route.name === "blip" ? (
        <BlipView board={board} num={route.num} reloadBoard={reload} />
      ) : (
        <FullView board={board} granularity={granularity} onGranularity={onGranularity} />
      )}
    </div>
  );
}
