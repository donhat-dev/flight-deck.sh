"""Reuse the `ccusage` CLI as the 5-hour-window / weekly usage engine.

ccusage (https://github.com/ccusage/ccusage) already models Claude's 5-hour
billing blocks, burn rate, and projections from the same `~/.claude` JSONL, and
exposes `--json`. We shell out to it (claude-scoped) and cache the result.

Config (env):
  TOKEN_AUDIT_CCUSAGE_CMD  base command (default "npx -y ccusage@latest";
                           in Docker we install it and set this to "ccusage")
  TOKEN_AUDIT_CCUSAGE_TTL  seconds to cache a snapshot (default 30)
  CLAUDE_CONFIG_DIR        dir containing projects/ (ccusage reads it); needed
                           when the logs are mounted somewhere non-default.
"""
import json
import os
import shlex
import subprocess
import threading
import time

_TTL = float(os.environ.get("TOKEN_AUDIT_CCUSAGE_TTL", "30"))
_lock = threading.Lock()
_cache = {"ts": 0.0, "data": None}


def _cmd():
    return shlex.split(os.environ.get("TOKEN_AUDIT_CCUSAGE_CMD", "npx -y ccusage@latest"))


def _run(args):
    """Run `ccusage claude <args> --json`; return parsed JSON or None on failure."""
    try:
        out = subprocess.run(
            [*_cmd(), "claude", *args, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except Exception:
        return None


def _blocks(data):
    if isinstance(data, dict):
        return data.get("blocks", []) or []
    return data or []


def _compute():
    # `blocks --recent` includes the active block, so one call covers both.
    recent_raw = _run(["blocks", "--recent"])
    if recent_raw is None:
        return {"available": False, "active": None, "recent": [], "weekly": []}
    recent = [b for b in _blocks(recent_raw) if not b.get("isGap")]
    active = next((b for b in recent if b.get("isActive")), None)
    weekly_raw = _run(["weekly"])
    weekly = weekly_raw.get("weekly", []) if isinstance(weekly_raw, dict) else []
    return {"available": True, "active": active, "recent": recent, "weekly": weekly}


def snapshot(force: bool = False) -> dict:
    """TTL-cached usage-window snapshot. Safe to call from request threads."""
    now = time.monotonic()
    with _lock:
        if not force and _cache["data"] is not None and now - _cache["ts"] < _TTL:
            return _cache["data"]
    data = _compute()
    with _lock:
        _cache["data"] = data
        _cache["ts"] = time.monotonic()
    return data
