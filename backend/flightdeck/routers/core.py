"""Core metrics + control endpoints.

summary / daily / by-model / usage-windows / quota / quota-refresh / rtk /
reingest / pulse / screenshot. Read-mostly: warm requests are pure dict lookups
against the cached snapshot (`cached()`); cold reads open a short-lived
connection via `db.open_read`.
"""
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from flightdeck import ccusage, db, metrics, pulse, quota
from flightdeck.runtime import _updated, cached, since

router = APIRouter(tags=["core"])

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


@router.get("/api/summary")
def summary(request: Request, range: str = "all"):
    hit = cached(request.app, range, "summary")
    if hit is not None:
        return hit
    cfg = request.app.state.cfg
    c = db.open_read(cfg["db_path"])
    try:
        return metrics.summary(
            c, cfg["subscription_monthly_usd"], request.app.state.rtk_savings,
            since=since(range))
    finally:
        c.close()


@router.get("/api/daily")
def daily(request: Request, range: str = "all"):
    hit = cached(request.app, range, "daily")
    if hit is not None:
        return hit
    cfg = request.app.state.cfg
    c = db.open_read(cfg["db_path"])
    try:
        return metrics.daily(c, since=since(range))
    finally:
        c.close()


@router.get("/api/by-model")
def by_model(request: Request, range: str = "all"):
    hit = cached(request.app, range, "by_model")
    if hit is not None:
        return hit
    cfg = request.app.state.cfg
    c = db.open_read(cfg["db_path"])
    try:
        return metrics.by_model(c, since=since(range))
    finally:
        c.close()


@router.get("/api/pulse")
def pulse_endpoint():
    # Pulse board: the attention-first projection of Claude Code background
    # agents, read straight from the ~/.claude/jobs store (mounted read-only
    # in the container via TOKEN_AUDIT_JOBS_DIR), enriched with supervisor
    # liveness from TOKEN_AUDIT_DAEMON_DIR/roster.json. Cheap enough to build
    # per request; the frontend polls it on the Pulse tab.
    return pulse.snapshot()


@router.get("/api/rtk")
def rtk_endpoint(request: Request):
    return {"savings_usd": request.app.state.rtk_savings}


@router.post("/api/reingest")
def reingest_endpoint(request: Request):
    # Manual full reingest + snapshot rebuild. Use when the watcher is off
    # (TOKEN_AUDIT_WATCH=0) or you want an immediate refresh without waiting
    # for the periodic sweep. The skip-cache keeps this cheap when little
    # changed.
    request.app.state.runtime.reingest()
    if getattr(request.app.state, "loop", None) is not None:
        request.app.state.loop.call_soon_threadsafe(_updated.set)
    snap = request.app.state.snap or {}
    return {"ok": True, "ranges": list(snap.keys())}


@router.get("/api/usage-windows")
def usage_windows():
    # Reuses the ccusage CLI (5h blocks + burn/projection + weekly).
    # TTL-cached inside ccusage.snapshot(); rolling windows, range-independent.
    return ccusage.snapshot()


@router.get("/api/quota")
def quota_endpoint():
    # Official quota merged from the /usage poll + statusLine capture.
    return quota.read()


@router.post("/api/quota/refresh")
def quota_refresh(request: Request):
    # Force-poll `claude -p /usage`. Works wherever the claude CLI + creds are
    # present (host, or container with the CLI installed and creds mounted);
    # the poll output is written to the RW local-report path. If poll() fails
    # (no CLI / no auth) we fall back to re-reading the freshest known files.
    polled = request.app.state.runtime.poll_once()
    return {**quota.read(), "polled": polled}


@router.get("/api/screenshot")
def screenshot(path: str):
    # Serve a local image file (e.g. a chrome-devtools take_screenshot output)
    # so the transcript view can preview it inline. Localhost dev dashboard:
    # only existing files with an image extension are served.
    p = os.path.expanduser(path)
    if os.path.splitext(p)[1].lower() not in _IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="not an image path")
    if not os.path.isfile(p):
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(p)
