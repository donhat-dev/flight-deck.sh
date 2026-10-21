from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # The rail opens narrow here. It is the whole navigation once the top bar
    # is gone, and a person who wants it wide says so once and it is kept.
    sidebar_type = fields.Selection(default="small")
