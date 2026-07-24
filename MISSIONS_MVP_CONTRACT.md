# Missions MVP — build contract (v1)

Scaffolding doc shared by 3 build tracks (backend · mcp · frontend). Temporary; remove before merge.
Design source of truth: `../pen.dev/FLIGHTDECK-MISSIONS-HANDOFF.md` (esp. §1 tokens, §4 data model).
Feature = a "Missions" tab in FlightDeck: a personal TODO/Note kanban whose defining trait is
**session-hold** — separate agent sessions read missions and see which session holds each one,
via a **missions MCP** that shares the same SQLite store as the web backend.

MVP scope: Night mode, **kanban view only**, session-hold (open/active/landed), card detail with
Session Log, new-mission (inline + modal), sessions indicator, 2s polling for live holds, seed data,
+ the missions MCP. OUT: board/bento/expressive/dynamic views, Day mode, AI drafts, filter/group-by,
collaborators, WYSIWYG/AI-enhance.

---

## 1. Data model

Mission JSON (API + MCP return shape):
```json
{
  "id": "m_ab12cd",
  "status": "INBOX|TODO|IN_FLIGHT|DONE",
  "title": "string",
  "note": "string",
  "tags": ["CRM-EVENT", "HIGH"],
  "priority": "LOW|NORMAL|HIGH",
  "hold": null,
  "hold_example": { "session_id": "90FB37EE", "state": "ACTIVE", "since": "2026-07-23T10:00:00Z" },
  "log": [ { "session_id": "90FB37EE", "action": "CLAIMED|RELEASED|NOTED|VIEWED|LANDED", "at": "iso8601" } ],
  "created_at": "iso8601",
  "updated_at": "iso8601"
}
```
Card render state (frontend derives): `landed` if status==DONE · `active` if hold!=null · else `open`.
(HELD/STALE hold sub-states are modeled but deferred; MVP sets hold.state=ACTIVE on claim.)

## 2. SQLite schema (backend owns; MCP reads/writes same file)

Feature-owned, created idempotently via `init(conn)` (call from `server.py` after `credentials.init`).
Use `status` (NOT `column` — reserved in Postgres). sqlite idiom, `?` placeholders.
```sql
CREATE TABLE IF NOT EXISTS missions (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'INBOX',
  title TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]',      -- JSON array of strings
  priority TEXT NOT NULL DEFAULT 'NORMAL',
  hold_session TEXT,                    -- NULL = unclaimed
  hold_state TEXT,                      -- 'ACTIVE' | 'HELD' | NULL
  hold_since TEXT,                      -- iso8601 | NULL
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mission_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id TEXT NOT NULL,
  session_id TEXT,
  action TEXT NOT NULL,
  at TEXT NOT NULL
);
```
Enable WAL on the db so the separate MCP process can write concurrently: `PRAGMA journal_mode=WAL;`
(run once in init). Keep writes short.

## 3. REST endpoints (prefix `/api/missions`)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/missions` | — | `{ "missions": [mission...], "sessions": [session...] }` |
| GET | `/api/missions/{id}` | — | `mission` (with `log`) |
| POST | `/api/missions` | `{title, note?, tags?, priority?, status?, claim_session?}` | created `mission` |
| PATCH | `/api/missions/{id}` | any of `{status,title,note,tags,priority}` | updated `mission` |
| POST | `/api/missions/{id}/claim` | `{session_id}` | `mission` (hold ACTIVE; overwrites an existing hold = take-over) |
| POST | `/api/missions/{id}/release` | — | `mission` (hold cleared, log RELEASED) |
| POST | `/api/missions/{id}/land` | — | `mission` (status DONE, hold cleared, log LANDED) |

`session` shape (derived from live holds): `{ "session_id": "4F2B", "state": "ACTIVE", "mission_id": "m_..", "since": "iso" }`.
List order: by status then updated_at desc. All timestamps UTC iso8601 (`Z`).

## 4. Hold state-machine
- **claim(id, session)**: set hold_session=session, hold_state=ACTIVE, hold_since=now; append log CLAIMED. If already held by a different session, overwrite (take-over) and still log CLAIMED.
- **release(id)**: hold_session/state/since = NULL; append log RELEASED.
- **land(id)**: status=DONE; clear hold; append log LANDED.
- **create(claim_session set)**: create then immediately claim.
- Every mutation bumps `updated_at`.

## 5. Missions MCP (greenfield, shares `audit.db`)
Tools (names snake_case): `missions_list(status?)` · `mission_get(id)` · `mission_create(title, note?, tags?, priority?, status?, claim_session?)` · `mission_claim(id, session_id)` · `mission_release(id)` · `mission_land(id)` · `sessions_list()`.
Each returns the same JSON as the REST layer. The MCP process opens the same SQLite file (path from
env `TOKEN_AUDIT_DB_PATH`, default `backend/audit.db`) in WAL mode. Reuse the backend service module if
importable; otherwise a thin sqlite duplicate of the same SQL is fine for MVP. Framework: `fastmcp`.

## 6. Seed (backend init, only if `missions` table empty)
~8 rows spanning all four statuses, mixing held/unclaimed, reusing the handoff's real examples:
- IN_FLIGHT, held by session 90FB37EE: "Re-confirm WVT facts ledger against source" (tags CRM-EVENT, HIGH)
- IN_FLIGHT, held by 4F2B: "Build Discount Service PoC endpoints" (CRM-11198)
- TODO, held by A1C4: "Post CRM-11007 estimate comment" (CRM-11007, HIGH)
- TODO, open: "Refresh nakivo-graph blast radius" (GRAPH)
- INBOX, open: "Draft CRM-11372 helpdesk scope decision" (HELPDESK, CRM-11372)
- INBOX, open: "Check Lago AGPL section 13 impact on portal" (BILLING)
- DONE: "Restructure docs into crm-tracks" (DOCS)
- DONE: "Run -u all migration on nakivo DB" (ODOO)

## 7. Litmus (integration acceptance)
Start `make dev`; run the MCP `mission_claim(<an open mission id>, "TESTMCP")`; within ~2s the web
kanban shows that card flip to the `active` hold badge "SESSION TESTMCP". Proves session-hold + MCP + shared store.
