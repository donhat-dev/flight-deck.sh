"""Checks of the Type switch (step 3 of the turn labels spec), rolled back at the end.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http --log-level=error < odoo/tests/check_type_switch.py
"""
T = env["flightdeck.turn"]
env.cr.execute("select count(*) from flightdeck_turn where intent in ('correction','answer','question')")
assert env.cr.fetchone()[0] == 0, "old Type values remain"
assert dict(T._fields["intent"].selection) == {"task": "Task", "follow_up": "Follow-up",
    "ask": "Question about the work", "side": "Side question", "no_work": "No work"}
env.cr.execute("select count(*) from flightdeck_turn where label_detail->>'old_intent' in ('correction','answer','question')")
assert env.cr.fetchone()[0] >= 500, "old values not kept"
env.cr.execute("""select count(*) from flightdeck_turn where label_source = 'human'
                  and label_detail->>'old_intent' = 'question' and intent <> 'side'""")
assert env.cr.fetchone()[0] == 0, "a person's side question moved"
for name in ("wrong_results", "revisions", "decisions", "pulls", "redirects", "rules_taught"):
    assert name in T._fields, name
assert "corrections" not in T._fields and "answers" not in T._fields
# A task's counts come from the feedback on its results.
env.cr.execute("""select t.id, t.wrong_results, t.revisions, t.decisions, t.pulls, t.redirects,
                         t.adjustments from flightdeck_turn t where t.is_task order by t.cost desc limit 50""")
rows = env.cr.fetchall()
assert any(r[1] or r[2] or r[3] for r in rows), "no task carries a feedback count"
env.cr.execute("""
  select count(*) from flightdeck_turn t
   where t.is_task and t.adjustments <> coalesce(t.wrong_results, 0) + coalesce(t.redirects, 0)""")
assert env.cr.fetchone()[0] == 0, "adjustments is not wrong_results + redirects"
# Attribution: a label counts on the task of the turn it judges.
env.cr.execute("""
  select t.id, p.task_id, t.task_id from flightdeck_turn t join flightdeck_turn p on p.id = t.previous_turn_id
   join flightdeck_turn_feedback_rel r on r.turn_id = t.id
   join flightdeck_feedback_kind k on k.id = r.kind_id and k.family = 'wrong'
   where p.task_id is not null and t.task_id is not null and p.task_id <> t.task_id limit 1""")
row = env.cr.fetchone()
assert row, "no wrong label judges another task's result: the attribution is not exercised"
judged = T.browse(row[1])
env.cr.execute("""select count(*) from flightdeck_turn_feedback_rel r join flightdeck_turn t on t.id = r.turn_id
  join flightdeck_turn p on p.id = t.previous_turn_id join flightdeck_feedback_kind k on k.id = r.kind_id
  where k.family = 'wrong' and p.task_id = %s""", [judged.id])
from_turns = env.cr.fetchone()[0]
env.cr.execute("""select count(*) from flightdeck_steer_feedback_rel r join flightdeck_turn_steer s on s.id = r.steer_id
  join flightdeck_turn t on t.id = s.turn_id join flightdeck_feedback_kind k on k.id = r.kind_id
  where k.family = 'wrong' and t.task_id = %s""", [judged.id])
from_steers = env.cr.fetchone()[0]
assert judged.wrong_results == from_turns + from_steers, "a label was counted on the wrong task"

# Model stats report every metric.
from odoo.addons.flight_deck.models import model_stats
stats = T.fd_model_stats()
if stats["models"]:
    assert set(stats["models"][0]["metrics"]) == set(model_stats.METRICS), "model stats miss a metric"
# One review queue: a person's Type stays while its Feedback is reviewed.
t = (T.search([("feedback_state", "=", "review")], order="started_at desc", limit=1)
     or T.search([], order="started_at desc", limit=1))
t.with_context(fd_label_engine=True).write({"feedback_state": "review", "label_source": "human", "needs_review": False})
panel = T.fd_task_panel([])
card = next((c for c in panel["review"] if c["id"] == t.id), None)
assert card and card["feedback_review"] and not card["type_review"], "a turn with Feedback to review is missing from the queue"
assert {k["code"] for k in panel["feedback_kinds"]} >= {"defect", "scope"}
T.fd_set_feedback(t.id, ["defect"])
assert t.feedback_source == "human" and t.feedback_state == "done" and t.label_source == "human"
assert t.feedback_main_id.code == "defect"
# Feedback that only asks to explain or report makes an engine's follow-up a question about the work.
E = T.with_context(fd_label_engine=True)
f1, f2, f3 = E.search([("label_source", "in", ("jev", "llm")), ("previous_turn_id", "!=", False),
                       ("feedback_source", "not in", ("human", "reference"))], order="id desc", limit=3)
for f in (f1, f2, f3):
    f.write({"intent": "follow_up"})
f3.write({"label_source": "human"})
pull = {"#%d" % f.id: {"codes": ["explain"], "source": "llm", "confidence": .9, "review": False,
                       "reason": "", "jev": None} for f in (f1, f3)}
pull["#%d" % f2.id] = {"codes": ["explain", "scope"], "source": "llm", "confidence": .9,
                       "review": False, "reason": "", "jev": None}
E._store_feedback(pull, {"#%d" % f.id: f for f in (f1, f2, f3)})
assert f1.intent == "ask" and f1.label_detail.get("retyped"), "a pull-only follow-up kept its Type"
assert f2.intent == "follow_up", "a follow-up that also asks for work moved"
assert f3.intent == "follow_up", "a person's Type moved"
ref = E.search([("feedback_source", "=", "reference"), ("label_source", "in", ("jev", "llm"))], limit=1)
if ref:
    ref.write({"intent": "follow_up"})
    ref._retype_from_feedback()
    assert ref.intent == "follow_up", "a reference label fed the Type"

# A due job waiting past the limit is shown as a stall, and an unlabelled turn names its job.
S = env["flightdeck.session"]
u = E.search([("session_ref_id", "!=", False)], order="id desc", limit=1)
u.write({"label_source": False})
env.cr.execute("""insert into queue_job (uuid, name, state, channel, identity_key, date_created)
                  values (md5(random()::text), 'check stall', 'pending', 'root', %s,
                          now() at time zone 'utc' - interval '20 minutes')""", ["fd-label-%s" % u.session_ref_id.id])
env.invalidate_all()
q = S._fd_queue()
assert q["stalled"] and q["oldest_minutes"] >= 19, q
assert (u.label_queue_note or "").startswith("Label job queued at"), u.label_queue_note
print("CHECKS PASSED")
env.cr.rollback()
