import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  Broadcast,
  CaretRight,
  CheckCircle,
  ClockCounterClockwise,
  Gauge,
  GitDiff,
  MagnifyingGlass,
  Moon,
  Pause,
  Play,
  PlugsConnected,
  Sun,
} from "@phosphor-icons/react";

const PHASES = ["observe", "plan", "change", "verify", "handoff"];

const NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];
const numberWord = (n) => NUMBER_WORDS[n] ?? String(n);
const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);
const pad = (n) => String(n).padStart(2, "0");
const formatClock = (date) => `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;

function formatElapsed(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

const initialBlockers = [
  {
    id: "mcp",
    kind: "connection",
    icon: PlugsConnected,
    title: "Odoo MCP lost its connection",
    detail: "Last successful call at 22:06. Three saved flows depend on it.",
    action: "Reconnect",
    done: { label: "connection restored", detail: "odoo-mcp · 3 flows back" },
  },
  {
    id: "quota",
    kind: "usage",
    icon: Gauge,
    title: "Weekly window will hit 81% by Thursday",
    detail: "Pace is 7.4% above the seven-day median. Move batch work to haiku to stay inside.",
    action: "Mute for today",
    done: { label: "pace alert muted", detail: "returns tomorrow 09:00" },
  },
  {
    id: "review",
    kind: "review",
    icon: GitDiff,
    title: "11 changed files await review",
    detail: "component-lab holds seven of them across two sessions.",
    action: "Open review",
    done: { label: "review opened", detail: "11 files queued" },
  },
];

const initialResumable = [
  {
    id: "component-contract",
    title: "Refine the component contract",
    project: "flight-deck.sh",
    model: "opus-4.1",
    idle: "4m",
    changes: "7 files · +184 −31",
    cost: "$1.84",
  },
  {
    id: "odoo-graph",
    title: "Map downstream model dependencies",
    project: "odoo-tools",
    model: "sonnet-4",
    idle: "1h 12m",
    changes: "12 nodes · 3 paths",
    cost: "$2.31",
  },
  {
    id: "mcp-cleanup",
    title: "Remove stale MCP registrations",
    project: "component-kit",
    model: "haiku-3.5",
    idle: "3h 05m",
    changes: "6 entries · 2 stale",
    cost: "$0.19",
  },
];

const flightLog = [
  {
    id: "pulse-widget",
    time: "17:58",
    title: "Ship the pulse widget states",
    project: "component-kit",
    model: "sonnet-4",
    duration: "41m",
    cost: "$1.12",
    outcome: "merged",
  },
  {
    id: "cost-backfill",
    time: "16:24",
    title: "Backfill session cost snapshots",
    project: "flight-deck.sh",
    model: "haiku-3.5",
    duration: "12m",
    cost: "$0.08",
    outcome: "verified",
  },
  {
    id: "health-probe",
    time: "15:07",
    title: "Repair the MCP health probe",
    project: "odoo-tools",
    model: "sonnet-4",
    duration: "28m",
    cost: "$0.44",
    outcome: "needs review",
  },
  {
    id: "kit-tokens",
    time: "13:52",
    title: "Extract spacing tokens from the kit",
    project: "component-kit",
    model: "sonnet-4",
    duration: "19m",
    cost: "$0.31",
    outcome: "merged",
  },
  {
    id: "quota-alarm",
    time: "11:36",
    title: "Tune the weekly quota alarm",
    project: "flight-deck.sh",
    model: "opus-4.1",
    duration: "52m",
    cost: "$2.60",
    outcome: "verified",
  },
];

const OUTCOME_TONE = { merged: "positive", verified: "positive", "needs review": "warning" };

const tapePool = [
  { type: "read", label: "source indexed", detail: "HomeConceptV2.jsx" },
  { type: "edit", label: "styles changed", detail: "home-concept-v2.css" },
  { type: "test", label: "vite build passed", detail: "4.86s" },
  { type: "plan", label: "todo updated", detail: "3 of 5 done" },
  { type: "usage", label: "window refreshed", detail: "64% of five-hour" },
  { type: "test", label: "eslint clean", detail: "0 warnings" },
];

const usageByPeriod = {
  today: { cost: "$7.84", tokens: "1.26m", cache: "18.7%", mix: "opus 22 · sonnet 61 · haiku 17" },
  week: { cost: "$43.17", tokens: "8.92m", cache: "22.4%", mix: "opus 31 · sonnet 55 · haiku 14" },
};

const usageWindows = [
  { id: "5h", label: "Five-hour window", value: 64, detail: "36% remains · resets 21:00" },
  { id: "wk", label: "Weekly window", value: 58, detail: "pace +7.4% · resets Mon 09:00" },
];

function PhysicalButton({ variant = "primary", className = "", children, ...props }) {
  return (
    <button type="button" className={`fd2-btn fd2-btn-${variant} ${className}`} {...props}>
      {children}
    </button>
  );
}

function StatusBadge({ tone, children }) {
  return (
    <span className="fd2-badge" data-tone={tone}>
      <span className="fd2-badge-dot" aria-hidden="true" />
      {children}
    </span>
  );
}

function LaneEmpty({ icon: Icon, tone, title, children, action }) {
  return (
    <div className="v2-empty" data-tone={tone} role="status">
      <Icon aria-hidden="true" />
      <div>
        <h3>{title}</h3>
        <p>{children}</p>
        {action}
      </div>
    </div>
  );
}

function HomeConceptV2() {
  const [theme, setTheme] = useState("night");
  const [now, setNow] = useState(() => new Date());
  const [blockers, setBlockers] = useState(() => initialBlockers.map((b) => ({ ...b, resolving: false })));
  const [running, setRunning] = useState(() => [
    {
      id: "usage-polling",
      title: "Trace the usage polling regression",
      project: "flight-deck.sh",
      model: "sonnet-4",
      startedAt: Date.now() - (23 * 60 + 41) * 1000,
      phaseIndex: 3,
      lastEvent: "vite build · 4.86s",
    },
  ]);
  const [resumable, setResumable] = useState(initialResumable);
  const [openingId, setOpeningId] = useState(null);
  const [period, setPeriod] = useState("today");
  const [search, setSearch] = useState("");
  const [tapePaused, setTapePaused] = useState(false);
  const [tape, setTape] = useState(() => {
    const t = Date.now();
    return [
      { id: "seed-4", type: "ready", label: "response ready", detail: "local handoff", time: formatClock(new Date(t - 9000)) },
      { id: "seed-3", type: "test", label: "build passed", detail: "vite build · 4.86s", time: formatClock(new Date(t - 26000)) },
      { id: "seed-2", type: "edit", label: "styles changed", detail: "index.css", time: formatClock(new Date(t - 71000)) },
      { id: "seed-1", type: "read", label: "source indexed", detail: "ComponentLab.jsx", time: formatClock(new Date(t - 94000)) },
    ];
  });

  const searchRef = useRef(null);
  const timersRef = useRef([]);
  const tapeSeq = useRef(0);
  const poolIndex = useRef(0);

  function pushTape(type, label, detail) {
    tapeSeq.current += 1;
    const entry = { id: `evt-${tapeSeq.current}`, type, label, detail, time: formatClock(new Date()) };
    setTape((current) => [entry, ...current].slice(0, 14));
  }

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => () => timersRef.current.forEach((id) => window.clearTimeout(id)), []);

  useEffect(() => {
    if (tapePaused) return undefined;
    const id = window.setInterval(() => {
      const next = tapePool[poolIndex.current % tapePool.length];
      poolIndex.current += 1;
      pushTape(next.type, next.label, next.detail);
    }, 7000);
    return () => window.clearInterval(id);
  }, [tapePaused]);

  useEffect(() => {
    function onKeydown(event) {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (typing) return;
      event.preventDefault();
      searchRef.current?.focus();
      searchRef.current?.select();
    }
    window.addEventListener("keydown", onKeydown);
    return () => window.removeEventListener("keydown", onKeydown);
  }, []);

  function resolveBlocker(blocker) {
    setBlockers((current) => current.map((b) => (b.id === blocker.id ? { ...b, resolving: true } : b)));
    const id = window.setTimeout(() => {
      setBlockers((current) => current.filter((b) => b.id !== blocker.id));
      pushTape("fix", blocker.done.label, blocker.done.detail);
    }, 1100);
    timersRef.current.push(id);
  }

  function resumeSession(item) {
    if (openingId) return;
    setOpeningId(item.id);
    const id = window.setTimeout(() => {
      setResumable((current) => current.filter((r) => r.id !== item.id));
      setRunning((current) => [
        ...current,
        {
          id: item.id,
          title: item.title,
          project: item.project,
          model: item.model,
          startedAt: Date.now(),
          phaseIndex: 0,
          lastEvent: "context restored",
        },
      ]);
      setOpeningId(null);
      pushTape("resume", "session resumed", item.title);
    }, 900);
    timersRef.current.push(id);
  }

  const usage = usageByPeriod[period];
  const nowMs = now.getTime();
  const query = search.trim().toLowerCase();

  const filteredResumable = useMemo(() => {
    if (!query) return resumable;
    return resumable.filter((r) =>
      [r.title, r.project, r.model].some((value) => value.toLowerCase().includes(query)),
    );
  }, [resumable, query]);

  const filteredLog = useMemo(() => {
    if (!query) return flightLog;
    return flightLog.filter((row) =>
      [row.title, row.project, row.model, row.outcome].some((value) => value.toLowerCase().includes(query)),
    );
  }, [query]);

  const headline = useMemo(() => {
    if (blockers.length) {
      return {
        lead: `${capitalize(numberWord(blockers.length))} ${blockers.length === 1 ? "blocker" : "blockers"}`,
        rest: ` ${blockers.length === 1 ? "needs" : "need"} you first.`,
      };
    }
    if (running.length) {
      return {
        lead: `${capitalize(numberWord(running.length))} ${running.length === 1 ? "session" : "sessions"}`,
        rest: ` ${running.length === 1 ? "is" : "are"} running, nothing blocked.`,
      };
    }
    if (resumable.length) {
      return {
        lead: "All clear.",
        rest: ` ${capitalize(numberWord(resumable.length))} ${resumable.length === 1 ? "session is" : "sessions are"} parked with changes.`,
      };
    }
    return { lead: "All clear.", rest: " The deck is quiet." };
  }, [blockers.length, running.length, resumable.length]);

  const subline = useMemo(() => {
    const parts = [
      running.length
        ? `${numberWord(running.length)} ${running.length === 1 ? "session is" : "sessions are"} running`
        : "nothing is running",
    ];
    if (resumable.length) {
      parts.push(`${numberWord(resumable.length)} ${resumable.length === 1 ? "sits" : "sit"} parked with changes`);
    }
    return `${capitalize(parts.join(" and "))}. The five-hour window sits at ${usageWindows[0].value}%.`;
  }, [running.length, resumable.length]);

  const hour = now.getHours();
  const daypart = hour < 5 ? "Night" : hour < 12 ? "Morning" : hour < 17 ? "Afternoon" : "Evening";
  const eyebrow = `${daypart} briefing · ${now.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" })}`;

  const deckStats = [
    ["Flights today", String(flightLog.length + running.length)],
    ["Spend today", usageByPeriod.today.cost],
    ["Open blockers", String(blockers.length)],
  ];

  return (
    <div className="v2-page" data-theme={theme}>
      <a className="v2-skip" href="#main-v2">Skip to briefing</a>

      <div className="v2-shell">
        <header className="v2-topbar">
          <a className="v2-wordmark" href="#main-v2">
            <span className="v2-wordmark-mark" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            FlightDeck
          </a>

          <div className="v2-topbar-status">
            <span className="v2-dot v2-dot-live" data-paused={false} aria-hidden="true" />
            <span className="v2-topbar-index">Local index live</span>
            <time className="v2-clock" dateTime={now.toISOString()}>{formatClock(now)}</time>
          </div>

          <label className="v2-find" htmlFor="v2-search">
            <MagnifyingGlass aria-hidden="true" />
            <span className="v2-find-label">Find</span>
            <input
              id="v2-search"
              ref={searchRef}
              type="search"
              value={search}
              placeholder="session, project, model"
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  setSearch("");
                  event.currentTarget.blur();
                }
              }}
            />
            <kbd>/</kbd>
          </label>

          <button
            className="v2-theme"
            type="button"
            aria-pressed={theme === "day"}
            onClick={() => setTheme((current) => (current === "day" ? "night" : "day"))}
          >
            {theme === "day" ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
            <span>Day view</span>
          </button>
        </header>

        <main id="main-v2" className="v2-main">
          <section className="v2-briefing" aria-labelledby="v2-headline">
            <div>
              <p className="v2-kicker">{eyebrow}</p>
              <h1 id="v2-headline">
                <em>{headline.lead}</em>
                {headline.rest}
              </h1>
              <p className="v2-sub">{subline}</p>
            </div>
            <dl className="v2-deck-stats">
              {deckStats.map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </section>

          <div className="v2-board" aria-label="Triage board">
            <section className="v2-lane" aria-labelledby="v2-lane-blocked">
              <header className="v2-lane-head">
                <h2 id="v2-lane-blocked">Waiting on you</h2>
                <span className="v2-count">{blockers.length}</span>
              </header>
              <div className="v2-lane-body">
                {blockers.length ? (
                  blockers.map((blocker) => {
                    const KindIcon = blocker.icon;
                    return (
                      <article className="v2-card" key={blocker.id} data-resolving={blocker.resolving}>
                        <header className="v2-card-head">
                          <span className="v2-kind">
                            <KindIcon aria-hidden="true" />
                            {blocker.kind}
                          </span>
                        </header>
                        <h3>{blocker.title}</h3>
                        <p className="v2-card-detail">{blocker.detail}</p>
                        <div className="v2-card-actions">
                          <PhysicalButton
                            variant="secondary"
                            onClick={() => resolveBlocker(blocker)}
                            disabled={blocker.resolving}
                            aria-busy={blocker.resolving}
                          >
                            {blocker.resolving ? "Working…" : blocker.action}
                          </PhysicalButton>
                        </div>
                      </article>
                    );
                  })
                ) : (
                  <LaneEmpty icon={CheckCircle} title="Nothing is waiting on you">
                    Approvals, failed connections, and review gaps land here.
                  </LaneEmpty>
                )}
              </div>
            </section>

            <section className="v2-lane" aria-labelledby="v2-lane-running">
              <header className="v2-lane-head">
                <h2 id="v2-lane-running">Running now</h2>
                <span className="v2-count">{running.length}</span>
              </header>
              <div className="v2-lane-body">
                {running.length ? (
                  running.map((session) => (
                    <article className="v2-card" key={session.id}>
                      <header className="v2-card-head">
                        <span className="v2-live-flag">
                          <span className="v2-dot v2-dot-live" aria-hidden="true" />
                          live
                        </span>
                        <code className="v2-elapsed">{formatElapsed(nowMs - session.startedAt)}</code>
                      </header>
                      <h3>{session.title}</h3>
                      <p className="v2-meta">
                        <span>{session.project}</span>
                        <code>{session.model}</code>
                      </p>
                      <div
                        className="v2-phase"
                        role="img"
                        aria-label={`Phase ${session.phaseIndex + 1} of ${PHASES.length}: ${PHASES[session.phaseIndex]}`}
                      >
                        {PHASES.map((phase, index) => (
                          <span
                            key={phase}
                            data-state={index < session.phaseIndex ? "done" : index === session.phaseIndex ? "now" : "todo"}
                          >
                            <i />
                            <em>{phase}</em>
                          </span>
                        ))}
                      </div>
                      <p className="v2-lastevent">
                        <CaretRight aria-hidden="true" />
                        {session.lastEvent}
                      </p>
                      <button
                        className="v2-linkish"
                        type="button"
                        onClick={() => {
                          setTapePaused(false);
                          document.querySelector(".v2-tape")?.scrollIntoView({ block: "end" });
                        }}
                      >
                        Watch on the live tape
                        <ArrowUpRight aria-hidden="true" />
                      </button>
                    </article>
                  ))
                ) : (
                  <LaneEmpty icon={CheckCircle} title="No live sessions">
                    Resume a parked session or start one from the terminal.
                  </LaneEmpty>
                )}
              </div>
            </section>

            <section className="v2-lane" aria-labelledby="v2-lane-resume">
              <header className="v2-lane-head">
                <h2 id="v2-lane-resume">Ready to resume</h2>
                <span className="v2-count">
                  {query ? `${filteredResumable.length} of ${resumable.length}` : resumable.length}
                </span>
              </header>
              <div className="v2-lane-body">
                {filteredResumable.length ? (
                  filteredResumable.map((item, index) => (
                    <article className="v2-card" key={item.id}>
                      <header className="v2-card-head">
                        <span className="v2-idle">
                          <ClockCounterClockwise aria-hidden="true" />
                          idle {item.idle}
                        </span>
                        <code>{item.cost}</code>
                      </header>
                      <h3>{item.title}</h3>
                      <p className="v2-meta">
                        <span>{item.project}</span>
                        <code>{item.model}</code>
                      </p>
                      <p className="v2-changes">{item.changes}</p>
                      <div className="v2-card-actions">
                        <PhysicalButton
                          variant={index === 0 ? "primary" : "secondary"}
                          onClick={() => resumeSession(item)}
                          disabled={Boolean(openingId)}
                          aria-busy={openingId === item.id}
                        >
                          {openingId === item.id ? "Opening…" : "Resume"}
                        </PhysicalButton>
                      </div>
                    </article>
                  ))
                ) : resumable.length ? (
                  <LaneEmpty
                    icon={MagnifyingGlass}
                    tone="search"
                    title="No parked session matches"
                    action={
                      <button className="v2-clear" type="button" onClick={() => setSearch("")}>
                        Clear search
                      </button>
                    }
                  >
                    “{search}” — try a project name or a model.
                  </LaneEmpty>
                ) : (
                  <LaneEmpty icon={CheckCircle} title="Nothing parked">
                    Sessions you leave mid-flight land here with their changes.
                  </LaneEmpty>
                )}
              </div>
            </section>
          </div>

          <section className="v2-instruments" aria-labelledby="v2-usage-heading">
            <header className="v2-band-head">
              <h2 id="v2-usage-heading">Usage</h2>
              <div className="v2-period" role="group" aria-label="Usage period">
                <button type="button" aria-pressed={period === "today"} onClick={() => setPeriod("today")}>
                  Today
                </button>
                <button type="button" aria-pressed={period === "week"} onClick={() => setPeriod("week")}>
                  7 days
                </button>
              </div>
            </header>

            <dl className="v2-instruments-body">
              {[
                ["Equivalent cost", usage.cost],
                ["Tokens", usage.tokens],
                ["Cache saved", usage.cache],
                ["Model mix", usage.mix],
              ].map(([label, value]) => (
                <div className="v2-cell" key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
              {usageWindows.map((quotaWindow) => (
                <div className="v2-cell v2-cell-meter" key={quotaWindow.id}>
                  <dt>{quotaWindow.label}</dt>
                  <dd>
                    <strong>{quotaWindow.value}%</strong>
                    <progress
                      className="v2-meter-bar"
                      max={100}
                      value={quotaWindow.value}
                      data-tone={quotaWindow.value >= 75 ? "warning" : "signal"}
                      aria-label={quotaWindow.label}
                    />
                    <span className="v2-meter-detail">{quotaWindow.detail}</span>
                  </dd>
                </div>
              ))}
            </dl>
          </section>

          <section className="v2-log" aria-labelledby="v2-log-heading">
            <header className="v2-band-head">
              <h2 id="v2-log-heading">Flight log</h2>
              <span className="v2-count">
                {query ? `${filteredLog.length} of ${flightLog.length}` : `${flightLog.length} today`}
              </span>
            </header>

            {filteredLog.length ? (
              <div className="v2-log-scroll">
                <table className="v2-table">
                  <thead>
                    <tr>
                      <th scope="col">Time</th>
                      <th scope="col">Work</th>
                      <th scope="col" className="v2-col-model">Model</th>
                      <th scope="col" className="v2-col-duration">Duration</th>
                      <th scope="col" className="v2-col-cost">Cost</th>
                      <th scope="col">Status</th>
                      <th scope="col" className="v2-col-open">
                        <span className="v2-vh">Open</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredLog.map((row) => (
                      <tr key={row.id}>
                        <td><code>{row.time}</code></td>
                        <td>
                          <strong>{row.title}</strong>
                          <span className="v2-proj">{row.project}</span>
                        </td>
                        <td className="v2-col-model"><code>{row.model}</code></td>
                        <td className="v2-col-duration"><code>{row.duration}</code></td>
                        <td className="v2-col-cost"><code>{row.cost}</code></td>
                        <td><StatusBadge tone={OUTCOME_TONE[row.outcome]}>{row.outcome}</StatusBadge></td>
                        <td className="v2-col-open">
                          <button
                            className="fd2-iconbtn"
                            type="button"
                            aria-label={`Open ${row.title}`}
                            title={`Open ${row.title}`}
                            onClick={() => pushTape("open", "session opened", row.title)}
                          >
                            <ArrowUpRight aria-hidden="true" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <LaneEmpty
                icon={MagnifyingGlass}
                tone="search"
                title="No flight matches"
                action={
                  <button className="v2-clear" type="button" onClick={() => setSearch("")}>
                    Clear search
                  </button>
                }
              >
                “{search}” — nothing in today’s log. Try a project, model, or status.
              </LaneEmpty>
            )}
          </section>
        </main>

        <aside className="v2-tape" aria-label="Live tape">
          <div className="v2-tape-head">
            <span className="v2-dot v2-dot-live" data-paused={tapePaused} aria-hidden="true" />
            <p className="v2-tape-title">Live tape</p>
            <button
              className="fd2-iconbtn"
              type="button"
              onClick={() => setTapePaused((value) => !value)}
              aria-label={tapePaused ? "Resume the live tape" : "Pause the live tape"}
              title={tapePaused ? "Resume the live tape" : "Pause the live tape"}
            >
              {tapePaused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}
            </button>
          </div>
          <ul className="v2-tape-list">
            {tape.map((event) => (
              <li key={event.id}>
                <span className="v2-tape-type">{event.type}</span>
                <code className="v2-tape-time">{event.time}</code>
                <span className="v2-tape-label">{event.label}</span>
                <span className="v2-tape-detail">{event.detail}</span>
              </li>
            ))}
          </ul>
        </aside>

        <footer className="v2-foot">
          <span>Local-first — nothing leaves this machine</span>
          <code>~/.claude/projects</code>
          <code>api :8010</code>
        </footer>
      </div>
    </div>
  );
}

export default HomeConceptV2;
