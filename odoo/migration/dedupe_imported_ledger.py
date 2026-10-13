"""Replace the rows copied from the old ledger with one row per API call.

Run it under `odoo shell`. It is a dry run unless FD_APPLY=1 is set:

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http --log-level=warn < odoo/migration/dedupe_imported_ledger.py
    docker exec -i -e FD_APPLY=1 flightdeck-odoo19 /entrypoint.sh odoo shell ... < (same)

Why it exists. `import_ledger.py` copied the ledger's `messages` table one row
per transcript line, and one API response is written as several lines that
each repeat its usage. The ledger has since marked the repeats (`dup_of`), so
a row with `dup_of IS NULL` there is one call. On 2026-09-23 this replaced the
61,079 rows Odoo held without `agent_key` with 13,949.

What it removes: every row with no `agent_key` dated before WINDOW_END. That is
the import, plus the rows `rekey_spend.py` rebuilt on 2026-09-15 for nine
sessions, before `agent_key` existed, from transcripts that are gone now.
Those nine are replaced from the ledger too: one of them is a resumed session
whose file repeated calls the ledger counts under the earlier sessions, and
the rescan read no subagent file, so the ledger holds calls they lack.

What goes in: each call the ledger counts once that Odoo does not already hold
under its API message id, priced the way the import prices it. A ledger row
with no message id is skipped when a row already kept is its copy: same model
and the same five counts, and either COPY_MIN_CACHED cached tokens or written
within COPY_SECONDS. A resumed or forked session repeats earlier calls under
new lines, and the ledger's own pass only joined copies with the same
timestamp, and only for Claude models.

It writes in one transaction and commits only at the end, so a container
restart mid-run leaves the table as it was; run it again. A second run finds
nothing to do. After it, a replacement row has `agent_key` '' like a main-agent
row the reader stores; its uuid is the ledger's line uuid rather than an API
message id (`msg_...`), which is what still tells the two apart.
"""
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

LEDGER = dict(
    host=os.environ.get("FD_LEDGER_HOST", "pgcore-17"),
    port=int(os.environ.get("FD_LEDGER_PORT", "5432")),
    dbname=os.environ.get("FD_LEDGER_DB", "flightdeck"),
    user=os.environ.get("FD_LEDGER_USER", "fdreader"),
    password=os.environ["FD_LEDGER_PASSWORD"],
)
APPLY = os.environ.get("FD_APPLY") == "1"
# Nothing on or after this day is touched: the reader has covered those days
# since, and its crons keep adding rows there while this runs.
WINDOW_END = datetime(2026, 8, 24)
FIRST_WEEK = datetime(2026, 7, 6)
COPY_MIN_CACHED = 10_000
COPY_SECONDS = 2
BATCH = 1000
COUNTS = ("input_tokens", "cache_read", "cache_create_5m", "cache_create_1h", "output_tokens")


def parse_ts(value):
    """ISO text to the naive UTC datetime Odoo stores."""
    if not value:
        return False
    text = str(value).strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return False
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
    return stamp


def week_of(stamp):
    return (stamp - timedelta(days=stamp.weekday())).date()


class Weekly:
    """Cost, output and calls per week starting Monday, UTC."""

    def __init__(self):
        self.rows = defaultdict(lambda: [0.0, 0, 0])

    def add(self, stamp, cost, output, sign=1):
        if not stamp or stamp < FIRST_WEEK:
            return
        row = self.rows[week_of(stamp)]
        row[0] += sign * float(cost or 0)
        row[1] += sign * (output or 0)
        row[2] += sign

    def get(self, week):
        return self.rows.get(week, [0.0, 0, 0])


class Copies:
    """Rows already kept, looked up by model and the five counts."""

    def __init__(self):
        self.stamps = defaultdict(list)

    @staticmethod
    def key(model, ints):
        return (model,) + tuple(ints[k] for k in COUNTS)

    def add(self, model, ints, stamp):
        self.stamps[self.key(model, ints)].append(stamp)

    def holds(self, model, ints, stamp):
        stamps = self.stamps.get(self.key(model, ints))
        if not stamps:
            return False
        if ints["cache_read"] + ints["cache_create_5m"] + ints["cache_create_1h"] >= COPY_MIN_CACHED:
            return True
        return any(abs((stamp - s).total_seconds()) <= COPY_SECONDS for s in stamps if s)


