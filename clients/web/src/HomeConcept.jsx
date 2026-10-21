import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  ArrowUpRight,
  Books,
  CaretRight,
  ChartLineUp,
  CheckCircle,
  ClockCounterClockwise,
  Code,
  Command,
  Cpu,
  Database,
  FlowArrow,
  FolderOpen,
  GitDiff,
  Graph,
  House,
  ListChecks,
  MagnifyingGlass,
  NotePencil,
  Pause,
  Play,
  PlugsConnected,
  SidebarSimple,
  Stack,
  TerminalWindow,
  WarningCircle,
  X,
} from "@phosphor-icons/react";

const modeData = {
  desk: {
    label: "Desk",
    eyebrow: "Local overview",
    heading: "Two things need attention.",
    description: "Continue active work, clear blockers, and understand current usage without changing context.",
    sections: [
      { id: "now", label: "Now", icon: House },
      { id: "attention", label: "Attention", icon: WarningCircle },
      { id: "recent", label: "Recent", icon: ClockCounterClockwise },
    ],
  },
  work: {
    label: "Work",
    eyebrow: "Active work",
    heading: "Four sessions are moving.",
    description: "Sessions, tasks, code changes, and work traces share one focused working context.",
    sections: [
      { id: "sessions", label: "Sessions", icon: TerminalWindow },
      { id: "tasks", label: "Tasks & notes", icon: ListChecks },
      { id: "changes", label: "Changes", icon: GitDiff },
    ],
  },
  review: {
    label: "Review",
    eyebrow: "Usage and evidence",
    heading: "Usage is steady this week.",
    description: "Inspect cost, quota, cache behavior, model mix, and dependency impact before deciding what changes.",
    sections: [
      { id: "usage", label: "Usage & cost", icon: ChartLineUp },
      { id: "trends", label: "Trends", icon: Graph },
      { id: "dependencies", label: "Dependencies", icon: Stack },
    ],
  },
  connect: {
    label: "Connect",
    eyebrow: "Local connections",
    heading: "One service needs a reconnect.",
    description: "Manage MCP servers, saved integration flows, and installed skills from a single local registry.",
    sections: [
      { id: "connections", label: "Connections", icon: PlugsConnected },
      { id: "flows", label: "Integration flows", icon: FlowArrow },
      { id: "skills", label: "Skills", icon: Books },
    ],
  },
  system: {
    label: "System",
    eyebrow: "Local runtime",
    heading: "The workspace is healthy.",
    description: "Review containers, event streams, and interface contracts without mixing them into daily work.",
    sections: [
      { id: "runtime", label: "Runtime", icon: Cpu },
      { id: "events", label: "Event stream", icon: Database },
      { id: "interface", label: "Interface", icon: Code },
    ],
  },
};

const workspaces = [
  { id: "flight-deck", label: "flight-deck.sh", meta: "12 sessions" },
  { id: "odoo-tools", label: "odoo-tools", meta: "3 sessions" },
  { id: "component-kit", label: "component-kit", meta: "7 sessions" },
];

const initialAttention = [
  {
    id: "quota",
    level: "Usage",
    title: "Weekly window reaches 81% in 1d 6h",
    detail: "Current pace is 7.4% above the seven-day median.",
  },
  {
    id: "mcp",
    level: "Connection",
    title: "Odoo MCP needs a reconnect",
    detail: "Last successful call was 38 minutes ago.",
  },
  {
    id: "diff",
    level: "Review",
    title: "11 changed files have not been reviewed",
    detail: "The latest component-lab session owns 7 of them.",
  },
];

