"""Print every task's cost and turn count, to compare before and after a rollup change.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http --log-level=warn < clients/odoo/migration/snapshot_task_costs.py

Prints one line, `SNAPSHOT {json}`, of `{task id: [cost, turn count]}`.
"""
import json

env.cr.execute("SELECT id, cost, turn_count FROM flightdeck_turn WHERE is_task ORDER BY id")
print("SNAPSHOT", json.dumps({i: [round(c or 0, 4), n or 0] for i, c, n in env.cr.fetchall()}))
