"""Item-routing flow engine. Runs nodes when their inputs are ready, routes each
node's per-socket output items along connections, records a per-node trace."""
import time
import traceback
import uuid
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional
from flightdeck.hub import registry
from flightdeck.hub.types import Item

_MAX_NODE_RUNS = 1000  # defensive backstop only; termination is guaranteed
                        # because each node is enqueued at most once (real
                        # cycles are rejected up front by _find_cycle_nodes)

class RunContext:
    def __init__(self, resolve_credential: Optional[Callable] = None):
        self.node_outputs: Dict[str, dict] = {}   # nodeLabel/id -> first output item
        self.vars: Dict[str, Any] = {}
        self.run_meta: Dict[str, Any] = {}
        self._resolve = resolve_credential or (lambda ref: None)
    def resolve_credential(self, ref):
        return self._resolve(ref)

def _find_cycle_nodes(node_ids, connections) -> List[str]:
    """Node-level cycle check over the directed connections graph (Kahn's
    algorithm). Returns the ids left with unresolved indegree once no more
    zero-indegree nodes remain to peel off -- empty list means acyclic."""
    node_ids = list(node_ids)
    indegree: Dict[str, int] = {n_id: 0 for n_id in node_ids}
    adjacency: Dict[str, List[str]] = defaultdict(list)
    for c in connections:
        from_id, to_id = c["from"][0], c["to"][0]
        adjacency[from_id].append(to_id)
        indegree[to_id] = indegree.get(to_id, 0) + 1
    queue = [n_id for n_id in node_ids if indegree[n_id] == 0]
    visited = 0
    while queue:
        n_id = queue.pop()
        visited += 1
        for m_id in adjacency.get(n_id, []):
            indegree[m_id] -= 1
            if indegree[m_id] == 0:
                queue.append(m_id)
    if visited == len(node_ids):
        return []
    return sorted(n_id for n_id in node_ids if indegree[n_id] != 0)

def run_flow(flow: dict, seed: Optional[dict] = None,
             resolve_credential: Optional[Callable] = None) -> dict:
    nodes = {n["id"]: n for n in flow["nodes"]}
    trace: Dict[str, dict] = {n_id: {"status": "skipped", "inputs": None,
                                     "outputsPerSocket": None, "timeMs": None,
                                     "error": None} for n_id in nodes}
    ctx = RunContext(resolve_credential)
    ctx.run_meta = {"id": uuid.uuid4().hex, "flowId": flow.get("id")}

    cyclic_nodes = _find_cycle_nodes(nodes.keys(), flow["connections"])
    if cyclic_nodes:
        return {"runId": ctx.run_meta["id"], "flowId": flow.get("id"),
                "startedAt": None, "finishedAt": None, "status": "error",
                "order": [], "nodes": trace,
                "error": f"flow has a cycle: {', '.join(cyclic_nodes)}"}

    # inbox[nodeId][inSocket] -> accumulated items
    inbox: Dict[str, Dict[int, List[Item]]] = defaultdict(lambda: defaultdict(list))
    # outgoing[(nodeId, outSocket)] -> list of (toNodeId, toSocket)
    outgoing: Dict[Any, List] = defaultdict(list)
    indegree: Dict[str, int] = defaultdict(int)
    for c in flow["connections"]:
        outgoing[(c["from"][0], c["from"][1])].append((c["to"][0], c["to"][1]))
        indegree[c["to"][0]] += 1

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
        t0 = time.time()
        inputs = None
        try:
            ndef = registry.get(node["type"])
            inputs = [inbox[n_id].get(i, []) for i in range(max(1, len(ndef.inputs)))]
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
            "order": order, "nodes": trace, "error": None}
