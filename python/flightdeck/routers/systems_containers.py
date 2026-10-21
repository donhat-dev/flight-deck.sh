"""Hangar — Docker container board routes (READ-ONLY).

The HTTP surface only: the docker-socket transport and the payload builders
live in `flightdeck.systems.containers` (stdlib-only, no web imports). Never
500s on a missing/unreachable daemon — the endpoints degrade to
available:false so the UI can show guidance.
"""
from fastapi import APIRouter, HTTPException

from flightdeck.systems import compose_scan
from flightdeck.systems.containers import (
    _build_container, _get, _group, _resolve_sock, _short_id, _sock_candidates)

router = APIRouter(prefix="/api/systems", tags=["systems"])


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


@router.get("/stacks")
def stacks_overview():
    """Compose stacks: the ones with containers, plus the ones fully down.

    Two sources, joined on the compose project name. Running containers carry it
    as `com.docker.compose.project`; a compose file on disk carries it as its
    top-level `name:` (or contributes its directory basename). A stack present
    only on disk is DOWN — the case the container board structurally cannot show,
    because a stack that was brought down leaves nothing to list.

    Read-only, like everything else in this module. Never 500s on a missing
    daemon: the disk half still answers, with every stack marked `unknown`.
    """
    try:
        on_disk = compose_scan.scan()
    except Exception as e:  # a bad root or unreadable tree must not take the board down
        on_disk = {}
        scan_error = str(e)
    else:
        scan_error = None

    sock_path = _resolve_sock()
    live_rows, docker_available, docker_reason = [], False, None
    docker_version = None
    if not sock_path:
        docker_reason = "docker socket not found"
    else:
        try:
            raw = _get(sock_path, "/containers/json?all=true") or []
            live_rows = [_build_container(c) for c in raw]
            docker_available = True
            try:
                ver = _get(sock_path, "/version")
                docker_version = ver.get("Version") if ver else None
            except Exception:
                docker_version = None  # non-fatal; the list already succeeded
        except Exception as e:
            docker_reason = f"docker socket unreachable: {e}"

    live = {}
    for c in live_rows:
        if c["project"]:
            live.setdefault(c["project"], []).append(c)

    def _state(rows):
        if not docker_available:
            return "unknown"
        if not rows:
            return "down"
        running = sum(1 for r in rows if r["state"] == "running")
        if running == 0:
            return "down"
        return "up" if running == len(rows) else "partial"

    stacks = []
    for project in sorted(set(on_disk) | set(live)):
        disk = on_disk.get(project)
        rows = sorted(live.get(project, []),
                      key=lambda r: (0 if r["state"] == "running" else 1, r["name"].lower()))
        # A stack with containers but no file on disk still knows its own paths.
        config_files = list(disk["config_files"]) if disk else []
        working_dir = disk["working_dir"] if disk else None
        if not config_files and rows:
            config_files = rows[0].get("config_files") or []
        if not working_dir and rows:
            working_dir = rows[0].get("working_dir")
        stacks.append({
            "project": project,
            "state": _state(rows),
            "on_disk": disk is not None,
            "declared": bool(disk and disk["declared"]),
            "working_dir": working_dir,
            "config_files": config_files,
            "total": len(rows),
            "running": sum(1 for r in rows if r["state"] == "running"),
            "containers": rows,
        })

    orphans = sorted(
        (c for c in live_rows if not c["project"]),
        key=lambda r: (0 if r["state"] == "running" else 1, r["name"].lower()),
    )

    return {
        "available": docker_available,
        "reason": docker_reason,
        "scan_error": scan_error,
        "socket": sock_path,
        "summary": {
            "stacks": len(stacks),
            "up": sum(1 for s in stacks if s["state"] == "up"),
            "partial": sum(1 for s in stacks if s["state"] == "partial"),
            "down": sum(1 for s in stacks if s["state"] == "down"),
            "orphan_containers": len(orphans),
            "docker_version": docker_version,
        },
        "stacks": stacks,
        "orphans": orphans,
    }
