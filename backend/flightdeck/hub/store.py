"""Flows persisted as JSON files (git-shareable, no secrets)."""
import json
import os
import re
import uuid
from typing import List, Optional

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_id(flow_id):
    """Reject anything that is not a safe slug (no path separators, no dot-prefix,
    no absolute paths) so a client-controlled id can never escape flows_dir."""
    if not isinstance(flow_id, str) or not _SAFE_ID.match(flow_id):
        raise ValueError("invalid flow id: %r" % (flow_id,))
    return flow_id


def _path(flows_dir: str, flow_id: str) -> str:
    return os.path.join(flows_dir, _validate_id(flow_id) + ".json")


def list_flows(flows_dir: str) -> List[dict]:
    """Return [{"id", "name"}, ...] summaries for every flow file, sorted by filename."""
    out = []
    if not os.path.isdir(flows_dir):
        return out
    for name in sorted(os.listdir(flows_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(flows_dir, name)) as fh:
                f = json.load(fh)
            out.append({"id": f.get("id"), "name": f.get("name")})
        except (OSError, ValueError):
            continue
    return out


def load_flow(flows_dir: str, flow_id: str) -> Optional[dict]:
    """Return the full flow dict, or None if flow_id is valid but no file exists
    for it (callers, e.g. the HTTP route, should map None -> 404). An invalid
    (unsafe) flow_id still raises ValueError from _path/_validate_id -- that is
    a client error, not a "not found"."""
    path = _path(flows_dir, flow_id)
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def save_flow(flows_dir: str, flow: dict) -> dict:
    """Assign an id if missing, write atomically (tmp file + rename), return the flow."""
    os.makedirs(flows_dir, exist_ok=True)
    if not flow.get("id"):
        flow["id"] = uuid.uuid4().hex
    dest = _path(flows_dir, flow["id"])
    tmp = dest + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(flow, fh, indent=2)
    os.replace(tmp, dest)
    return flow


def delete_flow(flows_dir: str, flow_id: str) -> None:
    """Remove the flow file if present; no-op if it does not exist."""
    path = _path(flows_dir, flow_id)
    if os.path.exists(path):
        os.remove(path)
