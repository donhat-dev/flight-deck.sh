"""Move Type to the values of step 3 of the turn labels spec, before the Selection changes.

`correction` and `answer` become `follow_up`; a person's `question` (shown to them as
"Side question") becomes `side`; an engine's `question` becomes `side` for now and is
asked again after the upgrade (clients/odoo/migration/retype_questions.py). The old value is
kept in label_detail.old_intent. A person's correction keeps its source, and its
Feedback goes to review.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE flightdeck_turn
           SET label_detail = coalesce(label_detail, '{}'::jsonb)
                              || jsonb_build_object('old_intent', intent,
                                                    'retype', intent = 'question' AND label_source IS DISTINCT FROM 'human'),
               intent = CASE WHEN intent IN ('correction', 'answer') THEN 'follow_up' ELSE 'side' END
         WHERE intent IN ('correction', 'answer', 'question')
    """)
    cr.execute("""
        UPDATE flightdeck_turn SET feedback_state = 'review'
         WHERE label_source = 'human' AND label_detail->>'old_intent' = 'correction'
           AND coalesce(feedback_source, '') NOT IN ('human', 'reference')
    """)