const recentWork = [
  {
    id: "component-contract",
    title: "Refine the component contract",
    project: "flight-deck.sh",
    model: "opus-4.1",
    cost: "$1.84",
    time: "4m",
    kind: "Session",
    prompt: "Adjust the pixel cloud forms, star blink, and sun/moon placement.",
    changes: "7 files · +184 / −31",
  },
  {
    id: "usage-polling",
    title: "Trace the usage polling regression",
    project: "flight-deck.sh",
    model: "sonnet-4",
    cost: "$0.67",
    time: "23m",
    kind: "Work trace",
    prompt: "Find why the quota refresh produces duplicate requests after reconnect.",
    changes: "4 files · +72 / −18",
  },
  {
    id: "odoo-graph",
    title: "Map downstream model dependencies",
    project: "odoo-tools",
    model: "sonnet-4",
    cost: "$2.31",
    time: "1h",
    kind: "Dependency",
    prompt: "Show modules affected by the account.move field changes.",
    changes: "12 nodes · 3 paths",
  },
  {
    id: "mcp-cleanup",
    title: "Remove stale MCP registrations",
    project: "component-kit",
    model: "haiku-3.5",
    cost: "$0.19",
    time: "3h",
    kind: "Task",
    prompt: "Find disconnected MCP entries and prepare a safe removal list.",
    changes: "6 entries · 2 stale",
  },
];

const traceEvents = [
  { id: "01", type: "select", label: "session selected", detail: "component-contract", time: "18:42:03" },
  { id: "02", type: "read", label: "source indexed", detail: "ComponentLab.jsx", time: "18:42:06" },
  { id: "03", type: "edit", label: "styles changed", detail: "index.css", time: "18:43:18" },
  { id: "04", type: "test", label: "build passed", detail: "vite build · 4.86s", time: "18:44:11" },
  { id: "05", type: "ready", label: "response ready", detail: "local handoff", time: "18:44:28", active: true },
];

const usageByPeriod = {
  today: {
    cost: "$7.84",
    tokens: "1.26m",
    cache: "18.7%",
    quota: 64,
    detail: "36% remains in the current five-hour window",
  },
  week: {
    cost: "$43.17",
    tokens: "8.92m",
    cache: "22.4%",
    quota: 81,
    detail: "19% remains in the weekly window",
  },
};

function IconLabel({ icon: Icon, children }) {
  return (
    <span className="wb-icon-label">
      <Icon aria-hidden="true" />
      <span>{children}</span>
    </span>
  );
}

function PhysicalButton({ children, className = "", ...props }) {
  return (
    <button className={`wb-physical-button ${className}`} type="button" {...props}>
      {children}
    </button>
  );
}

