"""Render one session's transcript into its chatter, as `mail.message` rows.

A display experiment: the same lines the Transcript tab shows are written as
chatter messages so the two renderings can be read side by side.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell \
        -c /etc/odoo/odoo.conf -d flightdeck_odoo --no-http \
        < odoo/migration/transcript_to_chatter.py

Set FD_CHATTER_SESSION to a `flightdeck.session` id. Re-running clears what it
wrote before, so the session never ends up with two copies of its transcript.
"""
import os
import time
from datetime import timedelta

from markupsafe import Markup, escape

SESSION_ID = int(os.environ.get("FD_CHATTER_SESSION", "432"))
BATCH = 200

ROLE_PARTNER = {
    "user": "flight_deck.partner_transcript_user",
    "assistant": "flight_deck.partner_transcript_assistant",
}

# A subtype per role rather than one `mt_comment` for everything: it is what the
# chatter prints beside the author, and what the role filter reads.
ROLE_SUBTYPE = {
    "user": "flight_deck.mt_turn_user",
    "assistant": "flight_deck.mt_turn_assistant",
}


def body_of(line):
    """The turn as HTML.

    Text is escaped and kept in a `pre` because a transcript carries its own
    indentation and markdown, and the chatter would otherwise collapse both.
    Tool input goes in a `details` so a turn stays readable until someone opens
    it — whether the sanitizer keeps that element is the thing this tries.
    """
    # The role travels as a class on the turn itself. The subtype carries the
    # label a reader sees, but its name is a translated field, so matching on it
    # in the browser would break the filter under any other language.
    out = Markup('<div class="fd_chat_turn fd_role_%s">') % (line.role or "unknown")
    # A turn that only ran a tool has no prose; an empty `pre` would render as a
    # blank box above the fold.
    if line.text:
        out += Markup('<pre class="fd_chat_text">%s</pre>') % line.text
    if line.tool_input:
        out += Markup(
            '<details class="fd_chat_tool"><summary>Tool input</summary>'
            '<pre>%s</pre></details>'
        ) % line.tool_input
    return out + Markup("</div>")


def run(env):
    session = env["flightdeck.session"].browse(SESSION_ID).exists()
    if not session:
        print("no session %s" % SESSION_ID)
        return
    Message = env["mail.message"]
    subtypes = {role: env.ref(xid).id for role, xid in ROLE_SUBTYPE.items()}
    fallback_subtype = env.ref("mail.mt_comment").id
    partners = {role: env.ref(xid).id for role, xid in ROLE_PARTNER.items()}

    old = Message.search([("model", "=", "flightdeck.session"), ("res_id", "=", session.id)])
    if old:
        print("clearing %d existing messages" % len(old))
        old.unlink()
        env.cr.commit()

    lines = env["flightdeck.message.text"].search(
        [("session_ref_id", "=", session.id)], order="seq asc")
    print("session %s — %s — %d lines" % (session.id, session.name, len(lines)))

    start = time.time()
    values = []
    for line in lines:
        # `seq` in the microseconds because many turns share a timestamp to the
        # second, and the chatter orders by date alone.
        date = (line.ts + timedelta(microseconds=line.seq % 1000000)) if line.ts else False
        values.append({
            "model": "flightdeck.session",
            "res_id": session.id,
            "record_name": session.name,
            "message_type": "comment",
            "subtype_id": subtypes.get(line.role, fallback_subtype),
            "author_id": partners.get(line.role),
            "date": date,
            "body": body_of(line),
        })
    created = 0
    for i in range(0, len(values), BATCH):
        Message.create(values[i:i + BATCH])
        env.cr.commit()
        created += len(values[i:i + BATCH])
    print("created %d messages in %.1fs" % (created, time.time() - start))

    kept = Message.search_count([("model", "=", "flightdeck.session"), ("res_id", "=", session.id)])
    with_tool = Message.search_count([
        ("model", "=", "flightdeck.session"), ("res_id", "=", session.id),
        ("body", "like", "%fd_chat_tool%")])
    print("in the chatter: %d messages, %d carry a tool block" % (kept, with_tool))


run(env)  # noqa: F821 — `env` comes from odoo shell
