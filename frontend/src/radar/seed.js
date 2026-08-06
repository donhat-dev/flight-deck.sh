/**
 * Radar seed data.
 *
 * Stands in for the API that does not exist yet, and is shaped like the API this
 * page needs rather than like something convenient to hand-write. Two properties
 * of that shape are decisions, not conveniences:
 *
 *   - a blip's ring is NOT stored on the blip in the real model; it is the ring of
 *     its newest move. It is duplicated here only because there is no store to
 *     derive it from, and `ringOf()` below is the single place that reads it, so
 *     swapping in a derived value is a one-line change.
 *   - a move without a `why` and at least one piece of evidence is not
 *     representable. That is the whole point of the radar: the position is
 *     worthless without the reason it holds, so the type refuses to carry one.
 *
 * Blip names are real decisions from this workspace. Ring placements, move counts
 * and dates are illustrative until a real radar is seeded from Treasures.
 */

/** Every radar in the install, newest activity first. */
export const RADARS = [
  {
    slug: "subscription-migration",
    title: "Subscription migration",
    subtitle: "Odoo 12 EE  →  Odoo 19 CE",
    jira: "CRM-11197",
    rings: { adopt: 9, trial: 6, assess: 12, caution: 7 },
    blipCount: 34,
    moveCount: 41,
    updated: "04 Aug",
    stale: 3,
    open: true,
  },
  {
    slug: "hr-split",
    title: "HR split",
    subtitle: "Odoo EE HR  →  Horilla",
    jira: "CRM-11385",
    rings: { adopt: 1, trial: 2, assess: 5, caution: 4 },
    blipCount: 12,
    moveCount: 14,
    updated: "28 Jul",
    stale: 2,
  },
  {
    slug: "helpdesk-replacement",
    title: "Helpdesk replacement",
    subtitle: "Odoo Helpdesk EE  →  OSS candidate",
    jira: "CRM-11372",
    rings: { adopt: 0, trial: 1, assess: 5, caution: 3 },
    blipCount: 9,
    moveCount: 11,
    updated: "21 Jul",
    stale: 1,
  },
  {
    slug: "promotion-engine",
    title: "Promotion engine",
    subtitle: "scattered Odoo logic  →  FastAPI service",
    jira: "CRM-11198",
    rings: { adopt: 2, trial: 1, assess: 3, caution: 1 },
    blipCount: 7,
    moveCount: 8,
    updated: "30 Jul",
    stale: 0,
  },
  {
    slug: "flightdeck-platform",
    title: "FlightDeck platform",
    subtitle: "internal tooling stack",
    rings: { adopt: 7, trial: 3, assess: 4, caution: 1 },
    blipCount: 15,
    moveCount: 22,
    updated: "04 Aug",
    stale: 0,
  },
];

/**
 * Blips of the open radar.
 *
 * `state` is the LAST move's direction, which is what the radar draws:
 * `in` and `out` get a facing arc, `new` a full ring, `held` nothing.
 */
