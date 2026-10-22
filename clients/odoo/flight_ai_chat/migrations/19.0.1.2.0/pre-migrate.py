"""Archive the retired "Workflow Studio LLM connector" provider and its model.

The provider read an ``api_key`` secret the LLM connector lost when the AI
harness started answering it; harness_muk_ai's provider serves the chat now.
The rows are archived, not deleted, so a chat that named them keeps its
history; their xmlids are noupdate and stay.
"""

import logging

_logger = logging.getLogger(__name__)

ROWS = (
    ("provider_llm_connector", "muk_ai_provider"),
    ("model_llm_connector_default", "muk_ai_model"),
)


def migrate(cr, version):
    archived = 0
    for xmlid, table in ROWS:
        cr.execute(
            "SELECT res_id FROM ir_model_data WHERE module = 'flight_ai_chat' AND name = %s",
            (xmlid,))
        row = cr.fetchone()
        if not row:
            continue
        cr.execute("UPDATE %s SET active = false WHERE id = %%s AND active" % table, (row[0],))
        archived += cr.rowcount
    _logger.info("flight_ai_chat 19.0.1.2.0: %d retired provider row(s) archived", archived)
