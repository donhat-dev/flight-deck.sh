import glob
import json
import logging
import os
from datetime import datetime, timezone

from odoo import api, fields, models

from . import follow_watcher
from . import transcript_reader

_logger = logging.getLogger(__name__)

TRANSCRIPT_ROOT = "/mnt/claude-projects"


class FlightdeckSession(models.Model):
    """One Claude Code session: the logbook row.

    The totals below are written by the importer, not computed from
    `@api.depends`. A session holds up to a few thousand messages and the
    library holds 103k of them; recomputing while loading turns minutes into
    hours. What that costs: editing a message by hand leaves the total stale
    until `action_recompute_totals` runs.
    """

    _name = "flightdeck.session"
    # mail.thread so a transcript can be read as a chatter conversation next to
    # the widget in the Transcript tab. Both read the same source rows; only the
    # rendering differs.
    # `bus.listener.mixin` makes the record itself a bus channel, which is
    # what a browser subscribes to for live turns.
    _inherit = ["mail.thread", "bus.listener.mixin"]
    _description = "FlightDeck session"
    # Most recently written first, the way Claude Code and FlightDeck list
    # sessions: a session moves up when something lands in it, and a followed
    # one moves up as it is being written.
    _order = "write_date desc"
    _rec_name = "name"

    session_id = fields.Char(required=True, index=True)
    name = fields.Char("Title", required=True)
    title_source = fields.Selection(
        [("custom", "Renamed"), ("ai", "Generated")], string="Title from"
    )
    project_id = fields.Many2one("flightdeck.project", index=True, ondelete="set null")
    start_ts = fields.Datetime("Started", index=True)
    end_ts = fields.Datetime("Ended")
    duration_hours = fields.Float("Hours", digits=(6, 2))

    # NOT `message_ids`: mail.thread owns that name for mail.message, and
    # redefining it here would point the chatter at these rows instead.
    usage_ids = fields.One2many("flightdeck.message", "session_ref_id")
    text_ids = fields.One2many("flightdeck.message.text", "session_ref_id")
    tool_call_ids = fields.One2many("flightdeck.tool.call", "session_ref_id")
    treasure_ids = fields.One2many("flightdeck.treasure", "origin_session_id")

    message_count = fields.Integer("Token rows")
    text_count = fields.Integer("Transcript lines")
    tool_call_count = fields.Integer("Tool calls")
    treasure_count = fields.Integer("Treasures", compute="_compute_treasure_count")

    # Float, not Integer: an Odoo integer column is int4, and a long session
    # reads billions of cached tokens. The sum overflows before the count does.
    input_tokens = fields.Float("Input", digits=(16, 0))
    output_tokens = fields.Float("Output", digits=(16, 0))
    cache_read = fields.Float("Cache read", digits=(16, 0))
    cache_create = fields.Float("Cache write", digits=(16, 0))
    total_cost = fields.Float("Cost", digits=(12, 4))

    following = fields.Boolean(
        "Following",
        help="Read this session's transcript file and show new turns as they arrive.",
    )
    follow_path = fields.Char("Transcript file", readonly=True)
    # Where the last read stopped: bytes for the cheap resume, line count so
    # `seq` keeps counting from the start of the file.
    follow_bytes = fields.Integer(readonly=True)
    follow_lines = fields.Integer(readonly=True)

    _session_uniq = models.Constraint("unique(session_id)", "Session already imported.")

    @api.depends("treasure_ids")
    def _compute_treasure_count(self):
        for rec in self:
            rec.treasure_count = len(rec.treasure_ids)

    def action_recompute_totals(self):
        """Refresh the stored totals from the child rows."""
        for rec in self:
            msgs = rec.usage_ids
            rec.message_count = len(msgs)
            rec.text_count = len(rec.text_ids)
            rec.tool_call_count = len(rec.tool_call_ids)
            rec.input_tokens = sum(msgs.mapped("input_tokens"))
            rec.output_tokens = sum(msgs.mapped("output_tokens"))
            rec.cache_read = sum(msgs.mapped("cache_read"))
            rec.cache_create = sum(msgs.mapped("cache_create_5m")) + sum(
                msgs.mapped("cache_create_1h")
            )
            rec.total_cost = sum(msgs.mapped("cost"))
            stamps = [m.ts for m in msgs if m.ts]
            if stamps:
                rec.start_ts = min(stamps)
                rec.end_ts = max(stamps)
                rec.duration_hours = (rec.end_ts - rec.start_ts).total_seconds() / 3600.0
        return True

    def action_open_channel(self):
        """Open the Discuss channel that holds this session's transcript."""
        self.ensure_one()
        channel = self.env["discuss.channel"].search([("session_id", "=", self.id)], limit=1)
        if not channel:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "message": "This session has no channel yet.",
                    "type": "warning",
                },
            }
        return {
            "type": "ir.actions.client",
            "tag": "mail.action_discuss",
            "params": {"active_id": channel.id},
        }

    def action_open_transcript(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "flightdeck.message.text",
            "name": self.name,
            "view_mode": "list",
            "domain": [("session_ref_id", "=", self.id)],
        }

    def action_open_messages(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "flightdeck.message",
            "name": "Spend — %s" % self.name,
            "view_mode": "pivot,graph,list",
            "domain": [("session_ref_id", "=", self.id)],
        }

    def _register_hook(self):
        """Start the watcher when the registry for this database is ready."""
        super()._register_hook()
        follow_watcher.ensure_started(self.env.cr.dbname)

    # --- following -----------------------------------------------------------
    def _transcript_path(self):
        """The session's own JSONL file, wherever its project directory sits."""
        self.ensure_one()
        if self.follow_path and os.path.exists(self.follow_path):
            return self.follow_path
        found = glob.glob(os.path.join(TRANSCRIPT_ROOT, "*", "%s.jsonl" % self.session_id))
        return found[0] if found else False

    def write(self, vals):
        """Turning following on starts at the end of the file.

        Anything already in the file is history someone else has loaded; the
        point of following is what arrives from now on.
        """
        if vals.get("following"):
            for session in self:
                if session.following:
                    continue
                path = session._transcript_path()
                vals = dict(vals, follow_path=path or False)
                session.update({
                    "follow_path": path or False,
                    "follow_bytes": os.path.getsize(path) if path else 0,
                    # The real line count of the file, not the number of rows
                    # stored: `seq` is a position in the file.
                    "follow_lines": transcript_reader.count_lines(path) if path else 0,
                })
        return super().write(vals)

    def action_toggle_following(self):
        for session in self:
            session.following = not session.following
        return True

    @api.model
    def _cron_follow(self):
        """Read whatever the followed sessions have written since last time."""
        for session in self.search([("following", "=", True)]):
            try:
                session._follow_tick()
                self.env.cr.commit()
            except Exception:
                self.env.cr.rollback()
                _logger.exception("following failed for session %s", session.id)
        return True

    def _follow_tick(self):
        self.ensure_one()
        path = self._transcript_path()
        if not path:
            return 0
        size = os.path.getsize(path)
        offset, first_line = self.follow_bytes, self.follow_lines
        if size < offset:
            # The file was rewritten, so the offset means nothing. Reading it
            # whole again is the only way `seq` stays consistent with itself.
            offset, first_line = 0, 0
        elif size == offset:
            return 0

        try:
            rows, results, consumed, lines = transcript_reader.read_tail(path, offset, first_line)
        except OSError as e:
            # A file the runner has not shared yet, or one whose ACL was lost.
            # The next tick reads it; failing the whole pass would stop every
            # other followed session too.
            _logger.warning("cannot read %s yet: %s", path, e)
            return 0
        Text = self.env["flightdeck.message.text"]
        by_uuid = {}
        if rows:
            existing = Text.search([("uuid", "in", [r["uuid"] for r in rows])])
            by_uuid = {rec.uuid: rec for rec in existing}
            fresh = [self._follow_values(r) for r in rows if r["uuid"] not in by_uuid]
            for row in rows:
                found = by_uuid.get(row["uuid"])
                if found and found.seq != row["seq"]:
                    found.seq = row["seq"]
            if fresh:
                created = Text.create(fresh)
                by_uuid.update({rec.uuid: rec for rec in created})

        if results:
            calls = Text.search([
                ("session_ref_id", "=", self.id),
                ("tool_use_id", "in", list(results)),
                ("tool_output", "=", False),
            ])
            for call in calls:
                found = results[call.tool_use_id]
                vals = {"tool_output": found["text"]}
                if found.get("images"):
                    vals["tool_images"] = json.dumps(found["images"])
                    vals["tool_result_offset"] = found.get("offset") or 0
                call.write(vals)
            calls._bus_update()

        self.write({
            "follow_path": path,
            "follow_bytes": offset + consumed,
            "follow_lines": first_line + lines,
        })
        return len(rows)

    def _follow_values(self, row):
        stamp = False
        if row.get("ts"):
            try:
                parsed = datetime.fromisoformat(row["ts"])
                stamp = parsed.astimezone(timezone.utc).replace(tzinfo=None) \
                    if parsed.tzinfo else parsed
            except ValueError:
                stamp = False
        return {
            "uuid": row["uuid"],
            "session_ref_id": self.id,
            "project_id": self.project_id.id,
            "role": row["role"],
            "ts": stamp,
            "seq": row["seq"],
            "text": row["text"],
            "tool_name": row["tool_name"],
            "tool_input": row["tool_input"],
            "tool_use_id": row["tool_use_id"],
        }
