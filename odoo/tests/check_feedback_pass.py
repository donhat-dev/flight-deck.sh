"""Checks of the feedback pass against the live database, rolled back at the end.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http --log-level=error < odoo/tests/check_feedback_pass.py
"""
Turn = env["flightdeck.turn"].with_context(fd_label_engine=True)
Turn._link_previous_turns()
Kind = env["flightdeck.feedback.kind"]
defect, scope = Kind.search([("code", "=", "defect")]), Kind.search([("code", "=", "scope")])
a, b, c = Turn.search([("previous_turn_id", "!=", False)], order="started_at desc", limit=3)

# 1. A person's label survives an engine's.
a.with_context(fd_label_engine=False).write({"feedback_ids": [(6, 0, defect.ids)]})
assert a.feedback_source == "human" and a.feedback_state == "done" and a.feedback_main_id == defect
Turn._store_feedback({"#%d" % a.id: {"codes": ["scope"], "source": "jev", "confidence": .9,
                                      "review": False, "reason": "", "jev": None}}, {"#%d" % a.id: a})
assert a.feedback_ids == defect, "an engine overwrote a person's label"

# 2. An engine label is stored, main first, with its state.
Turn._store_feedback({"#%d" % b.id: {"codes": ["scope", "defect"], "source": "llm", "confidence": .5,
                                      "review": True, "reason": "Luna was not sure", "jev": None}},
                     {"#%d" % b.id: b})
assert b.feedback_main_id == scope and b.feedback_ids == scope | defect
assert b.feedback_state == "review" and b.feedback_source == "llm"

# 3. A label-check turn is not picked before the person labels it.
c.write({"feedback_gold": True, "feedback_state": "todo"})
assert c not in Turn._find_feedback_turns(5000), "the pass picked a label-check turn"

# 4. No answer, no label: the turn stays waiting.
before = c.feedback_state
Turn._store_feedback({}, {"#%d" % c.id: c})
assert c.feedback_state == before == "todo"

# 5. The cron queues one job however often it fires.
Turn._cron_label_feedback()
Turn._cron_label_feedback()
jobs = env["queue.job"].search([("identity_key", "=", "fd-feedback-pass"),
                                ("state", "in", ("pending", "enqueued"))])
assert len(jobs) == 1, jobs

# 6. The label check records "no feedback": saving with nothing ticked makes it the person's.
# Once every check turn is labelled there is none left to save, so one is cleared first.
g = Turn.search([("feedback_gold", "=", True)], limit=1)
g.write({"feedback_source": False, "feedback_state": "todo", "feedback_ids": [(5, 0, 0)],
         "feedback_main_id": False})
g.with_context(fd_label_engine=False).action_save_label()
assert g.feedback_source == "human" and g.feedback_state == "done" and not g.feedback_ids

# 7. Probe and steer runs are not the turn's own feedback calls.
Run = env["flightdeck.label.run"]
steer = env["flightdeck.turn.steer"].search([], limit=1)
own = Run.create({"engine": "jev", "question": "feedback", "turn_id": b.id, "kind": "defect"})
probe = Run.create({"engine": "jev", "question": "feedback", "turn_id": b.id, "kind": "scope", "probe": True})
of_steer = Run.create({"engine": "jev", "question": "feedback", "turn_id": b.id, "kind": "look",
                       "steer_id": steer.id})
b.invalidate_recordset(["feedback_run_ids"])
assert own in b.feedback_run_ids and probe not in b.feedback_run_ids and of_steer not in b.feedback_run_ids

# 8. Jev's unclear answer is kept for the next pass, and cleared once labelled.
jev = {"main": "scope", "p1": .46, "margin": .14, "probs": {"scope": .46}, "clear": False}
d = Turn.search([("feedback_gold", "=", False), ("id", "not in", (a | b | c).ids)], limit=1)
d.write({"feedback_state": "todo", "feedback_source": False})
Turn._store_feedback({}, {"#%d" % d.id: d}, pending={"#%d" % d.id: jev})
assert d.feedback_jev == jev and d.feedback_state == "todo"
assert d not in Turn._find_feedback_turns(5000, llm=False), "Luna is off, yet a turn waiting for it was picked"
assert d in Turn._find_feedback_turns(5000, llm=True)
Turn._store_feedback({"#%d" % d.id: {"codes": ["scope"], "source": "llm", "confidence": .8,
                                      "review": False, "reason": "", "jev": jev}}, {"#%d" % d.id: d})
assert not d.feedback_jev and d.feedback_state == "done"

# 9. Opus's reference label is kept from the engines, and a person's label replaces it.
r = Turn.search([("feedback_gold", "=", True), ("id", "not in", (a | b | c | d | g).ids)], limit=1)
Turn._store_reference(r, {"codes": ["defect"], "type": "follow_up", "no_fit": False, "note": "x"})
assert r.feedback_source == "reference" and r.feedback_main_id == defect and r.check_intent == "follow_up"
Turn._store_feedback({"#%d" % r.id: {"codes": ["scope"], "source": "llm", "confidence": .9,
                                      "review": False, "reason": "", "jev": None}}, {"#%d" % r.id: r})
assert r.feedback_ids == defect, "an engine overwrote Opus's reference label"
assert r in Turn._find_label_check_done()
r.with_context(fd_label_engine=False).write({"feedback_ids": [(6, 0, scope.ids)]})
assert r.feedback_source == "human" and r.feedback_ids == scope

# 10. The report says who labelled the check.
report = Turn._label_check_report()
assert "by Opus" in report["summary"] and report["labelled"] >= 1, report

print("CHECKS PASSED")
env.cr.rollback()
