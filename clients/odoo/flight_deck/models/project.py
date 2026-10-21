from odoo import fields, models


class FlightdeckProject(models.Model):
    """A working directory, as a record rather than a repeated string.

    Grouping a pivot by a many2one gives the reader a filter dropdown and a
    drill-down; grouping by a char gives a list of strings.
    """

    _name = "flightdeck.project"
    _description = "FlightDeck project"
    _order = "name"

    name = fields.Char(required=True, index=True)
    session_ids = fields.One2many("flightdeck.session", "project_id", string="Sessions")

    _name_uniq = models.Constraint("unique(name)", "Project already exists.")
