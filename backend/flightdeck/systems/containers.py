"""Hangar — Docker container board (READ-ONLY).

Talks to the Docker Engine API over the unix socket (TOKEN_AUDIT_DOCKER_SOCK,
mounted :ro). v1 is strictly read-only: list containers grouped by compose
project, status/uptime/ports. No start/stop/kill endpoints — adding any
mutating action later must follow the fail-closed guard rules in CLAUDE.md.

STDLIB ONLY: the Docker Engine API is spoken over the unix socket with
http.client + a tiny HTTPConnection subclass. No docker/requests deps (the
image installs only what the Dockerfile pip-installs).
"""
import http.client
import json
import os
import socket
import time

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/systems", tags=["systems"])

# Docker Engine API version to pin the request path to. 1.41 ships with
# Docker 20.10+ (well below what runs here); the daemon negotiates down.
_API_VERSION = "v1.41"
_TIMEOUT = 4.0


def _sock_candidates():
    """Ordered socket paths to try. Explicit env wins; then the standard
    daemon socket; then the Docker Desktop (Linux) per-user socket used in
    demo.sh / host mode where /var/run/docker.sock is absent."""
    env = os.environ.get("TOKEN_AUDIT_DOCKER_SOCK")
    cands = []
    if env:
        cands.append(env)
    else:
        cands.append("/var/run/docker.sock")
    # Fallback for Docker Desktop on Linux (host / demo.sh mode).
    cands.append(os.path.expanduser("~/.docker/desktop/docker.sock"))
    # De-dupe, preserve order.
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _resolve_sock():
    """First candidate socket path that exists (S_ISSOCK not required — a
    mounted socket may not stat as one across some mounts). Returns None if
    none are present."""
    for path in _sock_candidates():
        if os.path.exists(path):
            return path
    return None


class _UnixHTTPConnection(http.client.HTTPConnection):
    """http.client connection over an AF_UNIX socket. The Docker Engine API
    speaks plain HTTP/1.1 over the socket, so only the transport differs."""

    def __init__(self, unix_path, timeout=_TIMEOUT):
        super().__init__("localhost", timeout=timeout)
        self._unix_path = unix_path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._unix_path)
        self.sock = s


def _get(sock_path, path):
    """GET a Docker Engine API path, return parsed JSON. Raises on transport
    or HTTP error (caller degrades those to available:false)."""
    conn = _UnixHTTPConnection(sock_path)
    try:
        conn.request("GET", f"/{_API_VERSION}{path}",
                     headers={"Host": "docker", "Accept": "application/json"})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"docker api {path} -> HTTP {resp.status}")
        if not body:
            return None
        return json.loads(body)
    finally:
        conn.close()


def _human_uptime(seconds):
    """Compact humanized duration: 3d 4h / 5h 12m / 3m / 45s."""
    seconds = int(max(0, seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h}h" if h else f"{d}d"
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    if m:
        return f"{m}m"
    return f"{s}s"


def _clean_name(names):
    """Container names arrive as ["/name"]; strip the leading slash."""
    if not names:
        return ""
    n = names[0]
    return n[1:] if n.startswith("/") else n


def _ports(port_list):
    """Published host->container ports only (PublicPort present). De-duped,
    sorted by host port. IPv4/IPv6 duplicates on the same host port collapse."""
    out, seen = [], set()
    for p in port_list or []:
        pub = p.get("PublicPort")
        if pub is None:
            continue
        priv = p.get("PrivatePort")
        proto = p.get("Type", "tcp")
        key = (pub, priv, proto)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "host": pub,
            "container": priv,
            "proto": proto,
            "label": f"{pub}→{priv}" if priv != pub else str(pub),
        })
    out.sort(key=lambda x: (x["host"], x["container"]))
    return out


def _health(status_str):
    """Extract a health token from the human status string if present, e.g.
    'Up 3 hours (healthy)' -> 'healthy'. None when absent."""
    if not status_str:
        return None
    lower = status_str.lower()
    for token in ("healthy", "unhealthy", "starting"):
        if f"({token})" in lower or token in lower:
            return token
    return None


def _short_id(cid):
    return (cid or "")[:12]


def _build_container(c):
    labels = c.get("Labels") or {}
    state = (c.get("State") or "").lower()
    status = c.get("Status") or ""
    created = c.get("Created")
    uptime = ""
    if created:
        # For running containers, uptime since creation is a fair proxy; the
        # human Status string ("Up 3 hours") is the authoritative display.
        uptime = _human_uptime(time.time() - created)
    return {
        "id": _short_id(c.get("Id")),
        "name": _clean_name(c.get("Names")),
        "image": c.get("Image") or "",
        "state": state,
        "status": status,
        "created": created,
        "uptime": uptime,
        "ports": _ports(c.get("Ports")),
        "project": labels.get("com.docker.compose.project"),
        "service": labels.get("com.docker.compose.service"),
        "health": _health(status),
    }