def print_weeks(title, columns):
    weeks = sorted({w for col in columns.values() for w in col.rows})
    names = list(columns)
    print("\n%s (weeks start Monday, UTC; output in thousands)" % title)
    print("  %-10s" % "week" + "".join(" | %-25s" % n for n in names))
    totals = {n: [0.0, 0, 0] for n in names}
    for week in weeks:
        line = "  %-10s" % week
        for n in names:
            cost, out, calls = columns[n].get(week)
            totals[n] = [totals[n][0] + cost, totals[n][1] + out, totals[n][2] + calls]
            line += " | %10.2f %7d %6d" % (cost, out // 1000, calls)
        print(line)
    print("  %-10s" % "total" + "".join(
        " | %10.2f %7d %6d" % (totals[n][0], totals[n][1] // 1000, totals[n][2]) for n in names))


def odoo_weekly(cr):
    cr.execute("""
        SELECT date_trunc('week', ts)::date, sum(cost), sum(output_tokens), count(*)
          FROM flightdeck_message WHERE ts >= %s GROUP BY 1
    """, (FIRST_WEEK,))
    weekly = Weekly()
    for week, cost, out, calls in cr.fetchall():
        weekly.rows[week] = [float(cost or 0), int(out or 0), int(calls)]
    return weekly


def run(env):
    cr = env.cr
    Price = env["flightdeck.model.price"]
    index = Price.rate_index()
    projects = {p.name: p.id for p in env["flightdeck.project"].search([])}
    sessions = {s.session_id: s.id for s in env["flightdeck.session"].search([])}

    # --- what Odoo holds ----------------------------------------------------
    cr.execute("""
        SELECT id, uuid, session_ref_id, ts, cost, output_tokens
          FROM flightdeck_message WHERE agent_key IS NULL AND ts < %s
    """, (WINDOW_END,))
    replaced = cr.dictfetchall()
    by_id_key = sum(1 for r in replaced if r["uuid"].startswith(("msg_", "resp_")))
    cr.execute("""
        SELECT uuid, model, ts, input_tokens, cache_read, cache_create_5m, cache_create_1h,
               output_tokens
          FROM flightdeck_message WHERE agent_key IS NOT NULL OR ts >= %s
    """, (WINDOW_END,))
    held = cr.dictfetchall()
    held_ids = {r["uuid"] for r in held}
    kept = Copies()
    for r in held:
        kept.add(r["model"], {k: r[k] or 0 for k in COUNTS}, r["ts"])

    # --- what the ledger counts ---------------------------------------------
    src = psycopg2.connect(**LEDGER)
    src.set_session(readonly=True)
    cur = src.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM messages WHERE dup_of IS NULL")
    ledger = []
    for r in cur:
        r = dict(r)
        r["stamp"] = parse_ts(r["ts"])
        ledger.append(r)
    cur.close()
    src.close()
    ledger.sort(key=lambda r: (r["stamp"] or datetime.min, r["uuid"]))

    backend = Weekly()
    todo, skipped, later, no_session = [], defaultdict(int), 0, 0
    copies, copy_cost = Counter(), 0.0
    for r in ledger:
        ints = {k: int(r.get(k) or 0) for k in COUNTS}
        stamp = r["stamp"]
        rate = Price.match(index, r["model"], stamp)
        cost = Price.cost_of(rate, *(ints[k] for k in COUNTS))
        backend.add(stamp, cost, ints["output_tokens"])
        uuid = r["message_id"] or r["uuid"]
        if uuid in held_ids:
            skipped["held under the same id"] += 1
            continue
        if not stamp or stamp >= WINDOW_END:
            later += 1
            continue
        if not r["message_id"] and kept.holds(r["model"], ints, stamp):
            skipped["copy of a row already kept"] += 1
            copies[r["model"]] += 1
            copy_cost += cost
            continue
        kept.add(r["model"], ints, stamp)
        session_ref = sessions.get(r["session_id"])
        no_session += not session_ref
        todo.append(dict(
            uuid=uuid,
            session_ref_id=session_ref,
            project_id=projects.get(r["project"]),
            model=r["model"],
            ts=stamp,
            service_tier=r["service_tier"],
            total_tokens=sum(ints.values()),
            cost=cost,
            priced=bool(rate),
            prompt_id=r["prompt_id"],
            agent_key=r["agent_id"] or "",
            **ints,
        ))

    # --- the plan -----------------------------------------------------------
    print("rows to remove (no agent_key, before %s): %d, $%.2f"
          % (WINDOW_END.date(), len(replaced), sum(float(r["cost"] or 0) for r in replaced)))
    print("  keyed on a transcript line (import_ledger.py): %d" % (len(replaced) - by_id_key))
    print("  keyed on an API message id (rekey_spend.py):   %d" % by_id_key)
    print("ledger calls (dup_of IS NULL): %d" % len(ledger))
    for reason, n in sorted(skipped.items()):
        print("  skipped, %s: %d" % (reason, n))
    print("  copies by model ($%.2f): %s" % (copy_cost, dict(copies.most_common())))
    print("  skipped, on or after %s, left to the reader: %d" % (WINDOW_END.date(), later))
    print("  to insert: %d, $%.2f, unpriced %d, without a session %d"
          % (len(todo), sum(v["cost"] for v in todo), sum(not v["priced"] for v in todo), no_session))
    print("  unpriced by model: %s" % dict(Counter(v["model"] for v in todo if not v["priced"])))

    before = odoo_weekly(cr)
    after = odoo_weekly(cr)
    for r in replaced:
        after.add(r["ts"], r["cost"], r["output_tokens"], sign=-1)
    for v in todo:
        after.add(v["ts"], v["cost"], v["output_tokens"])
    print_weeks("expected", {"odoo now": before, "odoo after": after, "ledger": backend})

    if not APPLY:
        cr.rollback()
        print("\ndry run: nothing written. FD_APPLY=1 writes it.")
        return

    # --- apply, one transaction ---------------------------------------------
    touched = {r["session_ref_id"] for r in replaced} | {v["session_ref_id"] for v in todo}
    touched = sorted(s for s in touched if s)
    cr.execute("DELETE FROM flightdeck_message WHERE agent_key IS NULL AND ts < %s", (WINDOW_END,))
    if cr.rowcount != len(replaced):
        raise RuntimeError("deleted %d rows, expected %d" % (cr.rowcount, len(replaced)))
    Message = env["flightdeck.message"].with_context(fd_no_bus=True)
    Message.invalidate_model()
    for start in range(0, len(todo), BATCH):
        Message.create(todo[start:start + BATCH])
    env.flush_all()

    # The stored session totals, as `import_ledger.py` sets them, for the
    # sessions whose rows changed. On 2026-09-23 none of them was live.
    cr.execute("""
        UPDATE flightdeck_session s SET
            message_count = COALESCE(m.n, 0),
            input_tokens  = COALESCE(m.inp, 0),
            output_tokens = COALESCE(m.outp, 0),
            cache_read    = COALESCE(m.cr, 0),
            cache_create  = COALESCE(m.cc, 0),
            total_cost    = COALESCE(m.cost, 0)
          FROM unnest(%s::int[]) AS base(id)
          LEFT JOIN (SELECT session_ref_id id, count(*) n, sum(input_tokens) inp,
                            sum(output_tokens) outp, sum(cache_read) cr,
                            sum(cache_create_5m + cache_create_1h) cc, sum(cost) cost
                       FROM flightdeck_message WHERE session_ref_id = ANY(%s)
                      GROUP BY 1) m ON m.id = base.id
         WHERE s.id = base.id
    """, (touched, touched))
    print("\nsession totals recomputed: %d" % cr.rowcount)
    env["flightdeck.session"].invalidate_model()

    written = odoo_weekly(cr)
    print_weeks("written, before commit", {"odoo after": written, "ledger": backend})
    cr.commit()
    print("committed: removed %d, inserted %d" % (len(replaced), len(todo)))

    # Separate from the change above: the rollup updates turns in every
    # session, live ones too, and a conflict there must not undo it.
    env["flightdeck.turn"]._rollup()
    cr.commit()
    print("turn rollup done")


run(env)  # noqa: F821  — `env` comes from odoo shell
