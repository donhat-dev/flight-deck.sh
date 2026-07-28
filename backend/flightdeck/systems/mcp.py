"""Comms — MCP server registry + usage.

Reads the global MCP registry (~/.claude.json `mcpServers`, container path via
TOKEN_AUDIT_CLAUDE_JSON) plus per-project .mcp.json files under the workspace,
and joins them with the ledger's tool_calls table (server column) so the UI can
show which servers are actually used and how much.

Response shape (GET /api/systems/mcp?range=today|7d|30d|all):
{
  "range": "7d",
  "generated_at": "<iso>",
  "registry": {
    "global_path": "/root/.claude.json",
    "global_mtime": 1784602818.17,          # epoch seconds, or null
    "global_mtime_iso": "<iso>",            # for the "registry as of ..." note
    "project_files": [{"path", "scope", "mtime"}]
  },
  "servers": [{
    "name", "scope" (primary badge), "scopes" [..],
    "transport" ("stdio"|"http"|"sse"|"unknown"),
    "command", "url",
    "registered" (bool), "status" ("used"|"idle"|"unregistered"),
    "calls", "sessions", "first_used", "last_used",
    "top_tools": [{"tool", "calls"}]
  }],
  "totals": {"servers", "registered", "used", "unregistered_used", "calls"},
  "warnings": [..]
}
Never raises to a 500 on missing/unreadable files: those degrade to an empty
registry plus a `warnings` entry.
"""
import json
import os
import re
import subprocess
import threading
import time
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request

from flightdeck import db

router = APIRouter(prefix="/api/systems", tags=["systems"])


def _since(range_key: str):
    """UI range -> ISO date cutoff (calendar-based). None = all. Kept local to
    this module on purpose (server.py has its own copy; don't couple them)."""
    today = date.today()
    if range_key == "today":
        return today.isoformat()
    if range_key == "7d":
        return (today - timedelta(days=6)).isoformat()
    if range_key == "30d":
        return (today - timedelta(days=29)).isoformat()
    return None  # "all" or unknown


def _claude_json_path() -> str:
    return os.environ.get("TOKEN_AUDIT_CLAUDE_JSON") or os.path.expanduser(
        "~/.claude.json")


