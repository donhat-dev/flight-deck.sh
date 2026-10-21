"""Comms — MCP server registry + usage routes.

The HTTP surface only: registry parsing, usage aggregation and the live-health
probe cache live in `flightdeck.systems.mcp` (no web imports there). See that
module's docstring for the response shape.
"""
from fastapi import APIRouter, Request

from flightdeck.systems.mcp import (
    _build_payload, _health_snapshot, _host_pid_visible, _load_registries,
    _scan_mcp_processes)

router = APIRouter(prefix="/api/systems", tags=["systems"])


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
    return _build_payload(request.app.state.cfg["db_path"], range,
                          _health_snapshot(force=False))


@router.post("/mcp/reprobe")
def mcp_reprobe(request: Request, range: str = "all"):
    """Re-run the live health probe and return the fresh, merged payload. Safe
    and idempotent: it only re-reads server connectivity (no process is killed
    or restarted) — the read-only guarantee of the Systems views holds."""
    return _build_payload(request.app.state.cfg["db_path"], range,
                          _health_snapshot(force=True))
