import base64
import json

from odoo import api, fields, models

from .transcript_reader import IMAGE_TYPES


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
    # Where the images a call returned can be found, never the images
    # themselves: `[{"media_type": ..., "index": ...}]` plus the byte the
    # result's line starts at. One screenshot is around 200 KB of base64, and
    # the file that already holds them is mounted here anyway.
    tool_images = fields.Text("Images returned")
    tool_result_offset = fields.Integer()

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
            "tool_images": self.tool_images,
        }

    def _image_block(self, index):
        """The bytes of one image this call returned, read back from the file.

        Returns `(data, media_type)`, or None when the file has moved on: a
        transcript that was rewritten leaves every offset pointing at the wrong
        line, which is why the line found there has to name this same call.
        """
        self.ensure_one()
        try:
            wanted = json.loads(self.tool_images or "[]")
        except ValueError:
            return None
        if not any(image.get("index") == index for image in wanted):
            return None
        path = self.session_ref_id._transcript_path()
        if not path:
            return None
        try:
            with open(path, "rb") as fh:
                fh.seek(self.tool_result_offset or 0)
                raw = fh.readline()
            blocks = (json.loads(raw).get("message") or {}).get("content") or []
        except (OSError, ValueError):
            return None
        for block in blocks:
            if not isinstance(block, dict) or block.get("tool_use_id") != self.tool_use_id:
                continue
            content = block.get("content")
            if not isinstance(content, list) or index >= len(content):
                return None
            item = content[index] if isinstance(content[index], dict) else {}
            source = item.get("source") or {}
            if item.get("type") != "image" or source.get("media_type") not in IMAGE_TYPES:
                return None
            try:
                return base64.b64decode(source.get("data") or "", validate=True), \
                    source["media_type"]
            except (ValueError, TypeError):
                return None
        return None

    def _bus_update(self):
        for line in self:
            if line.session_ref_id:
                line.session_ref_id._bus_send(self.BUS_UPDATE, line._bus_payload())
