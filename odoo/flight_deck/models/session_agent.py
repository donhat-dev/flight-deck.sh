"""One agent a session spawned: a row in the session's agent map.

The map starts at the session's last compact boundary, the same rule the CLI's
own reader applies, so what Odoo draws and what the terminal draws agree. The
rows are read-only in the sense that matters: nothing here drives an agent.
Stopping one means stopping the session that owns it.
"""
import glob
import json
import logging
import os
import time

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# An agent whose file has not been written for this long is not running any
# more. It is the only liveness signal for an agent whose own outcome was
# reported somewhere this side cannot read.
QUIET_SECONDS = 120

# What a task notification calls an outcome, and what this side calls it.
NOTIFY_STATUS = {
    "completed": "finished",
    "failed": "failed",
    "killed": "stopped",
    "stopped": "stopped",
}
STATUSES = [
    ("working", "Working"),
    ("finished", "Finished"),
    ("failed", "Failed"),
    ("stopped", "Stopped"),
]
BUS_AGENTS = "flightdeck.session/agents"


class FlightdeckSessionAgent(models.Model):
    _name = "flightdeck.session.agent"
    _description = "Agent spawned by a session"
    _order = "start_ts, id"
    _parent_store = True
    _parent_name = "parent_agent_id"
    _rec_name = "description"

    session_ref_id = fields.Many2one(
        "flightdeck.session", required=True, index=True, ondelete="cascade")
    parent_agent_id = fields.Many2one(
        "flightdeck.session.agent", index=True, ondelete="cascade")
    parent_path = fields.Char(index=True, unaccent=False)
    child_ids = fields.One2many("flightdeck.session.agent", "parent_agent_id")

    agent_id = fields.Char(index=True, help="The id the CLI gave this agent.")
    # Set on the row that IS a workflow run. Its agents carry it through their
    # parent, so one lookup finds the directory they all write into.
    workflow_id = fields.Char(index=True, help="The workflow run this row is.")
    journal_mtime = fields.Float(readonly=True)
    usage_bytes = fields.Integer(readonly=True)
    tool_use_id = fields.Char(index=True, help="The Agent call that started it.")
    description = fields.Char()
    prompt = fields.Text()
    agent_type = fields.Char("Kind")
    model_name = fields.Char("Model")
    spawn_depth = fields.Integer()
    is_background = fields.Boolean("Runs in background")

    status = fields.Selection(STATUSES, default="working", required=True, index=True)
    waiting = fields.Boolean(compute="_compute_waiting")
    summary = fields.Text()
    start_ts = fields.Datetime("Started", index=True)
    end_ts = fields.Datetime("Ended")
    duration_seconds = fields.Float("Seconds", compute="_compute_duration")
    # Reported by the task notification, which is the only place that counts a
    # subagent's own run. Float for the same reason the session totals are.
    total_tokens = fields.Float("Tokens", digits=(16, 0))
    tool_uses = fields.Integer("Tool calls")
    duration_ms = fields.Integer("Milliseconds")

    transcript_path = fields.Char(readonly=True)
    text_ids = fields.One2many("flightdeck.message.text", "agent_ref_id")
    text_count = fields.Integer("Transcript lines", readonly=True)
    follow_bytes = fields.Integer(readonly=True)
    follow_lines = fields.Integer(readonly=True)

    _agent_call_uniq = models.Constraint(
        "unique(session_ref_id, tool_use_id)", "This agent call is already on the map.")

    @api.depends("status", "session_ref_id.decision_ids.state")
    def _compute_waiting(self):
        """Working, and something of this agent's is waiting for a person.

        Not stored: it is a fact about a pending request, and the request is
        answered somewhere this record never hears about.
        """
        for agent in self:
            pending = agent.session_ref_id.decision_ids.filtered(
                lambda d: d.state == "pending" and d.agent_id == agent.agent_id)
            agent.waiting = bool(agent.status == "working" and agent.agent_id and pending)

    @api.depends("start_ts", "end_ts", "duration_ms")
    def _compute_duration(self):
        """What the run took, preferring the number the agent reported itself.

        The stamps are when the call was made and when the answer landed, which
        includes the wait; `duration_ms` is the run.
        """
        for agent in self:
            if agent.duration_ms:
                agent.duration_seconds = agent.duration_ms / 1000.0
            elif agent.start_ts and agent.end_ts:
                agent.duration_seconds = (agent.end_ts - agent.start_ts).total_seconds()
            else:
                agent.duration_seconds = 0.0

    # --- building the map ----------------------------------------------------
    @api.model
    def _values_from(self, session, found):
        """One scanned call to field values."""
        vals = {"session_ref_id": session.id, "tool_use_id": found["tool_use_id"]}
        for key, field in (("description", "description"), ("prompt", "prompt"),
                           ("subagent_type", "agent_type"), ("agent_id", "agent_id"),
                           ("workflow_id", "workflow_id"), ("summary", "summary")):
            if found.get(key):
                vals[field] = found[key]
        for key in ("total_tokens", "tool_uses", "duration_ms"):
            if found.get(key):
                vals[key] = found[key]
        if "is_background" in found:
            vals["is_background"] = found["is_background"]
        if found.get("ts"):
            vals["start_ts"] = found["ts"].replace("T", " ")[:19]
        if found.get("ts_end"):
            vals["end_ts"] = found["ts_end"].replace("T", " ")[:19]
        status = NOTIFY_STATUS.get(found.get("status") or "")
        if status:
            vals["status"] = status
        elif found.get("returned"):
            # A call that ran in the turn answers with its report, not with a
            # launch line, so its own result is the end of it.
            vals["status"] = "finished"
        return vals

    def _read_meta(self):
        """Fill in what the agent's own metadata file knows.

        The transcript says a call was made; this says what kind of agent ran,
        on which model, and under which parent. It is a few hundred bytes.
        """
        for agent in self.filtered("agent_id"):
            path = agent._agent_dir()
            if not path:
                continue
            meta_path = os.path.join(path, "agent-%s.meta.json" % agent.agent_id)
            own = os.path.join(path, "agent-%s.jsonl" % agent.agent_id)
            vals = {"transcript_path": own if os.path.exists(own) else False}
            try:
                with open(meta_path, encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                meta = {}
            for key, field in (("agentType", "agent_type"), ("model", "model_name"),
                               ("spawnDepth", "spawn_depth")):
                if meta.get(key):
                    vals[field] = meta[key]
            parent = meta.get("parentAgentId")
            if parent:
                found = self.search([("session_ref_id", "=", agent.session_ref_id.id),
                                     ("agent_id", "=", parent)], limit=1)
                if found:
                    vals["parent_agent_id"] = found.id
            agent.write(vals)

    @api.model
    def _adopt_nested(self, session):
        """Agents an agent spawned, which the session's transcript never sees.

        A nested call is made inside the parent agent's own file, so the only
        trace of it next to the session is the child's metadata naming its
        parent. Repeated because a child can spawn in turn.
        """
        directory = False
        for agent in session.agent_ids:
            directory = agent._agent_dir()
            if directory:
                break
        if not directory:
            return self.browse()
        # The directory has no cursor to read from, so every visit reads all of
        # it: measured at 17 microseconds per file, on a session holding a
        # hundred of them, on every turn that spawns an agent. Its own mtime
        # says when that is worth doing.
        try:
            stamp = os.stat(directory).st_mtime
        except OSError:
            stamp = 0.0
        if stamp and stamp == session.agent_meta_mtime:
            return self.browse()
        session.agent_meta_mtime = stamp
        metas = {}
        for name in os.listdir(directory):
            if not name.endswith(".meta.json"):
                continue
            try:
                with open(os.path.join(directory, name), encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                continue
            if meta.get("parentAgentId"):
                metas[name[len("agent-"):-len(".meta.json")]] = meta
        added = self.browse()
        for _pass in range(4):
            by_agent = {a.agent_id: a for a in session.agent_ids if a.agent_id}
            fresh = self.browse()
            for agent_id, meta in metas.items():
                parent = by_agent.get(meta["parentAgentId"])
                if not parent or agent_id in by_agent:
                    continue
                fresh |= self.create({
                    "session_ref_id": session.id,
                    "parent_agent_id": parent.id,
                    "agent_id": agent_id,
                    "tool_use_id": meta.get("toolUseId") or False,
                    "description": meta.get("description") or "Agent",
                    "agent_type": meta.get("agentType") or False,
                    "model_name": meta.get("model") or False,
                    "spawn_depth": meta.get("spawnDepth") or 2,
                    # A child outlives its parent often enough that the
                    # parent's state says nothing: the coordinator that spawns
                    # two agents and returns is the ordinary case. Its own file
                    # still growing is the signal that holds.
                    "status": self._liveness(directory, agent_id),
                    "start_ts": parent.start_ts,
                })
            if not fresh:
                break
            added |= fresh
        if added:
            added._read_meta()
            added._read_outcomes()
        return added

    @api.model
    def _adopt_workflow(self, session):
        """The agents a workflow ran, which the session's transcript never names.

        A workflow call says only where it will write. What it then started is
        written by the workflow itself, one `started` line per agent in the
        journal beside their transcripts, with the label and phase the script
        gave them.
        """
        added = self.browse()
        for row in session.agent_ids.filtered("workflow_id"):
            directory = row._agent_dir()
            if not directory:
                # A workflow that launched in a session whose transcripts are
                # gone leaves nothing to read and nothing to wait for.
                if row.status == "working" and row._quiet_for(QUIET_SECONDS):
                    row.status = "stopped"
                continue
            journal = os.path.join(directory, "journal.jsonl")
            try:
                stamp = os.stat(journal).st_mtime
            except OSError:
                continue
            if stamp != row.journal_mtime:
                row.journal_mtime = stamp
                added |= row._read_journal(journal, directory)
            alive = any(child.status == "working" for child in row.child_ids)
            if row.status == "working" and not alive and row.child_ids:
                row.status = "finished"
        if added:
            added._read_meta()
        return added

    def _read_journal(self, journal, directory):
        """One workflow's journal to rows, skipping the agents already there."""
        self.ensure_one()
        known = {child.agent_id for child in self.child_ids if child.agent_id}
        started, done = {}, set()
        try:
            with open(journal, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    agent_id = event.get("agentId")
                    if not agent_id:
                        continue
                    if event.get("type") == "started":
                        started[agent_id] = event
                    elif event.get("type") == "result":
                        done.add(agent_id)
        except OSError:
            return self.browse()
        fresh = self.browse()
        for agent_id, event in started.items():
            if agent_id in known:
                continue
            label = " · ".join(
                part for part in (event.get("phase"), event.get("label")) if part)
            fresh |= self.create({
                "session_ref_id": self.session_ref_id.id,
                "parent_agent_id": self.id,
                "agent_id": agent_id,
                "description": label or "Workflow agent",
                "spawn_depth": 2,
                # The journal records a result, not an end: an agent still
                # writing after one is the ordinary shape of a retry.
                "status": ("finished" if agent_id in done
                           else self._liveness(directory, agent_id)),
                "start_ts": self.start_ts,
            })
        return fresh

    def _quiet_for(self, seconds):
        """Started long enough ago that nothing more is expected of it."""
        self.ensure_one()
        if not self.start_ts:
            return False
        return (fields.Datetime.now() - self.start_ts).total_seconds() > seconds

    @api.model
    def _liveness(self, directory, agent_id):
        """Running or done, from how recently the agent wrote.

        Reading the file needs a permission the directory listing does not, so
        this works even where the content cannot be opened.
        """
        try:
            quiet = time.time() - os.path.getmtime(
                os.path.join(directory, "agent-%s.jsonl" % agent_id))
        except OSError:
            return "stopped"
        return "working" if quiet < QUIET_SECONDS else "finished"

    def _running(self):
        """The agents this side still believes are running, the map's own way.

        Three rules and no fourth. An agent whose outcome was reported is done.
        One still marked working whose file has been quiet longer than a run
        ever pauses is done too, and is written down as such — the same test
        `_liveness` applies when a nested agent is adopted. An agent the last
        compact boundary dropped is not on the map at all, so it never reaches
        here.
        """
        alive = self.browse()
        quiet = self.browse()
        for agent in self.filtered(lambda a: a.status == "working" and a.agent_id):
            path = agent.transcript_path
            if not path:
                continue
            try:
                since = time.time() - os.path.getmtime(path)
            except OSError:
                continue
            if since < QUIET_SECONDS:
                alive |= agent
            else:
                quiet |= agent
        if quiet:
            quiet.status = "finished"
            quiet._bus_update()
        return alive

    def _follow_running(self):
        """Read what the running agents have written since the last pass."""
        read = 0
        for agent in self._running():
            try:
                read += agent._load_transcript()
            except Exception:
                # One agent whose file is not readable yet does not get to stop
                # the session's own read, which is the thing someone is watching.
                _logger.warning("cannot follow agent %s", agent.id, exc_info=True)
        return read

    def _read_usage(self):
        """Tokens from the agent's own transcript.

        The agents a workflow ran are reported in its journal, which carries no
        usage at all, and a nested agent is reported in its parent's file. Their
        own transcript is the one place that always has the numbers, so it is
        read from where the last pass stopped and the totals added up.

        Cache reads are left out, because the notification that fills this field
        for every other agent leaves them out too: counting them puts a 39-turn
        agent at 2.6M against the 120k its notification reported. What is left
        still runs 10 to 25 per cent above that number on a long agent, so the
        two are the same measure but not the same figure.
        """
        billed = ("input_tokens", "output_tokens", "cache_create_5m", "cache_create_1h")
        from . import transcript_reader

        for agent in self:
            path = agent.transcript_path
            if not path or not os.access(path, os.R_OK):
                continue
            if agent.status == "stopped":
                # Adopted before its first write, so nothing could be stat-ed and
                # it was written down as gone. The file is here now.
                agent.status = self._liveness(os.path.dirname(path), agent.agent_id)
            try:
                rows, consumed = transcript_reader.read_usage(path, agent.usage_bytes)
            except OSError:
                continue
            if not consumed:
                continue
            vals = {"usage_bytes": agent.usage_bytes + consumed}
            if rows:
                vals["total_tokens"] = agent.total_tokens + sum(
                    row[key] for row in rows for key in billed)
                last = rows[-1].get("ts")
                if last and agent.status != "working":
                    vals["end_ts"] = last.replace("T", " ")[:19]
            agent.write(vals)

    def _read_outcomes(self):
        """What a parent agent's own transcript says about the agents it started.

        A nested agent reports to the agent that spawned it, so its status and
        its usage are written in that agent's file and nowhere the session can
        see.
        """
        from . import transcript_reader

        for parent in self.mapped("parent_agent_id"):
            path = parent.transcript_path
            children = {a.tool_use_id: a for a in parent.child_ids if a.tool_use_id}
            if not children or not path or not os.access(path, os.R_OK):
                continue
            # The parent's file runs to megabytes and is read from its start, so
            # it is read only while a child still has something to learn.
            if all(child.total_tokens and child.status != "working"
                   for child in children.values()):
                continue
            try:
                found, _boundary, _consumed = transcript_reader.scan_agents(
                    path, 0, known=children, reset=False)
            except OSError:
                continue
            for tool_use_id, call in found.items():
                child = children.get(tool_use_id)
                if child:
                    child.write(self._values_from(child.session_ref_id, call))

    def _agent_dir(self):
        """Where this row's own transcript lives.

        A workflow keeps its agents one level deeper, in a directory of its own.
        That directory sits under the session that was current when the workflow
        started, which a resume since then has changed, so the session's own
        tree is tried first and the whole project searched only after.
        """
        # Imported here: session.py names this module before it defines this.
        from .session import TRANSCRIPT_ROOT

        self.ensure_one()
        root = self.session_ref_id._subagent_dir()
        workflow = self.workflow_id or self.parent_agent_id.workflow_id
        if not workflow:
            return root
        if root:
            found = os.path.join(root, "workflows", workflow)
            if os.path.isdir(found):
                return found
        found = glob.glob(os.path.join(
            TRANSCRIPT_ROOT, "*", "*", "subagents", "workflows", workflow))
        return found[0] if found else False

    def _bus_payload(self):
        self.ensure_one()
        return {
            "id": self.id,
            "parent_id": self.parent_agent_id.id or False,
            "agent_id": self.agent_id or "",
            "description": self.description or "Agent",
            "agent_type": self.agent_type or "",
            "status": "waiting" if self.waiting else self.status,
            "is_background": self.is_background,
            "duration_seconds": self.duration_seconds,
            "total_tokens": self.total_tokens,
            "has_transcript": bool(self.transcript_path),
        }

    def _bus_update(self):
        for agent in self:
            agent.session_ref_id._bus_send(BUS_AGENTS, agent._bus_payload())

    # --- transcript ----------------------------------------------------------
    def action_open_transcript(self):
        """Load this agent's own transcript, once, and hand it to the dialog."""
        self.ensure_one()
        self._load_transcript()
        return {
            "type": "ir.actions.act_window",
            "res_model": "flightdeck.message.text",
            "name": self.description or "Agent",
            "view_mode": "list",
            "domain": [("agent_ref_id", "=", self.id)],
        }

    def action_load_transcript(self):
        """Read the agent's file up to now. What the map calls before it opens."""
        for agent in self:
            agent._load_transcript()
        return True

    def _load_transcript(self):
        """Read whatever the agent has written since the last read.

        A running agent is read on every follow tick; the rest are read when
        someone opens one. A session can hold two hundred of them, so nothing
        reads a transcript the map has no reason to.
        """
        from . import transcript_reader

        self.ensure_one()
        path = self.transcript_path
        if not path or not os.path.exists(path):
            return 0
        if not os.access(path, os.R_OK):
            # Said plainly rather than swallowed: the file is there and the
            # answer is a permission on the machine, not a missing transcript.
            raise UserError(_(
                "This agent's transcript is on disk but Odoo may not read it:\n%s") % path)
        offset, first_line = self.follow_bytes, self.follow_lines
        if os.path.getsize(path) <= offset:
            return 0
        try:
            rows, results, consumed, lines = transcript_reader.read_tail(path, offset, first_line)
        except OSError as e:
            # An agent file the owner has not shared yet. Saying so beats an
            # empty dialog with no reason in it.
            _logger.warning("cannot read %s: %s", path, e)
            return 0
        Text = self.env["flightdeck.message.text"]
        # By uuid alone: the column is unique across the whole table, so a line
        # already stored is never created again. One that sits on this agent's
        # session without naming an agent is this agent's line, stored before
        # the map existed, so it is claimed rather than skipped.
        stored = Text.search([("uuid", "in", [r["uuid"] for r in rows])])
        known = set(stored.mapped("uuid"))
        orphans = stored.filtered(
            lambda t: not t.agent_ref_id and t.session_ref_id == self.session_ref_id)
        if orphans:
            orphans.write({"agent_ref_id": self.id})
        fresh = [self._text_values(row) for row in rows if row["uuid"] not in known]
        if fresh:
            Text.create(fresh)
        if results:
            calls = Text.search([("agent_ref_id", "=", self.id),
                                 ("tool_use_id", "in", list(results)),
                                 ("tool_output", "=", False)])
            for call in calls:
                found = results[call.tool_use_id]
                vals = {"tool_output": found["text"]}
                if found.get("images"):
                    vals["tool_images"] = json.dumps(found["images"])
                    vals["tool_result_offset"] = found.get("offset") or 0
                call.write(vals)
        self.write({
            "follow_bytes": offset + consumed,
            "follow_lines": first_line + lines,
            "text_count": self.text_count + len(fresh) + len(orphans),
        })
        return len(fresh) + len(orphans)

    def _text_values(self, row):
        stamp = (row.get("ts") or "").replace("T", " ")[:19]
        return {
            "uuid": row["uuid"],
            "agent_ref_id": self.id,
            "session_ref_id": self.session_ref_id.id,
            "project_id": self.session_ref_id.project_id.id or False,
            "role": row.get("role") or False,
            "ts": stamp or False,
            "seq": row.get("seq") or 0,
            "text": row.get("text") or False,
            "tool_name": row.get("tool_name") or False,
            "tool_input": row.get("tool_input") or False,
            "tool_use_id": row.get("tool_use_id") or False,
            "tool_call_offset": row.get("offset") or 0,
        }
