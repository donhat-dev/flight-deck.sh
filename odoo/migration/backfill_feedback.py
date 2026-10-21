"""Label every turn and steer message that has no feedback, in passes of 300.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http < odoo/migration/backfill_feedback.py

Pause the "FlightDeck: label feedback" cron first, so its job and this script
do not label the same turns, and turn it back on after. The label check is
skipped by the pass itself. Each pass commits. Stops when a pass labels
nothing new, which also covers the engines being down.
"""
Turn = env["flightdeck.turn"]
Steer = env["flightdeck.turn.steer"]


def left():
    return (Turn.search_count([("feedback_state", "=", "todo"), ("feedback_gold", "=", False)])
            + Steer.search_count([("feedback_state", "=", "todo")]))


while True:
    before = left()
    if not before:
        break
    report = Turn._label_feedback_pass(limit=300)
    print(before, "left |", report["summary"], flush=True)
    if left() >= before:
        break
print("done:", left(), "left", flush=True)
