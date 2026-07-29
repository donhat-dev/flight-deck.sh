/**
 * FlightDeckRadio — the parallel control plane.
 *
 * Composition contract: docs/flightdeck-composition-and-radio.md §2, with the
 * region-to-rule mapping in radio.css. Four regions, one anchor.
 *
 * It is a control plane rather than a dashboard because it acts: tune to a
 * channel (which repoints the anchor), mute one (stop watching it), and open the
 * transcript. Read-only would make this a fourth way to look at the same
 * numbers.
 *
 * Data comes from endpoints that already exist — /api/summary, /api/sessions,
 * /api/quota, /api/usage-windows — plus the SSE ping for "something changed". No
 * backend work. When the API is unreachable the page falls back to a small
 * representative set so the composition can still be reviewed, and says so
 * rather than presenting sample numbers as live ones.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";

import { get, subscribe } from "./api.js";
import PaletteToggle from "./ui/PaletteToggle.jsx";

const MUTED_KEY = "flightdeck.radio.muted";

/* ---------------------------------------------------------------- format */

const money = (n) =>
  `$${(Number(n) || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

const compact = (n) => {
  const v = Number(n) || 0;
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return String(Math.round(v));
};

const clock = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "--:--"
    : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
};

const sinceText = (iso) => {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
};

const elapsedText = (from, to) => {
  const a = new Date(from).getTime();
  const b = new Date(to).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return "—";
  const mins = Math.max(0, Math.round((b - a) / 60000));
  return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, "0")}m`;
};

/** The project path is long and its tail is the part that identifies it. */
const channelName = (s) => {
  const parts = String(s.project || "").split("/").filter(Boolean);
  return s.title || parts.slice(-2).join("/") || s.session_id.slice(0, 8);
};

/* ---------------------------------------------------------------- sample */

/**
 * Representative, NOT live. Shaped exactly like the endpoints so the render path
 * is the same one production takes — a fallback with a different shape would
 * mean the design was reviewed against code that never runs.
 */
const SAMPLE = {
  live: false,
  sessions: [
    { session_id: "sample-1", project: "/home/u/Projects/flight-deck.sh", title: "flight-deck.sh",
      models: ["claude-opus-5"], turns: 269, cost: 12.35, cache_ratio: 0.958,
      first_ts: new Date(Date.now() - 5.4e6).toISOString(), last_ts: new Date().toISOString() },
    { session_id: "sample-2", project: "/home/u/Projects/nakivo", title: "nakivo",
      models: ["claude-sonnet-5"], turns: 88, cost: 2.1, cache_ratio: 0.91,
      first_ts: new Date(Date.now() - 9e6).toISOString(),
      last_ts: new Date(Date.now() - 1.8e6).toISOString() },
    { session_id: "sample-3", project: "/home/u/Projects/lago", title: "lago",
      models: ["claude-haiku-4-5"], turns: 24, cost: 0.34, cache_ratio: 0.77,
      first_ts: new Date(Date.now() - 1.4e7).toISOString(),
      last_ts: new Date(Date.now() - 7.2e6).toISOString() },
  ],
  summary: { total_cost: 14.79, cache_hit_rate: 0.94, output_tokens: 2753315, session_count: 3 },
  quota: { available: true, five_hour: { used_percentage: 22, resets_text: "6:50pm" } },
  window: { costUSD: 73.42, burnRate: { costPerHour: 25.52 }, projection: { remainingMinutes: 120 } },
};

/* ---------------------------------------------------------------- data */

function useRadioData() {
  const [state, setState] = useState({ ...SAMPLE, loading: true });

  const load = useCallback(async () => {
    try {
      const [sessions, summary, quota, windows] = await Promise.all([
        get("/api/sessions?range=today"),
        get("/api/summary?range=today"),
        get("/api/quota"),
        get("/api/usage-windows"),
      ]);
      setState({
        live: true,
        loading: false,
        sessions: Array.isArray(sessions) ? sessions : [],
        summary: summary || {},
        quota: quota || {},
        window: windows?.active || null,
      });
    } catch {
      // Keep the sample, but never claim it is live.
      setState({ ...SAMPLE, loading: false });
    }
  }, []);

  useEffect(() => {
    load();
    return subscribe(load, () => {});
  }, [load]);

  return state;
}

/* ---------------------------------------------------------------- regions */

