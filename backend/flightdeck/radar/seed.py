"""Seed the radar store with this workspace's real decisions.

Idempotent: a radar that already has blips is left alone, so this can run on every
start without duplicating a board.

Evidence carries a DATE, not an age. Ages are derived on read, which means the stale
count climbs on its own as evidence goes unrefreshed — that is the behaviour the
staleness flag exists for, and a stored age would freeze at whatever it was when the
row was written.
"""
from datetime import date, timedelta

from flightdeck.radar import store

# The dates below are expressed as "days before this reference", so the seed reads as
# a history rather than as a list of absolute strings nobody can sanity-check.
_REF = date(2026, 8, 4)


def _d(days_ago: int) -> str:
    return (_REF - timedelta(days=days_ago)).isoformat()


def _ev(kind, title, days_ago, ref=None):
    return {"kind": kind, "title": title, "ref": ref, "dated": _d(days_ago)}


# (num, name, quadrant, ring, entryPeriod, movePeriod, why, evidence)
_SUBSCRIPTION = [
    (17, "PostgreSQL 17", "platforms", "adopt", "Q1 2026", "Q2 2026",
     "One instance behind every service, so a second engine is a second thing to operate.",
     [_ev("trace", "pgcore-17 healthy across four consumers", 9)]),
    (6, "Odoo 19 CE", "platforms", "trial", "Q2 2026", "Q3 2026",
     "The sandbox issued invoices end to end without a single EE module loaded.",
     [_ev("treasure", "18. The OCA contract family", 12)]),
    (20, "OCA contract", "platforms", "trial", "Q2 2026", "Q3 2026",
     "The base subscription_oca builds on, so it is adopted or rejected with it.",
     [_ev("treasure", "14. EE sale_subscription vs subscription_oca", 16)]),
    (19, "Zammad", "platforms", "assess", "Q4 2026", "Q4 2026",
     "Helpdesk candidate for the pre-sale team; nothing probed yet.",
     [_ev("jira", "CRM-11372", 6)]),
    (9, "django-helpdesk", "platforms", "assess", "Q1 2026", "Q2 2026",
     "A PoC exists and no owner does, which is why it has not moved since.",
     [_ev("trace", "django-helpdesk PoC screenshots", 66)]),
    (22, "Metabase", "platforms", "assess", "Q3 2026", "Q3 2026",
     "Read-only reporting over the ledger, so it never becomes a write path.",
     [_ev("note", "read-only role verified on pgcore-17", 24)]),
    (21, "Keycloak", "platforms", "assess", "Q4 2026", "Q4 2026",
     "One identity across the extracted services, instead of three login pages.",
     [_ev("note", "identity requirement raised by the portal design", 4)]),
    (7, "Lago", "platforms", "caution", "Q1 2026", "Q2 2026",
     "AGPL section 13 reaches a customer-facing portal, and the fork cost is not recovered.",
     [_ev("treasure", "06. Lago fork and customisation playbook", 91)]),
    (18, "Odoo 12 EE", "platforms", "caution", "Q1 2026", "Q1 2026",
     "The licence cost this whole migration exists to remove.",
     [_ev("note", "EE modules in the nakivo_sale chain", 31)]),
    (8, "Horilla HRMS", "platforms", "caution", "Q2 2026", "Q3 2026",
     "Model-fit gaps on FTO and payroll that the probe could not close.",
     [_ev("treasure", "Horilla HR — model-fit probe findings", 74)]),

    (2, "Event-sourced rollup", "techniques", "adopt", "Q3 2026", "Q4 2026",
     "129,775 rows collapse to 628 cells with the totals matching to 4.5e-16.",
     [_ev("trace", "metrics rollup equivalence check on the live ledger", 0)]),
    (3, "Clean-room extraction", "techniques", "adopt", "Q1 2026", "Q2 2026",
     "The only route that keeps EE source out of the replacement.",
     [_ev("treasure", "00. Clean-room policy", 18)]),
    (23, "Service extraction", "techniques", "trial", "Q2 2026", "Q3 2026",
     "One EE-gated capability per service, so each can be licensed on its own.",
     [_ev("treasure", "EXECUTIVE-SUMMARY", 20)]),
    (24, "Fragment export", "techniques", "trial", "Q4 2026", "Q4 2026",
     "Publishes to an Artifact with no hand-edit, which is what made it worth keeping.",
     [_ev("treasure", "23. Approach 3a-coarse — register blocker", 0)]),
    (1, "PostgreSQL FDW", "techniques", "assess", "Q4 2026", "Q4 2026",
     "Cross-instance reads without an ETL hop, if the planner behaves under joins.",
     [_ev("note", "FDW raised while scoping the Odoo 12 to 19 read path", 3)]),
    (25, "Composition lint", "techniques", "assess", "Q3 2026", "Q4 2026",
     "The half of the composition contract a test can actually check.",
     [_ev("trace", "compositionLint covers five radar sheets by glob", 0)]),
    (4, "Agent clearance lock", "techniques", "caution", "Q4 2026", "Q4 2026",
     "Incursions self-heal in minutes; the upkeep of a clearance service does not.",
     [_ev("note", "dcg allows compose down -v — a static rule covers the one real risk", 0)]),
    (26, "Shared worktree agents", "techniques", "caution", "Q4 2026", "Q4 2026",
     "One checkout, two agents, no separation — a git checkout changes the other's running code.",
     [_ev("note", "nakivo/ is checked out on the shared odoo12CE_legal branch", 0)]),

    (10, "Treasures", "tools", "adopt", "Q2 2026", "Q4 2026",
     "Every artifact keeps its source, its versions and the origin it was wrapped from.",
     [_ev("trace", "fragment export published doc 23 unedited", 0)]),
    (11, "nakivo-graph", "tools", "adopt", "Q1 2026", "Q2 2026",
     "The source of truth for blast radius: 37 modules on nakivo_sale directly, 68 transitively.",
     [_ev("trace", "nakivo-graph.json regenerated from the manifests", 9)]),
    (12, "chrome-devtools MCP", "tools", "trial", "Q2 2026", "Q3 2026",
     "Drives the live browser, which is the only way a behavioural test is real.",
     [_ev("trace", "devtools_mcp_trace/ evidence for the radar page", 2)]),
    (27, "Pencil", "tools", "trial", "Q4 2026", "Q4 2026",
     "Design files an agent can edit directly, so the mock and the code stay one artefact.",
     [_ev("trace", "radar.pen — six screens driven from execute()", 0)]),
    (28, "Playwright CLI", "tools", "assess", "Q3 2026", "Q3 2026",
     "Headless runs that never touch the operator's own browser session.",
     [_ev("note", "used for standalone HTML artifact checks", 11)]),
    (29, "agent-browser", "tools", "assess", "Q3 2026", "Q4 2026",
     "Attaches to the live browser and drifts silently, which cost a wrong conclusion once.",
     [_ev("note", "accepts CSS selectors but loses the tab without saying so", 27)]),
    (30, "BYOR radar", "tools", "caution", "Q4 2026", "Q4 2026",
     "AGPL, snapshot-only, and no move history — which is the whole feature.",
     [_ev("note", "build-your-own-radar is AGPL-3.0; Zalando's renderer is MIT", 0)]),

    (16, "FastAPI", "lang", "adopt", "Q1 2026", "Q1 2026",
     "Every service in the workspace runs on it, so it is already the default.",
     [_ev("note", "FlightDeck, discount service, hub", 21)]),
    (31, "React + Vite", "lang", "adopt", "Q1 2026", "Q1 2026",
     "The deck and every standalone page, including this one.",
     [_ev("trace", "six Vite entries build clean", 0)]),
    (32, "Tailwind 3", "lang", "trial", "Q2 2026", "Q2 2026",
     "Utilities beside 87 tokens of our own; the pairing works but is not settled.",
     [_ev("note", "tailwind.config reads --fdx-font-* so utilities follow tokens", 0)]),
    (13, "Base UI", "lang", "trial", "Q4 2026", "Q4 2026",
     "Behaviour only and ships no CSS, so the 87 tokens survive adoption.",
     [_ev("note", "@base-ui/react 1.6.0, no styling peer dependency", 0)]),
    (15, "Mantine 8", "lang", "assess", "Q4 2026", "Q4 2026",
     "CSS variables and no emotion runtime, but one major behind on React support.",
     [_ev("note", "@mantine/core 8.3.18 accepts React 18", 0)]),
    (33, "Lit", "lang", "assess", "Q4 2026", "Q4 2026",
     "Custom elements need React 19 first, and shadow DOM discards the Tailwind layer.",
     [_ev("note", "React 19 required for clean custom-element interop", 0)]),
    (14, "MUI", "lang", "caution", "Q4 2026", "Q4 2026",
     "An emotion runtime beside Tailwind, and a second source of truth for typography.",
     [_ev("note", "@mui/material 9.2.0 still peers on @emotion/react", 0)]),
    (34, "HeroUI 3", "lang", "caution", "Q4 2026", "Q4 2026",
     "Wants React 19 and Tailwind 4 — two migrations before the first component.",
     [_ev("note", "@heroui/react 3.2.3 peers react >=19, tailwindcss >=4", 0)]),
]

