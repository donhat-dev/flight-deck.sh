"""Checks of the session message scan against the live transcripts, rolled back at the end.

    docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
        -d flightdeck_odoo --no-http --log-level=error < odoo/tests/check_session_messages.py
"""
import time

Session = env["flightdeck.session"]
Scan = env["flightdeck.session.message.scan"]
Message = env["flightdeck.session.message"]
Thread = env["flightdeck.session.thread"]

ORIGINAL, FORKS = "0bfc269a-d2eb-433c-a97c-1fc87bd31ce3", (
    "f927a2b4-61a8-4df4-887e-eb4e315ad83b", "ff3b89bf-22c5-48b8-b65c-7b85858e818d")
FIRST_MESSAGE = "b3a0cea3-1ac7-43d1-830d-58ec1c1f732b"
index = Session._transcript_index()


def session(sid):
    return Session.search([("session_id", "=", sid)], limit=1)


def read(sid):
    found = session(sid)
    cursor = Scan.search([("session_ref_id", "=", found.id)])
    return Scan._read_session(found, index[sid], cursor or None)


Scan.search([]).unlink()
Message.search([]).unlink()
Thread.search([]).unlink()

# 1. A fork read before its original: the original still ends up the recipient.
for sid in FORKS:
    read(sid)
first = Message.search([("msg_id", "=", FIRST_MESSAGE)])
assert len(first) == 1, first
assert first.recipient_session_id.session_id in FORKS
read(ORIGINAL)
first.invalidate_recordset()
assert first.recipient_session_id == session(ORIGINAL), first.recipient_session_id.session_id

# 2. Every other transcript, as the cron reads them.
started = time.time()
for found in Session.search([("session_id", "in", list(index))], order="id"):
    cursor = Scan.search([("session_ref_id", "=", found.id)])
    Scan._read_session(found, index[found.session_id], cursor or None)
print("read every transcript in %.1fs" % (time.time() - started))

messages = Message.search([])
by_state = {state: len(messages.filtered(lambda m, s=state: m.state == s))
            for state in ("pending", "failed", "sent", "received", "delivered")}
print("messages", len(messages), by_state)
assert by_state["delivered"] >= 50, by_state
# Sends to a session's own subagents are answered without an id and dropped;
# what waits is a send whose answer is not written yet.
assert by_state["pending"] <= 3, messages.filtered(lambda m: m.state == "pending").mapped("recipient_label")
assert not messages.filtered(lambda m: m.receive_uuid and not m.msg_id), "a received row without an id"
assert not messages.filtered(lambda m: "[Subagent hand-back]" in (m.body or "")), "a hand-back was read"
assert not messages.filtered(lambda m: "permission laundering" in (m.body or "")), "the CLI note is in a body"
first.invalidate_recordset()
assert first.sender_session_id and first.sender_session_id != first.recipient_session_id

# 3. Reading a file again changes nothing.
before = len(Message.search([]))
cursor = Scan.search([("session_ref_id", "=", session(ORIGINAL).id)])
cursor.read_bytes = 0
read(ORIGINAL)
assert len(Message.search([])) == before, "a second read added rows"

# 4. Conversations: a reply answers the other side, and the totals add up.
threads = Thread.search([])
print("conversations", len(threads))
for thread in threads:
    rows = thread.message_ids.sorted("sequence")
    assert [m.sequence for m in rows] == list(range(1, len(rows) + 1)), thread.name
    assert thread.message_count == len(rows)
    for message in rows.filtered("reply_to_id"):
        answered = message.reply_to_id
        assert answered.thread_id == thread and answered.sequence < message.sequence
        assert answered.sender_session_id == message.recipient_session_id, "a reply to its own side"
        assert message.reply_minutes >= 0
for thread in threads.sorted(lambda t: -t.message_count)[:5]:
    print("  %-90s %3d msgs %3d replies avg %.1f min" % (
        thread.name[:90], thread.message_count, thread.reply_count, thread.avg_reply_minutes))

env.cr.rollback()
print("all checks passed")
