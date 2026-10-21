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
        # Compose records the files and directory that produced this container.
        # They are what lets a running stack be matched to a compose file on
        # disk, so the board can also list the stacks that are fully down.
        "config_files": [
            f for f in (labels.get("com.docker.compose.project.config_files") or "").split(",")
            if f
        ],
        "working_dir": labels.get("com.docker.compose.project.working_dir") or None,
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
