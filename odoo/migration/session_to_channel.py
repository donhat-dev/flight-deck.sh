"""Put one session's transcript into a Discuss channel.

Discuss reads a thread the way a transcript is written — oldest first, full
width, scrolling instead of paging — which is what this tries.

    docker exec -i -e FD_CHANNEL_SESSION=432 flightdeck-odoo19 /entrypoint.sh \
        odoo shell -c /etc/odoo/odoo.conf -d flightdeck_odoo --no-http \
        < odoo/migration/session_to_channel.py

The channel is dropped and rebuilt on every run, so a session never ends up
with two of them.
"""
import os
import time
from datetime import timedelta

from markupsafe import Markup

SESSION_ID = int(os.environ.get("FD_CHANNEL_SESSION", "432"))
BATCH = 200

ROLE_PARTNER = {
    "user": "flight_deck.partner_transcript_user",
    "assistant": "flight_deck.partner_transcript_assistant",
}
ROLE_SUBTYPE = {
    "user": "flight_deck.mt_turn_user",
    "assistant": "flight_deck.mt_turn_assistant",
}


def body_of(line):
    """Same shape as the chatter version: the role rides on a class of its own,
    because a subtype name is translated and cannot be branched on."""
    out = Markup('<div class="fd_chat_turn fd_role_%s">') % (line.role or "unknown")
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

    Channel = env["discuss.channel"]
    old = Channel.search([("session_id", "=", session.id)])
    if old:
        print("dropping %d existing channel(s)" % len(old))
        env["mail.message"].search([
            ("model", "=", "discuss.channel"), ("res_id", "in", old.ids)]).unlink()
        old.unlink()
        env.cr.commit()

    channel = Channel.create({
        "name": session.name,
        "channel_type": "channel",
        "session_id": session.id,
        "description": "Transcript of session %s" % session.session_id,
    })
    # Membership is what puts the channel in the sidebar; without it the record
    # exists and nobody sees it. `odoo shell` runs as OdooBot, so adding
    # `env.user` alone puts the channel in a sidebar no person ever opens —
    # every internal user is added instead.
    readers = env["res.users"].search([("share", "=", False), ("active", "=", True)])
    channel._add_members(users=readers, post_joined_message=False)
    env.cr.commit()
    print("channel %s — %s" % (channel.id, channel.name))

    partners = {role: env.ref(xid).id for role, xid in ROLE_PARTNER.items()}
    subtypes = {role: env.ref(xid).id for role, xid in ROLE_SUBTYPE.items()}
    fallback = env.ref("mail.mt_comment").id

    lines = env["flightdeck.message.text"].search(
        [("session_ref_id", "=", session.id)], order="seq asc")
    start = time.time()
    values = [{
        "model": "discuss.channel",
        "res_id": channel.id,
        "record_name": session.name,
        "message_type": "comment",
        "subtype_id": subtypes.get(line.role, fallback),
        "author_id": partners.get(line.role),
        # `seq` in the microseconds: many turns share a timestamp to the second
        # and the thread is ordered by date.
        "date": (line.ts + timedelta(microseconds=line.seq % 1000000)) if line.ts else False,
        "body": body_of(line),
    } for line in lines]

    Message = env["mail.message"]
    for i in range(0, len(values), BATCH):
        Message.create(values[i:i + BATCH])
        env.cr.commit()
    print("posted %d messages in %.1fs" % (len(values), time.time() - start))

    kept = Message.search_count([("model", "=", "discuss.channel"), ("res_id", "=", channel.id)])
    print("in the channel: %d messages" % kept)
    print("open it at /odoo/discuss with channel id %s" % channel.id)


run(env)  # noqa: F821 — `env` comes from odoo shell