function OnAir({ session, window: win, live }) {
  const rate = win?.burnRate?.costPerHour;
  const model = (session?.models || [])[0] || "—";
  return (
    /* The anchor. Display type and the only depth on the page. */
    <section className="radio-onair" aria-labelledby="radio-onair-title">
      <p className="radio-eyebrow">{live ? "On air" : "On air · sample"}</p>
      <h1 className="radio-onair-title" id="radio-onair-title">
        {session ? channelName(session) : "No channel"}
      </h1>
      <p className="radio-onair-sub">
        {session ? `${model} · ${session.session_id.slice(0, 8)}` : "nothing running"}
      </p>

      <p className="radio-rate">
        <b data-num>{rate == null ? "—" : money(rate)}</b>
        <span>per hour{rate == null ? "" : " burn"}</span>
      </p>

      <dl className="radio-facts">
        <div className="radio-fact">
          <dt>Elapsed</dt>
          <dd data-num>{session ? elapsedText(session.first_ts, session.last_ts) : "—"}</dd>
        </div>
        <div className="radio-fact">
          <dt>Turns</dt>
          <dd data-num>{session ? session.turns : "—"}</dd>
        </div>
        <div className="radio-fact">
          <dt>Session cost</dt>
          <dd data-num>{session ? money(session.cost) : "—"}</dd>
        </div>
        <div className="radio-fact">
          <dt>Window spend</dt>
          <dd data-num>{win ? money(win.costUSD) : "—"}</dd>
        </div>
        <div className="radio-fact">
          <dt>Window left</dt>
          <dd data-num>
            {win?.projection?.remainingMinutes == null
              ? "—"
              : `${win.projection.remainingMinutes}m`}
          </dd>
        </div>
        <div className="radio-fact">
          <dt>Last heard</dt>
          <dd data-num>{session ? sinceText(session.last_ts) : "—"}</dd>
        </div>
      </dl>

      <div className="radio-onair-actions">
        <a
          className="fdx-button"
          href={session ? `/#/session/${encodeURIComponent(session.session_id)}` : "/"}
          data-variant="primary"
        >
          Open transcript
        </a>
        <a className="fdx-button" data-variant="secondary" href="/">
          Back to deck
        </a>
      </div>
    </section>
  );
}

function Channels({ sessions, selected, onTune, muted, onMute, onUnmuteAll }) {
  return (
    <section className="radio-channels" aria-labelledby="radio-channels-head">
      <div className="radio-channels-head">
        <p className="radio-eyebrow" id="radio-channels-head">
          Channels
        </p>
        {/* C5: a distinct fact, not a repeated word. */}
        <span className="radio-count" data-num>
          {sessions.length} live · {muted.length} muted
        </span>
      </div>

      <div className="radio-band" role="list">
        {sessions.map((s) => (
          <div className="radio-channel-wrap" role="listitem" key={s.session_id}>
            {/* data-selected, not a second class: depth cannot be rendered for
                every row even by accident (C3c). */}
            <button
              type="button"
              className="radio-channel"
              data-selected={s.session_id === selected ? "true" : "false"}
              aria-pressed={s.session_id === selected}
              onClick={() => onTune(s.session_id)}
            >
              <span>
                <span className="radio-channel-name">{channelName(s)}</span>
                <span className="radio-channel-meta">
                  {s.turns} turns · {sinceText(s.last_ts)} ago
                </span>
              </span>
              <span className="radio-channel-cost">{money(s.cost)}</span>
            </button>
            <button
              type="button"
              className="radio-mute"
              onClick={() => onMute(s.session_id)}
              aria-label={`Mute ${channelName(s)}`}
              title="Stop watching this channel"
            >
              mute
            </button>
          </div>
        ))}
      </div>

      {muted.length > 0 && (
        <p className="radio-muted-note">
          {muted.length} muted · <button type="button" onClick={onUnmuteAll}>unmute all</button>
        </p>
      )}
    </section>
  );
}

function Signal({ summary, quota, window: win }) {
  const headroom = quota?.five_hour?.used_percentage;
  const cache = summary?.cache_hit_rate;
  const rate = win?.burnRate?.costPerHour;
  return (
    /* The sparse pole. Three meters, deliberately surrounded by nothing. */
    <section className="radio-signal" aria-label="Signal">
      <dl className="radio-meter" data-tone={headroom > 80 ? "signal" : "default"}>
        <dt>Quota headroom</dt>
        <dd data-num>{headroom == null ? "—" : `${Math.round(100 - headroom)}%`}</dd>
        <div className="radio-meter-bar">
          <i style={{ width: `${Math.min(100, Math.max(0, 100 - (headroom ?? 100)))}%` }} />
        </div>
      </dl>
      <dl className="radio-meter" data-tone="positive">
        <dt>Cache hit</dt>
        <dd data-num>{cache == null ? "—" : `${Math.round(cache * 100)}%`}</dd>
        <div className="radio-meter-bar">
          <i style={{ width: `${Math.min(100, Math.max(0, (cache ?? 0) * 100))}%` }} />
        </div>
      </dl>
      <dl className="radio-meter" data-tone="signal">
        <dt>Cost rate</dt>
        <dd data-num>{rate == null ? "—" : money(rate)}</dd>
        <div className="radio-meter-bar">
          <i style={{ width: `${Math.min(100, ((rate ?? 0) / 40) * 100)}%` }} />
        </div>
      </dl>
    </section>
  );
}

