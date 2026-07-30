/**
 * FlightDeckRadio — the plane shell.
 *
 * The plane is what makes two views one control plane rather than two proposals
 * sitting next to each other. It owns:
 *
 *   - identity and instruments: wordmark, the burn-rate dial, the ON AIR lamp,
 *     the palette switch. The dial reads a rate that matters on every tab, so it
 *     belongs to the plane rather than to a view.
 *   - the tabs, and the hash route behind them, so a tab is linkable and survives
 *     a reload.
 *   - the data both tabs need — quota and the rolling window — fetched once, so
 *     switching tabs does not refetch from scratch. Each view still fetches what
 *     only it needs (sessions for ON AIR, daily/by-model for SPEND), because
 *     those differ by range.
 *   - view-scoped controls, placed beside the tabs: the range control appears on
 *     SPEND and is simply absent on ON AIR, where a time range means nothing next
 *     to "what is running now".
 *
 * What it deliberately does NOT own is an anchor. plane.css declares no
 * `composition: anchor`, so the one anchor on screen is always the mounted view's.
 * See docs/flightdeck-composition-and-radio.md §2 (C1).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";

import { get, subscribe } from "../api.js";
import PaletteToggle from "../ui/PaletteToggle.jsx";
import RangeControl from "../ui/RangeControl.jsx";
import OnAirView from "./OnAirView.jsx";
import SpendView from "../spend/SpendComposed.jsx";

const TABS = [
  { k: "now", label: "On air", hash: "#/now" },
  { k: "spend", label: "Spend", hash: "#/spend" },
];

/** Full scale for the dial, in $/hour. A needle without a unit is decoration. */
const DIAL_FULL_SCALE = 40;

const money = (n) =>
  `$${(Number(n) || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

function useTab() {
  const read = () => {
    if (typeof window === "undefined") return TABS[0].k;
    const hit = TABS.find((t) => t.hash === window.location.hash);
    return hit ? hit.k : TABS[0].k;
  };
  const [tab, setTab] = useState(read);
  useEffect(() => {
    const on = () => setTab(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const go = useCallback((k) => {
    window.location.hash = TABS.find((t) => t.k === k)?.hash || TABS[0].hash;
  }, []);
  return [tab, go];
}

/** Quota and the rolling window are range-independent, so the plane holds them
 *  for every tab and refreshes on the SSE ping. */
function usePlaneData() {
  const [state, setState] = useState({ live: false, loading: true, quota: null, window: null });

  const load = useCallback(async () => {
    try {
      const [quota, windows] = await Promise.all([get("/api/quota"), get("/api/usage-windows")]);
      setState({ live: true, loading: false, quota: quota || null, window: windows?.active || null });
    } catch {
      setState({ live: false, loading: false, quota: null, window: null });
    }
  }, []);

  useEffect(() => {
    load();
    return subscribe(load, () => {});
  }, [load]);

  return state;
}

export default function Plane() {
  const [tab, goTab] = useTab();
  const [range, setRange] = useState("30d");
  const { quota, window: win, live, loading } = usePlaneData();

  const rate = win?.burnRate?.costPerHour;
  const needle = useMemo(
    () => Math.min(98, Math.max(1, ((rate ?? 0) / DIAL_FULL_SCALE) * 100)),
    [rate],
  );

  return (
    <div className="radio-plane">
      <header className="radio-chrome">
        <div className="radio-masthead">
          <p className="radio-wordmark">
            Flight<b>Deck</b> Radio
          </p>
          <div className="radio-dial" aria-hidden="true">
            <span
              className="radio-needle"
              style={{ left: `${needle}%` }}
              data-label={rate ? money(rate) : ""}
            />
          </div>
          <div className="radio-masthead-right">
            <p className="radio-lamp" data-live={live ? "true" : "false"}>
              <i />
              {live ? "On air" : loading ? "Tuning" : "Offline"}
            </p>
            <PaletteToggle />
          </div>
        </div>

        <div className="radio-tabrow">
          <div className="radio-tabs" role="tablist" aria-label="Plane views">
            {/* composition-lint-allow: C3c — every key in a preset bank stands
                proud; that is the neo-brutalist idiom, not a list of raised rows.
                TABS is a module constant of two entries, so the count is bounded
                by a code change rather than by data. Costs 2 of the screen's 4
                depth slots — the trade is recorded in the plan's §3. */}
            {TABS.map((t) => (
              <button
                key={t.k}
                type="button"
                role="tab"
                className="radio-tab"
                aria-selected={tab === t.k}
                onClick={() => goTab(t.k)}
              >
                {t.label}
              </button>
            ))}
          </div>
          {/* Present only where it means something. */}
          {tab === "spend" && (
            <div className="radio-tabtools">
              <RangeControl range={range} onChange={setRange} />
            </div>
          )}
        </div>
      </header>

      {tab === "spend" ? (
        <SpendView range={range} />
      ) : (
        <OnAirView quota={quota} window={win} live={live} />
      )}
    </div>
  );
}
