# Integration Hub - Design Spec

**Date:** 2026-07-16
**Status:** Approved design, pre-plan
**Home:** a new tab ("Hub") inside FlightDeck (`token-audit`)
**Author flow:** brainstorming -> this spec -> implementation plan (writing-plans)

## 1. Context

A local, visual, drag-and-drop builder for **HTTP/API request flows** across the
NAKIVO internal stack - mainly Odoo and the internal services - for **testing
integration seams locally**. Mental model: n8n / Zapier flow chaining, with the
node-editor feel of GoRules Zen (already run locally at `:3100`).

**Why now.** The Odoo-off-EE migration is all about cross-service seams (Odoo <->
Discount Service <-> Lago). Testing those end to end today is manual and
ad-hoc. The Hub makes a seam an authored, runnable, inspectable, shareable flow.

**Prior art (in-repo).** `nakivo_rest_api` ships a **single-request** "API Test
Console" (`/api/test-ui`: auth -> one request -> inspect). The Hub is the
**multi-step, cross-service** evolution: chain requests, pass data step->step,
hit several services in one run. It does not replace the console; it starts where
it stops.

**Target services (already running as containers).** Odoo (`:8069`), CE-shim Odoo
(`:8179`), Discount Service (`:8100`), GoRules editor (`:3100`), FlightDeck
(`:8010`).

## 2. Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Primary job | **Phased**: MVP = interactive playground; phase 2 = assertions + saved-run pass/fail |
| Home + executor | **FlightDeck tab, backend-executed** (FastAPI runs the calls; no CORS; reaches services; enables XML-RPC) |
| Data passing | **Expressions** `{{ ... }}` (ZEN Expression Language, for parity with the Discount engine) |
| MVP nodes | HTTP (core) + **Odoo XML-RPC** + **Set/Transform** + **Condition/branch** (Start node implied) |
| Execution model | **n8n-style item routing through typed output sockets** (NOT DAG pruning) |
| Canvas | **React Flow** DAG canvas, FlightDeck Night styling |

## 3. Execution model (the core)

Data routing through **typed output sockets**. A node executor returns
`list[list[Item]]` - an outer list indexed by the node's declared **output
sockets**, each holding the items emitted to that socket. **The executor decides
which socket(s) receive items** - branching is intrinsic to the node.

```
Item        = { "json": <dict>, ...(binary/meta reserved for later) }
NodeOutput  = list[list[Item]]        # outputs[socketIndex] -> items
```

- **HTTP** declares outputs `[success, error]`. On 200 -> `[[{"json": …}], []]`;
  on 500 -> `[[], [{"json": …}]]`.
- **Connections** wire `(fromNode, outSocketIdx) -> (toNode, inSocketIdx)`.
- The engine runs a node, then pushes items from each **non-empty** socket along
  that socket's outgoing connections; a downstream node runs when items arrive at
  an input. Empty socket -> that path does not fire.
- Items are **lists**: a node may emit many (fan-out); downstream work maps over
  them. An input receiving several edges concatenates their items.

**Node executor contract** (backend):

```
run(params: dict, inputs: list[list[Item]], ctx: RunContext) -> list[list[Item]]
```

`ctx` exposes the expression evaluator + the accumulated run context (outputs of
already-run nodes, keyed by node name) + credential resolution.

**Engine loop (MVP).** Start node seeds one item. Maintain a queue of
(node, pending-input-items). Run a node when its inputs are ready; record a trace;
route outputs. MVP assumes acyclic graphs; a visited/step-budget guard prevents
runaway loops. (Full cycle support is deferred with delay/retry.)

**Expression context** (per item, ZEN Expression Language):

- `$json` - the current item's `json`
- `$node["Login"].json` - a named node's first output item
- `$vars` - values set by Set/Transform nodes
- `$run` - run metadata (ids, timestamps)

Evaluator is a **safe evaluator, never raw `eval`**. Preferred = ZEN Expression
Language (parity with the Promotion/Discount engine); fallback = a sandboxed
Python evaluator exposing the same context variables. See Open Items.

## 4. Node registry, Odoo auth, credentials

**Registry.** Each node type = a self-contained definition: `type`, `label`,
declared `inputs`/`outputs` (socket names), a `params` schema (drives the config
form), and `run(...)`. A new node type = one registered definition, no engine
change.

**MVP node types:**

- **Start** - outputs `[main]`; emits one seed item (optionally user-supplied JSON).
- **HTTP Request** - params: `method`, `url`, `headers`, `query`, `body`,
  `credentialRef?`, `successWhen` (default status `<400`); outputs `[success, error]`;
  item.json = `{status, headers, json|body, timeMs}`.
- **Odoo XML-RPC** - params: `credentialRef` (Odoo), `model`, `method`
  (`search_read`/`create`/`write`/`execute_kw`/…), `args`, `kwargs`; outputs
  `[success, error]`; server-side `xmlrpc.client` to `/xmlrpc/2/common`
  (authenticate) + `/xmlrpc/2/object` (execute_kw).
- **Set / Transform** - params: list of `{name, expression}`; outputs `[main]`;
  merges computed vars into the item (and into `$vars`).
- **Condition** - params: `expression`; outputs `[true, false]`; routes each input
  item by truthiness.

**Credentials - kept OUT of the flow.** A server-side credential store (in the
`token-audit-db` SQLite volume, never in files, never in git). Types: `Odoo
XML-RPC` (base/db/user/secret) and `HTTP` (bearer/basic/none). Nodes reference a
credential by id; the backend injects auth at run time. A flow JSON carries only a
credential **reference** -> shareable + git-safe.

