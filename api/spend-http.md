# The spend HTTP API

Two servers answer `/api/*`:

- **`fd serve`** (Go): five routes, then a JSON 404 for every other `/api/`
  path so the SPA fallback never swallows an API miss (go/server/server.go:34-58).
  Listens on `127.0.0.1:8011` by default (go/cmd/fd/serve.go:24).
- **FastAPI** (Python): the full surface, ~100 routes across
  `python/flightdeck/routers/*.py`, `python/flightdeck/systems/*.py` and
  `python/flightdeck/agui/routes.py`, wired in python/flightdeck/server.py:77-110.
  This is what `flightdeck.service` runs on port 8010.

Both mount the built web client when `clients/web/dist` (or
`FLIGHTDECK_WEB_DIST`) exists: Go's SPA handler at go/server/server.go:59-63,99-110,
FastAPI's `StaticFiles` mount at python/flightdeck/server.py:114-115.

## Shared conventions

- `?range=` is `today`, `7d`, `30d` or `all`; missing or unknown means `all`
  (go/server/server.go:67-77 with go/core/metrics/grain.go:63-74;
  python/flightdeck/runtime.py:121-131). Both compare the **local** calendar
  date against UTC timestamps (grain.go:61-63, runtime.py:124-130).
- Errors: `fd serve` answers `{"error": "<msg>"}` (go/server/server.go:46,74-80,93-97).
  FastAPI answers `{"detail": "<msg>"}`; the web client reads `detail`
  (clients/web/src/api.js:9-21; e.g. python/flightdeck/routers/sessions.py:43,49).
- `fd serve` recomputes per request (the price table is re-read each call,
  server.go:67-69); FastAPI answers warm requests from an in-process snapshot
  cache (routers/core.py:24-26, runtime.py:134-140).

## Routes `fd serve` serves

All methods are GET; there is no write route.

| Route | Query params | Response JSON (keys: types) | FastAPI match and differences |
|---|---|---|---|
| `/api/summary` | `range` | Object — `total_cost` f, `cache_savings` f, `saved_vs_subscription` f, `rtk_savings` f, `cache_hit_rate` f, `input_tokens` i, `input_tokens_raw` i, `output_tokens` i, `session_count` i, `unknown_model_tokens` i, `message_count` i, `total_context_tokens` i, `avg_context_per_turn` f (go/server/server.go:34-36; go/core/metrics/summary.go:12-26) | `GET /api/summary` (routers/core.py:22-34). Identical keys (python/flightdeck/metrics.py:195-211). One difference: fd's `rtk_savings` is a stub, always 0 (summary.go:16); FastAPI reports `app.state.rtk_savings` (routers/core.py:30-31, metrics.py:199). |
| `/api/daily` | `range` | Array of `date` s, `cost` f, `input` i, `output` i, `cache_read` i, `cache_write` i, `tokens` i, `agent_hours` f, `agent_hours_sub` f, `agent_hours_est` f (server.go:37-39; go/core/metrics/daily.go:9-20) | `GET /api/daily` (routers/core.py:37-47). Same keys (metrics.py:232-235,267). Difference: fd emits the three `agent_hours*` keys as 0 "until the activity slice" (daily.go:17-19); FastAPI fills them from the `agent_activity` table (metrics.py:214-229,258-266). |
| `/api/by-model` | `range` | Array of `model` s, `input` i, `input_raw` i, `output` i, `cache_read` i, `cost` f, `priced` b (server.go:40-42; go/core/metrics/bymodel.go:12-20) | `GET /api/by-model` (routers/core.py:50-60). Identical (metrics.py:360-376). Both exclude `<synthetic>` (bymodel.go:10,27-29; metrics.py:354-357,363-364). |
| `/api/sessions` | `range`, `limit` (default 100), `offset` (default 0) (server.go:43-44,86-91) | Array of `session_id` s, `project` s\|null, `models` [s], `first_ts` s, `last_ts` s, `turns` i, `input` i, `output` i, `cache_read` i, `cache_create` i, `cost` f, `title` s\|null, `context` i, `input_raw` i, `avg_context` f, `cache_ratio` f (go/core/metrics/sessions.go:10-27) | `GET /api/sessions` with the same params (routers/sessions.py:16-29). FastAPI adds one key per row: `subagents` [obj] read from transcript files (metrics.py:316-351); fd omits it by design (sessions.go:29-31). |
| `/api/health` | — | `{"ok": true, "version": s, "projects": s}` (server.go:51-53) | **Go-only** — no FastAPI route at this path. |
| `/api/*` (anything else) | — | 404 `{"error": "not served by fd yet: <path>"}` (server.go:54-58) | The FastAPI routes below. |

