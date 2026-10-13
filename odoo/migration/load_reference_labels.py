"""Write Opus's reference labels onto the label-check turns.

The labels come from a JSON file (a list of {id, type, feedback, no_fit, note}),
appended to this script as a call, since the container cannot read host paths:

    ( cat odoo/migration/load_reference_labels.py
      printf "\\nload(json.loads(r'''%s'''))\\n" "$(cat labels-all.json)" ) |
    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \\
        -d flightdeck_odoo --no-http

They are stored with source "Opus (reference)", never as a person's. A turn the
person labelled keeps their label; a turn outside the label check is refused.
"""
import json  # noqa: F401  (the appended call parses the labels with it)


def load(labels):
    Turn = env["flightdeck.turn"]
    wrote = kept = refused = 0
    for label in labels:
        turn = Turn.browse(label["id"]).exists()
        if not turn or not turn.feedback_gold:
            refused += 1
            continue
        if Turn._store_reference(turn, {"codes": label["feedback"], "type": label["type"],
                                        "no_fit": label["no_fit"], "note": label.get("note")}):
            wrote += 1
        else:
            kept += 1
    env.cr.commit()
    print("reference labels: %d written, %d kept (a person's), %d refused (not in the check)"
          % (wrote, kept, refused))