**Target presets.** Pre-seed base URLs for the known local services so they need
not be retyped (odoo, odoo12-shim, discount-service, gorules, flightdeck).

**Reachability.** FlightDeck's container is on its own docker network, so it
cannot resolve `odoo:8069` by hostname. Target the **host-published ports via
`host.docker.internal`** (`http://host.docker.internal:8069`, `:8100`, …), which
all these services already expose. This lives in the credential/preset base-URL
(config, not code). Confirm `host.docker.internal` resolves from the container on
this Linux host (Open Items).

## 5. Persistence & run trace

**Flows = JSON files**, one per file under `token-audit/flows/`:

```
{ "id", "name", "meta",
  "nodes": [ { "id", "type", "label", "position": {x,y}, "params", "credentialRef?" } ],
  "connections": [ { "from": [nodeId, outIdx], "to": [nodeId, inIdx] } ] }
```

Files (not DB rows) so a flow is diffable, reviewable, and committable - a useful
seam (Odoo deal -> Discount -> Odoo write) becomes shared, versioned repo
documentation. Backend CRUD: list/load/save/delete.

**Credentials + run history = SQLite** (`token-audit-db` volume; local-only).

**RunResult** (returned by a run; consumed by the inspector now and assertions in
phase 2 - no new capture needed):

```
{ "runId", "flowId", "startedAt", "finishedAt", "status",
  "order": [nodeId, …],
  "nodes": { nodeId: { "status", "inputs", "outputsPerSocket", "timeMs", "error?" } } }
```

## 6. Canvas UI

New **"Hub"** tab in FlightDeck (React Flow, FlightDeck Night). Three panes:
**left** = node palette (drag to add), **center** = canvas (pan/zoom, wire
socket->socket), **right** = inspector (selected node's params form + last-run
output). Top bar: flow name, Save, **Run**, credentials manager.

- **Typed sockets visible**: inputs left, outputs right, labeled
  (`success`/`error`, `true`/`false`). An input accepts multiple edges (items
  concatenate); an output fans to multiple downstream. Socket colors minimal
  (success = green, error = coral) so coral stays the single accent (also the Run
  CTA + error/attention).
- **Config panel** rendered per node type from its `params` schema; fields are
  expression-aware (monospace, `{{ }}` hinting).
- **Run UX**: Run -> backend executes -> nodes light by status
  (success/error/not-run-dim); click a node -> per-socket output items + timing; a
  run log along the bottom.
- **Custom React Flow pieces** (the only non-trivial UI): the socket-typed node
  component (N labeled outputs mapping exactly to executor `Item[][]` indices) and
  the expression-aware field - kept aligned to the engine contract so the visual
  model and execution model are one model.

## 7. Phasing

- **Phase 1 (MVP):** item-routing engine + 5 node types, React Flow canvas +
  config panel, credentials store + presets, Run + inspector, flow save/load
  (files).
- **Phase 2:** assertions (`{targetNode, socket, expression, expected}`) + saved-
  run history + per-flow pass/fail badge + a **headless run-flow endpoint** (for
  regression use).
- **Deferred:** delay/retry, loop, additional node types (GraphQL, SQL), scheduling
  / triggers, Postman/curl import, full cycle support.

## 8. Testing

Correctness lives in the engine:

- Unit: item routing + socket emission; expression eval + context vars; each
  executor (HTTP + XML-RPC with mocked transport).
- Golden: a small fixed flow -> deterministic `RunResult`.
- Branch: 200 -> `success` socket populated / `error` empty; 500 -> inverse.
- Optional live smoke: a flow against the running Discount Service (`:8100`) / Odoo
  (marked; needs services up).
- Frontend tests light; real validation via the `verify` skill driving a flow in
  the browser.

## 9. Backend integration (FlightDeck)

Follows the existing Diff-tool pattern (`repodiff.py` + `/api/repo/*` routes):

- `token_audit/hub/` (engine, node registry, executors, expression, credentials,
  store) + routes: `GET/POST/DELETE /api/hub/flows`, `POST /api/hub/run`,
  `GET/POST /api/hub/credentials`, `GET /api/hub/presets`.
- Runs are **on-demand** (user-triggered), never background polling - this must not
  reintroduce the load pattern that wedged the app (see the 2026-07-14 incident:
  threadpool exhaustion under mount-I/O stall). HTTP/XML-RPC executors get explicit
  timeouts so a hung target fails the node instead of a worker thread.
- New frontend dep: `reactflow`. New nav entry `{ k: "hub", label: "Hub" }`.

## 10. Open items (confirm during planning / a short spike)

1. **ZEN Expression Language Python binding** usable in the FastAPI backend? If
   not clean, use the sandboxed-Python fallback (same context vars). Decide before
   the engine is built.
2. **`host.docker.internal` resolution** from the token-audit container on this
   Linux host (add `extra_hosts: ["host.docker.internal:host-gateway"]` in compose
   if needed).
3. **React Flow** version/license fit (MIT core is fine) and bundle-size impact on
   the existing dist.
4. XML-RPC node: confirm the Odoo XML-RPC surface + auth (db/user/password ->
   uid) against the local instance.

## 11. Non-goals

- Not a production orchestrator / middleware (no live glue between services).
- Not a scheduler (no cron/triggers in MVP).
- No secrets in flow files.
- Not a replacement for `nakivo_rest_api`'s single-request console.
