"""Re-key the spend rows of every session whose transcript is still on disk.

Run it under `odoo shell`, once, before the scan cron is left to run:

    docker exec -i flightdeck-odoo19 odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http < clients/odoo/migration/rekey_spend.py

Why it exists. The ledger keyed a billed turn on the transcript line it was
read from, but one API response is written as several lines — thinking, text
and each tool call get their own — and every one of them repeats the whole
usage block. Measured on one long session, that counts 1.94x to 2.40x the
tokens actually billed. The module now keys on the API message id instead, so
the stored rows have to be rebuilt under the same key or the two would sit in
one pivot and neither number would mean anything.

Every other session is left at offset zero on purpose: the whole library is
under a gigabyte, and the scan cron reads it a bounded slice at a time, so the
sessions that never had spend rows gain them without a second migration.

What it does not touch: a session whose transcript is gone. Its rows cannot be
rebuilt from anything, and the stored id is a line id with no way back to the
response it belonged to, so those sessions keep the numbers they have. The
script prints which they are.
"""
import os

TRANSCRIPT_ROOT = "/mnt/claude-projects"


def run(env):
    Session = env["flightdeck.session"]
    index = Session._transcript_index()
    with_spend = Session.search([("message_count", ">", 0)]) | Session.search(
        [("usage_ids", "!=", False)])

    rebuilt = skipped = rows_before = rows_after = 0
    for session in with_spend:
        held = len(session.usage_ids)
        if session.session_id not in index:
            skipped += 1
            print("  kept as is (no transcript): %s  %s rows" % (session.session_id, held))
            continue
        rows_before += held
        session.usage_ids.unlink()
        session.write({
            "message_count": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read": 0, "cache_create": 0, "total_cost": 0, "spend_bytes": 0,
        })
        env.cr.commit()
        session.action_rescan_spend()
        rows_after += session.message_count
        rebuilt += 1
        print("  rebuilt %s  %s -> %s rows  $%.2f"
              % (session.session_id, held, session.message_count, session.total_cost))

    print("rebuilt %s session(s): %s rows -> %s" % (rebuilt, rows_before, rows_after))
    print("kept unchanged (transcript gone): %s session(s)" % skipped)
    print("left at zero for the scan to read: %s file(s), %.2f GB"
          % (len(index), sum(os.path.getsize(p) for p in index.values()) / 1e9))


run(env)  # noqa: F821 — `env` is what odoo shell puts in scope
