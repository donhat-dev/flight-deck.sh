"""Rename the shipped provider's data rows to neutral names (the endpoint is now
the OpenCode bridge, not FreeLLMAPI), so the noupdate records are found, not
duplicated, and give the chat model the bridge's default model id."""
import logging

_logger = logging.getLogger(__name__)

RENAMES = {
    "provider_freellmapi": "provider_llm_connector",
    "model_freellmapi_auto": "model_llm_connector_default",
}
BRIDGE_DEFAULT_MODEL = "opencode/deepseek-v4.1-flash-free"


def migrate(cr, version):
    for old, new in RENAMES.items():
        cr.execute(
            """
            UPDATE ir_model_data SET name = %s
            WHERE module = 'flight_ai_chat' AND name = %s
              AND NOT EXISTS (SELECT 1 FROM ir_model_data
                              WHERE module = 'flight_ai_chat' AND name = %s)
            """,
            (new, old, new),
        )
    # The shipped model record was "auto"; the bridge's default model is a real id.
    cr.execute(
        """
        UPDATE muk_ai_model SET technical_name = %s, name = 'DeepSeek V4.1 Flash (free)',
               notes = 'The OpenCode bridge''s default model.'
        WHERE technical_name = 'auto'
          AND provider_id = (SELECT res_id FROM ir_model_data
                             WHERE module = 'flight_ai_chat' AND name = 'provider_llm_connector')
          AND NOT EXISTS (SELECT 1 FROM muk_ai_model other
                          WHERE other.provider_id = muk_ai_model.provider_id
                            AND other.technical_name = %s AND other.id != muk_ai_model.id)
        """,
        (BRIDGE_DEFAULT_MODEL, BRIDGE_DEFAULT_MODEL),
    )
    if cr.rowcount:
        _logger.info("flight_ai_chat: the chat model is now %s.", BRIDGE_DEFAULT_MODEL)
