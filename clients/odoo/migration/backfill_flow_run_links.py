"""Link the code runs written before run-record links (ADR-015) to their records, once.

Run it under `odoo shell`:

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http < clients/odoo/migration/backfill_flow_run_links.py

Running it again adds nothing: a link already there is skipped. What it links, as the
recorder does today:

- every code run: the records each flow segment ran on, read from its step's input;
- "Label new turns" runs: the turns their steps name as "#<id>", from the first step
  that names each, and the engine calls stored for those turns while the segment ran.

Records deleted since are left out. Set DRY_RUN to count without writing.
"""
from datetime import timedelta

from odoo.addons.workflow_studio.tracking import recorder
from odoo.addons.workflow_engine.tracking.state import SEGMENT_STRIDE

DRY_RUN = False
LABEL_FLOW = "flight_deck.label_new_turns"
# Run times are stored to the second; engine calls to the microsecond.
SLACK = timedelta(seconds=1)


def existing(env, model, ids):
    if model not in env or not ids:
        return set()
    return set(env[model].browse(sorted(ids)).exists().ids)


def links_of(env, run):
    links = {}
    steps = run.node_run_ids.sorted("sequence")
    for step in steps.filtered(lambda s: s.node_type == "code_flow"):
        data = step.input_data if isinstance(step.input_data, dict) else {}
        me = data.get("self") if isinstance(data.get("self"), dict) else {}
        model, ids = me.get("model"), me.get("ids") or []
        for res_id in existing(env, model, ids):
            links.setdefault((model, res_id), step.span_id)
    if run.workflow_id.flow_key != LABEL_FLOW:
        return links
    named, by_segment = {}, {}
    for step in steps:
        # Runs recorded before `{"result": x}` wrapping may hold a bare value.
        for key in step.output_data if isinstance(step.output_data, dict) else ():
            if isinstance(key, str) and key.startswith("#") and key[1:].isdigit():
                named.setdefault(int(key[1:]), step)
                by_segment.setdefault(step.segment, set()).add(int(key[1:]))
    turns = existing(env, "flightdeck.turn", named)
    for turn_id in turns:
        links.setdefault(("flightdeck.turn", turn_id), named[turn_id].span_id)
    for root in steps.filtered(lambda s: s.sequence % SEGMENT_STRIDE == 0):
        segment_turns = sorted(turns & by_segment.get(root.segment, set()))
        if not (segment_turns and root.started_at and root.completed_at):
            continue
        calls = env["flightdeck.label.run"].search([
            ("turn_id", "in", segment_turns),
            ("create_date", ">=", root.started_at - SLACK),
            ("create_date", "<=", root.completed_at + SLACK),
        ])
        for call in calls:
            links.setdefault(("flightdeck.label.run", call.id), root.span_id)
    return links


def run(env):
    Link = env["workflow.run.record"]
    before = Link.search_count([])
    by_model = {}
    runs = env["workflow.run"].search([("execution_mode", "=", "code")], order="id")
    for code_run in runs:
        links = links_of(env, code_run)
        for model, _res_id in links:
            by_model[model] = by_model.get(model, 0) + 1
        recorder._write_links(env, code_run.id, links)
    added = Link.search_count([]) - before
    print("runs: %d, links found: %s, new rows: %d" % (len(runs), by_model, added))
    if DRY_RUN:
        env.cr.rollback()
        print("dry run: rolled back")
    else:
        env.cr.commit()


run(env)  # noqa: F821 - odoo shell provides it
