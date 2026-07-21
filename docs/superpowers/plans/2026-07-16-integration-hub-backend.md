# Integration Hub - Backend Implementation Plan (Plan A of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend of the Integration Hub - an n8n-style item-routing engine that runs multi-step HTTP/Odoo-XML-RPC flows locally, exposed as `/api/hub/*` in FlightDeck. Fully usable and testable without any UI (Plan B adds the React Flow canvas).

**Architecture:** A node executor returns `list[list[Item]]` (items per typed output socket); the engine routes items along socket->socket connections, records a per-node trace, and returns a `RunResult`. Nodes are registered definitions (Start, HTTP, Odoo XML-RPC, Set/Transform, Condition). Expressions use GoRules ZEN (`zen-engine`) for parity with the Discount engine. Flows persist as JSON files; credentials + run history in the existing SQLite volume. Requests execute server-side (no CORS, reaches services via `host.docker.internal`, enables XML-RPC).

**Tech Stack:** Python 3.7 (FlightDeck's runtime), FastAPI, `zen-engine==0.53.0`, stdlib `xmlrpc.client`, `urllib`/`httpx`, `sqlite3`, `pytest`.

## Global Constraints

- Runtime is **Python 3.12** (`python:3.12-slim`, per token-audit's Dockerfile); pytest is already in the image and existing tests live in `tests/` (import `from token_audit import ...`). Match that layout.
- All new backend code lives under `token_audit/hub/`; routes are added to `token_audit/server.py` following the existing Diff-tool pattern (`repodiff.py` + `/api/repo/*`).
- Runs are **on-demand only** (never background polling) - the 2026-07-14 wedge was threadpool exhaustion under mount-I/O stall; every HTTP/XML-RPC call MUST carry an explicit timeout so a hung target fails the node, not a worker thread.
- **No secrets in flow files.** Flows reference a credential by id; credential secrets live only in SQLite (`token-audit-db` volume).
- Expression evaluation is a **safe evaluator, never `eval`**. Preferred: `zen.evaluate_expression`. Context vars: `$json`, `$node`, `$vars`, `$run`.
- Item shape is fixed: `{"json": <dict>}` (keys `binary`/`meta` reserved, unused in phase 1).
- Node executor contract is fixed: `run(params, inputs, ctx) -> List[List[Item]]`, outer list indexed by the node's declared output sockets.
- Flows dir: `token-audit/flows/` (host), mounted read-write into the container at `/app/flows` via `TOKEN_AUDIT_FLOWS_DIR`.
- Commit after every task. Branch: create `crm-integration-hub` off the current branch before Task 1 (never commit straight to the default branch).

---

### Task 1: Dependencies + capability spike (evaluator, reachability)

**Files:**
- Modify: `requirements.txt` (or `Dockerfile` pip list - match where deps are declared)
- Create: `token_audit/hub/__init__.py`
- Test: `tests/hub/test_zen_available.py`

**Interfaces:**
- Produces: confirmed `zen.evaluate_expression(expression: str, context: dict) -> Any` is importable in the container; the `token_audit.hub` package exists.

- [ ] **Step 1: Add the dependency**

Add to `requirements.txt` (create if absent; also ensure the Dockerfile installs it):

```
zen-engine==0.53.0
```

- [ ] **Step 2: Create the package**

Create `token_audit/hub/__init__.py` (empty).

- [ ] **Step 3: Write the capability test**

Create `tests/hub/__init__.py` (empty) and `tests/hub/test_zen_available.py`:

```python
def test_zen_evaluate_expression_available():
    import zen
    # standalone expression evaluation against a context
    out = zen.evaluate_expression("$.a + $.b", {"a": 2, "b": 3})
    assert out == 5
```

- [ ] **Step 4: Rebuild the image and run the test in-container**

Run:
```bash
docker compose build token-audit
docker compose run --rm token-audit pytest tests/hub/test_zen_available.py -v
```
Expected: PASS. **If `evaluate_expression` is absent or the arg shape differs**, record the actual `zen` API surface (`docker compose run --rm token-audit python -c "import zen; print(dir(zen))"`) and adjust `expr.py` in Task 3 accordingly; if `zen` cannot evaluate standalone expressions at all, implement Task 3's evaluator with the documented Python-sandbox fallback (`simpleeval` + `jsonpath-ng`) instead. Do not proceed past Task 3 with an unverified evaluator.

- [ ] **Step 5: Verify reachability preset value**

Run:
```bash
docker compose run --rm token-audit python -c "import socket; print(socket.gethostbyname('host.docker.internal'))"
```
Expected: prints an IP (e.g. `172.29.0.2`). If it fails, add to `docker-compose.yml` under the `token-audit` service:
```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt token_audit/hub/__init__.py tests/hub/
git commit -m "feat(hub): add zen-engine dep + verify expression eval and reachability"
```

---

### Task 2: Core types + node registry

**Files:**
- Create: `token_audit/hub/types.py`
- Create: `token_audit/hub/registry.py`
- Test: `tests/hub/test_registry.py`

**Interfaces:**
- Produces:
  - `Item = Dict[str, Any]` (a dict with key `"json"`); helper `item(json_dict) -> Item`.
  - `NodeDef` dataclass-like: `type: str`, `label: str`, `inputs: List[str]`, `outputs: List[str]`, `run: Callable[[dict, List[List[Item]], "RunContext"], List[List[Item]]]`.
  - `register(nodedef)` and `get(type: str) -> NodeDef` and `all_defs() -> List[NodeDef]` in `registry.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/hub/test_registry.py`:

```python
from token_audit.hub import registry
from token_audit.hub.types import NodeDef, item

def test_register_and_get():
    d = NodeDef(type="dummy", label="Dummy", inputs=["main"], outputs=["main"],
                run=lambda params, inputs, ctx: [[item({"ok": True})]])
    registry.register(d)
    got = registry.get("dummy")
    assert got.label == "Dummy"
    assert got.outputs == ["main"]
    assert got.run({}, [], None) == [[{"json": {"ok": True}}]]

def test_unknown_type_raises():
    import pytest
    with pytest.raises(KeyError):
        registry.get("nope")
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm token-audit pytest tests/hub/test_registry.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement types + registry**

Create `token_audit/hub/types.py`:

```python
"""Core Hub types. Python 3.7 compatible (no PEP 604 unions)."""
from typing import Any, Callable, Dict, List, Optional

Item = Dict[str, Any]          # {"json": {...}}
NodeOutput = List[List[Item]]  # outputs[socketIndex] -> items

def item(json_dict: Optional[dict] = None) -> Item:
    return {"json": dict(json_dict or {})}

class NodeDef:
    def __init__(self, type: str, label: str, inputs: List[str],
                 outputs: List[str], run: Callable, params_schema: Optional[dict] = None):
        self.type = type
        self.label = label
        self.inputs = inputs
        self.outputs = outputs
        self.run = run
        self.params_schema = params_schema or {}
```

Create `token_audit/hub/registry.py`:

```python
from typing import Dict, List
from token_audit.hub.types import NodeDef

_REGISTRY: Dict[str, NodeDef] = {}

def register(nodedef: NodeDef) -> None:
    _REGISTRY[nodedef.type] = nodedef

def get(type: str) -> NodeDef:
    return _REGISTRY[type]

def all_defs() -> List[NodeDef]:
    return list(_REGISTRY.values())
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm token-audit pytest tests/hub/test_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add token_audit/hub/types.py token_audit/hub/registry.py tests/hub/test_registry.py
git commit -m "feat(hub): core Item/NodeDef types + node registry"
```

---

### Task 3: Expression evaluator + `{{ }}` interpolation

**Files:**
- Create: `token_audit/hub/expr.py`
- Test: `tests/hub/test_expr.py`

**Interfaces:**
- Consumes: `zen.evaluate_expression` (verified Task 1).
- Produces:
  - `build_context(current_item, node_outputs, vars, run_meta) -> dict` returning `{"$json":..., "$node":..., "$vars":..., "$run":...}`.
  - `evaluate(expression: str, context: dict) -> Any` (single ZEN expression).
  - `interpolate(value, context) -> Any` - walks a str/dict/list; replaces any `{{ expr }}` occurrences. A string that is exactly `{{ expr }}` returns the raw evaluated value (not stringified); a string with embedded `{{ }}` returns the string with each result substituted.

- [ ] **Step 1: Write the failing test**

Create `tests/hub/test_expr.py`:

```python
from token_audit.hub import expr

CTX = {"$json": {"id": 7, "name": "x"}, "$node": {"Login": {"json": {"token": "abc"}}},
       "$vars": {"rate": 0.9}, "$run": {"id": "r1"}}

def test_evaluate_scalar():
    assert expr.evaluate("$json.id * 2", CTX) == 14

def test_interpolate_whole_string_returns_raw_type():
    # exactly one expression -> keep the native type (int), not "7"
    assert expr.interpolate("{{ $json.id }}", CTX) == 7

def test_interpolate_embedded_returns_string():
    assert expr.interpolate("Bearer {{ $node.Login.json.token }}", CTX) == "Bearer abc"

def test_interpolate_walks_dict_and_list():
    out = expr.interpolate({"url": "/p/{{ $json.id }}", "xs": ["{{ $vars.rate }}"]}, CTX)
    assert out == {"url": "/p/7", "xs": [0.9]}
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm token-audit pytest tests/hub/test_expr.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the evaluator**

Create `token_audit/hub/expr.py`:

```python
"""Expression evaluation + templating. ZEN expression language via zen-engine.

ZEN references the context root with `$` (e.g. `$json.id`). We pass the context
dict straight through; `evaluate` is a thin wrapper so the rest of the engine
never imports zen directly (swappable for the sandbox fallback)."""
import re
from typing import Any, Dict, List, Optional
import zen

_EXPR = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)

def build_context(current_item: Optional[dict], node_outputs: Dict[str, dict],
                  vars: Dict[str, Any], run_meta: Dict[str, Any]) -> dict:
    return {
        "$json": (current_item or {}).get("json", {}),
        "$node": node_outputs,   # {nodeName: {"json": {...}}} first item per node
        "$vars": vars,
        "$run": run_meta,
    }

def evaluate(expression: str, context: dict) -> Any:
    return zen.evaluate_expression(expression.strip(), context)

def interpolate(value: Any, context: dict) -> Any:
    if isinstance(value, str):
        m = _EXPR.fullmatch(value.strip())
        if m:                                   # whole string is one expression
            return evaluate(m.group(1), context)
        return _EXPR.sub(lambda x: str(evaluate(x.group(1), context)), value)
    if isinstance(value, dict):
        return {k: interpolate(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, context) for v in value]
    return value
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm token-audit pytest tests/hub/test_expr.py -v`
Expected: PASS. (If ZEN's root token differs from `$json`, adjust `build_context` keys + these tests to the real surface confirmed in Task 1.)

- [ ] **Step 5: Commit**

```bash
git add token_audit/hub/expr.py tests/hub/test_expr.py
git commit -m "feat(hub): ZEN expression evaluator + {{ }} interpolation"
```

---

### Task 4: The engine (item routing + trace)

**Files:**
- Create: `token_audit/hub/engine.py`
- Test: `tests/hub/test_engine.py`

**Interfaces:**
- Consumes: `registry.get`, `types.Item`.
- Produces:
  - `run_flow(flow: dict, seed: Optional[dict] = None, resolve_credential=None) -> dict` returning a `RunResult`:
    `{"runId","flowId","startedAt","finishedAt","status","order":[nodeId,...], "nodes": {nodeId: {"status","inputs","outputsPerSocket","timeMs","error"}}}`.
  - `RunContext` object passed to executors, exposing `.node_outputs` (dict nodeName->first item), `.vars` (dict), `.run_meta` (dict), and `.resolve_credential(ref)`.
- Flow shape consumed: `{"id","name","nodes":[{"id","type","label","params","credentialRef"}],"connections":[{"from":[nodeId,outIdx],"to":[nodeId,inIdx]}]}`.

- [ ] **Step 1: Write the failing test**

Create `tests/hub/test_engine.py`:

```python
from token_audit.hub import engine, registry
from token_audit.hub.types import NodeDef, item

def _register_test_nodes():
    registry.register(NodeDef("t.start", "Start", [], ["main"],
        run=lambda p, ins, ctx: [[item({"seed": 1})]]))
    # emits its input's json.n doubled, onto socket 0 if even else socket 1
    registry.register(NodeDef("t.route", "Route", ["main"], ["even", "odd"],
        run=lambda p, ins, ctx: (
            [[item({"n": it["json"]["n"] * 2})], []] if it["json"]["n"] % 2 == 0
            else [[], [item({"n": it["json"]["n"]})]]
        ) if (it := (ins[0][0] if ins and ins[0] else item({"n": 0}))) else [[], []]))
    registry.register(NodeDef("t.sink", "Sink", ["main"], ["main"],
        run=lambda p, ins, ctx: [[item({"got": ins[0][0]["json"]})]] if ins and ins[0] else [[]]))

def test_routes_items_to_correct_socket_and_traces():
    _register_test_nodes()
    flow = {
        "id": "f1", "name": "t",
        "nodes": [
            {"id": "s", "type": "t.start", "params": {}},
            {"id": "r", "type": "t.route", "params": {}},
            {"id": "even", "type": "t.sink", "params": {}},
            {"id": "odd", "type": "t.sink", "params": {}},
        ],
        "connections": [
            {"from": ["s", 0], "to": ["r", 0]},
            {"from": ["r", 0], "to": ["even", 0]},   # even socket
            {"from": ["r", 1], "to": ["odd", 0]},    # odd socket
        ],
    }
    # seed makes start emit n; override start to carry n=4 (even)
    registry.register(NodeDef("t.start", "Start", [], ["main"],
        run=lambda p, ins, ctx: [[item({"n": 4})]]))
    res = engine.run_flow(flow)
    assert res["status"] == "ok"
    assert res["nodes"]["even"]["status"] == "ok"
    assert res["nodes"]["even"]["outputsPerSocket"][0][0]["json"]["got"]["n"] == 8
    # odd sink received nothing -> not run
    assert res["nodes"]["odd"]["status"] == "skipped"
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm token-audit pytest tests/hub/test_engine.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the engine**

Create `token_audit/hub/engine.py`:

```python
"""Item-routing flow engine. Runs nodes when their inputs are ready, routes each
node's per-socket output items along connections, records a per-node trace."""
import time
import traceback
import uuid
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional
from token_audit.hub import registry
from token_audit.hub.types import Item

_MAX_NODE_RUNS = 1000  # runaway guard (acyclic MVP)

class RunContext:
    def __init__(self, resolve_credential: Optional[Callable] = None):
        self.node_outputs: Dict[str, dict] = {}   # nodeLabel/id -> first output item
        self.vars: Dict[str, Any] = {}
        self.run_meta: Dict[str, Any] = {}
        self._resolve = resolve_credential or (lambda ref: None)
    def resolve_credential(self, ref):
        return self._resolve(ref)

def run_flow(flow: dict, seed: Optional[dict] = None,
             resolve_credential: Optional[Callable] = None) -> dict:
    nodes = {n["id"]: n for n in flow["nodes"]}
    # inbox[nodeId][inSocket] -> accumulated items
    inbox: Dict[str, Dict[int, List[Item]]] = defaultdict(lambda: defaultdict(list))
    # outgoing[(nodeId, outSocket)] -> list of (toNodeId, toSocket)
    outgoing: Dict[Any, List] = defaultdict(list)
    indegree: Dict[str, int] = defaultdict(int)
    for c in flow["connections"]:
        outgoing[(c["from"][0], c["from"][1])].append((c["to"][0], c["to"][1]))
        indegree[c["to"][0]] += 1

    ctx = RunContext(resolve_credential)
    ctx.run_meta = {"id": uuid.uuid4().hex, "flowId": flow.get("id")}
    trace: Dict[str, dict] = {n_id: {"status": "skipped", "inputs": None,
                                     "outputsPerSocket": None, "timeMs": None,
                                     "error": None} for n_id in nodes}
    order: List[str] = []

    # ready queue: start nodes = indegree 0
    ready = [n_id for n_id in nodes if indegree[n_id] == 0]
    runs = 0
    status = "ok"
    while ready:
        n_id = ready.pop(0)
        runs += 1
        if runs > _MAX_NODE_RUNS:
            status = "error"; break
        node = nodes[n_id]
        ndef = registry.get(node["type"])
        inputs = [inbox[n_id].get(i, []) for i in range(max(1, len(ndef.inputs)))]
        t0 = time.time()
        try:
            outputs = ndef.run(node.get("params", {}), inputs, ctx)
            trace[n_id] = {"status": "ok", "inputs": inputs, "outputsPerSocket": outputs,
                           "timeMs": round((time.time() - t0) * 1000, 2), "error": None}
            first = next((s[0] for s in outputs if s), None)
            if first is not None:
                ctx.node_outputs[node.get("label") or n_id] = first
            order.append(n_id)
            # route items to downstream inboxes
            for out_idx, items in enumerate(outputs):
                if not items:
                    continue
                for (to_id, to_sock) in outgoing.get((n_id, out_idx), []):
                    inbox[to_id][to_sock].extend(items)
                    indegree[to_id] -= 1
                    if indegree[to_id] <= 0 and to_id not in order and to_id not in ready:
                        ready.append(to_id)
        except Exception as e:
            trace[n_id] = {"status": "error", "inputs": inputs, "outputsPerSocket": None,
                           "timeMs": round((time.time() - t0) * 1000, 2),
                           "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"}
            order.append(n_id)
            status = "error"
    return {"runId": ctx.run_meta["id"], "flowId": flow.get("id"),
            "startedAt": None, "finishedAt": None, "status": status,
            "order": order, "nodes": trace}
```

Note for the implementer: the indegree/ready scheme fires a node once all *connected* inputs have delivered; a node whose upstream socket stayed empty keeps `indegree>0` and remains `skipped`. This matches the socket-routing semantics from the spec (empty socket -> path doesn't fire).

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm token-audit pytest tests/hub/test_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add token_audit/hub/engine.py tests/hub/test_engine.py
git commit -m "feat(hub): item-routing flow engine with per-node trace"
```

---

### Task 5: HTTP Request node

**Files:**
- Create: `token_audit/hub/nodes/__init__.py`, `token_audit/hub/nodes/http.py`
- Test: `tests/hub/test_node_http.py`

**Interfaces:**
- Consumes: `expr.interpolate`, `expr.build_context`, `RunContext`, `types.item`.
- Produces: a registered `NodeDef` type `"http"`, outputs `["success","error"]`; a testable `execute(params, inputs, ctx)` that uses an injectable `transport` (default = a urllib-based caller with timeout) so tests avoid real network.

- [ ] **Step 1: Write the failing test**

Create `tests/hub/test_node_http.py`:

```python
from token_audit.hub.nodes import http
from token_audit.hub.engine import RunContext
from token_audit.hub.types import item

def fake_transport(method, url, headers, body, timeout):
    # echo a deterministic response keyed by url
    if url.endswith("/boom"):
        return 500, {"h": "1"}, {"error": "kaboom"}
    return 200, {"h": "1"}, {"echo": {"method": method, "url": url}}

def test_http_success_goes_to_socket_0():
    ctx = RunContext()
    out = http.execute({"method": "GET", "url": "http://x/ok"}, [[item({})]], ctx,
                       transport=fake_transport)
    assert out[0] and not out[1]
    assert out[0][0]["json"]["status"] == 200
    assert out[0][0]["json"]["json"]["echo"]["url"] == "http://x/ok"

def test_http_error_goes_to_socket_1():
    ctx = RunContext()
    out = http.execute({"method": "GET", "url": "http://x/boom"}, [[item({})]], ctx,
                       transport=fake_transport)
    assert not out[0] and out[1]
    assert out[1][0]["json"]["status"] == 500

def test_http_interpolates_url_from_prior_node():
    ctx = RunContext(); ctx.node_outputs = {"Login": {"json": {"id": 9}}}
    out = http.execute({"method": "GET", "url": "http://x/p/{{ $node.Login.json.id }}"},
                       [[item({})]], ctx, transport=fake_transport)
    assert out[0][0]["json"]["json"]["echo"]["url"] == "http://x/p/9"
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm token-audit pytest tests/hub/test_node_http.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the HTTP node**

Create `token_audit/hub/nodes/__init__.py` (empty) and `token_audit/hub/nodes/http.py`:

```python
"""HTTP Request node. Server-side execution with a hard timeout so a hung target
fails the node (error socket), never a worker thread."""
import json as _json
import urllib.request
from typing import List
from token_audit.hub import registry, expr
from token_audit.hub.types import NodeDef, Item, item

DEFAULT_TIMEOUT = 20

def _urllib_transport(method, url, headers, body, timeout):
    data = None
    if body is not None:
        data = (body if isinstance(body, bytes)
                else _json.dumps(body).encode("utf-8"))
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            status, resp_headers = r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status, resp_headers = e.code, dict(e.headers or {})
    try:
        parsed = _json.loads(raw)
    except ValueError:
        parsed = None
    return status, resp_headers, parsed if parsed is not None else raw

def execute(params, inputs, ctx, transport=_urllib_transport) -> List[List[Item]]:
    src = (inputs[0][0] if inputs and inputs[0] else item({}))
    context = expr.build_context(src, ctx.node_outputs, ctx.vars, ctx.run_meta)
    method = (params.get("method") or "GET").upper()
    url = expr.interpolate(params.get("url", ""), context)
    headers = expr.interpolate(params.get("headers", {}) or {}, context)
    body = expr.interpolate(params.get("body"), context) if params.get("body") is not None else None
    # credential injection (bearer/basic) if a ref is set
    cred = ctx.resolve_credential(params.get("credentialRef"))
    if cred and cred.get("kind") == "bearer":
        headers = dict(headers); headers["Authorization"] = "Bearer " + cred["token"]
    timeout = params.get("timeout") or DEFAULT_TIMEOUT
    import time as _t; t0 = _t.time()
    status, resp_headers, parsed = transport(method, url, headers, body, timeout)
    out_item = item({"status": status, "headers": resp_headers, "json": parsed,
                     "timeMs": round((_t.time() - t0) * 1000, 2)})
    success_when = params.get("successMaxStatus", 399)
    if status <= success_when:
        return [[out_item], []]
    return [[], [out_item]]

registry.register(NodeDef("http", "HTTP Request", ["main"], ["success", "error"],
                          run=execute,
                          params_schema={"method": "string", "url": "string",
                                         "headers": "json", "body": "json",
                                         "credentialRef": "string"}))
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm token-audit pytest tests/hub/test_node_http.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add token_audit/hub/nodes/ tests/hub/test_node_http.py
git commit -m "feat(hub): HTTP Request node (success/error sockets, timeout, cred injection)"
```

---

### Task 6: Odoo XML-RPC node

**Files:**
- Create: `token_audit/hub/nodes/odoo_xmlrpc.py`
- Test: `tests/hub/test_node_xmlrpc.py`

**Interfaces:**
- Consumes: `expr`, `RunContext`, `types.item`.
- Produces: registered type `"odoo.xmlrpc"`, outputs `["success","error"]`; `execute(params, inputs, ctx, proxy_factory=...)` where `proxy_factory(url)` returns an object with `.authenticate(db,user,pwd,{})` and `.execute_kw(db,uid,pwd,model,method,args,kwargs)` so tests inject a fake.

- [ ] **Step 1: Write the failing test**

Create `tests/hub/test_node_xmlrpc.py`:

```python
from token_audit.hub.nodes import odoo_xmlrpc as x
from token_audit.hub.engine import RunContext
from token_audit.hub.types import item

class FakeCommon:
    def authenticate(self, db, user, pwd, opts): return 7
class FakeObject:
    def execute_kw(self, db, uid, pwd, model, method, args, kwargs=None):
        return [{"id": 1, "name": "Deal A"}]
def fake_factory(url):
    return FakeCommon() if url.endswith("/common") else FakeObject()

def test_xmlrpc_search_read_success():
    ctx = RunContext(resolve_credential=lambda ref: {
        "kind": "odoo", "base": "http://x", "db": "nakivo", "user": "a", "secret": "p"})
    out = x.execute({"credentialRef": "c1", "model": "crm.lead",
                     "method": "search_read", "args": [[]], "kwargs": {"limit": 1}},
                    [[item({})]], ctx, proxy_factory=fake_factory)
    assert out[0] and not out[1]
    assert out[0][0]["json"]["result"][0]["name"] == "Deal A"

def test_xmlrpc_error_to_socket_1():
    def boom_factory(url):
        class C:
            def authenticate(self, *a): return 7
        class O:
            def execute_kw(self, *a, **k): raise RuntimeError("bad domain")
        return C() if url.endswith("/common") else O()
    ctx = RunContext(resolve_credential=lambda ref: {
        "kind": "odoo", "base": "http://x", "db": "d", "user": "a", "secret": "p"})
    out = x.execute({"credentialRef": "c1", "model": "crm.lead", "method": "search_read",
                     "args": [[]]}, [[item({})]], ctx, proxy_factory=boom_factory)
    assert not out[0] and out[1]
    assert "bad domain" in out[1][0]["json"]["error"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm token-audit pytest tests/hub/test_node_xmlrpc.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the XML-RPC node**

Create `token_audit/hub/nodes/odoo_xmlrpc.py`:

```python
"""Odoo XML-RPC node: authenticate on /xmlrpc/2/common, call execute_kw on
/xmlrpc/2/object. proxy_factory is injectable for tests."""
import xmlrpc.client
from typing import List
from token_audit.hub import registry, expr
from token_audit.hub.types import NodeDef, Item, item

def _default_factory(url):
    return xmlrpc.client.ServerProxy(url, allow_none=True)

def execute(params, inputs, ctx, proxy_factory=_default_factory) -> List[List[Item]]:
    src = (inputs[0][0] if inputs and inputs[0] else item({}))
    context = expr.build_context(src, ctx.node_outputs, ctx.vars, ctx.run_meta)
    cred = ctx.resolve_credential(params.get("credentialRef")) or {}
    base = cred["base"].rstrip("/"); db = cred["db"]; user = cred["user"]; pwd = cred["secret"]
    model = params["model"]
    method = params.get("method", "search_read")
    args = expr.interpolate(params.get("args", []), context)
    kwargs = expr.interpolate(params.get("kwargs", {}) or {}, context)
    try:
        common = proxy_factory(base + "/xmlrpc/2/common")
        uid = common.authenticate(db, user, pwd, {})
        obj = proxy_factory(base + "/xmlrpc/2/object")
        result = obj.execute_kw(db, uid, pwd, model, method, args, kwargs)
        return [[item({"uid": uid, "model": model, "method": method, "result": result})], []]
    except Exception as e:
        return [[], [item({"error": "{}: {}".format(type(e).__name__, e),
                           "model": model, "method": method})]]

registry.register(NodeDef("odoo.xmlrpc", "Odoo XML-RPC", ["main"], ["success", "error"],
                          run=execute,
                          params_schema={"credentialRef": "string", "model": "string",
                                         "method": "string", "args": "json", "kwargs": "json"}))
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm token-audit pytest tests/hub/test_node_xmlrpc.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add token_audit/hub/nodes/odoo_xmlrpc.py tests/hub/test_node_xmlrpc.py
git commit -m "feat(hub): Odoo XML-RPC node (authenticate + execute_kw, success/error)"
```

---

### Task 7: Set/Transform + Condition + Start nodes

**Files:**
- Create: `token_audit/hub/nodes/basic.py`
- Test: `tests/hub/test_nodes_basic.py`

**Interfaces:**
- Produces: registered types `"start"` (outputs `["main"]`), `"set"` (outputs `["main"]`), `"condition"` (outputs `["true","false"]`).
  - Start: emits one item = `params.get("seed", {})` (interpolated? no - literal JSON).
  - Set: `params["assignments"] = [{"name","expression"}]`; merges evaluated values into the item's json AND into `ctx.vars`.
  - Condition: `params["expression"]`; each input item routed to socket 0 (true) or 1 (false).

- [ ] **Step 1: Write the failing test**

Create `tests/hub/test_nodes_basic.py`:

```python
from token_audit.hub.nodes import basic
from token_audit.hub.engine import RunContext
from token_audit.hub.types import item

def test_start_emits_seed():
    out = basic.start_execute({"seed": {"deal_id": 5}}, [], RunContext())
    assert out == [[{"json": {"deal_id": 5}}]]

def test_set_merges_and_sets_vars():
    ctx = RunContext()
    out = basic.set_execute(
        {"assignments": [{"name": "discounted", "expression": "$json.price * 0.9"}]},
        [[item({"price": 100})]], ctx)
    assert out[0][0]["json"]["discounted"] == 90
    assert ctx.vars["discounted"] == 90

def test_condition_routes_true_false():
    ctx = RunContext()
    t = basic.condition_execute({"expression": "$json.status == 200"},
                                [[item({"status": 200})]], ctx)
    assert t[0] and not t[1]
    f = basic.condition_execute({"expression": "$json.status == 200"},
                                [[item({"status": 500})]], ctx)
    assert not f[0] and f[1]
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm token-audit pytest tests/hub/test_nodes_basic.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the basic nodes**

Create `token_audit/hub/nodes/basic.py`:

```python
"""Start, Set/Transform, Condition nodes."""
from typing import List
from token_audit.hub import registry, expr
from token_audit.hub.types import NodeDef, Item, item

def start_execute(params, inputs, ctx) -> List[List[Item]]:
    return [[item(params.get("seed", {}))]]

def set_execute(params, inputs, ctx) -> List[List[Item]]:
    out = []
    for it in (inputs[0] if inputs and inputs[0] else [item({})]):
        context = expr.build_context(it, ctx.node_outputs, ctx.vars, ctx.run_meta)
        merged = dict(it["json"])
        for a in params.get("assignments", []):
            val = expr.evaluate(a["expression"], context)
            merged[a["name"]] = val
            ctx.vars[a["name"]] = val
        out.append(item(merged))
    return [out]

def condition_execute(params, inputs, ctx) -> List[List[Item]]:
    truthy, falsy = [], []
    for it in (inputs[0] if inputs and inputs[0] else []):
        context = expr.build_context(it, ctx.node_outputs, ctx.vars, ctx.run_meta)
        (truthy if expr.evaluate(params["expression"], context) else falsy).append(it)
    return [truthy, falsy]

registry.register(NodeDef("start", "Start", [], ["main"], run=start_execute,
                          params_schema={"seed": "json"}))
registry.register(NodeDef("set", "Set / Transform", ["main"], ["main"], run=set_execute,
                          params_schema={"assignments": "list"}))
registry.register(NodeDef("condition", "Condition", ["main"], ["true", "false"],
                          run=condition_execute, params_schema={"expression": "string"}))
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm token-audit pytest tests/hub/test_nodes_basic.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add token_audit/hub/nodes/basic.py tests/hub/test_nodes_basic.py
git commit -m "feat(hub): Start, Set/Transform, Condition nodes"
```

---

### Task 8: Node loading + end-to-end flow test

**Files:**
- Create: `token_audit/hub/nodes/load.py`
- Test: `tests/hub/test_flow_e2e.py`

**Interfaces:**
- Produces: `load_all()` importing every node module so their `register(...)` calls run; called once at app startup and at test setup.

- [ ] **Step 1: Write the failing test**

Create `tests/hub/test_flow_e2e.py`:

```python
from token_audit.hub import engine
from token_audit.hub.nodes import load, http

def test_end_to_end_start_http_condition(monkeypatch):
    load.load_all()
    # fake HTTP transport: /login -> token, and status echo
    def transport(method, url, headers, body, timeout):
        if url.endswith("/login"):
            return 200, {}, {"token": "T"}
        return 200, {}, {"auth": headers.get("Authorization")}
    monkeypatch.setattr(http, "_urllib_transport", transport)

    flow = {
        "id": "e2e", "name": "login-then-call",
        "nodes": [
            {"id": "s", "type": "start", "label": "Start", "params": {"seed": {}}},
            {"id": "login", "type": "http", "label": "Login",
             "params": {"method": "POST", "url": "http://svc/login"}},
            {"id": "call", "type": "http", "label": "Call",
             "params": {"method": "GET", "url": "http://svc/me",
                        "headers": {"Authorization": "Bearer {{ $node.Login.json.json.token }}"}}},
        ],
        "connections": [
            {"from": ["s", 0], "to": ["login", 0]},
            {"from": ["login", 0], "to": ["call", 0]},   # success socket -> call
        ],
    }
    res = engine.run_flow(flow)
    assert res["status"] == "ok"
    assert res["nodes"]["call"]["status"] == "ok"
    assert res["nodes"]["call"]["outputsPerSocket"][0][0]["json"]["json"]["auth"] == "Bearer T"
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm token-audit pytest tests/hub/test_flow_e2e.py -v`
Expected: FAIL (module `load` not found).

- [ ] **Step 3: Implement the loader**

Create `token_audit/hub/nodes/load.py`:

```python
"""Import all node modules so their registry.register(...) side-effects run."""
def load_all():
    from token_audit.hub.nodes import http, odoo_xmlrpc, basic  # noqa: F401
```

Note: the HTTP node's `execute` default arg binds `_urllib_transport` at definition time; for the monkeypatch in the test to take effect, change `http.execute`'s signature default to `transport=None` and inside do `transport = transport or _urllib_transport`. Apply that one-line change to `http.py` now and re-run Task 5's tests to confirm still green.

- [ ] **Step 4: Run to verify it passes**

Run:
```bash
docker compose run --rm token-audit pytest tests/hub/test_node_http.py tests/hub/test_flow_e2e.py -v
```
Expected: PASS (both files).

- [ ] **Step 5: Commit**

```bash
git add token_audit/hub/nodes/load.py token_audit/hub/nodes/http.py tests/hub/test_flow_e2e.py
git commit -m "feat(hub): node loader + end-to-end login->call flow test"
```

---

### Task 9: Flow file store + credential store (SQLite)

**Files:**
- Create: `token_audit/hub/store.py` (flows as JSON files), `token_audit/hub/credentials.py` (SQLite)
- Test: `tests/hub/test_store.py`

**Interfaces:**
- Produces:
  - `store.list_flows(flows_dir) -> List[dict]` (id+name summaries), `store.load_flow(flows_dir, id)`, `store.save_flow(flows_dir, flow) -> dict`, `store.delete_flow(flows_dir, id)`.
  - `credentials.init(conn)`, `credentials.list(conn) -> List[dict]` (id/name/kind, NO secret), `credentials.get(conn, id) -> dict` (WITH secret, for the resolver), `credentials.upsert(conn, cred) -> dict`, `credentials.delete(conn, id)`.

- [ ] **Step 1: Write the failing test**

Create `tests/hub/test_store.py`:

```python
import sqlite3
from token_audit.hub import store, credentials

def test_flow_roundtrip(tmp_path):
    f = {"name": "My Flow", "nodes": [], "connections": []}
    saved = store.save_flow(str(tmp_path), f)
    assert saved["id"]
    assert store.load_flow(str(tmp_path), saved["id"])["name"] == "My Flow"
    assert any(x["id"] == saved["id"] for x in store.list_flows(str(tmp_path)))
    store.delete_flow(str(tmp_path), saved["id"])
    assert store.list_flows(str(tmp_path)) == []

def test_credentials_hide_secret_in_list():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    credentials.init(conn)
    c = credentials.upsert(conn, {"name": "Odoo local", "kind": "odoo",
                                  "data": {"base": "http://x", "db": "d",
                                           "user": "a", "secret": "p"}})
    listed = credentials.list(conn)
    assert listed[0]["name"] == "Odoo local"
    assert "data" not in listed[0] and "secret" not in str(listed[0])
    assert credentials.get(conn, c["id"])["data"]["secret"] == "p"
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm token-audit pytest tests/hub/test_store.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the stores**

Create `token_audit/hub/store.py`:

```python
"""Flows persisted as JSON files (git-shareable, no secrets)."""
import json, os, uuid
from typing import List

def _path(flows_dir, flow_id): return os.path.join(flows_dir, flow_id + ".json")

def list_flows(flows_dir: str) -> List[dict]:
    out = []
    if not os.path.isdir(flows_dir): return out
    for name in sorted(os.listdir(flows_dir)):
        if not name.endswith(".json"): continue
        try:
            with open(os.path.join(flows_dir, name)) as fh:
                f = json.load(fh)
            out.append({"id": f.get("id"), "name": f.get("name")})
        except (OSError, ValueError):
            continue
    return out

def load_flow(flows_dir: str, flow_id: str) -> dict:
    with open(_path(flows_dir, flow_id)) as fh:
        return json.load(fh)

def save_flow(flows_dir: str, flow: dict) -> dict:
    os.makedirs(flows_dir, exist_ok=True)
    if not flow.get("id"): flow["id"] = uuid.uuid4().hex
    tmp = _path(flows_dir, flow["id"]) + ".tmp"
    with open(tmp, "w") as fh: json.dump(flow, fh, indent=2)
    os.replace(tmp, _path(flows_dir, flow["id"]))
    return flow

def delete_flow(flows_dir: str, flow_id: str) -> None:
    p = _path(flows_dir, flow_id)
    if os.path.exists(p): os.remove(p)
```

Create `token_audit/hub/credentials.py`:

```python
"""Credentials in SQLite (secrets never leave the DB volume)."""
import json, uuid
from typing import List

def init(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS hub_credentials "
                 "(id TEXT PRIMARY KEY, name TEXT, kind TEXT, data TEXT)")
    conn.commit()

def list(conn) -> List[dict]:
    rows = conn.execute("SELECT id, name, kind FROM hub_credentials ORDER BY name").fetchall()
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"]} for r in rows]

def get(conn, cred_id: str) -> dict:
    r = conn.execute("SELECT id,name,kind,data FROM hub_credentials WHERE id=?",
                     (cred_id,)).fetchone()
    if not r: return None
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "data": json.loads(r["data"])}

def upsert(conn, cred: dict) -> dict:
    if not cred.get("id"): cred["id"] = uuid.uuid4().hex
    conn.execute("INSERT INTO hub_credentials (id,name,kind,data) VALUES (?,?,?,?) "
                 "ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
                 "data=excluded.data",
                 (cred["id"], cred["name"], cred["kind"], json.dumps(cred["data"])))
    conn.commit()
    return {"id": cred["id"], "name": cred["name"], "kind": cred["kind"]}

def delete(conn, cred_id: str) -> None:
    conn.execute("DELETE FROM hub_credentials WHERE id=?", (cred_id,)); conn.commit()
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm token-audit pytest tests/hub/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add token_audit/hub/store.py token_audit/hub/credentials.py tests/hub/test_store.py
git commit -m "feat(hub): flow file store + SQLite credential store (secret-safe)"
```

---

### Task 10: FastAPI routes + presets + credential resolver

**Files:**
- Modify: `token_audit/server.py` (add `hub` import + `/api/hub/*` routes, following the `/api/repo/*` pattern)
- Create: `token_audit/hub/presets.py`
- Test: `tests/hub/test_routes.py`

**Interfaces:**
- Consumes: `engine.run_flow`, `store.*`, `credentials.*`, `nodes.load.load_all`, `registry.all_defs`.
- Produces routes: `GET /api/hub/node-types`, `GET /api/hub/presets`, `GET /api/hub/flows`, `GET /api/hub/flows/{id}`, `POST /api/hub/flows`, `DELETE /api/hub/flows/{id}`, `GET /api/hub/credentials`, `POST /api/hub/credentials`, `DELETE /api/hub/credentials/{id}`, `POST /api/hub/run` (body: `{flow}` or `{flowId}`; returns `RunResult`).

- [ ] **Step 1: Write the failing test**

Create `tests/hub/test_routes.py`:

```python
import os
from fastapi.testclient import TestClient

def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_AUDIT_FLOWS_DIR", str(tmp_path))
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", "config.toml")
    from token_audit.server import create_app
    return TestClient(create_app())

def test_node_types_lists_registered(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    types = {n["type"] for n in c.get("/api/hub/node-types").json()}
    assert {"start", "http", "odoo.xmlrpc", "set", "condition"} <= types

def test_flow_crud_and_run(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    flow = {"name": "just-start",
            "nodes": [{"id": "s", "type": "start", "label": "Start",
                       "params": {"seed": {"hello": 1}}}],
            "connections": []}
    saved = c.post("/api/hub/flows", json=flow).json()
    assert saved["id"]
    run = c.post("/api/hub/run", json={"flowId": saved["id"]}).json()
    assert run["status"] == "ok"
    assert run["nodes"]["s"]["outputsPerSocket"][0][0]["json"] == {"hello": 1}
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm token-audit pytest tests/hub/test_routes.py -v`
Expected: FAIL (routes 404 / not found).

- [ ] **Step 3: Implement presets + routes**

Create `token_audit/hub/presets.py`:

```python
"""Base-URL presets for known local services (reached via host.docker.internal)."""
import os
def presets():
    host = os.environ.get("TOKEN_AUDIT_HUB_HOST", "host.docker.internal")
    return [
        {"key": "odoo", "label": "Odoo", "baseUrl": "http://%s:8069" % host},
        {"key": "odoo-shim", "label": "Odoo CE shim", "baseUrl": "http://%s:8179" % host},
        {"key": "discount", "label": "Discount Service", "baseUrl": "http://%s:8100" % host},
        {"key": "gorules", "label": "GoRules", "baseUrl": "http://%s:3100" % host},
        {"key": "flightdeck", "label": "FlightDeck", "baseUrl": "http://%s:8010" % host},
    ]
```

In `token_audit/server.py`: add `from token_audit.hub import (engine, store, credentials, presets as hub_presets, registry as hub_registry)` and `from token_audit.hub.nodes import load as hub_load`. In `create_app()`, after the write_conn is set up, call `hub_load.load_all()` and `credentials.init(write_conn)`, and define `flows_dir = os.environ.get("TOKEN_AUDIT_FLOWS_DIR") or os.path.join(os.path.dirname(__file__), "..", "flows")`. Add a resolver:

```python
    def _resolve_cred(ref):
        if not ref:
            return None
        c = _read_conn()
        try:
            cred = credentials.get(c, ref)
        finally:
            c.close()
        if not cred:
            return None
        d = dict(cred["data"]); d["kind"] = cred["kind"]; return d

    @app.get("/api/hub/node-types")
    def hub_node_types():
        return [{"type": d.type, "label": d.label, "inputs": d.inputs,
                 "outputs": d.outputs, "params": d.params_schema}
                for d in hub_registry.all_defs()]

    @app.get("/api/hub/presets")
    def hub_presets_ep():
        return hub_presets.presets()

    @app.get("/api/hub/flows")
    def hub_flows():
        return store.list_flows(flows_dir)

    @app.get("/api/hub/flows/{flow_id}")
    def hub_flow_get(flow_id: str):
        try:
            return store.load_flow(flows_dir, flow_id)
        except (OSError, ValueError):
            raise HTTPException(status_code=404, detail="flow not found")

    @app.post("/api/hub/flows")
    def hub_flow_save(flow: dict):
        return store.save_flow(flows_dir, flow)

    @app.delete("/api/hub/flows/{flow_id}")
    def hub_flow_delete(flow_id: str):
        store.delete_flow(flows_dir, flow_id); return {"ok": True}

    @app.get("/api/hub/credentials")
    def hub_creds():
        c = _read_conn()
        try:
            return credentials.list(c)
        finally:
            c.close()

    @app.post("/api/hub/credentials")
    def hub_cred_save(cred: dict):
        return credentials.upsert(write_conn, cred)

    @app.delete("/api/hub/credentials/{cred_id}")
    def hub_cred_delete(cred_id: str):
        credentials.delete(write_conn, cred_id); return {"ok": True}

    @app.post("/api/hub/run")
    def hub_run(body: dict):
        flow = body.get("flow")
        if not flow and body.get("flowId"):
            flow = store.load_flow(flows_dir, body["flowId"])
        if not flow:
            raise HTTPException(status_code=400, detail="flow or flowId required")
        return engine.run_flow(flow, seed=body.get("seed"),
                               resolve_credential=_resolve_cred)
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm token-audit pytest tests/hub/test_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add token_audit/server.py token_audit/hub/presets.py tests/hub/test_routes.py
git commit -m "feat(hub): /api/hub routes (node-types, presets, flows, credentials, run)"
```

---

### Task 11: Compose wiring + live smoke against a real service

**Files:**
- Modify: `docker-compose.yml` (flows mount + env + extra_hosts)
- Create: `flows/.gitkeep`
- Test: `tests/hub/test_smoke_live.py` (marked, opt-in)

**Interfaces:**
- Produces: the running container serves `/api/hub/*`; `flows/` is a git-tracked dir holding saved flows.

- [ ] **Step 1: Add compose wiring**

In `docker-compose.yml` under `token-audit`:
- `environment:` add `TOKEN_AUDIT_FLOWS_DIR: /app/flows`
- `volumes:` add `- ./flows:/app/flows` (read-write; flows are saved here)
- add (if Task 1 Step 5 showed it was needed):
  ```yaml
      extra_hosts:
        - "host.docker.internal:host-gateway"
  ```

Create `flows/.gitkeep` (empty) so the dir is tracked.

- [ ] **Step 2: Write an opt-in live smoke test**

Create `tests/hub/test_smoke_live.py`:

```python
import os, pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("HUB_LIVE") != "1",
    reason="live smoke; set HUB_LIVE=1 with services up")

def test_discount_service_health_via_hub():
    from token_audit.hub import engine
    from token_audit.hub.nodes import load
    load.load_all()
    flow = {"id": "smoke", "name": "discount-health",
            "nodes": [
                {"id": "s", "type": "start", "label": "Start", "params": {"seed": {}}},
                {"id": "h", "type": "http", "label": "Health",
                 "params": {"method": "GET",
                            "url": "http://host.docker.internal:8100/health"}}],
            "connections": [{"from": ["s", 0], "to": ["h", 0]}]}
    res = engine.run_flow(flow)
    assert res["nodes"]["h"]["status"] == "ok"
```

- [ ] **Step 3: Recreate the container**

Run:
```bash
docker compose up -d token-audit
curl -s -m 8 http://127.0.0.1:8010/api/hub/node-types | head -c 200
```
Expected: JSON array including `"type":"http"`. (Adjust the Discount Service health path in Step 2 if its endpoint differs; confirm the real path first.)

- [ ] **Step 4: Run the full hub suite in-container + the live smoke**

Run:
```bash
docker compose run --rm token-audit pytest tests/hub -v
docker compose run --rm -e HUB_LIVE=1 token-audit pytest tests/hub/test_smoke_live.py -v
```
Expected: full suite PASS; live smoke PASS if the Discount Service is up (else skipped).

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml flows/.gitkeep tests/hub/test_smoke_live.py
git commit -m "feat(hub): compose wiring (flows mount, host-gateway) + live smoke test"
```

---

## Self-Review

**Spec coverage** (against `2026-07-16-integration-hub-design.md`):
- §3 execution model (item routing, `Item[][]`, connections, trace) -> Task 4. ✓
- §3 expression context ($json/$node/$vars/$run, ZEN) -> Task 3. ✓
- §4 nodes: HTTP -> T5, XML-RPC -> T6, Set + Condition + Start -> T7. ✓
- §4 credentials out of flow (SQLite) -> Task 9; resolver + injection -> T5/T10. ✓
- §4 presets + host.docker.internal reachability -> T10/T11 + T1 spike. ✓
- §5 flows as files, credentials/history in SQLite, RunResult -> T9/T4/T10. (Run *history* persistence is phase 2 - not in this plan; noted.) ✓
- §9 backend under `token_audit/hub/`, `/api/hub/*`, Diff-tool pattern, on-demand + timeouts -> T5 timeout, T10 routes. ✓
- §10 open items: ZEN binding -> T1 verify; host.docker.internal -> T1/T11. ✓
- Canvas (§6) and assertions/history (§7 phase 2) -> **out of scope for Plan A** (Plan B / later). Intentional.

**Placeholder scan:** no TBD/TODO; every code step has real code; edge behavior (empty socket, error socket) is concretely tested. One deliberate implementer note in Task 8 (change `transport` default to `None`) is spelled out with the exact change.

**Type consistency:** `run(params, inputs, ctx) -> List[List[Item]]` used uniformly; `Item = {"json": {...}}` throughout; `RunContext` attributes (`node_outputs`, `vars`, `run_meta`, `resolve_credential`) consistent between T4 (definition), T5/T6/T7 (consumers); credential dict shape (`{kind, base, db, user, secret}` for odoo; `{kind:"bearer", token}` for http) consistent between T5/T6 (consumers) and T9/T10 (resolver returns `data` merged with `kind`).

**Gap fixed during review:** the credential resolver must merge `kind` into the returned data dict so nodes can branch on `cred["kind"]` — encoded in T10 `_resolve_cred` (`d["kind"] = cred["kind"]`).

## Notes for the implementer

- Run everything **in-container** (`docker compose run --rm token-audit pytest ...`) so the Python 3.7 runtime + `zen-engine` match production.
- If Task 1 reveals `zen`'s expression API differs, fix `expr.py` (Task 3) once; all downstream nodes consume `expr.evaluate`/`expr.interpolate`, so they are insulated.
- Keep each node's network/RPC call behind an injectable transport/factory (done in T5/T6) so the suite never needs live services.
