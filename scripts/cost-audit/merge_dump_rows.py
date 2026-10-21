"""Put billed rows from a ledger dump back into the live Postgres ledger.

On 2026-09-29 crash recovery emptied the UNLOGGED `messages` table; a restart
re-read every transcript still on disk, but sessions whose transcripts Claude Code
had already deleted exist only in the 2026-09-23 dump. This script reads that
dump's `messages` data and inserts the billed rows the transcripts cannot give back:

- a row whose (message_id, request_id) is already billed is skipped;
- a row without message_id (deduplicated by the heuristic key) is inserted only
  when its session has no row from a transcript;
- a row whose uuid is already stored is skipped.

It also counts inserted heuristic rows that match a transcript row on the
input-side token tuple with >= 10k cached tokens (the cross-session key of
audit_cost.py): those may bill one response twice. They are reported, not removed.

    docker exec -i pgcore-17 pg_restore -f - --data-only --table=messages \
        < ~/.flightdeck/backups/flightdeck-2026-09-23-pre-idefix.dump > messages.copy
    set -a; . ./.env; set +a
    PYTHONPATH=python .venv/bin/python scripts/cost-audit/merge_dump_rows.py messages.copy [--apply]

Without --apply the transaction is rolled back.
"""
import argparse
import os
import sys

import psycopg

from flightdeck import pricing

ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "b": "\b", "f": "\f", "v": "\v"}
COLS = ("uuid", "session_id", "project", "model", "ts", "input_tokens", "cache_read",
        "cache_create_5m", "cache_create_1h", "output_tokens", "service_tier", "message_id",
        "request_id", "speed", "inference_geo", "dedupe_method", "prompt_id", "agent_id")
INTS = {"input_tokens", "cache_read", "cache_create_5m", "cache_create_1h", "output_tokens"}


def unescape(field):
    if field == r"\N":
        return None
    if "\\" not in field:
        return field
    out, i = [], 0
    while i < len(field):
        c = field[i]
        if c == "\\" and i + 1 < len(field):
            i += 1
            out.append(ESCAPES.get(field[i], field[i]))
        else:
            out.append(c)
        i += 1
    return "".join(out)


def read_copy(path):
    cols = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if cols is None:
                if line.startswith("COPY "):
                    cols = [c.strip().strip('"') for c in line[line.index("(") + 1:line.rindex(")")].split(",")]
                continue
            if line == r"\.":
                return
            yield dict(zip(cols, (unescape(f) for f in line.split("\t"))))
    if cols is None:
        sys.exit("no COPY header: expected pg_restore --data-only --table=messages output")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("copy_file")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = psycopg.connect(os.environ["TOKEN_AUDIT_DATABASE_URL"])
    cur = conn.cursor()
    keys = {(m, r or "") for m, r in cur.execute(
        "SELECT message_id, request_id FROM messages WHERE dup_of IS NULL AND message_id IS NOT NULL")}
    uuids = {u for (u,) in cur.execute("SELECT uuid FROM messages")}
    on_disk = {s for (s,) in cur.execute("SELECT DISTINCT session_id FROM messages")}
    tuples = {t for t in cur.execute(
        "SELECT model, input_tokens, cache_read, cache_create_5m, cache_create_1h FROM messages "
        "WHERE dup_of IS NULL AND cache_read + cache_create_5m + cache_create_1h >= 10000")}
    n = {"read": 0, "billed": 0, "inserted": 0, "key_present": 0, "session_on_disk": 0,
         "uuid_present": 0, "overlap_rows": 0}
    cost = overlap_cost = 0.0
    rows = []
    for r in read_copy(a.copy_file):
        n["read"] += 1
        if r.get("dup_of") is not None:
            continue
        n["billed"] += 1
        if r["uuid"] in uuids:
            n["uuid_present"] += 1
            continue
        if r.get("message_id"):
            if (r["message_id"], r.get("request_id") or "") in keys:
                n["key_present"] += 1
                continue
        elif r["session_id"] in on_disk:
            n["session_on_disk"] += 1
            continue
        row = {c: (int(r[c] or 0) if c in INTS else r.get(c)) for c in COLS}
        rows.append(row)
        args = (row["model"], row["input_tokens"], row["cache_read"], row["cache_create_5m"],
                row["cache_create_1h"], row["output_tokens"], row["ts"], row["speed"], row["inference_geo"])
        c = pricing.message_cost(*args) or 0.0
        cost += c
        if not row["message_id"] and (row["model"], row["input_tokens"], row["cache_read"],
                                      row["cache_create_5m"], row["cache_create_1h"]) in tuples:
            n["overlap_rows"] += 1
            overlap_cost += c
    cur.executemany(
        f"INSERT INTO messages ({', '.join(COLS)}) VALUES ({', '.join(['%s'] * len(COLS))}) "
        "ON CONFLICT (uuid) DO NOTHING", [tuple(r[c] for c in COLS) for r in rows])
    n["inserted"] = len(rows)
    print({**n, "cost_added": round(cost, 2), "overlap_cost": round(overlap_cost, 2),
           "sessions_added": len({r["session_id"] for r in rows}), "applied": a.apply})
    if a.apply:
        conn.commit()
    else:
        conn.rollback()


if __name__ == "__main__":
    main()
