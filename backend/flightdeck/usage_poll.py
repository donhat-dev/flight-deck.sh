"""Poll `claude -p "/usage"` for the official subscription quota.

The /usage command is the only local surface that reports ALL quota windows —
session (5h), weekly (all models), and per-model weekly buckets (e.g. Fable) —
with reset times. It runs headlessly in ~1.5s. This module parses that text and
writes ~/.claude/token-audit/usage-report.json, which the dashboard backend
merges with the statusLine capture (usage-quota.json).

Must run on the HOST (needs the claude CLI + credentials), e.g. via cron:
  */10 * * * * cd /path/to/token-audit && PYTHONPATH=. python3 -m flightdeck.usage_poll
"""
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# "Current session: 34% used · resets Jul 6, 2:20pm (Asia/Ho_Chi_Minh)"
# "Current week (all models): 46% used · resets Jul 7, 10am (Asia/Ho_Chi_Minh)"
_WINDOW = re.compile(
    r"^Current (session|week \(([^)]+)\)):\s*([\d.]+)% used"
    r"(?:\s*·\s*resets\s*(.+))?$")
# "Jul 6, 2:20pm (Asia/Ho_Chi_Minh)"
_RESET = re.compile(r"^(.+?)\s*\(([^)]+)\)\s*$")
# "Last 7d · 5007 requests · 36 sessions"
_INSIGHT_HDR = re.compile(r"^Last (24h|7d)\s*·\s*(.+)$")

_REPORT_DIR = "~/.claude/token-audit"
_REPORT_FILE = "usage-report.json"


def _parse_reset(text, now=None):
    """'Jul 6, 2:20pm (Asia/Ho_Chi_Minh)' -> (epoch, raw text). Epoch None on failure."""
    m = _RESET.match(text or "")
    if not m or ZoneInfo is None:
        return None, (text or "").strip() or None
    dt_text, tzname = m.group(1).strip(), m.group(2).strip()
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        return None, text.strip()
    for fmt in ("%b %d, %I:%M%p", "%b %d, %I%p"):
        try:
            naive = datetime.strptime(dt_text, fmt)
            break
        except ValueError:
            continue
    else:
        return None, text.strip()
    ref = now or datetime.now(tz)
    dt = naive.replace(year=ref.year, tzinfo=tz)
    if dt < ref - timedelta(hours=1):  # resets are in the future; handle year wrap
        dt = dt.replace(year=ref.year + 1)
    return int(dt.timestamp()), text.strip()


def parse_usage(text, now=None):
    """Parse the /usage report text into the quota schema."""
    out = {"five_hour": None, "seven_day": None, "weekly_models": [], "insights": {}}
    lines = [_ANSI.sub("", ln).rstrip() for ln in (text or "").splitlines()]
    current_block = None
    for ln in lines:
        m = _WINDOW.match(ln.strip())
        if m:
            which, bucket, pct_s, reset_s = m.groups()
            epoch, raw = _parse_reset(reset_s or "", now=now)
            win = {"used_percentage": float(pct_s), "resets_at": epoch,
                   "resets_text": raw}
            if which == "session":
                out["five_hour"] = win
            elif bucket and bucket.lower() == "all models":
                out["seven_day"] = win
            else:
                out["weekly_models"].append({"label": bucket, **win})
            continue
        h = _INSIGHT_HDR.match(ln.strip())
        if h:
            current_block = f"last_{h.group(1)}"
            out["insights"][current_block] = {"header": ln.strip(), "lines": []}
            continue
        if current_block and ln.startswith("  ") and ln.strip():
            out["insights"][current_block]["lines"].append(ln.strip())
        elif current_block and not ln.strip():
            current_block = None
    return out


def poll(claude_bin=None, timeout=60):
    """Run `claude -p /usage`, parse, and return the report dict (or None)."""
    cmd = [claude_bin or os.environ.get("TOKEN_AUDIT_CLAUDE_BIN", "claude"),
           "-p", "/usage"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    rep = parse_usage(r.stdout)
    if not (rep["five_hour"] or rep["seven_day"] or rep["weekly_models"]):
        return None  # output didn't look like a usage report; don't clobber
    rep["captured_at"] = int(time.time())
    return rep


def write_report(rep, path=None):
    d = os.path.expanduser(os.path.dirname(path) if path else _REPORT_DIR)
    p = path or os.path.join(d, _REPORT_FILE)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d)
    with os.fdopen(fd, "w") as f:
        json.dump(rep, f)
    os.replace(tmp, p)  # atomic
    return p


if __name__ == "__main__":
    report = poll()
    if report is None:
        print("usage poll failed (claude CLI unavailable or unparsable output)")
        raise SystemExit(1)
    p = write_report(report)
    buckets = [f"5h {report['five_hour']['used_percentage']:.0f}%" if report["five_hour"] else "5h -",
               f"wk {report['seven_day']['used_percentage']:.0f}%" if report["seven_day"] else "wk -"]
    buckets += [f"{m['label']} {m['used_percentage']:.0f}%" for m in report["weekly_models"]]
    print(f"wrote {p}: " + " · ".join(buckets))