## FastAPI routes the web client calls that `fd serve` does not serve

Grouped by router file; each line names the caller in clients/web/src.
`fd serve` answers these with the 404 above, so the React UI is fully
functional only against the FastAPI server.

**routers/core.py** (python/flightdeck/routers/core.py)
- `GET /api/usage-windows` (core.py:90) — SpendComposed.jsx:61, Plane.jsx:72, App.jsx:546
- `GET /api/quota` (core.py:97) — SpendComposed.jsx:62, Plane.jsx:72, App.jsx:547
- `POST /api/quota/refresh` (core.py:103) — App.jsx:561
- `GET /api/pulse` (core.py:63) — Pulse.jsx:256
- `GET /api/screenshot?path=` (core.py:113) — session/renderers.jsx:176, SessionDetail.jsx:413

**routers/sessions.py**
- `GET /api/session/{id}` `?offset&limit&anchor&subagent_turns` (sessions.py:32) — SessionDetail.jsx:1230,1250,1280
- `GET /api/session/{id}/subagent/{agent_id}` (sessions.py:53) — SessionDetail.jsx:256
- `GET /api/session/{id}/usage` (sessions.py:64) — SessionDetail.jsx:1437
- `GET /api/session/{id}/tools?limit` (sessions.py:82) — SessionDetail.jsx:1427
- `GET /api/session/{id}/clearance-routes?max_units` (sessions.py:126) — RouteLoom.jsx:360

**routers/stream.py**
- `GET /api/stream` (SSE; event names `summary-updated` and `fd-event`) (stream.py:45) — api.js:59,80

**routers/charts.py**
- `GET /api/graph` (charts.py:10) — GraphView.jsx:37

**routers/thinking.py**
- `GET /api/thinking?…` (thinking.py:12) — spend/SpendView.jsx:339

**routers/tasks.py**
- `GET /api/tasks?range&limit` (tasks.py:25) — SpendView.jsx:416
- `POST /api/tasks/{turn_id}/label` (tasks.py:34) — SpendView.jsx:427

**routers/burn.py**
- `GET /api/burn/stats?range&window` (burn.py:7) — SpendView.jsx:550

**routers/diff.py**
- `GET /api/repo/list` (diff.py:16) — RepoDiff.jsx:179
- `GET /api/repo/refs?repo` (diff.py:21) — RepoDiff.jsx:189,244
- `GET /api/repo/compare?repo&base&head` (diff.py:26) — RepoDiff.jsx:206,232
- `GET /api/repo/filediff?repo&base&head&path` (diff.py:31) — RepoDiff.jsx:84
- `POST /api/repo/fetch`, `POST /api/repo/pull` (diff.py:36,41) — RepoDiff.jsx:242

**routers/hub.py**
- `GET /api/hub/node-types` (hub.py:77) — hub/Hub.jsx:37
- `GET /api/hub/presets` (hub.py:84) — Hub.jsx:38
- `GET /api/hub/flows`, `POST /api/hub/flows`, `GET /api/hub/flows/{id}` (hub.py:89,94,105) — Hub.jsx:39,54,56,63
- `GET /api/hub/credentials`, `POST /api/hub/credentials`, `DELETE /api/hub/credentials/{id}` (hub.py:123,132,147) — Hub.jsx:30, hub/Credentials.jsx:13,28
- `POST /api/hub/run` (hub.py:154) — Hub.jsx:79

**routers/radar.py**
- `GET /api/radar/radars` (radar.py:41) — radar/data.js:47
- `GET /api/radar/{slug}/blips/{num}` (radar.py:62) — data.js:52
- `POST /api/radar/{slug}/blips/{num}/moves` (radar.py:74) — data.js:64

**routers/memory.py**
- `GET /api/memory/lint` (memory.py:53) — memory/MemoryView.jsx:39
- `GET /api/memory/graph` (memory.py:72) — MemoryView.jsx:39
- `GET /api/memory/search?query&limit` (memory.py:64) — MemoryView.jsx:52