/** The day's notable events, derived from the sessions already loaded: no new
 *  endpoint, and every line is a fact the deck can corroborate. */
function logFrom(sessions) {
  const now = Date.now();
  return sessions
    .map((s) => {
      const idleMinutes = (now - new Date(s.last_ts).getTime()) / 60000;
      const running = idleMinutes < 5;
      return {
        id: s.session_id,
        at: s.last_ts,
        // Running is *active*, which C6 gives to Coral Signal; idle is
        // unremarkable and stays muted. Warning and Critical are not used here
        // because /api/sessions cannot tell us a run was delayed or failed —
        // colouring an idle run as a problem would be an invented fact.
        state: running ? "live" : "idle",
        label: running ? "running" : "idle",
        text: `${channelName(s)} — ${s.turns} turns, ${compact(s.output || 0)} out, ${money(s.cost)}`,
      };
    })
    .sort((a, b) => new Date(b.at) - new Date(a.at))
    .slice(0, 6);
}

function Log({ sessions }) {
  const rows = useMemo(() => logFrom(sessions), [sessions]);
  return (
    <section className="radio-log" aria-label="Log" aria-live="polite">
      <div className="radio-log-head">
        <p className="radio-eyebrow">Log · today</p>
      </div>
      {rows.map((r) => (
        <div className="radio-log-item" key={r.id} data-state={r.state}>
          <time dateTime={r.at}>{clock(r.at)}</time>
          <p>{r.text}</p>
          <b>{r.label}</b>
        </div>
      ))}
    </section>
  );
}

/* ---------------------------------------------------------------- page */

export default function Radio() {
  const { sessions, summary, quota, window: win, live, loading } = useRadioData();

  const [muted, setMuted] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(MUTED_KEY) || "[]");
    } catch {
      return [];
    }
  });
  const [tuned, setTuned] = useState(null);

  useEffect(() => {
    try {
      localStorage.setItem(MUTED_KEY, JSON.stringify(muted));
    } catch {
      /* a full or blocked store must not take the page down */
    }
  }, [muted]);

  // Most recently heard first — a monitoring surface orders by recency, not cost.
  const audible = useMemo(
    () =>
      sessions
        .filter((s) => !muted.includes(s.session_id))
        .slice()
        .sort((a, b) => new Date(b.last_ts) - new Date(a.last_ts)),
    [sessions, muted],
  );

  const selected = tuned && audible.some((s) => s.session_id === tuned) ? tuned : audible[0]?.session_id;
  const onAir = audible.find((s) => s.session_id === selected) || null;

  // The needle reads burn rate against a 40 $/h full scale, so its position is a
  // unit, not a decoration.
  const needle = Math.min(98, Math.max(1, ((win?.burnRate?.costPerHour ?? 0) / 40) * 100));

  return (
    <div className="radio-shell" data-loading={loading ? "true" : "false"}>
      <header className="radio-masthead">
        <p className="radio-wordmark">
          Flight<b>Deck</b> Radio
        </p>
        <div className="radio-dial" aria-hidden="true">
          <span
            className="radio-needle"
            style={{ left: `${needle}%` }}
            data-label={win?.burnRate?.costPerHour ? money(win.burnRate.costPerHour) : ""}
          />
        </div>
        <div className="radio-masthead-right">
          <p className="radio-onair-lamp" data-live={live && onAir ? "true" : "false"}>
            <i />
            {live ? (onAir ? "On air" : "Quiet") : "Sample"}
          </p>
          <PaletteToggle />
        </div>
      </header>

      <div className="radio-body">
        <OnAir session={onAir} window={win} live={live} />
        <Channels
          sessions={audible}
          selected={selected}
          onTune={setTuned}
          muted={muted}
          onMute={(id) => setMuted((m) => [...new Set([...m, id])])}
          onUnmuteAll={() => setMuted([])}
        />
      </div>

      <Signal summary={summary} quota={quota} window={win} />
      <Log sessions={audible} />

      {!live && !loading && (
        <p className="radio-offline">
          API unreachable — the numbers above are a representative sample, not live state.
        </p>
      )}
    </div>
  );
}
