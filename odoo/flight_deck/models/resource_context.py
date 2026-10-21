import json
from datetime import datetime, timezone

from .resource_policy import RESOURCE_SNAPSHOT_TTL_SECONDS
from .resource_prompts import RESOURCE_CHAT_CONTEXT_PREFIX

PROCESS_CONTEXT_FIELDS = (
    "key", "pid", "parent_pid", "uid", "start_ticks", "namespace", "comm",
    "state", "exe_identity", "harness_evidence", "age_seconds", "rss_bytes", "fd_count", "socket_count",
    "harness", "role", "owner_key", "session_id", "runtime_state", "managed",
    "orphan", "protected", "manageable", "protection_reason", "warnings", "stdio_peer_key",
)
MCP_CONTEXT_FIELDS = (
    "key", "owner_key", "session_id", "name", "status", "type", "protected",
)
SOCKET_CONTEXT_FIELDS = ("inode", "protocol", "state")
RESOURCE_CONTEXT_PROCESSES = 50
RESOURCE_CONTEXT_MCP_SERVERS = 100
RESOURCE_CONTEXT_SOCKETS = 10


def resource_snapshot_fresh(snapshot, now=None):
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        return False
    if snapshot.get("error") or snapshot.get("available") is not True or snapshot.get("complete") is not True:
        return False
    if not isinstance(snapshot.get("boot_id"), str) or not snapshot["boot_id"]:
        return False
    if not isinstance(snapshot.get("namespace"), str) or not snapshot["namespace"]:
        return False
    if not isinstance(snapshot.get("processes"), list):
        return False
    try:
        taken = datetime.fromisoformat(snapshot["taken_at"].replace("Z", "+00:00"))
        if taken.tzinfo is None:
            return False
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        age = (now - taken).total_seconds()
        return 0 <= age <= RESOURCE_SNAPSHOT_TTL_SECONDS
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


def _resource_context_fields(row, fields):
    return {field: row[field] for field in fields if field in row}


def _resource_context_rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def build_resource_chat_prompt(snapshot, text, selected_keys=None):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    processes = [row for row in _resource_context_rows(snapshot.get("processes"))
                 if isinstance(row.get("key"), str)]
    requested = list(dict.fromkeys(selected_keys or []))
    available = {row["key"] for row in processes}
    scoped = [row for row in processes if row["key"] in requested] if requested else processes
    included = []
    for row in scoped[:RESOURCE_CONTEXT_PROCESSES]:
        entry = _resource_context_fields(row, PROCESS_CONTEXT_FIELDS)
        sockets = _resource_context_rows(row.get("sockets"))
        entry["sockets"] = [
            _resource_context_fields(socket, SOCKET_CONTEXT_FIELDS)
            for socket in sockets[:RESOURCE_CONTEXT_SOCKETS]
        ]
        entry["omitted_socket_observations"] = max(0, len(sockets) - RESOURCE_CONTEXT_SOCKETS)
        included.append(entry)
    servers = _resource_context_rows(snapshot.get("mcp_servers"))
    context = {
        "taken_at": snapshot.get("taken_at"),
        "boot_id": snapshot.get("boot_id"),
        "namespace": snapshot.get("namespace"),
        "stale": not resource_snapshot_fresh(snapshot),
        "error": snapshot.get("error"),
        "warnings": snapshot.get("warnings", []),
        "selected_keys": requested,
        "missing_selected_keys": [key for key in requested if key not in available],
        "observed_process_count": len(processes) if isinstance(snapshot.get("processes"), list) else None,
        "included_process_count": len(included),
        "omitted_scoped_processes": max(0, len(scoped) - RESOURCE_CONTEXT_PROCESSES),
        "processes": included,
        "mcp_servers": [
            _resource_context_fields(row, MCP_CONTEXT_FIELDS)
            for row in servers[:RESOURCE_CONTEXT_MCP_SERVERS]
        ],
        "omitted_mcp_servers": max(0, len(servers) - RESOURCE_CONTEXT_MCP_SERVERS),
    }
    return (RESOURCE_CHAT_CONTEXT_PREFIX + json.dumps(context, ensure_ascii=False, separators=(",", ":")) +
            "\n\nOperator request:\n" + text)