def _group(containers):
    """Group by compose project; label-less containers go to 'standalone'.
    Groups sorted by name; running containers first within each group."""
    groups = {}
    for c in containers:
        key = c["project"] or "standalone"
        groups.setdefault(key, []).append(c)

    def _row_sort(row):
        # running first (0), everything else after (1); then by name.
        return (0 if row["state"] == "running" else 1, row["name"].lower())

    out = []
    for name in sorted(groups.keys(), key=lambda k: (k == "standalone", k.lower())):
        rows = sorted(groups[name], key=_row_sort)
        out.append({
            "project": name,
            "standalone": name == "standalone",
            "containers": rows,
            "total": len(rows),
            "running": sum(1 for r in rows if r["state"] == "running"),
        })
    return out


@router.get("/containers")
def containers_overview():
    """Read-only container board. Never 500s on a missing/unreachable daemon —
    returns 200 with {available:false, reason} so the UI can show guidance."""
    sock_path = _resolve_sock()
    if not sock_path:
        return {
            "available": False,
            "reason": "docker socket not found",
            "detail": ("No Docker socket at any known path. If running in the "
                       "container, mount the socket and force-recreate."),
            "checked": _sock_candidates(),
        }
    try:
        raw = _get(sock_path, "/containers/json?all=true")
    except Exception as e:  # transport / HTTP / decode — degrade gracefully.
        return {
            "available": False,
            "reason": "docker socket unreachable",
            "detail": str(e),
            "socket": sock_path,
        }
    if raw is None:
        raw = []

    version = None
    try:
        ver = _get(sock_path, "/version")
        if ver:
            version = ver.get("Version")
    except Exception:
        version = None  # non-fatal; list already succeeded.

    containers = [_build_container(c) for c in raw]
    groups = _group(containers)
    return {
        "available": True,
        "socket": sock_path,
        "summary": {
            "total": len(containers),
            "running": sum(1 for c in containers if c["state"] == "running"),
            "exited": sum(1 for c in containers if c["state"] == "exited"),
            "docker_version": version,
        },
        "projects": groups,
    }


@router.get("/containers/{cid}/stats")
def container_stats(cid: str):
    """Lazy single-container resource snapshot (CPU %, memory). One
    non-streaming stats call is ~100-300ms, so this is fetched by the UI only
    on row expand — never for the whole list. Read-only. Degrades to
    available:false like the list endpoint."""
    if not cid or len(cid) > 64 or not cid.isalnum():
        raise HTTPException(status_code=400, detail="invalid container id")
    sock_path = _resolve_sock()
    if not sock_path:
        return {"available": False, "reason": "docker socket not found"}
    try:
        s = _get(sock_path, f"/containers/{cid}/stats?stream=false")
    except Exception as e:
        return {"available": False, "reason": "docker socket unreachable",
                "detail": str(e)}
    if not s:
        return {"available": False, "reason": "no stats"}

    # CPU %: delta of container CPU over delta of system CPU, scaled by #cpus.
    cpu_pct = None
    try:
        cpu = s.get("cpu_stats", {})
        pre = s.get("precpu_stats", {})
        cd = cpu.get("cpu_usage", {}).get("total_usage", 0) - \
            pre.get("cpu_usage", {}).get("total_usage", 0)
        sd = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        ncpu = cpu.get("online_cpus") or len(
            cpu.get("cpu_usage", {}).get("percpu_usage") or []) or 1
        if sd > 0 and cd > 0:
            cpu_pct = round((cd / sd) * ncpu * 100.0, 2)
    except Exception:
        cpu_pct = None

    mem = s.get("memory_stats", {})
    mem_usage = mem.get("usage")
    # Docker subtracts cache from the displayed usage; mirror `docker stats`.
    cache = (mem.get("stats") or {}).get("cache", 0)
    if isinstance(mem_usage, int):
        mem_usage = max(0, mem_usage - cache)
    mem_limit = mem.get("limit")
    mem_pct = None
    if mem_usage and mem_limit:
        mem_pct = round((mem_usage / mem_limit) * 100.0, 2)

    return {
        "available": True,
        "id": _short_id(cid),
        "cpu_pct": cpu_pct,
        "mem_usage": mem_usage,
        "mem_limit": mem_limit,
        "mem_pct": mem_pct,
    }
