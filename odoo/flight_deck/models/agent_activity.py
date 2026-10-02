"""Agent work-hours: how long agents were busy, per session, agent and hour.

Read from the transcripts by `activity_reader`, on its own cursor per file so
it never drags the spend, follow or agent-map cursors back. Every model counts
1:1 for now; a weighting by capability belongs on the reader of these rows,
not in them.
"""
import glob
import logging
import os

from odoo import api, fields, models

from . import activity_reader, transcript_reader
from .session import SPEND_BUDGET, TRANSCRIPT_ROOT

_logger = logging.getLogger(__name__)

# Bytes one file may read in a pass. The first pass over the library reads
# about a gigabyte; the budget keeps each transaction short and the next pass
# carries on from the cursor.
ACTIVITY_BUDGET = 32 * 1024 * 1024
# Bytes one cron run may read across all files.
RUN_BUDGET = 256 * 1024 * 1024


class FlightdeckAgentActivity(models.Model):
    _name = "flightdeck.agent.activity"
    _description = "Agent busy time"
    _order = "hour desc"
    _rec_name = "hour"

    session_ref_id = fields.Many2one(
        "flightdeck.session", "Session", required=True, index=True, ondelete="cascade")
    project_id = fields.Many2one(related="session_ref_id.project_id", store=True, index=True)
    # The agent id from `subagents/[workflows/<run>/]agent-<id>.jsonl`; empty
    # for the main agent.
    agent_key = fields.Char("Agent", index=True)
    is_subagent = fields.Boolean("Subagent", index=True)
    prompt_id = fields.Char("Turn", index=True)
    hour = fields.Datetime("Hour", required=True, index=True,
                           help="Start of the UTC hour the busy time fell in.")
    busy_hours = fields.Float("Work-hours", digits=(10, 4))

    _activity_uniq = models.Constraint(
        "unique(session_ref_id, agent_key, prompt_id, hour)",
        "Busy time is stored once per session, agent, turn and hour.")


class FlightdeckActivityCursor(models.Model):
    """How far one transcript file has been read, for busy time and spend.

    `spend_bytes` is used for agent files only: a session's own file keeps its
    spend position on the session, where the logbook reads it.
    """

    _name = "flightdeck.activity.cursor"
    _description = "Transcript read position"

    path = fields.Char(required=True, index=True)
    session_ref_id = fields.Many2one(
        "flightdeck.session", required=True, index=True, ondelete="cascade")
    agent_key = fields.Char()
    bytes_read = fields.Integer()
    spend_bytes = fields.Integer()
    state = fields.Json()

    _path_uniq = models.Constraint("unique(path)", "One cursor per file.")


class FlightdeckTurn(models.Model):
    """A prompt a person wrote, and the session it belongs to.

    Stored for one reason today: a resumed or forked session starts with a copy
    of the history it came from, and the prompt's owner is how the copy is
    recognised and left uncounted.
    """

    _name = "flightdeck.turn"
    _description = "Turn"
    _order = "started_at desc"
    _rec_name = "prompt_head"

    prompt_id = fields.Char("Prompt id", required=True, index=True)
    session_ref_id = fields.Many2one(
        "flightdeck.session", required=True, index=True, ondelete="cascade")
    started_at = fields.Datetime(index=True)
    prompt_head = fields.Char("Prompt")
    # What the reader saw of the turn: where it ran, what was asked, what the
    # agent said last, and where its lines sit in the session's file.
    ended_at = fields.Datetime()
    prompt_text = fields.Text("Prompt text")
    final_head = fields.Text("Last agent message")
    cwd = fields.Char("Working directory")
    git_branch = fields.Char("Branch")
    byte_start = fields.Integer(help="Where the prompt's line starts in the session's file.")
    byte_end = fields.Integer(help="Where the next prompt's line starts; 0 while the turn is the last.")

    _prompt_uniq = models.Constraint("unique(prompt_id)", "A prompt belongs to one session.")


