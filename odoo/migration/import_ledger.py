"""Load the FlightDeck ledger into the Odoo module, once.

Run it under `odoo shell`, which does not autocommit — every phase commits its
own batches:

    docker exec -i flightdeck-odoo19 odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http < odoo/migration/import_ledger.py

The ledger is opened read only. Re-running adds nothing: every phase skips the
natural keys it already finds.
"""
import os
import time
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

LEDGER = dict(
    host=os.environ.get("FD_LEDGER_HOST", "pgcore-17"),
    port=int(os.environ.get("FD_LEDGER_PORT", "5432")),
    dbname=os.environ.get("FD_LEDGER_DB", "flightdeck"),
    user=os.environ.get("FD_LEDGER_USER", "fdreader"),
    password=os.environ["FD_LEDGER_PASSWORD"],
)
TREASURE_ROOT = os.environ.get("FD_TREASURE_ROOT", "/mnt/treasures")
BATCH = 1000


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


class Timer:
    def __init__(self, label):
        self.label = label
        self.rows = 0

    def __enter__(self):
        self.start = time.time()
        print("… %s" % self.label, flush=True)
        return self

    def __exit__(self, *exc):
        print("  %-18s %8d rows  %7.1fs" % (self.label, self.rows, time.time() - self.start),
              flush=True)


def batched(env, model, values, timer):
    """Create in batches, committing each one.

    `fd_no_bus` because a transcript line normally announces itself on the bus;
    during a load of tens of thousands nobody is watching and every send is
    waste.
    """
    Model = env[model].with_context(fd_no_bus=True)
    for start in range(0, len(values), BATCH):
        Model.create(values[start:start + BATCH])
        env.cr.commit()
        timer.rows += len(values[start:start + BATCH])