function HomeConcept() {
  const [activeMode, setActiveMode] = useState("desk");
  const [activeSection, setActiveSection] = useState("now");
  const [workspace, setWorkspace] = useState(workspaces[0].id);
  const [attention, setAttention] = useState(initialAttention);
  const [usagePeriod, setUsagePeriod] = useState("today");
  const [search, setSearch] = useState("");
  const [streamPaused, setStreamPaused] = useState(false);
  const [mobileTraceOpen, setMobileTraceOpen] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState(recentWork[0].id);
  const [selectedTraceId, setSelectedTraceId] = useState(traceEvents.at(-1).id);
  const [syncState, setSyncState] = useState("ready");
  const [openState, setOpenState] = useState("idle");
  const syncTimer = useRef(null);
  const openTimer = useRef(null);

  const activeModeData = modeData[activeMode];
  const usage = usageByPeriod[usagePeriod];
  const selectedWorkspace = workspaces.find((item) => item.id === workspace) ?? workspaces[0];
  const selectedRun = recentWork.find((item) => item.id === selectedRunId) ?? recentWork[0];

  const filteredWork = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return recentWork;
    return recentWork.filter((item) =>
      [item.title, item.project, item.model, item.kind].some((value) =>
        value.toLowerCase().includes(query),
      ),
    );
  }, [search]);

  useEffect(() => () => {
    window.clearTimeout(syncTimer.current);
    window.clearTimeout(openTimer.current);
  }, []);

  function changeMode(mode) {
    setActiveMode(mode);
    setActiveSection(modeData[mode].sections[0].id);
  }

  function refreshWorkspace() {
    if (syncState === "loading") return;
    setSyncState("loading");
    window.clearTimeout(syncTimer.current);
    syncTimer.current = window.setTimeout(() => setSyncState("ready"), 950);
  }

  function selectWorkspace(id) {
    setWorkspace(id);
    const nextWorkspace = workspaces.find((item) => item.id === id);
    const nextRun = recentWork.find((item) => item.project === nextWorkspace?.label);
    if (nextRun) setSelectedRunId(nextRun.id);
  }

  function openSession() {
    if (openState === "opening") return;
    setOpenState("opening");
    window.clearTimeout(openTimer.current);
    openTimer.current = window.setTimeout(() => setOpenState("ready"), 820);
  }

  const openLabel =
    openState === "opening" ? "Opening session" : openState === "ready" ? "Session ready" : "Open session";

  return (
    <div className="wb-page">
      <a className="wb-skip-link" href="#main-content">Skip to workspace</a>

      <div className="wb-shell">
        <header className="wb-topbar">
          <div className="wb-global-nav">
            <a className="wb-wordmark" href="#main-content" aria-label="Local Workbench home">
              <span className="wb-wordmark-mark" aria-hidden="true">
                <span />
                <span />
                <span />
                <span />
              </span>
              <span>Local Workbench</span>
            </a>

            <nav aria-label="Global modes">
              {Object.entries(modeData).map(([id, mode]) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={activeMode === id}
                  onClick={() => changeMode(id)}
                >
                  {mode.label}
                </button>
              ))}
            </nav>
          </div>

          <div className="wb-top-actions">
            <button
              className="wb-sync-control"
              type="button"
              onClick={refreshWorkspace}
              aria-busy={syncState === "loading"}
            >
              <span className="wb-status-dot" data-loading={syncState === "loading"} aria-hidden="true" />
              <span>{syncState === "loading" ? "Indexing local data" : "Local index active"}</span>
              <ArrowClockwise aria-hidden="true" />
            </button>
            <button className="wb-command-button" type="button" onClick={() => document.querySelector("#work-search")?.focus()}>
              <Command aria-hidden="true" />
              <span>Quick find</span>
              <kbd>/</kbd>
            </button>
          </div>
        </header>

        <div className="wb-contextbar">
          <div className="wb-proof-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <div className="wb-context-name">
            <FolderOpen aria-hidden="true" />
            <span>{selectedWorkspace.label}</span>
            <span className="wb-context-meta">Local workspace</span>
          </div>
          <div className="wb-context-state">
            <span>Indexed 14 sec ago</span>
            <span className="wb-context-divider" aria-hidden="true" />
            <span>Private by default</span>
          </div>
        </div>

        <aside className="wb-context-rail" aria-label={`${activeModeData.label} sections`}>
          <div>
            <p className="wb-rail-label">{activeModeData.label}</p>
            <nav>
              {activeModeData.sections.map(({ id, label, icon }) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={activeSection === id}
                  onClick={() => setActiveSection(id)}
                >
                  <IconLabel icon={icon}>{label}</IconLabel>
                  <CaretRight aria-hidden="true" />
                </button>
              ))}
            </nav>
          </div>

          <div className="wb-workspace-list">
            <p className="wb-rail-label">Workspaces</p>
            {workspaces.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-pressed={workspace === item.id}
                onClick={() => selectWorkspace(item.id)}
              >
                <span>{item.label}</span>
                <small>{item.meta}</small>
              </button>
            ))}
          </div>

          <div className="wb-local-note">
            <Database aria-hidden="true" />
            <p>Stored on this machine</p>
            <code>~/.claude/projects</code>
          </div>
        </aside>

        <main id="main-content" className="wb-main">
          <section className="wb-main-heading" aria-labelledby="home-heading">
            <div>
              <p className="wb-eyebrow">{activeModeData.eyebrow}</p>
              <h1 id="home-heading">{activeModeData.heading}</h1>
              <p>{activeModeData.description}</p>
            </div>
            <label className="wb-search" htmlFor="work-search">
              <MagnifyingGlass aria-hidden="true" />
              <span className="wb-visually-hidden">Search recent work</span>
              <input
                id="work-search"
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search sessions, projects, models"
              />
            </label>
          </section>

          <section className="wb-continue" aria-labelledby="continue-heading">
            <div className="wb-continue-mark" aria-hidden="true">
              <TerminalWindow />
            </div>
            <div className="wb-continue-copy">
              <p className="wb-kicker">Continue where you stopped</p>
              <h2 id="continue-heading">{selectedRun.title}</h2>
              <p>“{selectedRun.prompt}”</p>
              <dl>
                <div>
                  <dt>Project</dt>
                  <dd>{selectedRun.project}</dd>
                </div>
                <div>
                  <dt>Last active</dt>
                  <dd>{selectedRun.time === "1h" ? "1 hour ago" : `${selectedRun.time} ago`}</dd>
                </div>
                <div>
                  <dt>Changes</dt>
                  <dd>{selectedRun.changes}</dd>
                </div>
              </dl>
            </div>
            <div className="wb-continue-actions">
              <PhysicalButton onClick={openSession} disabled={openState === "opening"} aria-busy={openState === "opening"}>
                <span>{openLabel}</span>
                {openState === "ready" ? <CheckCircle aria-hidden="true" /> : <ArrowUpRight aria-hidden="true" />}
              </PhysicalButton>
              <button
                className="wb-text-action"
                type="button"
                onClick={() => {
                  setStreamPaused(false);
                  setMobileTraceOpen(true);
                }}
              >
                View work trace
                <CaretRight aria-hidden="true" />
              </button>
            </div>
            <div className="wb-task-progress" aria-label="Session work trace: four of five steps complete">
              <span className="is-complete" />
              <span className="is-complete" />
              <span className="is-complete" />
              <span className="is-complete" />
              <span />
            </div>
          </section>

          <section className="wb-mid-grid">
            <div className="wb-attention" aria-labelledby="attention-heading">
              <header>
                <div>
                  <p className="wb-kicker">Action queue</p>
                  <h2 id="attention-heading">Needs attention</h2>
                </div>
                <span>{attention.length} open</span>
              </header>

              <div className="wb-attention-list">
                {attention.length ? attention.map((item) => (
                  <article key={item.id}>
                    <WarningCircle aria-hidden="true" />
                    <div>
                      <span>{item.level}</span>
                      <h3>{item.title}</h3>
                      <p>{item.detail}</p>
                    </div>
                    <button
                      type="button"
                      aria-label={`Resolve: ${item.title}`}
                      onClick={() => setAttention((current) => current.filter((entry) => entry.id !== item.id))}
                    >
                      <CheckCircle aria-hidden="true" />
                    </button>
                  </article>
                )) : (
                  <div className="wb-attention-empty" role="status">
                    <CheckCircle aria-hidden="true" />
                    <div>
                      <h3>No open blockers</h3>
                      <p>New approvals, failed services, and review gaps will appear here.</p>
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="wb-usage" aria-labelledby="usage-heading">
              <header>
                <div>
                  <p className="wb-kicker">Resource view</p>
                  <h2 id="usage-heading">Usage now</h2>
                </div>
                <div className="wb-period-control" aria-label="Usage period">
                  <button type="button" aria-pressed={usagePeriod === "today"} onClick={() => setUsagePeriod("today")}>
                    Today
                  </button>
                  <button type="button" aria-pressed={usagePeriod === "week"} onClick={() => setUsagePeriod("week")}>
                    7 days
                  </button>
                </div>
              </header>

              <dl className="wb-usage-metrics">
                <div>
                  <dt>Equivalent cost</dt>
                  <dd>{usage.cost}</dd>
                </div>
                <div>
                  <dt>Tokens</dt>
                  <dd>{usage.tokens}</dd>
                </div>
                <div>
                  <dt>Cache saved</dt>
                  <dd>{usage.cache}</dd>
                </div>
              </dl>

              <div className="wb-quota">
                <div>
                  <span>Current window</span>
                  <strong>{usage.quota}%</strong>
                </div>
                <div
                  className="wb-quota-track"
                  role="progressbar"
                  aria-label="Current usage window"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  aria-valuenow={usage.quota}
                >
                  <span style={{ "--wb-progress": `${usage.quota}%` }} />
                </div>
                <p>{usage.detail}</p>
              </div>
            </div>
          </section>

          <section className="wb-recent" aria-labelledby="recent-heading">
            <header>
              <div>
                <p className="wb-kicker">Local history</p>
                <h2 id="recent-heading">Recent work</h2>
              </div>
              <button type="button" className="wb-text-action" onClick={() => changeMode("work")}>
                View all sessions
                <ArrowUpRight aria-hidden="true" />
              </button>
            </header>

            <div className="wb-run-table">
              <div className="wb-run-head" aria-hidden="true">
                <span>Work</span>
                <span>Model</span>
                <span>Cost</span>
                <span>Updated</span>
                <span />
              </div>
              {filteredWork.length ? filteredWork.map((item) => (
                <button
                  className="wb-run-row"
                  key={item.id}
                  type="button"
                  aria-pressed={selectedRunId === item.id}
                  onClick={() => {
                    setSelectedRunId(item.id);
                    const runWorkspace = workspaces.find((entry) => entry.label === item.project);
                    if (runWorkspace) setWorkspace(runWorkspace.id);
                    setOpenState("idle");
                  }}
                >
                  <span className="wb-run-title">
                    <small>{item.kind}</small>
                    <strong>{item.title}</strong>
                    <em>{item.project}</em>
                  </span>
                  <code className="wb-run-model">{item.model}</code>
                  <code>{item.cost}</code>
                  <code>{item.time}</code>
                  <ArrowUpRight aria-hidden="true" />
                </button>
              )) : (
                <div className="wb-run-empty" role="status">
                  <MagnifyingGlass aria-hidden="true" />
                  <div>
                    <h3>No matching work</h3>
                    <p>Try a project name, model, or session title.</p>
                  </div>
                  <button type="button" onClick={() => setSearch("")}>Clear search</button>
                </div>
              )}
            </div>
          </section>
        </main>

        <aside className="wb-trace" data-open={mobileTraceOpen} aria-label="Live activity">
          <header>
            <div>
              <span className="wb-live-dot" data-paused={streamPaused} aria-hidden="true" />
              <div>
                <p>Live activity</p>
                <span>{streamPaused ? "Follow paused" : "Following selected session"}</span>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setStreamPaused((value) => !value)}
              aria-label={streamPaused ? "Resume live activity" : "Pause live activity"}
            >
              {streamPaused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}
            </button>
            <button
              className="wb-trace-close"
              type="button"
              onClick={() => setMobileTraceOpen(false)}
              aria-label="Close live activity"
            >
              <X aria-hidden="true" />
            </button>
          </header>

          <div className="wb-trace-events">
            {traceEvents.map((event) => (
              <button
                key={event.id}
                type="button"
                aria-pressed={selectedTraceId === event.id}
                data-active={selectedTraceId === event.id}
                onClick={() => setSelectedTraceId(event.id)}
              >
                <span className="wb-trace-index">{event.id}</span>
                <span className="wb-trace-content">
                  <small>{event.type}</small>
                  <strong>{event.label}</strong>
                  <em>{event.detail}</em>
                </span>
                <code>{event.time}</code>
                <CaretRight aria-hidden="true" />
              </button>
            ))}
          </div>

          <div className="wb-sequence">
            <p>Active sequence</p>
            <div>
              <span>observe</span>
              <span>decide</span>
              <span>change</span>
              <span>verify</span>
              <span>handoff</span>
            </div>
          </div>

          <footer>
            <span>Local stream</span>
            <code>index --follow</code>
          </footer>
        </aside>

        <button
          className="wb-mobile-trace-trigger"
          type="button"
          aria-expanded={mobileTraceOpen}
          onClick={() => setMobileTraceOpen(true)}
        >
          <SidebarSimple aria-hidden="true" />
          <span>Live activity</span>
          <small>{traceEvents.length} events</small>
        </button>
      </div>
    </div>
  );
}

export default HomeConcept;
