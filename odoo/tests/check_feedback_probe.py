"""Checks of the Feedback question on the Jev and LLM analytics pages, rolled back at the end.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http --log-level=error < odoo/tests/check_feedback_probe.py

Asks Jev once (about 1 s) and Luna once (about 30 s).
"""
Probe = env["flightdeck.label.probe"]
Run = env["flightdeck.label.run"]

# 1. The figures of each question count only that question's calls.
fb = Probe.create({"engine": "jev", "question": "feedback"})
ty = Probe.create({"engine": "jev", "question": "type"})
fb_calls = Run.search_count([("engine", "=", "jev"), ("question", "=", "feedback"), ("probe", "=", False)])
assert fb.calls <= fb_calls and fb.calls > 0, (fb.calls, fb_calls)
assert ty.calls > 0 and fb.kind_mix != ty.kind_mix

# 2. Try it with Jev: turn 1748 says the result does not work.
p = Probe.create({"engine": "jev", "question": "feedback", "source": "turn", "turn_id": 1748})
p.action_run()
assert p.result_kind == "defect", p.result_kind
assert Run.search([("probe", "=", True), ("question", "=", "feedback")], order="id desc", limit=1).kind == "defect"

# 3. Try it with Luna on my own text.
q = Probe.create({"engine": "llm", "question": "feedback", "source": "custom",
                  "custom_prompt": "The export still crashes on an empty report, it does not work",
                  "custom_previous": "Add a CSV export to the report page"})
q.action_run()
assert q.result_kind and "defect" in q.result_kind.split(","), (q.result_kind, q.result_error)

print("CHECKS PASSED")
env.cr.rollback()
