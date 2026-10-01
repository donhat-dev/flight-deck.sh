"""Spend metrics from the Python backend over a fresh SQLite ledger, as JSON.

The oracle for the Go core: the same transcripts, read in the same order (oldest
file first, as `fd` does), into a throwaway SQLite file, then the four spend
answers for every range. Never touches the live Postgres ledger.

    PYTHONPATH=backend .venv/bin/python scripts/go-parity/python_metrics.py \
        --projects ~/.cache/fd-parity/projects --db "$SCRATCH/py.db" \
        --subscription-usd 200 > "$SCRATCH/py.json"
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import date, timedelta

from flightdeck import db, ingest, metrics

RANGES = ("today", "7d", "30d", "all")


def since(range_key):
    # Copied from runtime.since(): importing runtime would pull in the FastAPI app.
    today = date.today()
    if range_key == "today":
        return today.isoformat()
    if range_key == "7d":
        return (today - timedelta(days=6)).isoformat()
    if range_key == "30d":
        return (today - timedelta(days=29)).isoformat()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--subscription-usd", type=float, default=0.0)
    a = ap.parse_args()
    if os.path.exists(a.db):
        sys.exit(f"{a.db} exists; the oracle needs a fresh ledger")
    db.configure({})  # no database_url: SQLite at a.db, whatever the environment says
    conn = db.connect(a.db)
    root = os.path.expanduser(a.projects)
    paths = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    paths.sort(key=lambda p: (os.stat(p).st_mtime_ns, p))
    t0 = time.time()
    for p in paths:
        ingest.ingest_file(conn, p)
    print(f"python ingest: {len(paths)} files in {time.time() - t0:.1f}s", file=sys.stderr)
    out = {}
    for r in RANGES:
        s = since(r)
        out[r] = {
            "summary": metrics.summary(conn, a.subscription_usd, 0.0, since=s),
            "daily": metrics.daily(conn, since=s),
            "by_model": metrics.by_model(conn, since=s),
            "sessions": metrics.sessions(conn, limit=10**7, since=s),
        }
    json.dump(out, sys.stdout, default=str)


if __name__ == "__main__":
    main()
