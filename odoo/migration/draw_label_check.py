"""Pick the 60 prompts of the label check.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http < odoo/migration/draw_label_check.py

A random sample (fixed seed, so it can be drawn again the same way) of the
ended turns of the last DAYS days that have a turn before them in their
session and are not scripted or empty. The most recent turns alone are one
day of one project's work.

Running it again adds nothing once SIZE are drawn. REDRAW releases the
current set and draws again; it is refused once the person has labelled any
of it. A drawn turn an engine already labelled is reset, so the person
labels it without seeing the engine's answer; released turns go back to the
feedback pass. Set DRY_RUN to list without writing.
"""
import random
from datetime import timedelta

from odoo import fields

DRY_RUN = False
REDRAW = False
SIZE = 60
DAYS = 30
SEED = 20260928

Turn = env["flightdeck.turn"].with_context(fd_label_engine=True)
Turn._link_previous_turns()
gold = Turn.search([("feedback_gold", "=", True)])
if REDRAW and gold:
    if gold.filtered(lambda t: t.feedback_source == "human"):
        raise SystemExit("The person has labelled part of the label check; it is not redrawn.")
    if not DRY_RUN:
        gold.write({"feedback_gold": False, "feedback_state": "todo"})
    print("released %d turns" % len(gold))
    gold = Turn.browse()
since = fields.Datetime.now() - timedelta(days=DAYS)
eligible = Turn.search([("feedback_gold", "=", False), ("feedback_source", "!=", "human"),
                        ("previous_turn_id", "!=", False), ("intent", "not in", ("no_work", False)),
                        ("ended_at", "!=", False), ("started_at", ">=", since)], order="id")
todo = Turn.browse(sorted(random.Random(SEED).sample(eligible.ids, min(max(SIZE - len(gold), 0),
                                                                        len(eligible)))))
for t in todo:
    print(t.id, t.started_at.date(), t.intent, (t.prompt_head or "")[:60].replace("\n", " "))
if not DRY_RUN and todo:
    todo.write({"feedback_gold": True, "feedback_state": "todo", "feedback_ids": [(5, 0, 0)],
                "feedback_main_id": False, "feedback_source": False, "feedback_jev": False})
env.cr.commit()
print("label check: %d drawn now, %d in all, from %d eligible" % (len(todo), len(gold) + len(todo),
                                                                   len(eligible)))
