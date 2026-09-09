from odoo import api, fields, models


class FlightdeckMessageText(models.Model):
    """One transcript line. `seq` is the only reliable order inside a session."""

    _name = "flightdeck.message.text"
    _description = "FlightDeck transcript line"
    _order = "session_ref_id, seq"
    _rec_name = "uuid"

    uuid = fields.Char(required=True, index=True)
    session_ref_id = fields.Many2one("flightdeck.session", "Session", index=True, ondelete="cascade")
    project_id = fields.Many2one("flightdeck.project", index=True, ondelete="set null")
    role = fields.Char(index=True)
    ts = fields.Datetime("When")
    seq = fields.Integer(index=True)
    text = fields.Text()
    tool_name = fields.Char("Tool")
    tool_input = fields.Text("Tool input")
    tool_output = fields.Text("Tool output")
    # The id the tool result comes back under, so a later line can find this row.
    tool_use_id = fields.Char(index=True)

    _uuid_uniq = models.Constraint("unique(uuid)", "Transcript line already imported.")

    # The notification a browser waits on. One name, because a typo in it fails
    # silently: no subscriber matches and nothing arrives.
    BUS_APPEND = "flightdeck.transcript/append"
    # A tool result arrives on a later line than the call, so a turn already on
    # screen has to be replaced rather than added.
    BUS_UPDATE = "flightdeck.transcript/update"

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # The importer loads tens of thousands of rows at once and nobody is
        # watching, so it turns this off by context.
        if not self.env.context.get("fd_no_bus"):
            for line in lines:
                if line.session_ref_id:
                    line.session_ref_id._bus_send(self.BUS_APPEND, line._bus_payload())
        return lines

    def _bus_payload(self):
        """Everything the box needs to draw the turn, so it draws no round trip."""
        self.ensure_one()
        return {
            "session_id": self.session_ref_id.id,
            "id": self.id,
            "role": self.role,
            "seq": self.seq,
            "ts": fields.Datetime.to_string(self.ts) if self.ts else False,
            "text": self.text,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_output": self.tool_output,
        }

    def _bus_update(self):
        for line in self:
            if line.session_ref_id:
                line.session_ref_id._bus_send(self.BUS_UPDATE, line._bus_payload())
