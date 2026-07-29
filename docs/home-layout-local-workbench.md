# Home layout proposal — Local Workbench

Status: interactive proposal  
Prototype: `http://127.0.0.1:5190/home-concept.html`

## 1. Direction

The home becomes a local workbench for coding-agent activity. It answers four questions:

1. What should I continue?
2. What needs a decision?
3. What resources are being used?
4. Are local tools and connections healthy?

This direction keeps the product operational and local-first while removing dependence on aircraft, cockpit, runway, mission, hangar, and logbook metaphors. Character comes from an editorial grid, ruled work surfaces, event tape, warm paper, dark ink, and tactile controls.

## 2. Information architecture

| Global mode | Purpose | Existing surfaces |
| --- | --- | --- |
| **Desk** | Current work, attention, recent activity | New neutral home |
| **Work** | Sessions, tasks, code changes, work traces | Logbook, Missions, Diff, Route Loom |
| **Review** | Usage, cost, trends, dependency evidence | Spend, Charts, Graph |
| **Connect** | MCP servers, integration flows, installed skills | Comms, Hub, Manuals |
| **System** | Runtime, event stream, interface contract | Hangar, Relay, Components |

Recommended vocabulary changes:

| Current | Proposed |
| --- | --- |
| Spend | Usage & cost |
| Logbook | Sessions |
| Route Loom | Work trace |
| Missions | Tasks & notes |
| Hangar | Runtime |
| Comms | MCP connections |
| Manuals | Skills |
| Relay | Event stream |
| Hub | Integration flows |
| Charts | Dependencies or trends |

## 3. Desktop anatomy

```text
┌ Desk | Work | Review | Connect | System ─ Search ─ Local index ┬ LIVE ACTIVITY ┐
├ Workspace / local status / last indexed                         ┤ follow control │
├──────────────┬──────────────────────────────────────────────────┤ event rows      │
│ CONTEXT      │ mode heading + search                            │                 │
│ Now          │                                                  │                 │
│ Attention    │ Continue where you stopped                       │                 │
│ Recent       │ selected session / project / changes / action    │                 │
│              ├──────────────────────┬───────────────────────────┤                 │
│ WORKSPACES   │ Needs attention      │ Usage now                 │                 │
│ project      ├──────────────────────┴───────────────────────────┤                 │
│ project      │ Recent work ledger                               │                 │
└──────────────┴──────────────────────────────────────────────────┴─────────────────┘
```

- The primary action is one broad “Continue where you stopped” plane.
- Attention and usage share a joined ruled surface rather than becoming floating summary cards.
- Recent work is a compact ledger with project, model, cost, and time.
- Live Activity is a structurally separate dark rail. Selecting an event highlights the corresponding point in the sequence.
- Global mode changes preserve the selected workspace.

## 4. Interaction states

- Search filters recent work and provides a composed empty state.
- The usage period changes metrics, progress, and explanatory copy together.
- Attention items can be resolved inline.
- The primary session action exposes opening and ready states with stable geometry.
- The local index control exposes a compact loading state.
- Live Activity can pause follow mode.
- On mobile, Live Activity becomes a bottom sheet opened by a persistent compact trigger.
- All motion stops under `prefers-reduced-motion: reduce`.

## 5. Visual contract

- Canvas: warm paper.
- Main text: dark brown ink, never pure black.
- One accent: coral for selected, live, primary action, and progress.
- Dark activity rail: warm brown-black with coral proof marks.
- Outfit for interface hierarchy; IBM Plex Mono for paths, values, times, models, and states.
- Joined rules and negative space do most of the grouping.
- Tactile actions retain separate face, frame, and offset-depth layers.
- No clouds, aircraft, route lines, instrument gauges, or aviation labels.

## 6. Responsive behavior

Below `900px`:

- The context rail becomes a horizontal section row.
- The main content becomes one column.
- Live Activity leaves the page grid and becomes a bottom sheet.
- A persistent trigger reports the event count.

Below `640px`:

- Global modes remain horizontally scrollable.
- Search becomes full width.
- Continue, attention, usage, and recent work stack in reading order.
- Secondary run metadata is reduced without hiding the title, project, cost, or update time.
- No horizontal page overflow is allowed.

## 7. Integration recommendation

The proposal is intentionally a separate Vite entry so it can be reviewed without touching the running dashboard: `/` mounts `App.jsx`, which is the real product.

For production integration:

1. Add a `HomeView` branch to `App.jsx` while preserving its existing data loaders, SSE subscription, range logic, session detail route, and specialist surfaces.
2. Feed the home from existing summary, sessions, quota, MCP/system, task, and diff endpoints.
3. Rename navigation in one migration after the neutral home is accepted; do not mix old and new vocabulary in the same shell.

The prototype uses representative local data and does not mutate backend state.
