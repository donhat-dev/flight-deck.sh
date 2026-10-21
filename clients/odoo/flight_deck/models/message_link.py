"""Which prompt, agent and turn a billed row belongs to.

`prompt_id` and `agent_key` are what the transcript said when the row was
read. `turn_id` is worked out from them by the task rollup: the turn with the
same prompt id, or else the session's turn that was running when the row was
written, since a turn a task notification started carries a prompt id of its
own that no person wrote.
"""
from odoo import fields, models


class FlightdeckMessageLink(models.Model):
    _inherit = "flightdeck.message"

    prompt_id = fields.Char("Prompt id", index=True)
    agent_key = fields.Char(
        "Agent", index=True,
        help="Empty for the session's own agent; the agent id for a subagent's call.")
    turn_id = fields.Many2one("flightdeck.turn", "Turn", index=True, ondelete="set null")
