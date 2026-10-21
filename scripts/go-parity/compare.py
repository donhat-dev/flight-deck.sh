"""Diff the Go core against the Python oracle, field by field.

    python3 scripts/go-parity/compare.py --py "$SCRATCH/py.json" --fd go/bin/fd \
        --db "$SCRATCH/fd.db" --projects ~/.cache/fd-parity/projects --subscription-usd 200

Exit 0 when every compared value agrees (numbers within 1e-9 relative), 1 otherwise.
Not compared in slice 1 (see the spec): rtk_savings, agent_hours*, sessions[].subagents.
"""
import argparse
import json
import math
import subprocess
import sys
import time

RANGES = ("today", "7d", "30d", "all")
SKIP = {"rtk_savings", "agent_hours", "agent_hours_sub", "agent_hours_est", "subagents"}


def fd(binary, *args):
    done = subprocess.run([binary, *args, "--json"], capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"fd {' '.join(args)} exited {done.returncode}: {done.stdout}{done.stderr}")
    return json.loads(done.stdout)


def same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    if a in (None, "") and b in (None, ""):
        return True
    return a == b


def diff(path, a, b, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k in SKIP:
                continue
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'go' if k in b else 'python'}")
                continue
            diff(f"{path}.{k}", a[k], b[k], out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} in python, {len(b)} in go")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            diff(f"{path}[{i}]", x, y, out)
    elif not same(a, b):
        out.append(f"{path}: python {a!r} go {b!r}")


def keyed(rows, key):
    return {(r[key] or ""): r for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--py", required=True)
    ap.add_argument("--fd", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--projects", required=True)
    ap.add_argument("--subscription-usd", default="0")
    a = ap.parse_args()
    py = json.load(open(a.py))
    t0 = time.time()
    ing = fd(a.fd, "ingest", "--db", a.db, "--projects", a.projects)
    print(f"go ingest: {ing['files']} files, {ing['new_rows']} responses in {time.time() - t0:.1f}s")
    problems = []
    for r in RANGES:
        spend = fd(a.fd, "spend", "--no-ingest", "--range", r, "--db", a.db,
                   "--subscription-usd", a.subscription_usd)
        sessions = fd(a.fd, "sessions", "--no-ingest", "--range", r, "--limit", "10000000", "--db", a.db)
        p = py[r]
        # Python adds a day that has agent busy time but no billed response (a turn
        # running past midnight); fd has no activity yet, so such days are dropped.
        p_daily = [d for d in p["daily"] if d["tokens"] or d["cost"]]
        diff(f"{r}.summary", p["summary"], spend["summary"], problems)
        diff(f"{r}.daily", keyed(p_daily, "date"), keyed(spend["daily"], "date"), problems)
        diff(f"{r}.by_model", keyed(p["by_model"], "model"), keyed(spend["by_model"], "model"), problems)
        diff(f"{r}.sessions", keyed(p["sessions"], "session_id"), keyed(sessions, "session_id"), problems)
    for line in problems[:50]:
        print(line)
    print(f"{len(problems)} differences")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
