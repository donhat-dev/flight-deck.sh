from odoo import fields, models


class DiscussChannel(models.Model):
    """A channel can stand for a session transcript.

    The link is a field rather than a naming convention: it is what tells a
    channel carrying a transcript apart from an ordinary one, and it survives a
    rename.
    """

    _inherit = "discuss.channel"

    session_id = fields.Many2one(
        "flightdeck.session", "FlightDeck session", index=True, ondelete="cascade"
    )