class FlightdeckSessionActivity(models.Model):
    _inherit = "flightdeck.session"

    work_hours = fields.Float(
        "Work-hours", digits=(8, 2), readonly=True,
        help="Time agents were busy on this session, subagents included. "
             "Not the span from first to last message.")

    # --- reading -------------------------------------------------------------
    @api.model
    def _activity_files(self):
        """Every transcript file with its session id and agent key."""
        found = []
        for path in glob.glob(os.path.join(TRANSCRIPT_ROOT, "*", "*.jsonl")):
            found.append((path, os.path.basename(path)[: -len(".jsonl")], ""))
        # A plain agent writes `subagents/agent-*.jsonl`; a workflow's agents
        # write one level further down, in `subagents/workflows/<run>/`.
        for path in glob.glob(os.path.join(
                TRANSCRIPT_ROOT, "*", "*", "subagents", "**", "agent-*.jsonl"), recursive=True):
            key = activity_reader.AGENT_FILE.search(path)
            parts = path.split(os.sep)
            session_id = parts[parts.index("subagents") - 1]
            found.append((path, session_id, key.group(1) if key else ""))
        return found

    @api.model
    def _cron_activity(self, budget=RUN_BUDGET):
        """Read busy time from every file that grew since its cursor."""
        Cursor = self.env["flightdeck.activity.cursor"]
        files = self._activity_files()
        cursors = {c.path: c for c in Cursor.search([("path", "in", [f[0] for f in files])])}
        sessions = {s.session_id: s for s in self.search(
            [("session_id", "in", list({f[1] for f in files}))])}
        spent = 0
        # Main files first: a subagent's turn is known through its parent's.
        for path, session_id, agent_key in sorted(files, key=lambda f: bool(f[2])):
            session = sessions.get(session_id)
            if not session:
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            cursor = cursors.get(path)
            if cursor and cursor.bytes_read == size and (
                    not agent_key or cursor.spend_bytes == size):
                continue
            try:
                spent += session._activity_tick(path, agent_key, cursor)
                if agent_key:
                    spent += session._agent_spend_tick(path, agent_key)
                self.env.cr.commit()
            except Exception:
                self.env.cr.rollback()
                _logger.exception("busy time failed for %s", path)
            if spent >= budget:
                break
        return spent

    def _agent_spend_tick(self, path, agent_key, budget=SPEND_BUDGET):
        """An agent file's billed turns, stored under this session.

        An agent's calls are billed like the session's own, so they belong in
        the ledger with it; `agent_key` on each row says which agent made it.
        """
        self.ensure_one()
        cursor = self.env["flightdeck.activity.cursor"].search([("path", "=", path)], limit=1)
        if not cursor:
            return 0
        self.env.cr.execute(
            "SELECT id FROM flightdeck_activity_cursor WHERE id = %s FOR UPDATE SKIP LOCKED",
            (cursor.id,))
        if not self.env.cr.fetchone():
            return 0
        cursor.invalidate_recordset()
        offset = cursor.spend_bytes
        try:
            if os.path.getsize(path) < offset:
                offset = 0
            rows, consumed = transcript_reader.read_usage(path, offset, limit=budget)
        except OSError as e:
            _logger.warning("cannot read spend in %s: %s", path, e)
            return 0
        if rows:
            self.with_context(fd_agent_key=agent_key)._store_spend(rows)
        cursor.spend_bytes = offset + consumed
        return consumed

    def _activity_tick(self, path, agent_key, cursor=None, budget=ACTIVITY_BUDGET):
        """Read one file's busy time as far as `budget` bytes reach."""
        self.ensure_one()
        Cursor = self.env["flightdeck.activity.cursor"]
        cursor = cursor or Cursor.search([("path", "=", path)], limit=1)
        if cursor:
            # The cron and the follow tick both read files that grew. Busy time
            # is added, not set, so two readers of one file would count it
            # twice: the one that does not get the row leaves the file alone.
            self.env.cr.execute(
                "SELECT id FROM flightdeck_activity_cursor WHERE id = %s FOR UPDATE SKIP LOCKED",
                (cursor.id,))
            if not self.env.cr.fetchone():
                return 0
            cursor.invalidate_recordset()
        offset, state = (cursor.bytes_read, cursor.state) if cursor else (0, None)
        try:
            if os.path.getsize(path) < offset:
                offset, state = 0, None
            records, consumed = activity_reader.slim_records(path, offset, limit=budget)
        except OSError as e:
            _logger.warning("cannot read busy time in %s: %s", path, e)
            return 0
        if offset == 0:
            # A read from the start recounts the whole file.
            self._drop_activity(agent_key)
        tracker = activity_reader.Tracker(bool(agent_key), state)
        if agent_key and not tracker.prompt_id:
            tracker.prompt_id = self._agent_prompt(records)
        foreign_msgs = self._foreign_messages(records)
        foreign_prompts = self._foreign_prompts(records)
        for rec in records:
            tracker.feed(
                rec,
                copied=rec["msg_id"] in foreign_msgs,
                foreign_prompt=rec["human"] and rec["prompt_id"] in foreign_prompts)
        if not agent_key:
            self._store_turns(tracker.opened, tracker.closed)
            self._store_steers(tracker.steers)
        added = self._store_activity(agent_key, tracker.busy)
        vals = {"bytes_read": offset + consumed, "state": tracker.to_state()}
        if cursor:
            cursor.write(vals)
        else:
            Cursor.create(dict(vals, path=path, session_ref_id=self.id, agent_key=agent_key))
        if added:
            self.work_hours = self.work_hours + added
        return consumed

    def _foreign_messages(self, records):
        """API message ids in `records` already stored under another session."""
        ids = list({r["msg_id"] for r in records if r["msg_id"]})
        if not ids:
            return set()
        rows = self.env["flightdeck.message"].search_read(
            [("uuid", "in", ids), ("session_ref_id", "!=", self.id)], ["uuid"])
        return {r["uuid"] for r in rows}

    def _foreign_prompts(self, records):
        ids = list({r["prompt_id"] for r in records if r["human"] and r["prompt_id"]})
        if not ids:
            return set()
        rows = self.env["flightdeck.turn"].search_read(
            [("prompt_id", "in", ids), ("session_ref_id", "!=", self.id)], ["prompt_id"])
        return {r["prompt_id"] for r in rows}

    def _agent_prompt(self, records):
        """The turn an agent file's work belongs to.

        An agent's lines carry the prompt id of the turn that started it. When
        that turn began with a task notification rather than a person, the id
        is not a turn, and the person's turn running at that moment is.
        """
        first = next((r for r in records if r["prompt_id"]), None)
        if not first:
            return ""
        Turn = self.env["flightdeck.turn"]
        if Turn.search_count([("prompt_id", "=", first["prompt_id"])]):
            return first["prompt_id"]
        at = activity_reader.parse_ts(first["ts"])
        if at is None:
            return first["prompt_id"]
        running = Turn.search([("session_ref_id", "=", self.id),
                               ("started_at", "<=", at.replace(tzinfo=None))],
                              order="started_at desc", limit=1)
        return running.prompt_id or first["prompt_id"]

    def _store_turns(self, opened, closed=None):
        ids = set(opened) | set(closed or {})
        if not ids:
            return
        Turn = self.env["flightdeck.turn"]
        held = {t.prompt_id: t for t in Turn.search([("prompt_id", "in", list(ids))])}
        fresh = []
        for prompt_id, seen in opened.items():
            text = seen["text"] or ""
            vals = {
                "started_at": seen["started"].replace(tzinfo=None),
                "prompt_head": " ".join(text.split())[:200] or False,
                "prompt_text": text or False,
                "cwd": seen["cwd"] or False,
                "git_branch": seen["branch"] or False,
                "byte_start": seen["byte_start"] or 0,
            }
            turn = held.get(prompt_id)
            if turn is None:
                fresh.append(dict(vals, prompt_id=prompt_id, session_ref_id=self.id))
            elif turn.session_ref_id == self:
                # The first read wins: a later line with the same prompt id is
                # a replay under a stale id, not the prompt.
                vals = {k: v for k, v in vals.items() if v and not turn[k]}
                if vals:
                    turn.write(vals)
        for turn in Turn.create(fresh):
            held[turn.prompt_id] = turn
        for prompt_id, end in (closed or {}).items():
            turn = held.get(prompt_id)
            if not turn or turn.session_ref_id != self:
                continue
            vals = {}
            if "byte_end" in end:
                vals["byte_end"] = end["byte_end"] or 0
            if end.get("ended"):
                vals["ended_at"] = end["ended"].replace(tzinfo=None)
            elif end.get("last") and not turn.ended_at:
                vals["ended_at"] = end["last"].replace(tzinfo=None)
            if end.get("final"):
                vals["final_head"] = end["final"]
            if vals:
                turn.write(vals)

    def _store_steers(self, steers):
        """Store each message typed during a turn once, on that turn.

        A steer whose prompt is not a turn of this session goes to the person's
        turn running when it was sent.
        """
        steers = [s for s in steers if s.get("uuid")]
        if not steers:
            return
        Steer = self.env["flightdeck.turn.steer"]
        held = set(Steer.search([("uuid", "in", [s["uuid"] for s in steers])]).mapped("uuid"))
        Turn = self.env["flightdeck.turn"]
        turns = {t.prompt_id: t for t in Turn.search([
            ("prompt_id", "in", list({s["prompt_id"] for s in steers})),
            ("session_ref_id", "=", self.id)])}
        vals = []
        for steer in steers:
            if steer["uuid"] in held:
                continue
            at = steer["at"].replace(tzinfo=None)
            turn = turns.get(steer["prompt_id"]) or Turn.search(
                [("session_ref_id", "=", self.id), ("started_at", "<=", at)],
                order="started_at desc", limit=1)
            if not turn:
                continue
            held.add(steer["uuid"])
            vals.append({"turn_id": turn.id, "uuid": steer["uuid"], "sent_at": at,
                         "text": steer["text"]})
        Steer.create(vals)

    @api.model
    def _fd_backfill_steers(self):
        """Read every main transcript once for steer messages alone.

        The busy-time cursors stay where they are; the tracker runs only to
        know which turn is running and whether the lines are a resumed copy.
        """
        added = 0
        Steer = self.env["flightdeck.turn.steer"]
        for session in self.search([]):
            path = session._transcript_path()
            if not path or not os.path.exists(path):
                continue
            before = Steer.search_count([])
            tracker = activity_reader.Tracker(False)
            offset = 0
            while True:
                try:
                    records, consumed = activity_reader.slim_records(path, offset, limit=ACTIVITY_BUDGET)
                except OSError as e:
                    _logger.warning("cannot read steers in %s: %s", path, e)
                    break
                if not consumed:
                    break
                foreign_msgs = session._foreign_messages(records)
                foreign_prompts = session._foreign_prompts(records)
                tracker.steers = []
                for rec in records:
                    tracker.feed(rec, copied=rec["msg_id"] in foreign_msgs,
                                 foreign_prompt=rec["human"] and rec["prompt_id"] in foreign_prompts)
                session._store_steers(tracker.steers)
                offset += consumed
            added += Steer.search_count([]) - before
        return added

    def _store_activity(self, agent_key, busy):
        """Add busy seconds to their hour rows. Returns the hours added."""
        if not busy:
            return 0.0
        total = 0.0
        for (prompt_id, hour), seconds in busy.items():
            hours = seconds / 3600.0
            total += hours
            # An upsert, because an hour keeps filling while a session runs.
            self.env.cr.execute("""
                INSERT INTO flightdeck_agent_activity
                    (session_ref_id, project_id, agent_key, is_subagent, prompt_id, hour,
                     busy_hours, create_uid, write_uid, create_date, write_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now() at time zone 'UTC',
                        now() at time zone 'UTC')
                ON CONFLICT (session_ref_id, agent_key, prompt_id, hour)
                DO UPDATE SET busy_hours = flightdeck_agent_activity.busy_hours + EXCLUDED.busy_hours,
                              write_date = EXCLUDED.write_date
            """, (self.id, self.project_id.id or None, agent_key, bool(agent_key), prompt_id,
                  hour.replace(tzinfo=None), hours, self.env.uid, self.env.uid))
        self.env["flightdeck.agent.activity"].invalidate_model()
        return total

    def _drop_activity(self, agent_key):
        # SQL, not a domain: the main agent's key is the empty string, which a
        # domain would read as "not set".
        self.env.cr.execute(
            "DELETE FROM flightdeck_agent_activity WHERE session_ref_id = %s AND agent_key = %s"
            " RETURNING busy_hours", (self.id, agent_key))
        dropped = sum(row[0] or 0.0 for row in self.env.cr.fetchall())
        if dropped:
            self.work_hours = max(0.0, self.work_hours - dropped)
            self.env["flightdeck.agent.activity"].invalidate_model()

    # --- live sessions -------------------------------------------------------
    def _follow_tick(self):
        """Keep a followed session's busy time current between cron runs."""
        read = super()._follow_tick()
        path = self._transcript_path()
        if not path:
            return read
        files = [(path, "")]
        agents_dir = os.path.join(os.path.dirname(path), self.session_id, "subagents")
        for agent_path in glob.glob(os.path.join(agents_dir, "**", "agent-*.jsonl"), recursive=True):
            key = activity_reader.AGENT_FILE.search(agent_path)
            files.append((agent_path, key.group(1) if key else ""))
        Cursor = self.env["flightdeck.activity.cursor"]
        cursors = {c.path: c for c in Cursor.search([("path", "in", [f[0] for f in files])])}
        for file_path, agent_key in files:
            cursor = cursors.get(file_path)
            try:
                size = os.path.getsize(file_path)
            except OSError:
                continue
            if cursor and cursor.bytes_read == size and (
                    not agent_key or cursor.spend_bytes == size):
                continue
            self._activity_tick(file_path, agent_key, cursor)
            if agent_key:
                self._agent_spend_tick(file_path, agent_key)
        self._queue_label()
        return read
