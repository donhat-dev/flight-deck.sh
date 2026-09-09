from odoo import models


class IrUiView(models.Model):
    """Tell open browsers that a view changed.

    Without this the new arch is only noticed the next time a view is loaded,
    because that is when the client re-reads `get_views`. The notification is
    what makes an open form re-render on its own.
    """

    _inherit = "ir.ui.view"

    VIEW_CHANGED = "view_changed"

    ARCH_FIELDS = ("arch", "arch_db", "arch_base", "active")

    def write(self, vals):
        result = super().write(vals)
        self._notify_view_changed(vals)
        return result

    def _write(self, vals):
        """Also here: assigning `arch` defers to a flush, and the flush writes
        `arch_db` through this low-level path rather than through `write`."""
        result = super()._write(vals)
        self._notify_view_changed(vals)
        return result

    def _notify_view_changed(self, vals):
        if any(field in vals for field in self.ARCH_FIELDS):
            self.env["bus.bus"]._sendone(
                "broadcast", self.VIEW_CHANGED, {"model": self.mapped("model")}
            )
