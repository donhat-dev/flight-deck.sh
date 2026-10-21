"""Ask Jev and Luna the Type of the old `question` turns an engine had labelled.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http --log-level=warn < clients/odoo/migration/retype_questions.py

Run once after the 19.0.1.6.0 upgrade. They were moved to `side` for the upgrade;
the label pass now picks `ask` or `side`, and `ask` turns get a parent, like any
follow-up. A new label drops the `retype` mark and keeps `old_intent`, so a turn
no engine answered is picked again by a second run. Prints the split and checks
that no ask turn is left without a parent.
"""
Turn = env["flightdeck.turn"]
todo = Turn.search([("label_source", "!=", "human")]).filtered(
    lambda t: (t.label_detail or {}).get("retype"))
print("to ask again:", len(todo), flush=True)
if todo:
    Turn._label_turns(todo, force=True, commit=True)
    env.cr.commit()
todo.invalidate_recordset()
split = {}
for t in todo:
    key = "%s (still marked)" % t.intent if (t.label_detail or {}).get("retype") else t.intent
    split[key] = split.get(key, 0) + 1
print("now:", split)
lost = todo.filtered(lambda t: not (t.label_detail or {}).get("old_intent"))
print("old_intent lost:", len(lost))
orphans = Turn.search([("intent", "=", "ask"), ("parent_id", "=", False)])
print("ask without a parent:", len(orphans), "of which with an earlier turn:",
      len(orphans.filtered("previous_turn_id")))
