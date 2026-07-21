"""Merge the official subscription quota from all available local sources.

Sources (any may be absent; freshest wins per window):
  usage-quota.json  (statusLine hook)   -> five_hour / seven_day only
  local report      (in-container poll)  -> full /usage report, RW path
  host report       (host cron poll)     -> full /usage report, mounted RO

The full reports add per-model weekly buckets (e.g. Fable) + usage insights;
weekly_models/insights come from the freshest report. Env overrides:
  TOKEN_AUDIT_QUOTA_FILE         (statusline capture)
  TOKEN_AUDIT_LOCAL_REPORT_FILE  (in-container poll output)
  TOKEN_AUDIT_REPORT_FILE        (host cron poll output)
"""
import json
import os
import time

_QUOTA_DEFAULT = "~/.claude/token-audit/usage-quota.json"
_REPORT_DEFAULT = "~/.claude/token-audit/usage-report.json"


def _load(path):
    if not path:
        return None
    try:
        with open(os.path.expanduser(path)) as f:
            return json.load(f)
    except Exception:
        return None


def _cap(d):
    v = (d or {}).get("captured_at")
    return v if isinstance(v, (int, float)) else 0


def read() -> dict:
    now = time.time()
    statusline = _load(os.environ.get("TOKEN_AUDIT_QUOTA_FILE", _QUOTA_DEFAULT))
    reports = [
        _load(os.environ.get("TOKEN_AUDIT_LOCAL_REPORT_FILE")),
        _load(os.environ.get("TOKEN_AUDIT_REPORT_FILE", _REPORT_DEFAULT)),
    ]
    reports = [r for r in reports if r]
    reports.sort(key=_cap, reverse=True)
    best_report = reports[0] if reports else None

    def pick(key):
        cands = [(_cap(d), d[key]) for d in ([statusline] if statusline else []) + reports
                 if d and d.get(key)]
        if not cands:
            return None
        cands.sort(key=lambda c: c[0], reverse=True)
        return cands[0][1]

    five = pick("five_hour")
    seven = pick("seven_day")
    weekly_models = (best_report or {}).get("weekly_models") or []
    insights = (best_report or {}).get("insights") or None

    def age(d):
        return int(now - _cap(d)) if d and _cap(d) else None

    all_ages = [a for a in (age(statusline), age(best_report)) if a is not None]
    return {
        "available": bool(five or seven or weekly_models),
        "five_hour": five,
        "seven_day": seven,
        "weekly_models": weekly_models,
        "insights": insights,
        "age_seconds": min(all_ages) if all_ages else None,
        "sources": {"statusline_age": age(statusline), "report_age": age(best_report)},
    }