def _workspace_root() -> str:
    # Same default as repodiff.WORKSPACE: parent of token-audit/ on the host,
    # overridable for the container mount via FLIGHTDECK_WORKSPACE.
    return os.path.realpath(
        os.environ.get("FLIGHTDECK_WORKSPACE")
        or os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _transport(cfg: dict) -> dict:
    """Normalize a raw mcpServers entry into (transport, command, url)."""
    if not isinstance(cfg, dict):
        return {"transport": "unknown", "command": None, "url": None}
    url = cfg.get("url")
    if url:
        # Explicit type wins ("http"/"sse"); else infer sse from the path.
        t = cfg.get("type")
        if t not in ("http", "sse"):
            t = "sse" if str(url).rstrip("/").endswith("/sse") else "http"
        return {"transport": t, "command": None, "url": url}
    cmd = cfg.get("command")
    if cmd:
        args = cfg.get("args") or []
        full = " ".join([cmd, *[str(a) for a in args]]) if args else cmd
        return {"transport": cfg.get("type") or "stdio",
                "command": full, "url": None}
    return {"transport": cfg.get("type") or "unknown",
            "command": None, "url": None}


def _parse_registry(path: str, warnings: list) -> dict:
    """Return {name: transport-dict} from a file's `mcpServers` object. Missing
    file -> {} silently (caller decides if that is worth a warning); unreadable
    or malformed -> {} + a warning."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        warnings.append(f"could not read {path}: {e}")
        return {}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return {}
    out = {}
    for name, cfg in servers.items():
        out[name] = _transport(cfg)
    return out


def _mtime(path: str):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _load_registries(warnings: list):
    """Global (~/.claude.json) + per-project (.mcp.json) registries.

    Returns (registry, global_meta, project_files) where registry maps
    name -> {"scopes": set, "transport", "command", "url"} (global wins for the
    transport fields when a name is registered in both places)."""
    registry = {}

    def _merge(name, info, scope):
        entry = registry.get(name)
        if entry is None:
            registry[name] = {
                "scopes": [scope], "transport": info["transport"],
                "command": info["command"], "url": info["url"]}
        else:
            if scope not in entry["scopes"]:
                entry["scopes"].append(scope)

    # Global
    gpath = _claude_json_path()
    gmtime = _mtime(gpath)
    if not os.path.exists(gpath):
        warnings.append(f"global registry not found at {gpath}")
    for name, info in _parse_registry(gpath, warnings).items():
        _merge(name, info, "global")
    global_meta = {
        "global_path": gpath,
        "global_mtime": gmtime,
        "global_mtime_iso": (datetime.fromtimestamp(gmtime).isoformat()
                             if gmtime else None),
    }

    # Per-project .mcp.json: <ws>/.mcp.json + <ws>/*/.mcp.json (depth 1 only).
    ws = _workspace_root()
    project_files = []
    candidates = [os.path.join(ws, ".mcp.json")]
    try:
        for entry in sorted(os.listdir(ws)):
            sub = os.path.join(ws, entry)
            if os.path.isdir(sub) and not entry.startswith("."):
                candidates.append(os.path.join(sub, ".mcp.json"))
    except OSError as e:
        warnings.append(f"could not scan workspace {ws}: {e}")
    for path in candidates:
        if not os.path.exists(path):
            continue
        parent = os.path.dirname(path)
        scope = "workspace" if parent == ws else os.path.basename(parent)
        found = _parse_registry(path, warnings)
        project_files.append(
            {"path": path, "scope": scope, "mtime": _mtime(path)})
        for name, info in found.items():
            _merge(name, info, scope)

    return registry, global_meta, project_files


def _usage(db_path: str, since):
    """Aggregate tool_calls per MCP server. Returns {server: {...}}."""
    conn = db.open_read(db_path)
    try:
        where = "server IS NOT NULL"
        params = []
        if since:
            where += " AND ts >= ?"
            params.append(since)
        agg = conn.execute(
            f"SELECT server, COUNT(*) AS calls, "
            f"COUNT(DISTINCT session_id) AS sessions, "
            f"MIN(ts) AS first_used, MAX(ts) AS last_used "
            f"FROM tool_calls WHERE {where} GROUP BY server", params).fetchall()
        usage = {r["server"]: {
            "calls": r["calls"], "sessions": r["sessions"],
            "first_used": r["first_used"], "last_used": r["last_used"],
            "top_tools": []} for r in agg}
        # Top tools per server: rank by call count, take 5 in Python. `detail`
        # is the short tool name (mcp__server__<detail>); fall back to `tool`.
        tools = conn.execute(
            f"SELECT server, COALESCE(detail, tool) AS t, COUNT(*) AS c "
            f"FROM tool_calls WHERE {where} "
            f"GROUP BY server, t ORDER BY server, c DESC", params).fetchall()
        for r in tools:
            slot = usage.get(r["server"])
            if slot is not None and len(slot["top_tools"]) < 5:
                slot["top_tools"].append({"tool": r["t"], "calls": r["c"]})
        return usage
    finally:
        conn.close()


# --- live health via `claude mcp list` --------------------------------------
# `claude mcp list` probes every configured server for real (it spawns each
# stdio server to test the handshake, so it takes seconds and must be cached,
# never run per-request). Output lines look like:
#   "chrome-devtools: npx chrome-devtools-mcp@latest ... - <STATUS>"
#   "claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - <STATUS>"
# where STATUS is one of the ✔/✘/! variants below. It reflects THIS host's
# registry (global + connectors); project-scoped servers resolved elsewhere
# (e.g. odoo-debugger) simply won't appear here — the usage rows still show
# them from history.
_HEALTH_TTL = 60.0            # seconds a probe result stays fresh
_HEALTH_TIMEOUT = 45.0        # hard cap on the subprocess (probe is slow)
_health_lock = threading.Lock()
_health_cache = {"map": None, "checked_at": None, "error": None}


def _server_slug(name: str) -> str:
    """Map a `claude mcp list` display name to the tool_calls `server` slug.

    Tool names arrive as mcp__<slug>__<tool>; for connectors the slug replaces
    "." and " " with "_" ("claude.ai Google Calendar" -> "claude_ai_Google_
    Calendar"), while plain stdio names pass through ("chrome-devtools")."""
    return name.replace(".", "_").replace(" ", "_")


def _parse_mcp_list(text: str) -> dict:
    """Parse `claude mcp list` into {name: {target, status_text, state}}."""
    out = {}
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line or line.startswith("Checking") or ": " not in line \
                or " - " not in line:
            continue
        left, _, status = line.rpartition(" - ")
        name, _, target = left.partition(": ")
        name = name.strip()
        s = status.strip()
        low = s.lower()
        if "connected" in low:
            state = "connected"
        elif "fail" in low:
            state = "failed"
        elif "auth" in low:
            state = "needs_auth"
        else:
            state = "unknown"
        out[name] = {"target": target.strip(), "status_text": s,
                     "state": state, "slug": _server_slug(name)}
    return out


def _probe_health() -> dict:
    """Run `claude mcp list` once and cache the parsed result. Serialized by a
    lock so concurrent callers never spawn duplicate probes. Never raises: a
    failed/empty probe records an error string the UI can surface."""
    binary = os.environ.get("TOKEN_AUDIT_CLAUDE_BIN") or "claude"
    with _health_lock:
        try:
            proc = subprocess.run(
                [binary, "mcp", "list"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_HEALTH_TIMEOUT,
            )
            text = proc.stdout or proc.stderr or ""
            parsed = _parse_mcp_list(text)
            _health_cache.update(
                map=parsed, checked_at=time.time(),
                error=None if parsed else "no servers parsed from probe output")
        except subprocess.TimeoutExpired:
            _health_cache.update(checked_at=time.time(),
                                 error=f"probe timed out after {_HEALTH_TIMEOUT:.0f}s")
        except (OSError, ValueError) as e:
            _health_cache.update(checked_at=time.time(),
                                 error=f"probe failed: {e}")
    return dict(_health_cache)


def _health_snapshot(force: bool) -> dict:
    """Health cache. GET never probes (force=False just serves the cache, which
    may be empty on first load or stale — the UI decides when to re-probe from
    `age_s`); only /reprobe (force=True) spawns the slow probe. Returns the
    parsed map (may be None), checked_at, age_s and any error."""
    if force:
        _probe_health()
    checked = _health_cache["checked_at"]
    return {
        "map": _health_cache["map"] or {},
        "checked_at": (datetime.fromtimestamp(checked).isoformat()
                       if checked else None),
        "age_s": (round(time.time() - checked, 1) if checked else None),
        "error": _health_cache["error"],
        "probed": checked is not None,
        "ttl_s": _HEALTH_TTL,
    }


def _build_payload(request: Request, range: str, health: dict) -> dict:
    """Assemble the Comms payload: registry + usage rows, with live health
    merged onto matching rows and health-only servers (connectors) appended."""
    warnings = []
    registry, global_meta, project_files = _load_registries(warnings)

    since = _since(range)
    try:
        db_path = request.app.state.cfg["db_path"]
        usage = _usage(db_path, since)
    except Exception as e:  # never 500 the panel on a DB hiccup
        warnings.append(f"usage query failed: {e}")
        usage = {}

    hmap = health.get("map") or {}

    names = set(registry) | set(usage)
    servers = []
    matched_health = set()
    for name in names:
        reg = registry.get(name)
        u = usage.get(name) or {}
        registered = reg is not None
        scopes = reg["scopes"] if registered else []
        if not registered:
            status = "unregistered"      # used but not in any registry
        elif u.get("calls"):
            status = "used"
        else:
            status = "idle"              # registered, zero usage in range
        if "global" in scopes:
            scope = "global"
        elif scopes:
            scope = scopes[0]
        else:
            scope = "unregistered"
        # Live health: match by exact name, else by normalized slug.
        live = hmap.get(name)
        if live is None:
            for hn, hv in hmap.items():
                if hv["slug"] == name:
                    live = hv
                    matched_health.add(hn)
                    break
        else:
            matched_health.add(name)
        servers.append({
            "name": name,
            "scope": scope,
            "scopes": scopes,
            "transport": reg["transport"] if registered else "unknown",
            "command": reg["command"] if registered else None,
            "url": reg["url"] if registered else None,
            "registered": registered,
            "status": status,
            "calls": u.get("calls", 0),
            "sessions": u.get("sessions", 0),
            "first_used": u.get("first_used"),
            "last_used": u.get("last_used"),
            "top_tools": u.get("top_tools", []),
            "live": {"state": live["state"], "status_text": live["status_text"]}
            if live else None,
        })

    # Health-only servers (connectors present in the probe but not in the
    # mcpServers registry or the usage rows) — append so the deck shows every
    # configured integration and its auth/connect state, usage joined by slug.
    for hn, hv in hmap.items():
        if hn in matched_health:
            continue
        u = usage.get(hv["slug"]) or {}
        target = hv["target"]
        transport = "http" if target.startswith("http") else "unknown"
        if target.rstrip("/").endswith("/sse"):
            transport = "sse"
        servers.append({
            "name": hn,
            "scope": "connector",
            "scopes": ["connector"],
            "transport": transport,
            "command": None,
            "url": target if target.startswith("http") else None,
            "registered": True,       # configured (connector store), just not mcpServers
            "status": "used" if u.get("calls") else "idle",
            "calls": u.get("calls", 0),
            "sessions": u.get("sessions", 0),
            "first_used": u.get("first_used"),
            "last_used": u.get("last_used"),
            "top_tools": u.get("top_tools", []),
            "live": {"state": hv["state"], "status_text": hv["status_text"]},
        })

    servers.sort(key=lambda s: (-s["calls"], s["scope"] == "connector",
                                not s["registered"], s["name"]))

    def _live_count(state):
        return sum(1 for s in servers if s.get("live") and s["live"]["state"] == state)

    totals = {
        "servers": len(servers),
        "registered": sum(1 for s in servers if s["registered"]),
        "used": sum(1 for s in servers if s["calls"]),
        "unregistered_used": sum(
            1 for s in servers if not s["registered"] and s["calls"]),
        "calls": sum(s["calls"] for s in servers),
        "connected": _live_count("connected"),
        "failed": _live_count("failed"),
        "needs_auth": _live_count("needs_auth"),
    }

    return {
        "range": range,
        "generated_at": datetime.now().isoformat(),
        "registry": {**global_meta, "project_files": project_files},
        "servers": servers,
        "totals": totals,
        "health": {k: health.get(k) for k in
                   ("checked_at", "age_s", "error", "probed", "ttl_s")},
        "warnings": warnings,
    }


# --- local MCP processes (read-only; needs host PID visibility) --------------
# stdio MCP servers run as child processes of the interactive Claude Code CLI.
# token-audit runs in its own PID namespace, so it can only see them when the
# container is started with `pid: host` (or when running on the host via
# demo.sh). This endpoint degrades fail-closed with an instruction otherwise —
# and it is strictly read-only (no signal/kill is ever sent).
_HZ = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
_GENERIC_TOKENS = {"npx", "node", "python", "python3", "-y", "exec",
                   "usr", "bin", "local", "env", "run"}
# pid-1 command names that mean we are inside an isolated container namespace
# (our own entrypoint), i.e. host processes are NOT visible.
_CONTAINER_PID1 = {"uvicorn", "python", "python3", "sh", "bash", "tini",
                   "docker-init", "gunicorn", "node"}


def _host_pid_visible():
    """Are HOST processes visible in /proc? The reliable marker is pid 1: with
    `pid: host` (or running on the host via demo.sh) it is the init system
    (systemd/init); in an isolated container it is our own entrypoint (uvicorn).
    A raw PID count is NOT reliable — uvicorn --reload alone spawns 60+ threads.
    Returns (visible: bool, pid1_comm: str)."""
    try:
        with open("/proc/1/comm") as f:
            pid1 = f.read().strip()
    except OSError:
        return False, "unknown"
    # Deny-list the typical container entrypoints (isolated namespace); anything
    # else at pid 1 is a host init (systemd / init / initd / tini-as-host / ...)
    # so host processes are visible. This is more robust than an init allow-list
    # across the many init names in the wild.
    isolated = pid1 in _CONTAINER_PID1
    return not isolated, pid1


def _server_signatures(registry):
    """Distinctive cmdline tokens per registered stdio server, for attributing
    a running process back to a server name."""
    sigs = {}
    for name, info in registry.items():
        cmd = info.get("command")
        if not cmd:
            continue                    # remote/http server -> no local process
        toks = [t for t in re.split(r"[\s/=@:]+", cmd)
                if len(t) > 3 and t.lower() not in _GENERIC_TOKENS
                and not t.startswith("-")]
        sigs[name] = toks
    return sigs


def _read_proc(pid, sys_uptime):
    """One /proc/<pid> entry -> dict, or None if it vanished / is unreadable."""
    base = f"/proc/{pid}"
    try:
        with open(f"{base}/cmdline", "rb") as f:
            cmdline = f.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        if not cmdline:
            return None
        with open(f"{base}/stat") as f:
            stat = f.read()
        # comm may contain spaces/parens: split on the last ')'.
        rparen = stat.rfind(")")
        fields = stat[rparen + 2:].split()
        # fields[0]=state ... global field 4=ppid -> here index 1; starttime
        # is global field 22 -> index 19; rss (pages) global 24 -> index 21.
        ppid = int(fields[1])
        starttime = int(fields[19])
        rss_pages = int(fields[21])
        uptime_s = max(0.0, sys_uptime - starttime / _HZ)
        return {
            "pid": pid, "ppid": ppid, "cmd": cmdline,
            "uptime_s": round(uptime_s, 1),
            "rss_mb": round(rss_pages * _PAGE / (1024 * 1024), 1),
        }
    except (OSError, ValueError, IndexError):
        return None


def _human_uptime(seconds):
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


def _scan_mcp_processes(registry):
    """Read-only scan of /proc for MCP server processes. A process is a
    candidate if its cmdline mentions 'mcp' or matches a registered stdio
    server's signature; each is attributed to the server with the most token
    hits (else left unattributed)."""
    try:
        with open("/proc/uptime") as f:
            sys_uptime = float(f.read().split()[0])
    except (OSError, ValueError):
        sys_uptime = 0.0
    sigs = _server_signatures(registry)
    out = []
    try:
        pids = [int(e) for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        info = _read_proc(pid, sys_uptime)
        if info is None:
            continue
        low = info["cmd"].lower()
        # Attribute to the best-matching server.
        best, best_hits = None, 0
        for name, toks in sigs.items():
            hits = sum(1 for t in toks if t.lower() in low)
            if hits > best_hits:
                best, best_hits = name, hits
        is_mcp = ("mcp" in low) or best_hits > 0
        if not is_mcp:
            continue
        info["server"] = best
        info["uptime"] = _human_uptime(info["uptime_s"])
        out.append(info)
    out.sort(key=lambda p: (p["server"] is None, p["server"] or "", -p["uptime_s"]))
    return out


@router.get("/mcp/processes")
def mcp_processes(request: Request):
    """Read-only view of locally-running MCP server processes. Never sends a
    signal; degrades fail-closed when host PIDs are not visible."""
    warnings = []
    registry, _, _ = _load_registries(warnings)
    visible, pid1 = _host_pid_visible()
    if not visible:
        return {
            "available": False,
            "pid1": pid1,
            "reason": "host process visibility not enabled",
            "detail": ("token-audit runs in an isolated PID namespace, so it "
                       "cannot see MCP server processes spawned by Claude Code "
                       "on the host. Add `pid: host` to the token-audit service "
                       "in docker-compose.yml and `docker compose up -d "
                       "--force-recreate`. (On the host via demo.sh they are "
                       "visible automatically.)"),
            "processes": [],
        }
    procs = _scan_mcp_processes(registry)
    return {
        "available": True,
        "pid1": pid1,
        "process_count": len(procs),
        "processes": procs,
        "warnings": warnings,
    }


@router.get("/mcp")
def mcp_overview(request: Request, range: str = "all"):
    # GET never blocks on a probe: it uses whatever health is cached (possibly
    # none on first load). The UI calls /mcp/reprobe to populate/refresh it.
    return _build_payload(request, range, _health_snapshot(force=False))


@router.post("/mcp/reprobe")
def mcp_reprobe(request: Request, range: str = "all"):
    """Re-run the live health probe and return the fresh, merged payload. Safe
    and idempotent: it only re-reads server connectivity (no process is killed
    or restarted) — the read-only guarantee of the Systems views holds."""
    return _build_payload(request, range, _health_snapshot(force=True))


