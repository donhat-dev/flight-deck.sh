import glob
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from odoo import _, api, fields, models

from . import follow_watcher
from . import transcript_reader
from .session_agent import BUS_AGENTS as AGENT_BUS

_logger = logging.getLogger(__name__)

TRANSCRIPT_ROOT = "/mnt/claude-projects"
# One small JSON file per running session, written by the CLI and removed when
# it exits. It is the only thing here that knows a session is open before it has
# written anything.
ROSTER_ROOT = "/mnt/claude-sessions"
# How long a session with no process behind it stays followed. Long enough to
# read the tail of one that ended mid-write.
LIVE_AFTER_WRITE = 15 * 60
# How many sessions one scan job takes. The jobs are serial, so this only says
# how often the queue gets a checkpoint.
SCAN_BATCH = 50
# Bytes one spend pass may read. A session's file runs to hundreds of
# megabytes, and the cron has to finish inside a transaction someone else is
# waiting on; whatever is left is taken on the next pass.
SPEND_BUDGET = 32 * 1024 * 1024
# What the browser is told when a session's spend moved.
BUS_SPEND = "flightdeck.session/spend"


def _stamp(value):
    """A transcript timestamp as a naive UTC datetime, or False."""
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


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
    # sessions. On `last_write`, never on `write_date`: the latter says when
    # Odoo last touched the row, so one migration pass puts the whole logbook
    # in the same minute and the order stops meaning anything.
    _order = "last_write desc, start_ts desc"
    _rec_name = "name"

    session_id = fields.Char(required=True, index=True)
    name = fields.Char("Title", required=True)
    title_source = fields.Selection(
        [("custom", "Renamed"), ("ai", "Generated")], string="Title from"
    )
    project_id = fields.Many2one("flightdeck.project", index=True, ondelete="set null")
    start_ts = fields.Datetime("Started", index=True)
    # When the transcript file itself was last written. Carried across from the
    # file by the scan, because nothing in the database observes a session that
    # is being written by another program.
    last_write = fields.Datetime("Last write", index=True, readonly=True)
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

    agent_ids = fields.One2many("flightdeck.session.agent", "session_ref_id")
    agent_count = fields.Integer("Agents", readonly=True)
    # Where the agent map starts: the last compact boundary. Everything before
    # it left the model's context, so the CLI's own reader drops it too, and a
    # map that kept it would show agents the session can no longer talk about.
    map_offset = fields.Integer(readonly=True)
    # How far the agent scan has read. Separate from `follow_bytes` because the
    # map backfills from the boundary while the transcript only takes new turns.
    agent_bytes = fields.Integer(readonly=True)
    # The transcript's mtime as of the last pass. `last_write` cannot serve as
    # this cursor: it holds the last message's own timestamp, which the CLI
    # leaves behind every time it appends a row that is not a message.
    file_mtime = fields.Float(readonly=True)
    # When the agents' own metadata directory was last touched, so a map
    # rebuild can tell whether reading it again would find anything.
    agent_meta_mtime = fields.Float(readonly=True)
    map_from_start = fields.Boolean(
        "Map every agent",
        help="Build the agent map from the whole file instead of from the last compact boundary.")
    # A third cursor beside `follow_bytes` and `agent_bytes`: the same file is
    # read for three different things and none of them may drag the others back.
    spend_bytes = fields.Integer(readonly=True)

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
    def _subagent_dir(self):
        """Where this session's agents keep their own transcripts."""
        self.ensure_one()
        path = self._transcript_path()
        if not path:
            return False
        found = os.path.join(os.path.dirname(path), self.session_id, "subagents")
        return found if os.path.isdir(found) else False

    def _transcript_path(self):
        """The session's own JSONL file, wherever its project directory sits."""
        self.ensure_one()
        if self.follow_path and os.path.exists(self.follow_path):
            return self.follow_path
        found = glob.glob(os.path.join(TRANSCRIPT_ROOT, "*", "%s.jsonl" % self.session_id))
        return found[0] if found else False

    def _start_at_end(self):
        """Read from the end of the file rather than from where it stopped.

        What a person means by "follow this": anything already in the file is
        history someone else has loaded. Nothing automatic may do this — the
        rule below switches following on and off as a session goes quiet, and a
        jump on each of those would drop the turns written in between.
        """
        for session in self:
            path = session._transcript_path()
            session.update({
                "follow_path": path or False,
                # The map looks back to the last boundary; the transcript only
                # takes what arrives from now on.
                "map_offset": session._map_start(),
                "agent_bytes": session._map_start(),
                "follow_bytes": os.path.getsize(path) if path else 0,
                # The real line count of the file, not the number of rows
                # stored: `seq` is a position in the file.
                "follow_lines": transcript_reader.count_lines(path) if path else 0,
            })

    def action_toggle_following(self):
        for session in self:
            if not session.following:
                session._start_at_end()
            session.following = not session.following
        return True

    @api.model
    def _read_roster(self):
        """The session ids with a process behind them, from the CLI's roster."""
        live = set()
        try:
            names = os.listdir(ROSTER_ROOT)
        except OSError:
            # The roster is not mounted. The write times below still answer.
            return live
        for name in names:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(ROSTER_ROOT, name), encoding="utf-8") as fh:
                    found = json.load(fh).get("sessionId")
            except (OSError, ValueError):
                continue
            if found:
                live.add(found)
        return live

    @api.model
    def _apply_following(self):
        """Switch following on where a session is live, off where it is not.

        Two signals, taken together, because each covers the other's failure. A
        roster entry left behind by a kill costs one stat a minute; a roster
        entry that disappears mid-write still gets its tail read. A session the
        runner starts may never register at all, and the write time carries it.
        """
        live = self._read_roster()
        floor = fields.Datetime.now() - timedelta(seconds=LIVE_AFTER_WRITE)
        wanted = self.search(["|", ("session_id", "in", list(live)),
                              ("last_write", ">", floor)])
        stale = self.search([("following", "=", True), ("id", "not in", wanted.ids)])
        fresh = wanted.filtered(lambda s: not s.following)
        if stale:
            stale.following = False
        if fresh:
            fresh.following = True
        return len(fresh) + len(stale)

    @api.model
    def _cron_follow(self):
        """Work out who is live, then read what they have written.

        Both halves in one cron because both write `flightdeck.session`. Split
        across two crons a minute apart, they take turns failing on "could not
        serialize access due to concurrent update".

        A new transcript gets its row here too, from the same file list, so a
        new session's first prompt is read (and labelled) as it arrives, not
        at the next scan.
        """
        index = self._transcript_index()
        self._discover_sessions(index)
        self._scan_writes(index)
        self._apply_following()
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
        # Before the early return below, not after it: a running agent writes to
        # its own file, and the session's file is precisely the one that does
        # not move while that happens.
        self.agent_ids._follow_running()
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
        self._rebuild_agents()
        return len(rows)

    # --- agent map -----------------------------------------------------------
    def _map_start(self):
        """Where this session's agent map begins."""
        self.ensure_one()
        path = self._transcript_path()
        if not path:
            return 0
        return 0 if self.map_from_start else transcript_reader.find_compact_offset(path)

    def action_rebuild_agents(self):
        """Read the map again from its start.

        Rows are matched on the call that made them rather than deleted and
        remade, so the ids a browser is already showing stay valid. Only a call
        the file no longer names is dropped.
        """
        for session in self:
            session.write({"map_offset": session._map_start()})
            session.agent_bytes = session.map_offset
            session._rebuild_agents(prune=True)
            session._bus_send(AGENT_BUS, {"session_id": session.id, "reset": True})
        return True

    def _rebuild_agents(self, prune=False):
        """Take the agent map as far as the transcript has been written.

        A compact boundary in the new bytes empties the map: the calls before it
        are out of the session's context, so they are out of the map too.
        """
        self.ensure_one()
        path = self._transcript_path()
        if not path:
            return 0
        Agent = self.env["flightdeck.session.agent"]
        known = {a.tool_use_id: a for a in self.agent_ids if a.tool_use_id}
        try:
            found, boundary, consumed = transcript_reader.scan_agents(
                path, self.agent_bytes, known=known, reset=not self.map_from_start)
        except OSError as e:
            _logger.warning("cannot scan agents in %s: %s", path, e)
            return 0
        if prune:
            # A call the file no longer names belongs to an older read.
            stale = self.agent_ids.filtered(
                lambda a: not a.parent_agent_id and a.tool_use_id not in found)
            for agent in stale:
                self._bus_send(AGENT_BUS, {"session_id": self.id, "id": agent.id,
                                           "removed": True})
            stale.unlink()
            known = {a.tool_use_id: a for a in self.agent_ids if a.tool_use_id}
        # A boundary at the offset the read started from is the one the map is
        # already built on, not a new one.
        if boundary is not None and boundary > self.map_offset and not self.map_from_start:
            for agent in self.agent_ids:
                self._bus_send(AGENT_BUS, {"session_id": self.id, "id": agent.id,
                                           "removed": True})
            self.agent_ids.unlink()
            known = {}
            self.map_offset = boundary
        fresh = Agent
        for call in found.values():
            vals = Agent._values_from(self, call)
            row = known.get(call["tool_use_id"])
            if row:
                row.write(vals)
                fresh |= row
            else:
                fresh |= Agent.create(vals)
        self.agent_bytes = self.agent_bytes + consumed
        if fresh:
            fresh._read_meta()
            fresh |= Agent._adopt_nested(self)
            fresh |= Agent._adopt_workflow(self)
            # Nobody reports an agent an agent started, so its own row is filled
            # in from the two files that do know: its parent's, and its own.
            nested = self.agent_ids.filtered("parent_agent_id")
            nested._read_outcomes()
            nested.filtered(
                lambda a: a.transcript_path
                and (a.status in ("working", "stopped") or not a.total_tokens)
            )._read_usage()
            fresh._bus_update()
            self.agent_count = len(self.agent_ids)
        return len(fresh)

    def _follow_values(self, row):
        return {
            "uuid": row["uuid"],
            "session_ref_id": self.id,
            "project_id": self.project_id.id,
            "role": row["role"],
            "ts": _stamp(row.get("ts")),
            "seq": row["seq"],
            "text": row["text"],
            "tool_name": row["tool_name"],
            "tool_input": row["tool_input"],
            "tool_use_id": row["tool_use_id"],
            "tool_call_offset": row.get("offset") or 0,
            "peer_name": row.get("peer_name") or False,
            "peer_from": row.get("peer_from") or False,
            "peer_mode": row.get("peer_mode") or False,
            "peer_msg_id": row.get("peer_msg_id") or False,
        }

    # --- the session list, and what it spent ---------------------------------
    @api.model
    def _transcript_index(self):
        """Every transcript file on disk, by session id: one glob, not one each."""
        found = {}
        for path in glob.glob(os.path.join(TRANSCRIPT_ROOT, "*", "*.jsonl")):
            found[os.path.basename(path)[: -len(".jsonl")]] = path
        return found

    @api.model
    def _cron_scan(self):
        """Find new sessions, then hand the reading to the queue."""
        index = self._transcript_index()
        self._discover_sessions(index)
        return self._enqueue_spend(index)

    @api.model
    def _enqueue_spend(self, index=None):
        """One job per batch of sessions whose file has outgrown its cursor.

        Returns the number of jobs queued. `identity_key` is what stops a second
        trigger queueing the same batch while the first is still waiting.
        """
        index = self._transcript_index() if index is None else index
        rows = self.search_read(
            [("session_id", "in", list(index))], ["session_id", "spend_bytes"])
        behind = []
        for row in rows:
            try:
                if os.path.getsize(index[row["session_id"]]) != row["spend_bytes"]:
                    behind.append(row["id"])
            except OSError:
                continue
        jobs = 0
        for at in range(0, len(behind), SCAN_BATCH):
            batch = behind[at:at + SCAN_BATCH]
            self.with_delay(
                description="FlightDeck: read spend for %s session(s)" % len(batch),
                identity_key="fd-spend-%s" % "-".join(str(i) for i in batch),
            )._scan_batch(batch)
            jobs += 1
        return jobs

    @api.model
    def _scan_batch(self, ids):
        """Read the billed turns of these sessions. The body of one queue job."""
        index = self._transcript_index()
        for session in self.browse(ids).exists():
            path = index.get(session.session_id)
            if not path:
                continue
            try:
                with self.env.cr.savepoint():
                    session._spend_tick(path)
            except Exception:
                _logger.exception("spend scan failed for session %s", session.id)
        return True

    def action_scan_now(self):
        """The list view's own button: scan every transcript, queue the reading.

        Not `@api.model`: a list header button hands the method the ids that are
        ticked, and a model method has nowhere to put them. The selection is
        ignored on purpose — the scan is about the files, not about a row.
        """
        jobs = self._cron_scan()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("%s scan job(s) queued.", jobs) if jobs
                          else _("Every transcript is already read."),
                "sticky": False,
            },
        }

    @api.model
    def _discover_sessions(self, index=None):
        """Make a row for every transcript that has none yet."""
        index = self._transcript_index() if index is None else index
        known = set(self.search([("session_id", "in", list(index))]).mapped("session_id"))
        Project = self.env["flightdeck.project"]
        made = self.browse()
        for session_id, path in index.items():
            if session_id in known:
                continue
            try:
                cwd, title = transcript_reader.first_meta(path)
            except OSError as e:
                _logger.warning("cannot read %s: %s", path, e)
                continue
            project = Project.search([("name", "=", cwd)], limit=1) if cwd else Project
            if cwd and not project:
                project = Project.create({"name": cwd})
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                mtime = None
            made |= self.create({
                "session_id": session_id,
                "name": title or session_id,
                "project_id": project.id or False,
                "follow_path": path,
                "last_write": datetime.fromtimestamp(mtime, timezone.utc).replace(
                    tzinfo=None, microsecond=0) if mtime else False,
            })
        if made:
            _logger.info("FlightDeck: %s new session(s) on disk", len(made))
        return made

    @api.model
    def _scan_writes(self, index=None):
        """Carry what a transcript says about itself across into its row.

        A stat on every file, and the end of a file read only where that stat
        moved — two to six of them in a steady pass, against 141 ms to read the
        end of all of them.

        Two things come out of that read, and neither can be had from the
        outside. The last message's timestamp, which is what the CLI orders its
        own list by; and the name it shows, which a session is given after the
        first prompt and renamed with afterwards, so a title taken once at
        discovery is wrong for a third of the library.
        """
        index = self._transcript_index() if index is None else index
        rows = self.search_read(
            [("session_id", "in", list(index))],
            ["session_id", "last_write", "file_mtime", "name"])
        touched = 0
        for row in rows:
            path = index[row["session_id"]]
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                continue
            if mtime == row["file_mtime"]:
                continue
            last_ts, title, source = transcript_reader.tail_meta(path)
            stamp = _stamp(last_ts) or datetime.fromtimestamp(
                mtime, timezone.utc).replace(tzinfo=None, microsecond=0)
            vals = {"file_mtime": mtime}
            if row["last_write"] != stamp:
                vals["last_write"] = stamp
            # The transcript wins: `custom-title` is the name the person typed
            # in the CLI, and this list is read beside the one that shows it.
            if title and title != row["name"]:
                vals.update(name=title, title_source=source)
            self.browse(row["id"]).write(vals)
            touched += 1
        self._seed_last_write()
        return touched

    @api.model
    def _seed_last_write(self):
        """Give a session with no file on disk something to sort by.

        More than half the library is older than the transcripts that survive,
        and a descending sort puts an empty column first, so those would take
        over the top of the logbook. The end of the session is the closest
        thing the ledger knows to its last write; a row that has not even that
        falls back to when it was imported, so the column is never empty.
        """
        empty = self.search([("last_write", "=", False)])
        for rec in empty:
            rec.last_write = rec.end_ts or rec.start_ts or rec.create_date
        return len(empty)

    def _spend_tick(self, path, budget=SPEND_BUDGET):
        """This session's billed turns, as far as `budget` bytes reach.

        Returns the bytes read, which is what bounds one cron pass.
        """
        self.ensure_one()
        offset = self.spend_bytes
        try:
            if os.path.getsize(path) < offset:
                # The file was rewritten, so its offsets mean nothing.
                offset = 0
            rows, consumed = transcript_reader.read_usage(path, offset, limit=budget)
        except OSError as e:
            _logger.warning("cannot read spend in %s: %s", path, e)
            return 0
        if rows:
            self._store_spend(rows)
        self.spend_bytes = offset + consumed
        return consumed

    def _store_spend(self, rows):
        """Price the turns that are not stored yet, and move the totals.

        The totals are incremented rather than recomputed: a session holds tens
        of thousands of these rows and summing them on every tick is what the
        stored columns exist to avoid.
        """
        self.ensure_one()
        Message = self.env["flightdeck.message"]
        Price = self.env["flightdeck.model.price"]
        held = set(Message.search([("uuid", "in", [r["uuid"] for r in rows])]).mapped("uuid"))
        index = Price.rate_index()
        vals, stamps = [], []
        added = dict.fromkeys(
            ("input_tokens", "output_tokens", "cache_read", "cache_create", "total_cost"), 0.0)
        for row in rows:
            if row["uuid"] in held:
                continue
            stamp = _stamp(row["ts"])
            rate = Price.match(index, row["model"], stamp)
            cost = Price.cost_of(
                rate, row["input_tokens"], row["cache_read"], row["cache_create_5m"],
                row["cache_create_1h"], row["output_tokens"])
            vals.append({
                "uuid": row["uuid"],
                "session_ref_id": self.id,
                "project_id": self.project_id.id,
                "model": row["model"],
                "ts": stamp,
                "service_tier": row["service_tier"],
                "input_tokens": row["input_tokens"],
                "cache_read": row["cache_read"],
                "cache_create_5m": row["cache_create_5m"],
                "cache_create_1h": row["cache_create_1h"],
                "output_tokens": row["output_tokens"],
                "total_tokens": row["total_tokens"],
                "cost": cost,
                "priced": bool(rate),
            })
            if stamp:
                stamps.append(stamp)
            added["input_tokens"] += row["input_tokens"]
            added["output_tokens"] += row["output_tokens"]
            added["cache_read"] += row["cache_read"]
            added["cache_create"] += row["cache_create_5m"] + row["cache_create_1h"]
            added["total_cost"] += cost
        if not vals:
            return 0
        Message.create(vals)
        totals = {
            "message_count": self.message_count + len(vals),
            "input_tokens": self.input_tokens + added["input_tokens"],
            "output_tokens": self.output_tokens + added["output_tokens"],
            "cache_read": self.cache_read + added["cache_read"],
            "cache_create": self.cache_create + added["cache_create"],
            "total_cost": self.total_cost + added["total_cost"],
        }
        if stamps:
            totals["start_ts"] = min(stamps + ([self.start_ts] if self.start_ts else []))
            totals["end_ts"] = max(stamps + ([self.end_ts] if self.end_ts else []))
            totals["duration_hours"] = (
                totals["end_ts"] - totals["start_ts"]).total_seconds() / 3600.0
        self.write(totals)
        self._bus_send(BUS_SPEND, dict(
            totals, session_id=self.id,
            start_ts=fields.Datetime.to_string(totals.get("start_ts")) or False,
            end_ts=fields.Datetime.to_string(totals.get("end_ts")) or False))
        return len(vals)

    def action_rescan_spend(self):
        """Read this session's spend again from the start of its file.

        Rows already stored are left alone: the API message id is the key, so a
        turn is recognised whatever pass first saw it.
        """
        for session in self:
            path = session._transcript_path()
            if not path:
                continue
            session.spend_bytes = 0
            while session._spend_tick(path):
                self.env.cr.commit()
        return True