export const BLIPS = [
  // Platforms
  { num: 5, name: "OCA subscription_oca", quadrant: "platforms", ring: "adopt", state: "in", evidenceAgeDays: 0, lastMove: "Q4 → Adopt", why: "39/39 rule pairs reconciled on real data" },
  { num: 17, name: "PostgreSQL 17", quadrant: "platforms", ring: "adopt", state: "held", evidenceAgeDays: 9, lastMove: "Q2 → Adopt", why: "one instance behind every service" },
  { num: 6, name: "Odoo 19 CE", quadrant: "platforms", ring: "trial", state: "in", evidenceAgeDays: 12, lastMove: "Q3 → Trial", why: "sandbox issued invoices end to end" },
  { num: 20, name: "OCA contract", quadrant: "platforms", ring: "trial", state: "held", evidenceAgeDays: 16, lastMove: "Q3 → Trial", why: "the base subscription_oca builds on" },
  { num: 19, name: "Zammad", quadrant: "platforms", ring: "assess", state: "new", evidenceAgeDays: 6, lastMove: "Q4 → Assess", why: "helpdesk candidate, not probed yet" },
  { num: 9, name: "django-helpdesk", quadrant: "platforms", ring: "assess", state: "held", evidenceAgeDays: 66, lastMove: "Q2 → Assess", why: "PoC only, no owner assigned" },
  { num: 22, name: "Metabase", quadrant: "platforms", ring: "assess", state: "held", evidenceAgeDays: 24, lastMove: "Q3 → Assess", why: "read-only reporting over the ledger" },
  { num: 21, name: "Keycloak", quadrant: "platforms", ring: "assess", state: "new", evidenceAgeDays: 4, lastMove: "Q4 → Assess", why: "one identity across the extracted services" },
  { num: 7, name: "Lago", quadrant: "platforms", ring: "caution", state: "out", evidenceAgeDays: 91, lastMove: "Q2 → Caution", why: "AGPL §13 reaches a customer portal" },
  { num: 18, name: "Odoo 12 EE", quadrant: "platforms", ring: "caution", state: "held", evidenceAgeDays: 31, lastMove: "Q1 → Caution", why: "the licence cost this migration exists to remove" },
  { num: 8, name: "Horilla HRMS", quadrant: "platforms", ring: "caution", state: "out", evidenceAgeDays: 74, lastMove: "Q3 → Caution", why: "model-fit gaps on FTO and payroll" },

  // Techniques
  { num: 2, name: "Event-sourced rollup", quadrant: "techniques", ring: "adopt", state: "in", evidenceAgeDays: 0, lastMove: "Q4 → Adopt", why: "129,775 rows collapse to 628 cells, exactly" },
  { num: 3, name: "Clean-room extraction", quadrant: "techniques", ring: "adopt", state: "held", evidenceAgeDays: 18, lastMove: "Q2 → Adopt", why: "the only route that keeps EE code out" },
  { num: 23, name: "Service extraction", quadrant: "techniques", ring: "trial", state: "held", evidenceAgeDays: 20, lastMove: "Q3 → Trial", why: "one EE-gated capability per service" },
  { num: 24, name: "Fragment export", quadrant: "techniques", ring: "trial", state: "new", evidenceAgeDays: 0, lastMove: "Q4 → Trial", why: "publishes without a hand-edit" },
  { num: 1, name: "PostgreSQL FDW", quadrant: "techniques", ring: "assess", state: "new", evidenceAgeDays: 3, lastMove: "Q4 → Assess", why: "cross-instance reads without an ETL hop" },
  { num: 25, name: "Composition lint", quadrant: "techniques", ring: "assess", state: "in", evidenceAgeDays: 1, lastMove: "Q4 → Assess", why: "the contract half a test can actually check" },
  { num: 4, name: "Agent clearance lock", quadrant: "techniques", ring: "caution", state: "out", evidenceAgeDays: 0, lastMove: "Q4 → Caution", why: "incursions self-heal in minutes; upkeep does not" },
  { num: 26, name: "Shared worktree agents", quadrant: "techniques", ring: "caution", state: "new", evidenceAgeDays: 0, lastMove: "Q4 → Caution", why: "one checkout, two agents, no separation" },

  // Tools
  { num: 10, name: "Treasures", quadrant: "tools", ring: "adopt", state: "in", evidenceAgeDays: 0, lastMove: "Q4 → Adopt", why: "every artifact keeps its source and versions" },
  { num: 11, name: "nakivo-graph", quadrant: "tools", ring: "adopt", state: "held", evidenceAgeDays: 9, lastMove: "Q2 → Adopt", why: "source of truth for blast radius" },
  { num: 12, name: "chrome-devtools MCP", quadrant: "tools", ring: "trial", state: "held", evidenceAgeDays: 2, lastMove: "Q3 → Trial", why: "drives the live browser for behavioural tests" },
  { num: 27, name: "Pencil", quadrant: "tools", ring: "trial", state: "new", evidenceAgeDays: 0, lastMove: "Q4 → Trial", why: "design files an agent can edit directly" },
  { num: 28, name: "Playwright CLI", quadrant: "tools", ring: "assess", state: "held", evidenceAgeDays: 11, lastMove: "Q3 → Assess", why: "headless runs that never touch the live browser" },
  { num: 29, name: "agent-browser", quadrant: "tools", ring: "assess", state: "out", evidenceAgeDays: 27, lastMove: "Q4 → Assess", why: "attaches to the live browser and drifts silently" },
  { num: 30, name: "BYOR radar", quadrant: "tools", ring: "caution", state: "new", evidenceAgeDays: 0, lastMove: "Q4 → Caution", why: "AGPL, snapshot-only, no move history" },

  // Languages & frameworks
  { num: 16, name: "FastAPI", quadrant: "lang", ring: "adopt", state: "held", evidenceAgeDays: 21, lastMove: "Q1 → Adopt", why: "every service in the workspace runs on it" },
  { num: 31, name: "React + Vite", quadrant: "lang", ring: "adopt", state: "held", evidenceAgeDays: 0, lastMove: "Q1 → Adopt", why: "the deck and every standalone page" },
  { num: 32, name: "Tailwind 3", quadrant: "lang", ring: "trial", state: "held", evidenceAgeDays: 0, lastMove: "Q2 → Trial", why: "utilities beside 87 tokens of our own" },
  { num: 13, name: "Base UI", quadrant: "lang", ring: "trial", state: "new", evidenceAgeDays: 0, lastMove: "Q4 → Trial", why: "behaviour only, ships no CSS" },
  { num: 15, name: "Mantine 8", quadrant: "lang", ring: "assess", state: "new", evidenceAgeDays: 0, lastMove: "Q4 → Assess", why: "CSS variables, no emotion runtime" },
  { num: 33, name: "Lit", quadrant: "lang", ring: "assess", state: "held", evidenceAgeDays: 0, lastMove: "Q4 → Assess", why: "custom elements need React 19 first" },
  { num: 14, name: "MUI", quadrant: "lang", ring: "caution", state: "out", evidenceAgeDays: 0, lastMove: "Q4 → Caution", why: "emotion runtime beside Tailwind and our tokens" },
  { num: 34, name: "HeroUI 3", quadrant: "lang", ring: "caution", state: "new", evidenceAgeDays: 0, lastMove: "Q4 → Caution", why: "wants React 19 and Tailwind 4" },
];