**routers/missions.py**
- `GET /api/missions`, `POST /api/missions` (missions.py:47,68) — systems/MissionsView.jsx:116,259,429
- `GET`/`PATCH`/`DELETE /api/missions/{mid}` (missions.py:56,78,98) — MissionsView.jsx:292,295,300,524
- `POST /api/missions/{mid}/claim|release|land|read` (missions.py:88,118,128,108) — MissionsView.jsx:400,404,408,518

**routers/tickets.py**
- `GET /api/tickets` (tickets.py:94) — tickets/TicketsView.jsx:71
- `GET`/`PATCH /api/tickets/{key}` (tickets.py:119,131) — tickets/TicketForm.jsx:55,66
- `POST /api/tickets/{key}/phase` (tickets.py:140) — TicketForm.jsx:61
- `POST /api/tickets/{key}/steps` (tickets.py:154) — tickets/PlanPane.jsx:134
- `PATCH`/`DELETE /api/tickets/steps/{sid}` (tickets.py:163,172) — PlanPane.jsx:120,246

**routers/treasures.py** (+ `?include_source=true`, `?confirm=true` query flags)
- `GET /api/treasures`, `POST /api/treasures`, `GET /api/treasure-tags` (treasures.py:27,169,41) — treasures/TreasuresView.jsx:258,268,269,99
- `POST /api/treasures/discover` (treasures.py:143) — TreasuresView.jsx:312
- `GET`/`PATCH`/`DELETE /api/treasures/{ident}` (treasures.py:65,241,299) — treasures/TreasureDetail.jsx:69,94,531
- `PUT /api/treasures/{ident}/source` (treasures.py:209) — TreasureDetail.jsx:81
- `POST /api/treasures/{ident}/rerender`, `/refresh` (treasures.py:263,77) — TreasureDetail.jsx:108,117
- `GET /api/treasures/{ident}/versions`, `/raw` (treasures.py:99,115) — treasures/detail/VersionHistory.jsx:91, TreasureDetail.jsx:726

**routers/treasure_config.py**
- `GET`/`PUT /api/treasure-config` (treasure_config.py:30,39) — treasures/TreasureConfig.jsx:26

**routers/appearance.py**
- `GET`/`PUT`/`DELETE /api/appearance` (appearance.py:41,60,77) — ui/appearance.js:298,259,278

**routers/decisions.py**
- `GET /api/decisions`, `GET /api/decisions/{did}` (decisions.py:62,71) — agui/ApprovalDock.jsx:116 (and per its comment :10)
- `POST /api/decisions/{did}/resolve` (decisions.py:83) — ApprovalDock.jsx:137

**systems/*.py** (prefix `/api/systems`)
- `GET /api/systems/stacks` (systems/containers.py:318) — systems/HangarView.jsx:428
- `GET /api/systems/containers/{cid}/stats` (containers.py:263) — HangarView.jsx:121
- `GET /api/systems/skills?range` (systems/skills.py:234) — systems/ManualsView.jsx:130
- `GET /api/systems/mcp?range`, `GET /api/systems/mcp/processes`, `POST /api/systems/mcp/reprobe` (systems/mcp.py:592,562,599) — systems/CommsView.jsx:351,167,342

**agui/routes.py** (prefix `/api/agui`)
- `POST /api/agui/run`, `POST /api/agui/resume` (agui/routes.py:69,108) — agui/client.js:35,45
- `GET /api/agui/sessions?limit` (agui/routes.py:128) — agui/RelayView.jsx:201

## Registered in FastAPI but not fetched by the web client

Still served (a hook or external caller may use them); listed so the table
above is complete: `GET /api/rtk` and `POST /api/reingest` (core.py:73,78),
`POST /api/session/{id}/send` (send.py:66 — "writes into a session by spawning
the CLI"), `GET /api/session/{id}/route` (sessions.py:114), `GET /api/burn`
(burn.py:6), `GET /api/memory/history` (memory.py:78), `GET /api/radar/{slug}`
(radar.py:50), `POST /api/decisions` (decisions.py:53 — the PreToolUse hook
writes here, server.py:92-95), `POST /api/hub/webhook/{token}` (hub.py:171 —
external webhooks), `DELETE /api/hub/flows/{id}` (hub.py:114),
`GET /api/systems/containers` (containers.py:215), `POST /api/tickets`
(tickets.py:103), `POST /api/treasures/{ident}/tags` and `/link`
(treasures.py:54,283).
