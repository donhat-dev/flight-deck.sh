"""Give the messages from other sessions, already imported as user lines, role `peer`.

Only lines that started a turn were imported: a message queued into a running
turn arrives as an attachment, which the reader skipped. Their text is the
wrapper the CLI shows the model; the sender's socket and name move to their
own columns before the wrapper is cut off, so nothing the line said is lost.
A subagent's hand-back uses the same opening sentence with an `agent-message`
tag; it is not a message between sessions and stays as it is.
"""

PATTERN = (r'^Another Claude session sent a message:\s*'
           r'<cross-session-message from="([^"]*)" from-name="([^"]*)"[^>]*>\s*')
# The CLI's own note to the model follows the closing tag; it is not the message.
TAIL = r'\s*</cross-session-message>.*$'


def migrate(cr, version):
    cr.execute(r"""
        UPDATE flightdeck_message_text
           SET role = 'peer',
               peer_mode = 'turn',
               peer_from = substring(text from %(pattern)s),
               peer_name = (regexp_match(text, %(pattern)s))[2],
               text = regexp_replace(regexp_replace(text, %(pattern)s, ''), %(tail)s, '')
         WHERE role = 'user' AND text ~ %(pattern)s
    """, {"pattern": PATTERN, "tail": TAIL})