def run(env):
    src = psycopg2.connect(**LEDGER)
    src.set_session(readonly=True)
    cur = src.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # --- projects -----------------------------------------------------------
    with Timer("projects") as t:
        cur.execute("""
            SELECT DISTINCT project FROM messages WHERE project IS NOT NULL
            UNION SELECT DISTINCT project FROM message_text WHERE project IS NOT NULL
            UNION SELECT DISTINCT project FROM tool_calls WHERE project IS NOT NULL
        """)
        names = sorted(r["project"] for r in cur.fetchall())
        known = {p.name: p.id for p in env["flightdeck.project"].search([])}
        todo = [{"name": n} for n in names if n not in known]
        batched(env, "flightdeck.project", todo, t)
        known = {p.name: p.id for p in env["flightdeck.project"].search([])}

    # --- sessions -----------------------------------------------------------
    # Every session id that appears anywhere, not only the ones that were given
    # a title: a message whose session is missing would have nothing to hang on.
    with Timer("sessions") as t:
        cur.execute("""
            SELECT s.session_id,
                   m.title,
                   m.title_source,
                   (SELECT project FROM messages x
                     WHERE x.session_id = s.session_id AND x.project IS NOT NULL
                     LIMIT 1) AS project
              FROM (SELECT DISTINCT session_id FROM messages WHERE session_id IS NOT NULL
                    UNION SELECT DISTINCT session_id FROM message_text WHERE session_id IS NOT NULL
                    UNION SELECT DISTINCT session_id FROM tool_calls WHERE session_id IS NOT NULL
                    UNION SELECT session_id FROM session_meta) s
              LEFT JOIN session_meta m ON m.session_id = s.session_id
        """)
        rows = cur.fetchall()
        have = set(env["flightdeck.session"].search([]).mapped("session_id"))
        todo = [{
            "session_id": r["session_id"],
            "name": r["title"] or r["session_id"],
            "title_source": r["title_source"] if r["title_source"] in ("custom", "ai") else False,
            "project_id": known.get(r["project"]),
        } for r in rows if r["session_id"] not in have]
        batched(env, "flightdeck.session", todo, t)
    sessions = {s.session_id: s.id for s in env["flightdeck.session"].search([])}

    # --- messages -----------------------------------------------------------
    with Timer("messages") as t:
        index = env["flightdeck.model.price"].rate_index()
        Price = env["flightdeck.model.price"]
        cur.execute("SELECT * FROM messages")
        have = set(env["flightdeck.message"].search([]).mapped("uuid"))
        todo = []
        for r in cur:
            if r["uuid"] in have:
                continue
            ts = parse_ts(r["ts"])
            ints = {k: int(r.get(k) or 0) for k in
                    ("input_tokens", "cache_read", "cache_create_5m",
                     "cache_create_1h", "output_tokens")}
            rate = Price.match(index, r["model"], ts)
            todo.append(dict(
                uuid=r["uuid"],
                session_ref_id=sessions.get(r["session_id"]),
                project_id=known.get(r["project"]),
                model=r["model"],
                ts=ts,
                service_tier=r["service_tier"],
                total_tokens=sum(ints.values()),
                cost=Price.cost_of(rate, ints["input_tokens"], ints["cache_read"],
                                   ints["cache_create_5m"], ints["cache_create_1h"],
                                   ints["output_tokens"]),
                priced=bool(rate),
                **ints,
            ))
        batched(env, "flightdeck.message", todo, t)

    # --- transcript ---------------------------------------------------------
    with Timer("message text") as t:
        cur.execute("SELECT * FROM message_text")
        have = set(env["flightdeck.message.text"].search([]).mapped("uuid"))
        todo = [dict(
            uuid=r["uuid"],
            session_ref_id=sessions.get(r["session_id"]),
            project_id=known.get(r["project"]),
            role=r["role"],
            ts=parse_ts(r["ts"]),
            seq=r["seq"] or 0,
            text=r["text"],
            tool_input=r["tool_input"],
        ) for r in cur if r["uuid"] not in have]
        batched(env, "flightdeck.message.text", todo, t)

    # --- tool calls ---------------------------------------------------------
    with Timer("tool calls") as t:
        cur.execute("SELECT * FROM tool_calls")
        have = set(env["flightdeck.tool.call"].search([]).mapped("call_id"))
        todo = [dict(
            call_id=r["id"],
            session_ref_id=sessions.get(r["session_id"]),
            project_id=known.get(r["project"]),
            ts=parse_ts(r["ts"]),
            tool=r["tool"],
            server=r["server"],
            detail=r["detail"],
        ) for r in cur if r["id"] not in have]
        batched(env, "flightdeck.tool.call", todo, t)

    # --- treasures ----------------------------------------------------------
    with Timer("treasures") as t:
        cur.execute("SELECT * FROM treasures")
        rows = cur.fetchall()
        cur.execute("SELECT * FROM treasure_tags")
        tags_by_treasure = {}
        for r in cur:
            tags_by_treasure.setdefault(r["treasure_id"], []).append(r["tag"])

        Tag = env["flightdeck.treasure.tag"]
        wanted = sorted({tag for tags in tags_by_treasure.values() for tag in tags})
        tag_ids = {tg.name: tg.id for tg in Tag.search([])}
        for name in wanted:
            if name not in tag_ids:
                tag_ids[name] = Tag.create({"name": name}).id
        env.cr.commit()

        have = set(env["flightdeck.treasure"].search([]).mapped("treasure_id"))
        status_map = {"draft": "draft", "ready": "ready", "published": "published"}
        todo = [dict(
            treasure_id=r["id"],
            name=r["title"],
            slug=r["slug"],
            dir_path=r["dir_path"],
            kind=r["kind"],
            language=r["language"],
            status=status_map.get(r["status"], "draft"),
            version=r["version"] or 1,
            source_format=r["source_format"],
            render_bytes=r["render_bytes"] or 0,
            origin_kind=r["origin_kind"],
            origin_id=r["origin_id"],
            origin_session_id=sessions.get(r["origin_id"]),
            origin_path=r["origin_path"],
            published_url=r["published_url"],
            authored_at=parse_ts(r["authored_at"]),
            ingested_at=parse_ts(r["ingested_at"]),
            updated_at=parse_ts(r["updated_at"]),
            font=r["font"],
            tag_ids=[(6, 0, [tag_ids[x] for x in tags_by_treasure.get(r["id"], [])])],
        ) for r in rows if r["id"] not in have]
        batched(env, "flightdeck.treasure", todo, t)

    # --- rendered pages -----------------------------------------------------
    # The filestore is the system of record for content; the index only points
    # at it. The page is attached so the browser can be handed a document.
    with Timer("artifacts") as t:
        Attachment = env["ir.attachment"]
        for rec in env["flightdeck.treasure"].search([("attachment_id", "=", False)]):
            folder = os.path.basename(rec.dir_path or "")
            path = os.path.join(TREASURE_ROOT, folder, "v%d" % rec.version, "artifact.html")
            if not folder or not os.path.exists(path):
                continue
            with open(path, "rb") as fh:
                raw = fh.read()
            rec.attachment_id = Attachment.create({
                "name": "%s-v%d.html" % (rec.slug or rec.treasure_id, rec.version),
                "raw": raw,
                "mimetype": "text/html",
                "res_model": "flightdeck.treasure",
                "res_id": rec.id,
            }).id
            t.rows += 1
            if t.rows % 20 == 0:
                env.cr.commit()
        env.cr.commit()

    # --- session project ----------------------------------------------------
    # Most sessions carry transcript lines but no token rows, so a project taken
    # from `messages` alone leaves them ungrouped. Fall back through the other
    # two tables before giving up.
    with Timer("session project") as t:
        env.cr.execute("""
            UPDATE flightdeck_session s SET project_id = p.project_id
              FROM (SELECT session_ref_id, min(project_id) project_id FROM (
                        SELECT session_ref_id, project_id FROM flightdeck_message
                        UNION ALL
                        SELECT session_ref_id, project_id FROM flightdeck_message_text
                        UNION ALL
                        SELECT session_ref_id, project_id FROM flightdeck_tool_call) u
                     WHERE project_id IS NOT NULL AND session_ref_id IS NOT NULL
                     GROUP BY session_ref_id) p
             WHERE s.id = p.session_ref_id AND s.project_id IS NULL
        """)
        t.rows = env.cr.rowcount
        env.cr.commit()

    # --- session totals -----------------------------------------------------
    # Once, at the end. Recomputing while loading is what turns minutes into hours.
    with Timer("session totals") as t:
        env.cr.execute("""
            UPDATE flightdeck_session s SET
                message_count   = COALESCE(m.n, 0),
                input_tokens    = COALESCE(m.inp, 0),
                output_tokens   = COALESCE(m.outp, 0),
                cache_read      = COALESCE(m.cr, 0),
                cache_create    = COALESCE(m.cc, 0),
                total_cost      = COALESCE(m.cost, 0),
                start_ts        = COALESCE(m.first_ts, x.first_ts),
                end_ts          = COALESCE(m.last_ts, x.last_ts),
                duration_hours  = COALESCE(EXTRACT(EPOCH FROM (
                                      COALESCE(m.last_ts, x.last_ts) - COALESCE(m.first_ts, x.first_ts)
                                  )) / 3600.0, 0),
                text_count      = COALESCE(x.n, 0),
                tool_call_count = COALESCE(c.n, 0)
            FROM (SELECT id FROM flightdeck_session) base
            LEFT JOIN (SELECT session_ref_id id, count(*) n, sum(input_tokens) inp,
                              sum(output_tokens) outp, sum(cache_read) cr,
                              sum(cache_create_5m + cache_create_1h) cc, sum(cost) cost,
                              min(ts) first_ts, max(ts) last_ts
                         FROM flightdeck_message GROUP BY 1) m ON m.id = base.id
            LEFT JOIN (SELECT session_ref_id id, count(*) n, min(ts) first_ts, max(ts) last_ts
                         FROM flightdeck_message_text GROUP BY 1) x ON x.id = base.id
            LEFT JOIN (SELECT session_ref_id id, count(*) n
                         FROM flightdeck_tool_call GROUP BY 1) c ON c.id = base.id
            WHERE s.id = base.id
        """)
        t.rows = env.cr.rowcount
        env.cr.commit()

    cur.close()
    src.close()

    counts = {m: env[m].search_count([]) for m in (
        "flightdeck.project", "flightdeck.session", "flightdeck.message",
        "flightdeck.message.text", "flightdeck.tool.call", "flightdeck.treasure")}
    print("\nin Odoo now:")
    for name, n in counts.items():
        print("  %-26s %8d" % (name, n))


run(env)  # noqa: F821  — `env` comes from odoo shell
