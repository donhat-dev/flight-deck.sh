"""Tickets store: schema, seed, the phase state-machine and the plan tree.

Phases are FlightDeck's own, not Jira's workflow: Jira status is mirrored onto
the ticket as a label and the two are allowed to disagree. A phase move is
refused when its precondition is unmet, so the board cannot claim a state the
ticket has no evidence for.

Sqlite idiom (`?` placeholders) so db.py's PostgreSQL adapter can translate.
Reads take a short-lived connection; writes go through the shared write
connection under the runtime lock (see routers/tickets.py).
"""
import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from flightdeck import db

LANES = ("SPEC_REVIEW", "IMPLEMENTATION")

# Ordered; the board renders columns in this order and the form's statusbar
# walks them. Deliberately the Jira four rather than a longer dev-side chain:
# what is actually happening inside In progress is carried by the baton.
PHASES = [
    ("OPEN", "Open"),
    ("IN_PROGRESS", "In progress"),
    ("RESOLVED", "Resolved"),
    ("CLOSED", "Closed"),
]
PHASE_KEYS = [p[0] for p in PHASES]

# Rows written before the stages were shortened.
_LEGACY_PHASE = {
    "INTAKE": "OPEN", "READING_SPEC": "OPEN",
    "QUESTIONS_OUT": "IN_PROGRESS", "ESTIMATING": "IN_PROGRESS",
    "ESTIMATE_POSTED": "IN_PROGRESS", "BREAKDOWN": "IN_PROGRESS",
    "PLANNED": "IN_PROGRESS", "SELF_VERIFIED": "IN_PROGRESS", "MR_OPEN": "IN_PROGRESS",
    "MERGED": "RESOLVED", "CLOSED_TO_PM": "CLOSED",
}
# What the dropped phases actually meant. A stage says how far the work is; the
# reason says what is holding it, and the two are independent.
_LEGACY_BLOCK = {
    "QUESTIONS_OUT": "Waiting on the spec owner to answer the inline comments",
    "ESTIMATE_POSTED": "Waiting on the dev lead to approve the estimate",
}

# Offered in the form as a starting point; the field takes any text.
BLOCK_REASONS = [
    "Waiting on the spec owner to answer the inline comments",
    "Waiting on the dev lead to approve the estimate",
    "Waiting on QA to finish the test round",
    "Blocked by another ticket",
    "Waiting on an environment or access",
]

# Who the ticket is waiting on. The board's primary filter.
BATONS = ("MINE", "PM", "LEAD", "QA", "AGENT")

STEP_STATUSES = ("planned", "doing", "done", "dropped")
# Child steps only: what kind of unplanned work this is.
CHILD_KINDS = ("TODO", "FIX", "BUG", "AMEND", "RERUN")

