"""Turns get an intent; the kind now follows from it.

A task and a turn with no work keep their meaning. An old subtask was any of
a follow-up, a correction or an answer, so its intent is left empty until the
label pass is run on it again. Jev answers kept for turns waiting for Luna
were to the old two-way question and are dropped, so Jev is asked again.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE flightdeck_turn SET intent = kind
         WHERE kind IN ('task', 'no_work') AND intent IS NULL
    """)
    _logger.info("flight_deck: %s turn(s) given an intent from their kind", cr.rowcount)
    cr.execute("""
        UPDATE flightdeck_turn SET jev_probs = NULL, jev_kind = NULL, jev_top1 = 0, jev_margin = 0
         WHERE label_source IS NULL AND jev_probs IS NOT NULL
    """)
    # The seed tags are not updated on an upgrade.
    cr.execute("""
        UPDATE flightdeck_task_tag SET path_pattern = 'flight-deck.sh,nakivo-graph,/.claude'
         WHERE path_pattern IS NULL AND id = (
               SELECT res_id FROM ir_model_data
                WHERE module = 'flight_deck' AND name = 'tag_harness')
    """)
