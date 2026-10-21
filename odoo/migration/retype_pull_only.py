"""Apply the Feedback-to-Type rule to turns labelled before it existed.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http --log-level=warn < odoo/migration/retype_pull_only.py

An engine's follow-up whose Feedback only asks to explain or report becomes `ask`
(`feedback_classifier.type_from_feedback`). Both count as a subtask, so no task's
cost moves; only Type does.
"""
Turn = env["flightdeck.turn"]
moved = Turn.search([("intent", "=", "follow_up"), ("label_source", "in", ("rule", "jev", "llm"))]
                    )._retype_from_feedback()
env.cr.commit()
print("moved to ask:", len(moved), "by source:",
      {s: len(moved.filtered(lambda t: t.label_source == s)) for s in ("rule", "jev", "llm")})
