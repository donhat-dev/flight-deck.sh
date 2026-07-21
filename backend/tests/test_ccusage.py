import json
from flightdeck import ccusage


class _R:
    def __init__(self, rc, out):
        self.returncode = rc
        self.stdout = out


def _reset():
    ccusage._cache["data"] = None
    ccusage._cache["ts"] = 0.0


def test_snapshot_parses_active_and_weekly(monkeypatch):
    _reset()

    def fake_run(cmd, **kw):
        if "weekly" in cmd:
            return _R(0, json.dumps({"weekly": [
                {"cost": 10.0, "inputTokens": 1, "outputTokens": 2,
                 "cacheReadTokens": 3, "cacheCreationTokens": 4}]}))
        # blocks --recent: a gap block (filtered out) + the active block
        return _R(0, json.dumps({"blocks": [
            {"isActive": False, "isGap": True},
            {"isActive": True, "isGap": False, "costUSD": 9.5,
             "projection": {"totalCost": 15.0, "remainingMinutes": 90},
             "burnRate": {"tokensPerMinuteForIndicator": 2000},
             "entries": 100, "models": ["claude-opus-4-8"],
             "startTime": "2026-07-03T00:00:00Z", "endTime": "2026-07-03T05:00:00Z"}]}))

    monkeypatch.setattr(ccusage.subprocess, "run", fake_run)
    s = ccusage.snapshot(force=True)
    assert s["available"] is True
    assert s["active"]["costUSD"] == 9.5
    assert len(s["recent"]) == 1          # gap block filtered
    assert s["weekly"][0]["cost"] == 10.0


def test_snapshot_unavailable_when_cli_missing(monkeypatch):
    _reset()

    def boom(cmd, **kw):
        raise FileNotFoundError("ccusage not installed")

    monkeypatch.setattr(ccusage.subprocess, "run", boom)
    s = ccusage.snapshot(force=True)
    assert s["available"] is False
    assert s["active"] is None and s["recent"] == [] and s["weekly"] == []
