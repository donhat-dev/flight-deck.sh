"""Append one turn to a session, the way anything inside Odoo would.

    docker exec -i -e FD_SESSION=432 flightdeck-odoo19 /entrypoint.sh odoo shell \
        -c /etc/odoo/odoo.conf -d flightdeck_odoo --no-http < odoo/migration/inject_turn.py

Nothing here touches the bus. Creating the row is what sends the notification,
so any other writer in Odoo gets the same behaviour for free.
"""
import os
import uuid
from datetime import datetime

SESSION_ID = int(os.environ.get("FD_SESSION", "432"))
ROLE = os.environ.get("FD_ROLE", "assistant")
TEXT = os.environ.get("FD_TEXT") or (
    "## Live turn\n\nThis arrived over the bus at **%s**, with `no reload`."
    % datetime.now().strftime("%H:%M:%S")
)


def run(env):
    session = env["flightdeck.session"].browse(SESSION_ID).exists()
    if not session:
        print("no session %s" % SESSION_ID)
        return
    last = env["flightdeck.message.text"].search(
        [("session_ref_id", "=", session.id)], order="seq desc", limit=1)
    line = env["flightdeck.message.text"].create({
        "uuid": str(uuid.uuid4()),
        "session_ref_id": session.id,
        "project_id": session.project_id.id,
        "role": ROLE,
        "ts": datetime.now(),
        "seq": (last.seq or 0) + 1,
        "text": TEXT,
    })
    env.cr.commit()
    print("sent turn %s (seq %s, role %s) on channel of session %s"
          % (line.id, line.seq, line.role, session.id))


run(env)  # noqa: F821 — `env` comes from odoo shell