# Blip 5 carries a full history rather than the two-move default, because it is the
# decision the whole radar exists to record and the panel needs something to show.
_OCA_HISTORY = [
    (None, "Q2 2026", "Entered the radar as a candidate to replace EE sale_subscription.", []),
    ("assess", "Q2 2026",
     "The OCA contract family surveyed — two directions, both measured against sale_subscription.",
     [_ev("treasure", "18. The OCA contract family", 24)]),
    ("trial", "Q3 2026",
     "PoC on the Odoo 19 CE sandbox issued invoices end to end without EE modules.",
     [_ev("treasure", "14. EE sale_subscription vs subscription_oca", 19)]),
    ("adopt", "Q4 2026",
     "39/39 rule pairs reconciled against the real DB; the currency-conversion branch matched too.",
     [_ev("treasure", "23. Approach 3a-coarse — register blocker", 0),
      _ev("treasure", "19. Replacing EE sale_subscription", 4),
      _ev("trace", "odoo19-oca invoice smoke run", 1),
      _ev("jira", "CRM-11197", 23)]),
]

_RADARS = [
    ("subscription-migration", "Subscription migration", "Odoo 12 EE  →  Odoo 19 CE", "CRM-11197"),
    ("hr-split", "HR split", "Odoo EE HR  →  Horilla", "CRM-11385"),
    ("helpdesk-replacement", "Helpdesk replacement", "Odoo Helpdesk EE  →  OSS candidate", "CRM-11372"),
    ("promotion-engine", "Promotion engine", "scattered Odoo logic  →  FastAPI service", "CRM-11198"),
    ("flightdeck-platform", "FlightDeck platform", "internal tooling stack", None),
]


