"""Remove the retired "Workflow Studio LLM connector" provider and its model.

The provider read an ``api_key`` secret the LLM connector lost when the AI
harness started answering it; harness_muk_ai's provider serves the chat now.
The rows go rather than being archived: their ``name`` is no longer one of
MuK's provider choices, so an archived row's form would fail to open. Nothing
else points at them (no agent or company default; the model's other links are
set null or cascade), so this is the same removal Odoo applies to data a module
stops shipping.
"""

import logging

_logger = logging.getLogger(__name__)

# Model first: muk_ai_model.provider_id cascades, but deleting it by name keeps
# the count in the log honest.
ROWS = (
    ("model_llm_connector_default", "muk_ai_model"),
    ("provider_llm_connector", "muk_ai_provider"),
)


def migrate(cr, version):
    removed = 0
    for xmlid, table in ROWS:
        cr.execute(
            "SELECT id, res_id FROM ir_model_data WHERE module = 'flight_ai_chat' AND name = %s",
            (xmlid,))
        row = cr.fetchone()
        if not row:
            continue
        data_id, res_id = row
        cr.execute("DELETE FROM %s WHERE id = %%s" % table, (res_id,))
        removed += cr.rowcount
        cr.execute("DELETE FROM ir_model_data WHERE id = %s", (data_id,))
    _logger.info("flight_ai_chat 19.0.1.2.0: %d retired provider row(s) removed", removed)
