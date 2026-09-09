import re

from odoo import models
from odoo.exceptions import AccessError

CHANNEL = re.compile(r"^flightdeck\.session_(\d+)$")


class IrWebsocket(models.AbstractModel):
    _inherit = "ir.websocket"

    def _build_bus_channel_list(self, channels):
        """Turn `flightdeck.session_<id>` from a browser into the record channel.

        The check is the point: a client may ask for any string, so the record
        is resolved and read access verified here before it becomes a channel.
        """
        if self.env.uid:
            channels = list(channels)
            for channel in channels:
                match = CHANNEL.match(channel) if isinstance(channel, str) else None
                if not match:
                    continue
                session = self.env["flightdeck.session"].browse(int(match[1])).exists()
                if not session:
                    continue
                try:
                    session.check_access("read")
                except AccessError:
                    continue
                channels.append(session)
        return super()._build_bus_channel_list(channels)