def seed(conn) -> dict:
    """Write the boards if they are not there. Returns what it did."""
    made = {"radars": 0, "blips": 0, "moves": 0}
    for slug, title, subtitle, jira in _RADARS:
        if store.get_radar(conn, slug) is None:
            store.upsert_radar(conn, slug=slug, title=title, subtitle=subtitle, jira=jira)
            made["radars"] += 1

    slug = "subscription-migration"
    if store.blips_of(conn, slug):
        return made   # already seeded; adding again would double every blip

    # The entry move is stamped a day before the positioning move so `ORDER BY ts`
    # puts them in the order they happened. Same-second timestamps would leave the
    # newest move ambiguous and the derived ring a coin toss.
    for num, name, quadrant, ring, entry_period, period, why, evidence in _SUBSCRIPTION:
        blip = store.add_blip(conn, radar=slug, num=num, name=name, quadrant=quadrant)
        made["blips"] += 1
        store.add_move(conn, blip=blip["id"], ring=None, period=entry_period,
                       why=f"Entered the radar: {name}.", evidence=[],
                       ts=f"{_d(120)}T09:00:00+00:00")
        store.add_move(conn, blip=blip["id"], ring=ring, period=period, why=why,
                       evidence=evidence, ts=f"{evidence[0]['dated']}T12:00:00+00:00")
        made["moves"] += 2

    oca = store.add_blip(conn, radar=slug, num=5, name="OCA subscription_oca",
                         quadrant="platforms")
    made["blips"] += 1
    for i, (ring, period, why, evidence) in enumerate(_OCA_HISTORY):
        stamp = evidence[0]["dated"] if evidence else _d(150)
        store.add_move(conn, blip=oca["id"], ring=ring, period=period, why=why,
                       evidence=evidence, ts=f"{stamp}T{9 + i:02d}:00:00+00:00")
        made["moves"] += 1
    return made


# Blips that genuinely travelled, with the ring they held before their current one.
# (num, prior ring, period, why, days-ago-of-its-evidence)
_PRIOR = [
    (7, "assess", "Q1 2026", "Lago read as the strongest billing candidate before the licence was read closely.", 150),
    (8, "assess", "Q2 2026", "Horilla looked like a drop-in until the model-fit probe ran.", 130),
    (14, "trial", "Q3 2026", "MUI was the default choice until the emotion runtime was weighed against the token layer.", 100),
    (29, "trial", "Q3 2026", "agent-browser was in use before the silent tab drift cost a wrong conclusion.", 95),
    (6, "assess", "Q2 2026", "Odoo 19 CE was a candidate before the sandbox proved it could invoice.", 140),
    (10, "trial", "Q3 2026", "Treasures held versions but could not publish without a hand-edit.", 90),
    (2, "trial", "Q3 2026", "The rollup was a proposal until the equivalence check ran on the live ledger.", 85),
    (25, "trial", "Q3 2026", "The lint checked one sheet before the contract was written down.", 80),
    (34, "assess", "Q3 2026", "HeroUI looked adoptable until its React and Tailwind floors were read.", 75),
    (30, "assess", "Q3 2026", "BYOR was the obvious build-vs-adopt answer before the licence and the missing history.", 70),
]


def enrich_history(conn) -> dict:
    """Add the move a blip made BEFORE its current one, where it really made one.

    Additive and idempotent: a blip that already has three or more moves is left
    alone. Nothing is deleted — the board is real data now, and rewriting history to
    make a drawing look busier is the one thing this feature exists to prevent.

    Without this every blip reads as "just entered", because the initial seed gave
    each one an entry and a single placement. The arrows on the drawing were correct
    and uniformly uninformative.
    """
    slug = "subscription-migration"
    added = 0
    for num, ring, period, why, days in _PRIOR:
        blip = store.blip_by_num(conn, slug, num)
        if blip is None:
            continue
        if len(store.moves_of(conn, blip["id"])) >= 3:
            continue
        store.add_move(conn, blip=blip["id"], ring=ring, period=period, why=why,
                       evidence=[_ev("note", f"prior position of blip {num}", days)],
                       ts=f"{_d(days)}T10:00:00+00:00")
        added += 1
    return {"moves": added}