# Resolved is the one stage with a precondition, and what proves it differs by
# lane: implementation work needs a merge request, a spec review needs the
# estimate it exists to produce.
_RESOLVE_GATE = {
    "IMPLEMENTATION": ("mr_url", "no merge request is linked"),
    "SPEC_REVIEW": ("estimate", "the estimate comment is empty"),
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return "s_" + uuid.uuid4().hex[:8]


def _days_since(iso: Optional[str]) -> int:
    if not iso:
        return 0
    try:
        t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    return max(0, int((datetime.now(timezone.utc) - t).total_seconds() // 86400))


def init(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
          key TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          lane TEXT NOT NULL DEFAULT 'IMPLEMENTATION',
          phase TEXT NOT NULL DEFAULT 'OPEN',
          baton TEXT NOT NULL DEFAULT 'MINE',
          jira_status TEXT NOT NULL DEFAULT 'Open',
          track TEXT NOT NULL DEFAULT '',
          sprint TEXT NOT NULL DEFAULT '',
          priority TEXT NOT NULL DEFAULT 'Normal',
          assignee TEXT NOT NULL DEFAULT '',
          estimate TEXT NOT NULL DEFAULT '',
          parent_key TEXT,
          frd_url TEXT NOT NULL DEFAULT '',
          spec_url TEXT NOT NULL DEFAULT '',
          worktree TEXT NOT NULL DEFAULT '',
          branch TEXT NOT NULL DEFAULT '',
          mr_url TEXT NOT NULL DEFAULT '',
          blocked_reason TEXT NOT NULL DEFAULT '',
          blocked_since TEXT NOT NULL DEFAULT '',
          tags TEXT NOT NULL DEFAULT '[]',
          phase_since TEXT NOT NULL,
          synced_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticket_steps (
          id TEXT PRIMARY KEY,
          ticket_key TEXT NOT NULL,
          parent_id TEXT,
          code TEXT NOT NULL DEFAULT '',
          title TEXT NOT NULL,
          body TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'planned',
          kind TEXT NOT NULL DEFAULT '',
          estimate_h REAL NOT NULL DEFAULT 0,
          origin TEXT NOT NULL DEFAULT '',
          position INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )""")
    _migrate_phases(conn)
    conn.commit()
    _seed_if_empty(conn)


def _migrate_phases(conn) -> None:
    """Fold the old dev-phase chain into the four stages, and carry what the
    dropped phases meant into a blocked reason rather than losing it."""
    for col, default in (("blocked_reason", "''"), ("blocked_since", "''"), ("tags", "'[]'")):
        if db.is_postgres():
            conn.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS %s text NOT NULL DEFAULT %s"
                         % (col, default))
        else:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(tickets)").fetchall()}
            if col not in cols:
                conn.execute("ALTER TABLE tickets ADD COLUMN %s TEXT NOT NULL DEFAULT %s"
                             % (col, default))
    now = _now()
    for old, reason in _LEGACY_BLOCK.items():
        conn.execute("UPDATE tickets SET blocked_reason=?, blocked_since=? "
                     "WHERE phase=? AND blocked_reason=''", (reason, now, old))
    for old, new in _LEGACY_PHASE.items():
        conn.execute("UPDATE tickets SET phase=? WHERE phase=?", (new, old))


def phase_catalog() -> List[dict]:
    return [{"key": k, "label": lb} for k, lb in PHASES]


def _ticket_row(r) -> dict:
    return {
        "key": r["key"], "title": r["title"], "lane": r["lane"], "phase": r["phase"],
        "baton": r["baton"], "jira_status": r["jira_status"], "track": r["track"],
        "sprint": r["sprint"], "priority": r["priority"], "assignee": r["assignee"],
        "estimate": r["estimate"], "parent_key": r["parent_key"],
        "frd_url": r["frd_url"], "spec_url": r["spec_url"], "worktree": r["worktree"],
        "branch": r["branch"], "mr_url": r["mr_url"],
        "blocked_reason": r["blocked_reason"], "blocked_since": r["blocked_since"],
        "blocked_days": _days_since(r["blocked_since"]) if r["blocked_reason"] else 0,
        "tags": json.loads(r["tags"] or "[]"),
        "phase_since": r["phase_since"], "days_in_phase": _days_since(r["phase_since"]),
        "synced_at": r["synced_at"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def _step_row(r) -> dict:
    return {
        "id": r["id"], "ticket_key": r["ticket_key"], "parent_id": r["parent_id"],
        "code": r["code"], "title": r["title"], "body": r["body"],
        "status": r["status"], "kind": r["kind"],
        "estimate_h": float(r["estimate_h"] or 0), "origin": r["origin"],
        "position": r["position"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def list_tickets(conn) -> dict:
    rows = conn.execute("SELECT * FROM tickets ORDER BY key").fetchall()
    tickets = [_ticket_row(r) for r in rows]
    counts = {}
    for t in tickets:
        counts[t["phase"]] = counts.get(t["phase"], 0) + 1
    stalled = sum(1 for t in tickets if t["days_in_phase"] >= 5)
    return {
        "tickets": tickets,
        "phases": phase_catalog(),
        "block_reasons": BLOCK_REASONS,
        "tags": sorted({tag for t in tickets for tag in t["tags"]}),
        "counts": counts,
        "totals": {
            "all": len(tickets),
            "mine": sum(1 for t in tickets if t["baton"] == "MINE"),
            "waiting": sum(1 for t in tickets if t["baton"] in ("PM", "LEAD", "QA")),
            "blocked": sum(1 for t in tickets if t["blocked_reason"]),
            "stalled": stalled,
        },
    }


def plan_tree(conn, key: str) -> List[dict]:
    rows = conn.execute(
        "SELECT * FROM ticket_steps WHERE ticket_key=? ORDER BY position, created_at", (key,)
    ).fetchall()
    steps = [_step_row(r) for r in rows]
    by_parent = {}
    for s in steps:
        by_parent.setdefault(s["parent_id"], []).append(s)
    out = []
    for m in by_parent.get(None, []):
        m = dict(m)
        m["children"] = by_parent.get(m["id"], [])
        out.append(m)
    return out


def plan_rollup(tree: List[dict]) -> dict:
    planned = sum(m["estimate_h"] for m in tree)
    emergent = sum(c["estimate_h"] for m in tree for c in m["children"])
    return {
        "planned_h": round(planned, 2),
        "emergent_h": round(emergent, 2),
        "milestones": len(tree),
        "children": sum(len(m["children"]) for m in tree),
    }


def get_ticket(conn, key: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM tickets WHERE key=?", (key,)).fetchone()
    if not r:
        return None
    t = _ticket_row(r)
    tree = plan_tree(conn, key)
    t["plan"] = tree
    t["rollup"] = plan_rollup(tree)
    t["phases"] = phase_catalog()
    t["block_reasons"] = BLOCK_REASONS
    return t


def create_ticket(conn, key: str, title: str, **fields) -> dict:
    now = _now()
    cols = {"key": key, "title": title, "lane": "IMPLEMENTATION", "phase": "OPEN",
            "baton": "MINE", "jira_status": "Open", "track": "", "sprint": "",
            "priority": "Normal", "assignee": "", "estimate": "", "parent_key": None,
            "frd_url": "", "spec_url": "", "worktree": "", "branch": "", "mr_url": "",
            "blocked_reason": "", "blocked_since": "", "tags": "[]",
            "phase_since": now, "synced_at": now, "created_at": now, "updated_at": now}
    cols.update({k: v for k, v in fields.items() if k in cols and v is not None})
    names = list(cols)
    conn.execute(
        "INSERT INTO tickets (%s) VALUES (%s)" % (", ".join(names), ", ".join(["?"] * len(names))),
        tuple(cols[n] for n in names))
    conn.commit()
    return get_ticket(conn, key)


_PATCHABLE = ("title", "lane", "baton", "jira_status", "track", "sprint", "priority",
              "assignee", "estimate", "parent_key", "frd_url", "spec_url", "worktree",
              "branch", "mr_url", "blocked_reason", "tags")


def update_ticket(conn, key: str, **fields) -> Optional[dict]:
    if "tags" in fields and isinstance(fields["tags"], (list, tuple)):
        fields["tags"] = json.dumps(list(fields["tags"]))
    if "blocked_reason" in fields:
        was = conn.execute("SELECT blocked_reason FROM tickets WHERE key=?", (key,)).fetchone()
        now_blocked = (fields["blocked_reason"] or "").strip()
        if not was or (was["blocked_reason"] or "") != now_blocked:
            fields["blocked_since"] = _now() if now_blocked else ""
    sets, vals = [], []
    for k in _PATCHABLE + ("blocked_since",):
        if k in fields and fields[k] is not None:
            sets.append(k + "=?")
            vals.append(fields[k])
    if not sets:
        return get_ticket(conn, key)
    sets.append("updated_at=?")
    vals.extend([_now(), key])
    conn.execute("UPDATE tickets SET " + ", ".join(sets) + " WHERE key=?", tuple(vals))
    conn.commit()
    return get_ticket(conn, key)


def gate_reason(ticket: dict, phase: str) -> Optional[str]:
    """Why `phase` is refused for this ticket, or None when the move is allowed."""
    if phase not in ("RESOLVED", "CLOSED"):
        return None
    gate = _RESOLVE_GATE.get(ticket.get("lane") or "IMPLEMENTATION")
    if not gate:
        return None
    field, reason = gate
    return None if (ticket.get(field) or "").strip() else reason


def move_phase(conn, key: str, phase: str) -> Tuple[Optional[dict], Optional[str]]:
    if phase not in PHASE_KEYS:
        return None, "unknown phase"
    t = get_ticket(conn, key)
    if not t:
        return None, None
    reason = gate_reason(t, phase)
    if reason:
        return t, reason
    now = _now()
    conn.execute("UPDATE tickets SET phase=?, phase_since=?, updated_at=? WHERE key=?",
                 (phase, now, now, key))
    conn.commit()
    return get_ticket(conn, key), None


def create_step(conn, ticket_key: str, title: str, parent_id: Optional[str] = None,
                code: str = "", body: str = "", status: str = "planned",
                kind: str = "", estimate_h: float = 0, origin: str = "") -> dict:
    sid = _new_id()
    now = _now()
    row = conn.execute(
        "SELECT MAX(position) AS p FROM ticket_steps WHERE ticket_key=? AND "
        + ("parent_id IS NULL" if parent_id is None else "parent_id=?"),
        (ticket_key,) if parent_id is None else (ticket_key, parent_id)).fetchone()
    pos = (row["p"] or 0) + 1 if row else 1
    conn.execute(
        "INSERT INTO ticket_steps (id, ticket_key, parent_id, code, title, body, status, "
        "kind, estimate_h, origin, position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sid, ticket_key, parent_id, code, title, body,
         status if status in STEP_STATUSES else "planned",
         kind if kind in CHILD_KINDS else "", float(estimate_h), origin, pos, now, now))
    conn.commit()
    return get_step(conn, sid)


def get_step(conn, sid: str) -> Optional[dict]:
    r = conn.execute("SELECT * FROM ticket_steps WHERE id=?", (sid,)).fetchone()
    return _step_row(r) if r else None


_STEP_PATCHABLE = ("title", "body", "status", "kind", "estimate_h", "origin", "code", "position")


def update_step(conn, sid: str, **fields) -> Optional[dict]:
    sets, vals = [], []
    for k in _STEP_PATCHABLE:
        if k in fields and fields[k] is not None:
            v = fields[k]
            if k == "status" and v not in STEP_STATUSES:
                continue
            if k == "estimate_h":
                v = float(v)
            sets.append(k + "=?")
            vals.append(v)
    if not sets:
        return get_step(conn, sid)
    sets.append("updated_at=?")
    vals.extend([_now(), sid])
    conn.execute("UPDATE ticket_steps SET " + ", ".join(sets) + " WHERE id=?", tuple(vals))
    conn.commit()
    return get_step(conn, sid)


def delete_step(conn, sid: str) -> bool:
    r = conn.execute("SELECT id FROM ticket_steps WHERE id=?", (sid,)).fetchone()
    if not r:
        return False
    conn.execute("DELETE FROM ticket_steps WHERE parent_id=?", (sid,))
    conn.execute("DELETE FROM ticket_steps WHERE id=?", (sid,))
    conn.commit()
    return True


def _ago(days: float) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


_SEED_TICKETS = [
    # key, title, lane, phase, baton, jira status, track, days, blocked reason, tags
    ("CRM-12246", "eShop coupon stacking rules", "SPEC_REVIEW", "OPEN", "MINE", "Open", "eShop", 1, "", ["spec-review"]),
    ("CRM-12251", "Partner portal CSV export", "SPEC_REVIEW", "OPEN", "MINE", "Open", "Portal", 2, "", ["spec-review"]),
    ("CRM-12211", "HR leave allocation import", "SPEC_REVIEW", "OPEN", "PM", "Open", "HR", 8,
     "Waiting on the spec owner to answer the inline comments", ["spec-review", "hr"]),
    ("CRM-12190", "Website visitor tracking", "SPEC_REVIEW", "IN_PROGRESS", "MINE", "In Progress", "CRM Event", 2, "", ["spec-review"]),
    ("CRM-11372", "Pre-sale helpdesk scope decision", "SPEC_REVIEW", "IN_PROGRESS", "PM", "In Progress", "Pre-sale", 5,
     "Waiting on the spec owner to answer the inline comments", ["spec-review", "pre-sale"]),
    ("CRM-12203", "Subscription proration rules", "SPEC_REVIEW", "IN_PROGRESS", "PM", "In Progress", "Subscription", 3,
     "Waiting on the spec owner to answer the inline comments", ["spec-review", "billing"]),
    ("CRM-11007", "Discount service — build API and UI", "SPEC_REVIEW", "IN_PROGRESS", "MINE", "In Progress", "Discount", 1, "", ["estimate"]),
    ("CRM-11197", "Promotion engine rule model", "SPEC_REVIEW", "IN_PROGRESS", "LEAD", "In Progress", "Promotion", 2,
     "Waiting on the dev lead to approve the estimate", ["estimate"]),
    ("CRM-11557", "Quote PDF rework", "SPEC_REVIEW", "IN_PROGRESS", "LEAD", "In Progress", "Sale", 4,
     "Waiting on the dev lead to approve the estimate", ["estimate"]),
    ("CRM-9573", "Pre-sale helpdesk — parent feature", "SPEC_REVIEW", "IN_PROGRESS", "MINE", "In Progress", "Pre-sale", 1, "", ["parent-feature", "pre-sale"]),
    ("CRM-11475", "Helpdesk migration — build API", "IMPLEMENTATION", "IN_PROGRESS", "MINE", "In Progress", "Pre-sale", 3, "", ["build-api", "pre-sale"]),
    ("CRM-11385", "HR split out of CRM", "IMPLEMENTATION", "IN_PROGRESS", "AGENT", "In Progress", "HR", 2,
     "Blocked by another ticket", ["build-api", "hr"]),
    ("CRM-11198", "Promotion engine — build UI", "IMPLEMENTATION", "RESOLVED", "QA", "Resolved", "Promotion", 1,
     "Waiting on QA to finish the test round", ["build-ui"]),
    ("CRM-11009", "Rate-limit the eShop coupon endpoint", "IMPLEMENTATION", "CLOSED", "MINE", "Closed", "eShop", 12, "", ["build-api", "eshop"]),
]

_M3_BODY = """# Show the 19-side ticket inside the CRM record, with no second login

> A sales user opens a CRM contact and reads the pre-sale ticket in place. The 19
> instance authenticates through the handshake, never through the user's own
> credentials.

## Build

- [x] `views/partner_form.xml` — embed the ticket panel on res.partner, after the messaging tab.
- [x] `models/embed_token.py` — a short-lived token minted per view; never the raw uid.
- [ ] `static/src/js/embed_frame.js` — the frame posts its height back, so the form never scrolls twice.
- [ ] `tests/test_embed_auth.py` — a sales user must not be able to read another team's ticket.

## Model definition

| Field | Type | Required | Note |
| --- | --- | --- | --- |
| `embed_token` | Char | yes | short-lived, one per view, never stored on the ticket |
| `ticket_ref` | Char | yes | the 19-side id; a Many2one would need a live cross-database link |
| `last_seen` | Datetime | — | written by the frame callback, drives the unread marker |
"""

_SEED_PLAN = [
    ("M1", "Scope + technical plan", "done", 4, "", [
        ("FIX", "Correct the §4 owner rule in the plan", "done", 0.5, "raised in review by Khang"),
    ]),
    ("M2", "remote_odoo_link handshake", "done", 6, "", []),
    ("M3", "Ticket embed view", "doing", 7, _M3_BODY, [
        ("BUG", "Attachment thumbnails 404 inside the portal frame", "doing", 1, "raised by session 90FB37EE"),
        ("AMEND", "Amend a91f2c4 — the view file was left out of the commit", "planned", 0.5, "raised in review by Khang"),
        ("RERUN", "Re-run the embed smoke test on the 19 side", "planned", 1, "raised by you"),
    ]),
    ("M4", "Queue job mirror", "planned", 5, "", []),
    ("M5", "REST guard", "planned", 3, "", []),
    ("M6", "Deep link back to CRM", "planned", 4, "", [
        ("TODO", "Confirm the base URL with ops", "planned", 0.5, "raised by you"),
    ]),
    ("M7", "Integration + QA", "planned", 3, "", []),
]


def _seed_if_empty(conn) -> None:
    if conn.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"]:
        return
    for key, title, lane, phase, baton, jira, track, days, blocked, tags in _SEED_TICKETS:
        create_ticket(conn, key, title, lane=lane, phase=phase, baton=baton,
                      jira_status=jira, track=track, sprint="26.09",
                      assignee="Nathan Do", priority="High",
                      estimate="" if phase == "OPEN" else "~3d (1 DEV + AI Agent)",
                      blocked_reason=blocked, blocked_since=_ago(days) if blocked else "",
                      tags=json.dumps(tags),
                      phase_since=_ago(days), synced_at=_now())
    create_ticket(
        conn, "CRM-12135", "Pre-sale helpdesk embed",
        lane="IMPLEMENTATION", phase="IN_PROGRESS", baton="MINE",
        jira_status="In Progress", track="Pre-sale", sprint="26.09",
        priority="High", assignee="Nathan Do",
        estimate="~3d (1 DEV + AI Agent) — Build API ~12h / Build UI ~10h",
        parent_key="CRM-9573",
        frd_url="https://confluence.nakivo.com/display/CRM/FRD-Pre-sale-helpdesk",
        spec_url="https://confluence.nakivo.com/display/CRM/CRM-12135-pre-sale-embed",
        worktree="nakivo-12-CRM-12135 + nakivo-19-CRM-12135",
        branch="CRM-12135-presale", mr_url="",
        tags=json.dumps(["build-api", "build-ui", "pre-sale"]),
        phase_since=_ago(6), synced_at=_now())
    for code, title, status, hours, body, kids in _SEED_PLAN:
        m = create_step(conn, "CRM-12135", title, code=code, body=body,
                        status=status, estimate_h=hours)
        for kind, ktitle, kstatus, khours, origin in kids:
            create_step(conn, "CRM-12135", ktitle, parent_id=m["id"], kind=kind,
                        status=kstatus, estimate_h=khours, origin=origin)