export const blipByNum = (num) => BLIPS.find((b) => b.num === num) || null;

/** The move history of one blip, newest first. */
export const MOVES = {
  5: [
    {
      quarter: "Q4 2026",
      ring: "adopt",
      why: "39/39 rule pairs reconciled against the real DB; the currency-conversion branch matched too.",
      evidence: ["23-approach-3a-coarse", "19-research-report"],
    },
    {
      quarter: "Q3 2026",
      ring: "trial",
      why: "PoC on the Odoo 19 CE sandbox issued invoices end to end without EE modules.",
      evidence: ["14-ee-vs-oca"],
    },
    {
      quarter: "Q2 2026",
      ring: "assess",
      why: "The OCA contract family surveyed — two directions, both measured against sale_subscription.",
      evidence: ["18-oca-contract-family"],
    },
    {
      quarter: "Q2 2026",
      ring: null,
      why: "Entered the radar as a candidate to replace EE sale_subscription.",
      evidence: [],
    },
  ],
};

/** Evidence attached to a blip, newest first. */
export const EVIDENCE = {
  5: [
    { kind: "treasure", title: "23. Approach 3a-coarse — register blocker", date: "04 Aug" },
    { kind: "treasure", title: "19. Replacing EE sale_subscription", date: "31 Jul" },
    { kind: "treasure", title: "18. The OCA contract family", date: "24 Jul" },
    { kind: "treasure", title: "14. EE sale_subscription vs subscription_oca", date: "16 Jul" },
    { kind: "trace", title: "odoo19-oca invoice smoke run", date: "03 Aug" },
    { kind: "jira", title: "CRM-11197", date: "12 Jul" },
  ],
};

export const SUMMARY = {
  5: "OCA subscription_oca carries recurring billing on community Odoo, so the EE "
    + "sale_subscription dependency that 68 modules inherit transitively can be cut. It reads "
    + "the same contract shape the NAKIVO chain already assumes, which is why the 39/39 pricing "
    + "reconciliation held against real data rather than a fixture.",
};

/** Quarters on the history scrubber, and how many moves each one holds. */
export const TIMELINE = [
  { key: "Q1 2026", moves: 3 },
  { key: "Q2 2026", moves: 11 },
  { key: "Q3 2026", moves: 14 },
  { key: "Q4 2026", moves: 13, current: true },
];

export const GRANULARITY = ["day", "week", "month", "quarter"];
