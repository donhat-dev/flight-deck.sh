"""Charts endpoints: the NAKIVO module dependency graph feed."""
import json
import os

from fastapi import APIRouter, Request

router = APIRouter(tags=["charts"])


@router.get("/api/graph")
def graph_endpoint(request: Request):
    # Serves the NAKIVO module dependency graph produced by nakivo-graph/ingest.py.
    # Path via TOKEN_AUDIT_GRAPH_FILE; falls back to the sibling project for local
    # dev. mtime-cached so re-running the ingester is picked up without a restart.
    path = os.environ.get("TOKEN_AUDIT_GRAPH_FILE")
    if not path:
        cand = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "nakivo-graph", "nakivo-graph.json")
        path = cand if os.path.exists(cand) else None
    if not path or not os.path.exists(path):
        return {"available": False}
    try:
        mt = os.path.getmtime(path)
        cache = request.app.state.graph_cache
        if cache.get("mtime") != mt:
            with open(path) as f:
                cache["data"] = json.load(f)
            cache["mtime"] = mt
        return {"available": True, **cache["data"]}
    except Exception:
        return {"available": False}
