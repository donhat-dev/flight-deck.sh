"""Messages a person typed while the agent was working on a turn.

Claude Code queues them into the running turn instead of starting a new one,
so they have no prompt id of their own. They count as adjustments of the task
the turn belongs to.
"""
from odoo import fields, models


class FlightdeckTurnSteer(models.Model):
    _name = "flightdeck.turn.steer"
    _description = "Steer message"
    _order = "sent_at, id"

    turn_id = fields.Many2one("flightdeck.turn", required=True, index=True, ondelete="cascade")
    session_ref_id = fields.Many2one(related="turn_id.session_ref_id", store=True, index=True)
    uuid = fields.Char(required=True, help="The transcript line it was read from.")
    sent_at = fields.Datetime("Sent")
    text = fields.Text()

    _uuid_uniq = models.Constraint("unique(uuid)", "A transcript line is one steer message.")
