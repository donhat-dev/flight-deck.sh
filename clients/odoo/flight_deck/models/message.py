from odoo import fields, models


class FlightdeckMessage(models.Model):
    """One billed turn. This is the grain spend is measured on.

    `cost` is a stored float written at import from the message's own timestamp.
    It has to be stored because a pivot can only aggregate a real column, and it
    has to use the message's timestamp because a rate can expire: a turn sent in
    August really did cost the August price.
    """

    _name = "flightdeck.message"
    _description = "FlightDeck message"
    _order = "ts desc"
    _rec_name = "uuid"

    uuid = fields.Char(required=True, index=True)
    session_ref_id = fields.Many2one("flightdeck.session", "Session", index=True, ondelete="cascade")
    project_id = fields.Many2one("flightdeck.project", index=True, ondelete="set null")
    model = fields.Char(index=True)
    ts = fields.Datetime("When", index=True)
    service_tier = fields.Char()

    input_tokens = fields.Integer()
    cache_read = fields.Integer("Cache read")
    cache_create_5m = fields.Integer("Cache write 5m")
    cache_create_1h = fields.Integer("Cache write 1h")
    output_tokens = fields.Integer()
    total_tokens = fields.Integer("Tokens")
    cost = fields.Float("Cost", digits=(12, 6))
    priced = fields.Boolean("Priced", help="False when no rate matched the model ID.")

    _uuid_uniq = models.Constraint("unique(uuid)", "Message already imported.")
