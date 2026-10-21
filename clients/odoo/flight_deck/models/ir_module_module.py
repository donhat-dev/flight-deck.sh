from odoo import models


class IrModuleModule(models.Model):
    """Tell open browsers that a module was updated.

    `bundle_changed` is Odoo's own signal, but it is sent when a bundle is
    regenerated — which happens lazily, on the first request for it. An open
    page never asks again until it reloads, so it would wait for a reload to
    learn that it needs one. This sends the same signal at the moment the
    upgrade finishes.
    """

    _inherit = "ir.module.module"

    def write(self, vals):
        result = super().write(vals)
        if "latest_version" in vals or vals.get("state") == "installed":
            self.env["bus.bus"]._sendone("broadcast", "bundle_changed", {})
        return result
