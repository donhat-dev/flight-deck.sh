from odoo import fields, models


class FlightdeckToolCall(models.Model):
    _name = "flightdeck.tool.call"
    _description = "FlightDeck tool call"
    _order = "ts desc"
    _rec_name = "tool"

    call_id = fields.Char("Call", required=True, index=True)
    session_ref_id = fields.Many2one("flightdeck.session", "Session", index=True, ondelete="cascade")
    project_id = fields.Many2one("flightdeck.project", index=True, ondelete="set null")
    ts = fields.Datetime("When", index=True)
    tool = fields.Char(index=True)
    server = fields.Char(index=True, help="MCP server, empty for a built-in tool.")
    detail = fields.Text()

    _call_uniq = models.Constraint("unique(call_id)", "Tool call already imported.")
