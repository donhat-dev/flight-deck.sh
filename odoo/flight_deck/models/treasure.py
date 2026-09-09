from odoo import api, fields, models


class FlightdeckTreasureTag(models.Model):
    _name = "flightdeck.treasure.tag"
    _description = "Treasure tag"
    _order = "name"

    name = fields.Char(required=True)
    treasure_ids = fields.Many2many("flightdeck.treasure")

    _name_uniq = models.Constraint("unique(name)", "Tag already exists.")


class FlightdeckTreasure(models.Model):
    """A library artifact. The rendered page lives in an attachment, not in a
    field: an `Html` field sanitizes what it stores, and an artifact is a whole
    document carrying its own styles and embedded fonts."""

    _name = "flightdeck.treasure"
    _description = "Treasure"
    _order = "authored_at desc, name"

    treasure_id = fields.Char("Reference", required=True, index=True)
    name = fields.Char("Title", required=True)
    slug = fields.Char()
    dir_path = fields.Char("Directory")
    kind = fields.Char(index=True)
    language = fields.Char()
    status = fields.Selection(
        [("draft", "Draft"), ("ready", "Ready"), ("published", "Published")],
        default="draft",
        index=True,
    )
    version = fields.Integer(default=1)
    source_format = fields.Char()
    render_bytes = fields.Integer("Size")
    origin_kind = fields.Char()
    origin_id = fields.Char("Origin session", index=True)
    origin_session_id = fields.Many2one("flightdeck.session", "Session", ondelete="set null")
    origin_path = fields.Char()
    published_url = fields.Char("Published at")
    authored_at = fields.Datetime()
    ingested_at = fields.Datetime()
    updated_at = fields.Datetime()
    font = fields.Char()
    tag_ids = fields.Many2many("flightdeck.treasure.tag", string="Tags")
    attachment_id = fields.Many2one("ir.attachment", "Rendered page", ondelete="set null")
    artifact_url = fields.Char(compute="_compute_artifact_url")

    _treasure_uniq = models.Constraint("unique(treasure_id)", "Treasure already imported.")

    @api.depends("attachment_id")
    def _compute_artifact_url(self):
        for rec in self:
            rec.artifact_url = (
                "/flight_deck/artifact/%s" % rec.id if rec.attachment_id else False
            )
