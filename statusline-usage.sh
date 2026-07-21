#!/usr/bin/env bash
# Optional Claude Code statusLine for Token Audit.
#
# It renders a compact usage line in the terminal AND persists the official
# rate_limits (5h / weekly %) to ~/.claude/token-audit/usage-quota.json so the
# Token Audit dashboard's "Subscription quota" panel can show the real
# subscription usage % (the only source of the true quota for local tools).
#
# Install: copy this to ~/.claude/statusline-usage.sh, chmod +x, then add to
# ~/.claude/settings.json:
#   "statusLine": { "type": "command", "command": "bash ~/.claude/statusline-usage.sh" }
#
# Note: statusLine only fires in TERMINAL Claude Code sessions (not the IDE
# extension), so the quota file is as fresh as your last terminal statusline render.
input=$(cat)
STATUSLINE_INPUT="$input" python3 - <<'PY'
import json, os, time, tempfile
raw = os.environ.get("STATUSLINE_INPUT", "")
try:
    d = json.loads(raw)
except Exception:
    print("token-audit"); raise SystemExit

rl = d.get("rate_limits") or {}
if rl:
    out = {
        "five_hour": rl.get("five_hour"),
        "seven_day": rl.get("seven_day"),
        "captured_at": int(time.time()),
    }
    d_ = os.path.expanduser("~/.claude/token-audit")
    p = os.path.join(d_, "usage-quota.json")
    try:
        os.makedirs(d_, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d_)
        with os.fdopen(fd, "w") as f:
            json.dump(out, f)
        os.replace(tmp, p)   # atomic
    except Exception:
        pass

model = (d.get("model") or {}).get("display_name") or (d.get("model") or {}).get("id") or ""
def pf(x):
    v = (x or {}).get("used_percentage")
    return f"{round(v)}%" if v is not None else "-"
print(f"5h {pf(rl.get('five_hour'))} · wk {pf(rl.get('seven_day'))} · {model}")
PY
