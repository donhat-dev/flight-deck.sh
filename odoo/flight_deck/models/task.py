"""Cost per task: each turn labelled by what its prompt does, and its cost rolled up.

`flightdeck.turn` is one prompt a person wrote. The label pass gives it an
intent (`task_classifier` holds the rules), and the kind the rollup uses
follows from it; the rollup ties each billed row to its turn and adds the
turn's cost, tokens, agent work-hours, feedback counts and steer messages into
the task it counts under. Both run from one cron, and the rollup again after
a person changes a label.
"""
import json
import logging
import os
import statistics
from datetime import datetime, time, timedelta, timezone

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain

from odoo.addons.workflow_studio.workflow import workflow

from . import model_stats
from . import task_classifier as classifier
from .dashboard import _zone

_logger = logging.getLogger(__name__)

# Code runs record prompts and answers in summary; the same masking as for the engines.
workflow.register_redactor(classifier.redact)

INTENTS = [("task", "Task"), ("follow_up", "Follow-up"), ("ask", "Question about the work"),
           ("side", "Side question"), ("no_work", "No work")]
KINDS = [("task", "Task"), ("subtask", "Subtask"), ("no_work", "Not a task")]
SOURCES = [("rule", "Rule"), ("jev", "Jev"), ("llm", "Luna"), ("human", "Person")]
# Turns one pass labels. The first pass after an install has the whole backlog.
LABEL_LIMIT = 300
REVIEW_LIMIT = 40
# How long after a turn ends the follow tick still queues its label job.
LABEL_FRESH = timedelta(minutes=10)
# Spend reads a label job makes before labelling; one almost always reaches
# the end of the file, a second catches what was written meanwhile.
SPEND_TICKS = 3
# The label fields a person sets; writing one from the UI makes the label theirs.
HUMAN_FIELDS = {"intent", "parent_id"}


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value else None


def _by_turn(rec_of, ask):
    """`ask` called with turn keys ("#12"), which a person looks up in a code run;
    its answers handed back under the classifier's prompt ids (`rec_of` maps them)."""
    def call(payload):
        keys = {"#%d" % rec_of[p]: p for p in payload}
        answers = ask({k: payload[p] for k, p in keys.items()})
        return {p: answers[k] for k, p in keys.items() if k in answers}
    return call


def _jev_reads(turns, states, work_tags):
    """What the Ask Jev step records as input: per turn, the new prompt Jev judges and
    how many lines of context came before it; the work tags it could pick."""
    reads = {}
    for key, state in states.items():
        cut = max(state.rfind("NEW PROMPT"), 0)
        reads[key] = {"new prompt": state[cut:], "context lines": state[:cut].count("\n")}
    return {"work tags": sorted(work_tags), **reads}


def _luna_reads(turns, items, work_tags):
    """What the Ask Luna step records as input: per turn, the prompt and the agent's
    final message Luna reads, and how much context came with them."""
    reads = {key: {
        "prompt": item["prompt"],
        "final": item["this_turn_final"],
        "minutes since previous": item["minutes_since_previous_turn"],
        "previous turns": len(item["previous_turns"]),
        "session tasks": len(item["session_tasks"]),
        "other recent": len(item["other_recent"]),
    } for key, item in items.items()}
    return {"work tags": sorted(work_tags), **reads}


def _jev_said(runs):
    """What the Ask Jev step records: per turn, Jev's picks and whether it is sure."""
    said, sure, failed = {}, 0, 0
    for key, run in runs.items():
        kind = run["answers"].get("kind")
        if not kind:
            failed += 1
            said[key] = {"error": run.get("error") or "no answer", "engine call": run.get("call_id")}
            continue
        entry = {"said": kind["kind"], "p1": round(kind["p1"], 2),
                 "margin": round(kind["margin"], 2), "sure": classifier.clear(kind),
                 "engine call": run.get("call_id")}
        work = run["answers"].get("work")
        if work:
            entry["work"] = "%s %.2f" % (work["kind"], work["p1"])
        sure += entry["sure"]
        said[key] = entry
    summary = "%s: %d sure, %d not sure" % (classifier.JEV_MODEL, sure, len(runs) - sure - failed)
    if failed:
        summary += ", %d failed" % failed
    return {"summary": summary, **said}


def _detail(turn, lab):
    """A new label's detail: the rule's reason, and the Type a turn had before step 3."""
    detail = {"reason": lab["reason"]} if lab["source"] == "rule" and lab.get("reason") else {}
    old = (turn.label_detail or {}).get("old_intent")
    if old:
        detail["old_intent"] = old
    return detail or False


def _luna_said(answers):
    """What the Ask Luna step records: per turn, Luna's label."""
    said = {}
    for key, answer in answers.items():
        raw = answer.get("raw")
        if not raw:
            said[key] = {"error": answer.get("error") or "no label",
                         "engine call": answer.get("call_id")}
            continue
        entry = {"intent": raw.get("intent"), "confidence": raw.get("confidence"),
                 "engine call": answer.get("call_id")}
        if raw.get("title"):
            entry["title"] = raw["title"]
        if raw.get("work_tag"):
            entry["work"] = raw["work_tag"]
        said[key] = entry
    labelled = sum(1 for a in answers.values() if a.get("raw"))
    return {"summary": "%s: %d of %d labelled" % (classifier.LLM_MODEL, labelled, len(answers)),
            **said}



class FlightdeckSessionTasks(models.Model):
    _name = "flightdeck.session"
    _inherit = ["flightdeck.session", "workflow.run.linked.mixin"]

    turn_ids = fields.One2many("flightdeck.turn", "session_ref_id", "Turns")

    def _workflow_run_link_domain(self):
        # The session's own runs and its turns': what the Tasks tab shows.
        return list(Domain.OR([
            super()._workflow_run_link_domain(),
            [("res_model", "=", "flightdeck.turn"), ("res_id", "in", self.turn_ids.ids)],
        ]))

    def _spend_caught_up(self):
        """Whether this session's own file has been read for spend to its end.

        A turn is not labelled before: with its billed rows still unread it
        would look like a prompt that caused no work.
        """
        self.ensure_one()
        path = self._transcript_path()
        try:
            return not path or os.path.getsize(path) <= self.spend_bytes
        except OSError:
            return True

    def action_open_tasks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "flightdeck.turn",
            "name": "Turns — %s" % self.display_name,
            "view_mode": "list,form",
            "domain": [("session_ref_id", "=", self.id)],
        }

    # --- labelling a live session's turns as prompts arrive ---------------------
    def _queue_label(self):
        """Queue the label job when this session has a turn to label or to check.

        Called by the follow tick, which the file watcher wakes within a second
        of a write. A turn to label: no label and no Jev answer yet, started or
        ended in the last LABEL_FRESH (after that a failing job stops being
        queued, and the label cron takes the turn). A turn to check: labelled
        while running, and now ended. A turn the job tried but could not label
        (Jev failed) is marked open too, so it is tried once more when it ends,
        not on every write.
        """
        self.ensure_one()
        fresh = fields.Datetime.now() - LABEL_FRESH
        Turn = self.env["flightdeck.turn"]
        session = [("session_ref_id", "=", self.id)]
        new = Turn.search_count(session + [
            ("label_source", "=", False), ("jev_probs", "=", False), ("label_open", "=", False),
            "|", ("started_at", ">=", fresh), ("ended_at", ">=", fresh)], limit=1)
        ended = Turn.search_count(session + [("label_open", "=", True), ("ended_at", "!=", False)],
                                  limit=1)
        if new or ended:
            self.with_delay(
                priority=5,
                description="FlightDeck: label the new turns of %s" % self.display_name,
                identity_key="fd-label-%s" % self.id,
            )._label_live()

    @workflow.flow("flight_deck.label_new_turns", name="Label new turns")
    def _label_live(self):
        """Queue job: label this session's new turns, then check the ended ones.

        Returns what was decided per turn (`_label_report`), which is the
        run's output in Workflow Studio.

        1. New turns get the rules' or Jev's label at once, running or not;
           Luna, for a task's title and for what Jev was unsure of, follows in
           a job of its own (`_label_luna`), so it never holds this one up.
        2. Then spend is read and the rollup run, and a turn labelled while
           running that has ended with no billed call becomes "no work".

        Jobs run one at a time (`channels = root:1` in odoo.conf), so this
        never overlaps the label pass or a spend scan, which write the same
        rows. Its job function allows commits (data/task_data.xml), and each
        step commits: an uncommitted write to the session or a live turn,
        held through an engine call, collides with the follow tick's writes.
        """
        self.ensure_one()
        Turn = self.env["flightdeck.turn"]
        new = self._new_turns()
        if new.filtered("ended_at"):
            # An ended turn is labelled on its billed calls, which only the
            # spend read and the rollup make current.
            self._catch_up()
        if new:
            self._label_first(new)
        # After the labels, so a follow-up is placed under its task at once.
        self._catch_up()
        no_work = self._check_ended(Turn.search([
            ("session_ref_id", "=", self.id), ("label_open", "=", True), ("ended_at", "!=", False)]))
        if no_work:
            Turn._rollup()
        self.env.cr.commit()
        luna = self._queue_luna(new)
        return Turn._label_report(new | no_work, luna=luna)

    @workflow.task(name="Find new turns")
    def _new_turns(self):
        """This session's turns with no label and no Jev answer yet, oldest first."""
        return self.env["flightdeck.turn"].search([
            ("session_ref_id", "=", self.id), ("label_source", "=", False),
            ("jev_probs", "=", False), ("label_open", "=", False), ("started_at", "!=", False),
        ], order="started_at")

    @workflow.task(name="Label with rules and Jev")
    def _label_first(self, turns):
        """The rules' label, else Jev's; a turn Jev is unsure of keeps its answer for Luna.

        Returns who decided each turn. What Jev said is in its own step, and the
        flow's result holds the full label.
        """
        Turn = self.env["flightdeck.turn"]
        workflow.link(turns)
        Turn._label_turns(turns, commit=True, rollup=False, llm=False)
        turns.with_context(fd_label_engine=True).write({"label_open": True})
        self.env.cr.commit()
        decided = {}
        for t in turns:
            if not t.label_source:
                decided["#%d" % t.id] = "Jev not sure: for Luna"
            elif t.label_source == "rule" and (t.label_detail or {}).get("reason"):
                decided["#%d" % t.id] = "%s by rule: %s" % (t.intent, t.label_detail["reason"])
            else:
                decided["#%d" % t.id] = "%s by %s" % (t.intent, t.label_source)
        return decided or "No turn to label"

    @workflow.task(name="Queue Luna", summary=lambda why: {
        "#%d" % turn_id: reason for turn_id, reason in why.items()} or "No turn needs Luna")
    def _queue_luna(self, turns):
        """Queue Luna for the turns that need it; returns {turn id: why}.

        Luna is asked for what Jev was unsure of, for a task's title, and for a
        follow-up that Jev could not place under its task.
        """
        why = {}
        for t in turns:
            if not t.label_source:
                why[t.id] = "Jev was unsure"
            elif t.label_source == "jev" and t.intent == "task":
                why[t.id] = "a task needs a title"
            elif t.label_source == "jev" and t.intent in classifier.CONTINUES and not t.parent_id:
                why[t.id] = "no task to continue"
        if not why:
            return why
        self.with_delay(
            priority=20,
            description="FlightDeck: Luna for the new turns of %s" % self.display_name,
            identity_key="fd-luna-%s" % "-".join(str(i) for i in why),
        )._label_luna(list(why))
        return why

    @workflow.task(name="Read spend and roll up")
    def _catch_up(self):
        """Read this session's spend to the end of its file, then run the rollup."""
        path = self._transcript_path()
        for _ in range(SPEND_TICKS):
            read = path and self._spend_tick(path)
            self.env.cr.commit()
            if not read:
                break
        self.env["flightdeck.turn"]._rollup()
        self.env.cr.commit()

    @workflow.task(name="Mark ended turns without calls as no work")
    def _check_ended(self, turns):
        """A turn labelled while running that ended with no billed call is no work.

        Returns the turns relabelled, which the rollup has to move.
        """
        Turn = self.env["flightdeck.turn"]
        empty = turns.filtered(lambda t: not t.own_calls and t.label_source != "human")
        workflow.link(empty)
        if empty:
            Turn._apply_labels(
                {t.prompt_id: {"intent": "no_work", "source": "rule", "confidence": 1.0,
                               "reason": "empty"} for t in empty},
                [{"id": t.prompt_id, "rec": t.id} for t in empty])
        turns.with_context(fd_label_engine=True).write({"label_open": False})
        return empty

    @workflow.flow("flight_deck.label_new_turns", name="Label with Luna")
    def _label_luna(self, turn_ids):
        """Queue job: Luna for turns Jev labelled or was unsure of as they arrived.

        Luna judges them as in a full pass: when it disagrees with Jev its
        label is kept and the turn is queued for review. Jev is not asked
        again; its answer is on the turn. Returns what was decided per turn.
        """
        self.ensure_one()
        Turn = self.env["flightdeck.turn"]
        todo = Turn.browse(turn_ids).exists().filtered(lambda t: t.label_source in (False, "jev"))
        workflow.link(todo)
        if todo:
            Turn._label_turns(todo, commit=True)
        return Turn._label_report(todo)


class FlightdeckTaskTag(models.Model):
    _name = "flightdeck.task.tag"
    _description = "Task tag"
    _order = "kind, name"

    name = fields.Char(required=True)
    kind = fields.Selection(
        [("ticket", "Ticket"), ("work", "Work type")], required=True, default="work")
    code = fields.Char(
        "Ticket key", index=True,
        help="The key found in prompts, branches and session titles, such as CRM-12140.")
    path_pattern = fields.Char(
        "Working directory contains",
        help="Comma-separated. A task whose working directory contains one gets this work "
             "tag without asking Jev or Luna.")
    description = fields.Text(
        help="What the classifier reads when it picks a work type. "
             "A work tag without a description is never picked.")
    color = fields.Integer()
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint("unique(code)", "One tag per ticket key.")
    _name_uniq = models.Constraint("unique(name)", "Tag names are unique.")


class FlightdeckTurnTask(models.Model):
    _name = "flightdeck.turn"
    _inherit = ["flightdeck.turn", "workflow.run.linked.mixin"]
    _order = "started_at desc, id desc"

    # --- the label -----------------------------------------------------------
    intent = fields.Selection(INTENTS, "Type", index=True,
                              help="What the prompt does. The kind follows from it.")
    kind = fields.Selection(KINDS, index=True, readonly=True,
                            help="How the turn counts: as a task, under a task, or not at all.")
    parent_id = fields.Many2one(
        "flightdeck.turn", "Continues", index=True, ondelete="set null",
        help="The task a follow-up or a question about the work continues.")
    task_id = fields.Many2one(
        "flightdeck.turn", "Task", index=True, ondelete="set null", readonly=True,
        help="The task this turn counts under: itself for a task, the top of the "
             "chain for a subtask.")
    turn_ids = fields.One2many("flightdeck.turn", "task_id", "Turns of this task")
    is_task = fields.Boolean(index=True, readonly=True)
    title = fields.Char()
    label_source = fields.Selection(SOURCES, "Labelled by", index=True, readonly=True)
    label_confidence = fields.Float("Confidence", digits=(4, 2), readonly=True)
    needs_review = fields.Boolean(index=True, readonly=True)
    review_reason = fields.Char(readonly=True)
    label_queue_note = fields.Char(
        "Label job", compute="_compute_label_queue_note",
        help="For a turn with no label yet: the job that will label it, and since when it waits.")
    label_open = fields.Boolean(
        "Labelled while running", index=True, readonly=True,
        help="Labelled when the prompt arrived. Once the turn ends it is checked for a "
             "prompt that caused no billed call, which is labelled no work.")
    labeled_at = fields.Datetime("Labelled", readonly=True)
    label_detail = fields.Json(readonly=True)
    jev_kind = fields.Char("Jev said", readonly=True)
    jev_top1 = fields.Float("Jev top", digits=(4, 2), readonly=True)
    jev_margin = fields.Float("Jev margin", digits=(4, 2), readonly=True)
    jev_probs = fields.Json(readonly=True)
    llm_kind = fields.Char("Luna said", readonly=True)
    llm_confidence = fields.Float("Luna confidence", digits=(4, 2), readonly=True)
    run_ids = fields.One2many("flightdeck.label.run", "turn_id", "Engine calls")

    tag_ids = fields.Many2many(
        "flightdeck.task.tag", "flightdeck_turn_tag_rel", "turn_id", "tag_id", "Tags")
    task_tag_ids = fields.Many2many(
        "flightdeck.task.tag", "flightdeck_turn_task_tag_rel", "turn_id", "tag_id",
        "Task tags", readonly=True,
        help="The tags of every turn of this task.")
    tags_of_task = fields.Many2many(related="task_id.task_tag_ids", string="Tags of the task")
    steer_ids = fields.One2many("flightdeck.turn.steer", "turn_id", "Steer messages")

    # --- where it ran --------------------------------------------------------
    project_id = fields.Many2one(related="session_ref_id.project_id", store=True, index=True)

    # --- what it cost: this turn, then the task ---------------------------------
    own_cost = fields.Float("Turn cost", digits=(12, 4), readonly=True)
    own_output = fields.Float("Turn output", digits=(16, 0), readonly=True)
    own_tokens = fields.Float("Turn tokens", digits=(16, 0), readonly=True)
    own_calls = fields.Integer("Turn calls", readonly=True)
    own_hours = fields.Float("Turn agent-hours", digits=(8, 3), readonly=True)
    own_steers = fields.Integer("Turn steers", readonly=True)
    duration = fields.Float(
        digits=(8, 3), readonly=True, aggregator="sum",
        help="Hours from the prompt to the agent's last reply in this turn, at most until "
             "the next prompt.")
    cost = fields.Float(digits=(12, 4), readonly=True, aggregator="sum")
    output = fields.Float("Output tokens", digits=(16, 0), readonly=True, aggregator="sum")
    tokens = fields.Float(digits=(16, 0), readonly=True, aggregator="sum")
    calls = fields.Integer(readonly=True, aggregator="sum")
    hours = fields.Float("Agent-hours", digits=(8, 2), readonly=True, aggregator="sum")
    turn_count = fields.Integer("Turns", readonly=True, aggregator="sum")
    follow_ups = fields.Integer("Follow-ups", readonly=True, aggregator="sum")
    wrong_results = fields.Integer("Agent wrong", readonly=True, aggregator="sum",
                                   help="Feedback labels of the Agent wrong group on this task's results.")
    revisions = fields.Integer(readonly=True, aggregator="sum",
                               help="Content edits, look and feel changes and added scope asked of this task.")
    decisions = fields.Integer(readonly=True, aggregator="sum",
                               help="Replies to the agent's questions and proposals in this task.")
    pulls = fields.Integer("Pulls", readonly=True, aggregator="sum",
                           help="Explanations and status the person asked for, and steps they did themselves.")
    redirects = fields.Integer(readonly=True, aggregator="sum")
    rules_taught = fields.Integer("Rules taught", readonly=True, aggregator="sum")
    steers = fields.Integer(readonly=True, aggregator="sum")
    adjustments = fields.Integer(
        readonly=True, aggregator="avg",
        help="Agent wrong labels and redirects in this task: the times a person had to "
             "fix or redirect the agent. Averaged when grouped.")
    model = fields.Char(readonly=True, index=True,
                        help="The main agent's model with the most output in this task.")

    def _compute_label_queue_note(self):
        # Until its job runs, a turn has no run record: this is the only sign it is queued.
        Job = self.env["queue.job"].sudo()
        for turn in self:
            note = False
            if not turn.label_source and turn.session_ref_id:
                job = Job.search([("identity_key", "=", "fd-label-%s" % turn.session_ref_id.id),
                                  ("state", "in", ("pending", "enqueued", "started"))], limit=1)
                if job:
                    since = fields.Datetime.context_timestamp(turn, job.date_created).strftime("%H:%M")
                    note = ("Label job running since %s" if job.state == "started"
                            else "Label job queued at %s, not started yet") % since
            turn.label_queue_note = note

    @api.depends("title", "prompt_text", "prompt_head")
    def _compute_display_name(self):
        for turn in self:
            turn.display_name = (turn.title or classifier.title_of(turn.prompt_text or turn.prompt_head)
                                 or turn.prompt_id)

    # --- a person's label ------------------------------------------------------
    def write(self, vals):
        if "intent" in vals:
            vals = dict(vals, kind=classifier.INTENT_KIND.get(vals["intent"]) or False)
            if vals["intent"] not in classifier.CONTINUES and "parent_id" not in vals:
                vals["parent_id"] = False
        if HUMAN_FIELDS & set(vals) and not self.env.context.get("fd_label_engine"):
            vals = dict(vals, label_source="human", label_confidence=1.0, needs_review=False,
                        review_reason=False, labeled_at=fields.Datetime.now())
            if vals.get("intent") in classifier.CONTINUES and "parent_id" not in vals:
                for turn in self:
                    super(FlightdeckTurnTask, turn).write(
                        dict(vals, parent_id=turn.parent_id.id or turn._default_parent().id or False))
            else:
                super().write(vals)
            self._rollup()
            return True
        return super().write(vals)

    def _default_parent(self):
        """The session's latest task before this turn, else the turn before it."""
        self.ensure_one()
        earlier = [("session_ref_id", "=", self.session_ref_id.id),
                   ("started_at", "<", self.started_at)]
        return (self.search(earlier + [("intent", "=", "task")], order="started_at desc", limit=1)
                or self.search(earlier, order="started_at desc", limit=1))

    @api.model
    def fd_set_label(self, turn_id, intent):
        """The review queue's buttons."""
        turn = self.browse(turn_id).exists()
        if not turn or intent not in dict(INTENTS):
            return False
        turn.write({"intent": intent})
        return True

    def action_classify_again(self):
        """Run the stages again for these turns, a person's label included.

        Spend is read and the rollup run first: a turn whose billed rows are
        not stored yet would look like a prompt that caused no work. A turn
        still running is checked for that again when it ends. Both are
        committed before the engine calls, and the labels after them, as in
        the label jobs: a live session's row and its running turn are written
        by the follow tick every few seconds, and a write held through the
        calls fails on it.
        """
        for session in self.session_ref_id:
            path = session._transcript_path()
            if path:
                session._spend_tick(path)
        self._rollup()
        self.env.cr.commit()
        self._label_turns(self, force=True, commit=True)
        self.filtered(lambda t: not t.ended_at).with_context(fd_label_engine=True).write(
            {"label_open": True})
        return True

    def action_open_session(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "flightdeck.session",
            "res_id": self.session_ref_id.id,
            "view_mode": "form",
        }

    # --- the label pass -------------------------------------------------------
    @api.model
    def _cron_label(self):
        """Queue the label pass.

        As a job it runs one at a time with the job that labels a live
        session's turns as they end (`_label_live`), so the two never ask
        the engines about the same turn or write it at once.
        """
        self.with_delay(description="FlightDeck: label turns",
                        identity_key="fd-label-pass")._label_pass()
        return True

    @api.model
    def _label_pass(self, limit=LABEL_LIMIT):
        """Queue job: every settled turn without a label, and tasks still without a title.

        Its job function allows commits, like `_label_live`, and the rollup
        is committed before the engine calls for the same reason.
        """
        self._rollup()
        self.env.cr.commit()
        now = datetime.now(timezone.utc)
        settled = fields.Datetime.to_string(now - classifier.SETTLE)
        todo = self.search([
            ("label_source", "=", False),
            "|", ("ended_at", "!=", False), ("started_at", "<", settled),
        ], order="started_at", limit=limit)
        # A live session's file grows between two spend reads, so waiting for
        # the whole file to be read would never label its turns. A turn whose
        # own lines have been read is enough.
        todo = todo.filtered(
            lambda t: (t.byte_end and t.session_ref_id.spend_bytes >= t.byte_end)
            or t.session_ref_id._spend_caught_up())
        # Tasks labelled while Luna was away have no title yet.
        untitled = self.search([("intent", "=", "task"), ("title", "=", False),
                                ("label_source", "!=", False)], order="started_at", limit=limit)
        if todo or untitled:
            self._label_turns(todo, titles=untitled, commit=True)
        return len(todo)

    def _turn_dicts(self):
        """Every turn as the classifier reads it, sorted by start."""
        rows = self.search_read([], [
            "prompt_id", "session_ref_id", "started_at", "ended_at", "prompt_text",
            "prompt_head", "final_head", "cwd", "git_branch", "own_calls", "kind", "parent_id",
            "intent", "title",
        ], order="started_at, id")
        by_rec = {r["id"]: r["prompt_id"] for r in rows}
        turns = []
        for r in rows:
            if not r["started_at"]:
                continue
            turns.append({
                "rec": r["id"],
                "id": r["prompt_id"],
                "session": r["session_ref_id"] and r["session_ref_id"][0],
                "start": _aware(r["started_at"]),
                "end": _aware(r["ended_at"]),
                "prompt": r["prompt_text"] or r["prompt_head"] or "",
                "final": r["final_head"] or "",
                "cwd": r["cwd"] or "",
                "branch": r["git_branch"] or "",
                "calls": r["own_calls"],
                "kind": r["kind"],
                "intent": r["intent"],
                "title": r["title"] or "",
                "parent": by_rec.get(r["parent_id"] and r["parent_id"][0]),
            })
        return turns

    @api.model
    def _work_tags(self):
        tags = self.env["flightdeck.task.tag"].search([("kind", "=", "work")])
        return {t.name: t.description for t in tags if t.description}

    @api.model
    def _path_tags(self):
        tags = self.env["flightdeck.task.tag"].search([("kind", "=", "work"), ("path_pattern", "!=", False)])
        return {t.name: t.path_pattern for t in tags}

    @api.model
    def _label_settings(self):
        get = self.env["ir.config_parameter"].sudo().get_param
        return {
            "prefixes": [p.strip() for p in (get("flight_deck.ticket_prefixes") or "CRM").split(",")
                         if p.strip()],
            "llm": (get("flight_deck.llm_enabled") or "1") not in ("0", "false", "False"),
        }

    def _label_turns(self, todo, force=False, titles=None, commit=False, rollup=True, llm=True):
        """Label `todo`, and title the tasks in `titles`.

        `commit` is for the cron and batch runs. The engine calls take
        minutes, and writes made on the snapshot taken before them collide
        with the readers' updates of live turns; so the transaction ends after
        the calls, the labels are written on a fresh one and committed, and the
        rollup runs on its own. `llm=False` asks the rules and Jev only.
        """
        turns = self._turn_dicts()
        contexts = classifier.build_contexts(turns)
        settings = self._label_settings()
        work_tags = self._work_tags()
        llm = llm and settings["llm"]
        if llm:
            llm, message = classifier.llm_ready()
            if not llm:
                _logger.info("tasks: Luna skipped, codex is not ready: %s", message)
        titles = (titles or self.browse()) - todo if llm else self.browse()
        wanted = set(todo.ids) | set(titles.ids)
        batch = [t for t in turns if t["rec"] in wanted]
        # Jev's answer is kept on the turn, so a later pass does not ask Jev
        # the same thing again: a turn Jev was unsure of, waiting for Luna, or
        # one labelled by Jev when its prompt arrived, waiting for Luna's title.
        # An answer over Type values that no longer exist is asked again.
        known = {} if force else {
            t.prompt_id: {q: dict(zip(("kind", "p1", "margin"), classifier.spread(p)), probs=p)
                          for q, p in t.jev_probs.items() if p}
            for t in todo if t.jev_probs and t.label_source in (False, "jev")
            and set(t.jev_probs.get("kind") or ()) <= set(classifier.INTENTS)}
        rec_of = {t["id"]: t["rec"] for t in turns}
        labels, runs, answers = classifier.classify(
            batch, contexts, work_tags, key=classifier.jev_key(), llm=llm, known=known,
            log=_logger.info, turns=turns, titles_only=set(titles.mapped("prompt_id")),
            ask_jev=_by_turn(rec_of, lambda states: self._ask_jev(states, work_tags)),
            ask_luna=_by_turn(rec_of, lambda items: self._ask_luna(items, work_tags)))
        if commit:
            self.env.cr.commit()
        self.env["flightdeck.label.run"]._store_runs(runs, rec_of)
        self._apply_labels(labels, turns, force=force)
        todo._retype_from_feedback()
        self._keep_jev({p: a for p, a in answers.items() if p not in labels and p not in known},
                       {t["id"]: t["rec"] for t in batch})
        self._tag_tickets(todo, settings["prefixes"])
        if commit:
            self.env.cr.commit()
        if rollup:
            self._rollup()
        _logger.info("tasks: labelled %s of %s turn(s), %s engine call(s)",
                     len(labels), len(batch), len(runs))
        return labels

    @api.model
    @workflow.task(name="Ask Jev", input_summary=_jev_reads, summary=_jev_said)
    def _ask_jev(self, states, work_tags):
        """Ask Jev (TypeSafe System One) what each new prompt does.

        Input: `states`, per turn ("#<id>"), the text Jev reads: the new prompt,
        the turns before it in its session and recent turns elsewhere, redacted;
        `work_tags`, the kinds of work it may pick, with their descriptions.

        Output: per turn, Jev's pick of intent (task, follow_up, ask, side),
        its probability `p1` and its `margin` over the second,
        and the work tag it picked. `sure` means p1 and margin are high enough
        (`classifier.clear`): the pick is the label. A turn Jev is not sure of,
        and every task for its title, goes to Luna. The summary names the model.
        `engine call` is the id of the turn's Engine call (flightdeck.label.run),
        which holds the full text sent and Jev's whole answer; the step's input
        keeps only the start of the prompt.
        """
        runs = classifier.jev_batch(states, classifier.jev_key(), work_tags)
        # Stored here, not after classify, so each turn's output names its call.
        for key, run in runs.items():
            run["turn"] = key
        stored = self.env["flightdeck.label.run"]._store_runs(
            list(runs.values()), {key: int(key[1:]) for key in runs})
        for run, call in zip(runs.values(), stored):
            run["call_id"] = call.id
        return runs

    @api.model
    @workflow.task(name="Ask Luna", input_summary=_luna_reads, summary=_luna_said)
    def _ask_luna(self, items, work_tags):
        """Ask Luna (GPT-6 Luna through codex exec) to label turns, 15 to a call.

        Input: `items`, per turn ("#<id>"), what Luna reads: the prompt, the
        agent's final message, the minutes since the previous turn, the previous
        turns, the session's tasks and recent turns elsewhere, redacted;
        `work_tags` as for Jev.

        Output: per turn, Luna's intent and confidence, and for a task its title
        and work tag. A label Luna is not sure of, one naming a turn it was not
        offered, or one against a sure Jev goes to review. The summary names the
        model. `engine call` is the id of the turn's Engine call
        (flightdeck.label.run), which holds the item Luna read and its whole
        answer; the step's input keeps only the start of each text.
        """
        answers = classifier.luna_batch(items, work_tags)
        # Stored here, not after classify, so each turn's output names its call:
        # one row per turn, holding that turn's item and answer, as classify builds it.
        keys = list(items)
        stored = self.env["flightdeck.label.run"]._store_runs([{
            "engine": "llm", "turn": key, "sent": json.dumps(items[key], ensure_ascii=False),
            "raw": answers[key]["raw"], "error": answers[key]["error"],
            "latency_ms": answers[key]["latency_ms"],
        } for key in keys], {key: int(key[1:]) for key in keys})
        for key, call in zip(keys, stored):
            answers[key]["call_id"] = call.id
        return answers

    @api.model
    def _label_report(self, turns, luna=None):
        """What a label run decided, for its output in Workflow Studio.

        A summary line, then one entry per turn keyed "#<id>": the prompt's first
        line, the label and who gave it, what Jev and Luna said, the task it
        continues, and why it goes to Luna (`luna`: {turn id: reason}).
        """
        luna = luna or {}
        intents, sources = dict(INTENTS), dict(SOURCES)
        report, counts = {}, {}
        for t in turns.sorted("started_at"):
            entry = {"prompt": classifier.title_of(t.prompt_text or t.prompt_head)}
            if t.intent:
                entry["label"] = intents[t.intent]
                by = sources.get(t.label_source) or ""
                reason = (t.label_detail or {}).get("reason")
                if t.label_source == "rule" and reason:
                    by += " (%s)" % reason
                elif t.label_confidence:
                    by += " %.2f" % t.label_confidence
                entry["by"] = by
            else:
                entry["label"] = "none yet"
            counts[entry["label"]] = counts.get(entry["label"], 0) + 1
            if t.title:
                entry["title"] = t.title
            if t.parent_id:
                entry["continues"] = "#%d %s" % (t.parent_id.id, t.parent_id.display_name)
            if t.jev_kind:
                entry["jev said"] = "%s %.2f" % (t.jev_kind, t.jev_top1)
            if t.llm_kind:
                entry["luna said"] = "%s %.2f" % (t.llm_kind, t.llm_confidence)
            if t.needs_review:
                entry["review"] = t.review_reason or "yes"
            if t.id in luna:
                entry["next"] = "Luna: %s" % luna[t.id]
            report["#%d" % t.id] = entry
        if not report:
            return {"summary": "No turn to label"}
        summary = "%d turn(s): %s" % (len(report), ", ".join(
            "%d %s" % (n, label.lower()) for label, n in counts.items()))
        if luna:
            summary += "; %d sent to Luna" % len(luna)
        return {"summary": summary, **report}

    def _apply_labels(self, labels, turns, force=False):
        rec_of = {t["id"]: t["rec"] for t in turns}
        now = fields.Datetime.now()
        Tag = self.env["flightdeck.task.tag"]
        work_tags = {t.name: t for t in Tag.search([("kind", "=", "work")])}
        path_tags = self._path_tags()
        engine = self.with_context(fd_label_engine=True)
        for turn in engine.browse(list(rec_of[p] for p in labels if p in rec_of)):
            lab = labels[turn.prompt_id]
            if lab.get("titles_only"):
                vals = {"title": lab["title"] or False}
                work = work_tags.get(classifier.path_tag(turn.cwd, path_tags) or lab["work_tag"])
                if work and not turn.tag_ids.filtered(lambda t: t.kind == "work"):
                    vals["tag_ids"] = [(4, work.id)]
                turn.write(vals)
                continue
            if turn.label_source == "human" and not force:
                continue
            jev = (lab.get("jev") or {}).get("kind") or {}
            llm = lab.get("llm") or {}
            vals = {
                "intent": lab["intent"],
                "parent_id": rec_of.get(lab.get("parent")) or False,
                "title": lab.get("title") or False,
                "label_source": lab["source"],
                "label_confidence": lab.get("confidence") or 0.0,
                "needs_review": bool(lab.get("review")),
                "review_reason": lab.get("reason") if lab.get("review") else False,
                "labeled_at": now,
                "label_detail": _detail(turn, lab),
                "jev_kind": jev.get("kind") or False,
                "jev_top1": jev.get("p1") or 0.0,
                "jev_margin": jev.get("margin") or 0.0,
                "jev_probs": lab.get("jev") and {q: a["probs"] for q, a in lab["jev"].items()} or False,
                "llm_kind": llm.get("kind") or False,
                "llm_confidence": llm.get("confidence") or 0.0,
            }
            # One work tag per task, on the task's own turn; the turns under it
            # show the task's tags.
            commands = [(3, t.id) for t in turn.tag_ids if t.kind == "work"]
            if lab["intent"] == "task":
                work = work_tags.get(classifier.path_tag(turn.cwd, path_tags) or lab.get("work_tag"))
                if work:
                    commands.append((4, work.id))
            if commands:
                vals["tag_ids"] = commands
            turn.write(vals)

    def _keep_jev(self, answers, rec_of):
        """Write Jev's answer onto turns still waiting for Luna."""
        engine = self.with_context(fd_label_engine=True)
        for prompt_id, answer in answers.items():
            kind = answer.get("kind") or {}
            engine.browse(rec_of[prompt_id]).write({
                "jev_kind": kind.get("kind") or False,
                "jev_top1": kind.get("p1") or 0.0,
                "jev_margin": kind.get("margin") or 0.0,
                "jev_probs": {q: a["probs"] for q, a in answer.items()},
            })

    def action_pick_work_tag(self):
        """Ask Jev for the work type of these turns, and tag the clear answers.

        For turns labelled before work tags existed, such as the ones imported
        from the backend. A flat answer leaves the turn untagged.
        """
        key = classifier.jev_key()
        question = classifier.work_question(self._work_tags())
        if not key or not question:
            raise UserError("Work tags need a TypeSafe key and at least one work tag with a description.")
        turns = self._turn_dicts()
        contexts = classifier.build_contexts(turns)
        wanted = set(self.ids)
        batch = [t for t in turns if t["rec"] in wanted]
        got = classifier.jev_batch({t["id"]: classifier.state_text(t, contexts[t["id"]]) for t in batch},
                                   key, questions={"work": question})
        runs = [got[t["id"]] for t in batch]
        Tag = self.env["flightdeck.task.tag"]
        tags = {t.name: t for t in Tag.search([("kind", "=", "work")])}
        tagged = 0
        for t, run in zip(batch, runs):
            run["turn"] = t["id"]
            work = run["answers"].get("work")
            run["accepted"] = classifier.clear(work) and work["kind"] in tags
            if run["accepted"]:
                self.browse(t["rec"]).with_context(fd_label_engine=True).write(
                    {"tag_ids": [(4, tags[work["kind"]].id)]})
                tagged += 1
        self.env["flightdeck.label.run"]._store_runs(runs, {t["id"]: t["rec"] for t in batch})
        self._rollup()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": "%s of %s turn(s) tagged; Jev was not sure of the rest." % (tagged, len(batch)),
                "type": "success" if tagged else "warning",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _tag_tickets(self, turns, prefixes):
        """Ticket tags from the keys a turn's prompt, branch or session title names."""
        Tag = self.env["flightdeck.task.tag"]
        for turn in turns:
            keys = classifier.ticket_keys(
                [turn.prompt_text or turn.prompt_head, turn.git_branch, turn.session_ref_id.name],
                prefixes)
            if not keys:
                continue
            tags = Tag.with_context(active_test=False).search([("code", "in", keys)])
            known = {t.code for t in tags}
            tags |= Tag.create([{"name": k, "code": k, "kind": "ticket"}
                                for k in keys if k not in known])
            turn.with_context(fd_label_engine=True).write({"tag_ids": [(4, t.id) for t in tags]})

    # --- the rollup -------------------------------------------------------------
    @api.model
    @workflow.task(name="Roll up")
    def _rollup(self):
        """Tie billed rows to turns, then add turns up into their tasks.

        Only rows with `agent_key` set are tied. Every row has it since the
        old ledger's line-keyed copies were replaced with one row per call
        (`migration/dedupe_imported_ledger.py`); a row without it is unknown.
        """
        cr = self.env.cr
        self._link_previous_turns()
        # A turn ends at the latest when the next prompt of its session starts:
        # a reply that answers both, or a replayed line, runs past it.
        cr.execute("""
            WITH w AS (
                SELECT id, lead(started_at) OVER (PARTITION BY session_ref_id
                                                  ORDER BY started_at, id) AS next_start
                  FROM flightdeck_turn)
            UPDATE flightdeck_turn t SET ended_at = w.next_start
              FROM w
             WHERE t.id = w.id AND w.next_start IS NOT NULL AND t.ended_at > w.next_start
        """)
        cr.execute("""
            WITH v AS (
                SELECT id, CASE WHEN started_at IS NULL OR ended_at IS NULL THEN 0
                                ELSE greatest(extract(epoch FROM ended_at - started_at) / 3600.0, 0)
                           END AS d
                  FROM flightdeck_turn)
            UPDATE flightdeck_turn t SET duration = v.d
              FROM v
             WHERE t.id = v.id AND t.duration IS DISTINCT FROM v.d
        """)
        cr.execute("""
            WITH w AS (
                SELECT id, session_ref_id, started_at,
                       lead(started_at) OVER (PARTITION BY session_ref_id
                                              ORDER BY started_at, id) AS next_start
                  FROM flightdeck_turn),
            target AS (
                SELECT m.id, COALESCE(e.id, w.id) AS turn_id
                  FROM flightdeck_message m
                  LEFT JOIN flightdeck_turn e ON e.prompt_id = m.prompt_id
                  LEFT JOIN w ON e.id IS NULL AND w.session_ref_id = m.session_ref_id
                             AND m.ts >= w.started_at
                             AND (w.next_start IS NULL OR m.ts < w.next_start)
                 WHERE m.agent_key IS NOT NULL)
            UPDATE flightdeck_message m SET turn_id = target.turn_id
              FROM target
             WHERE m.id = target.id AND m.turn_id IS DISTINCT FROM target.turn_id
        """)
        cr.execute("""
            WITH s AS (
                SELECT turn_id, sum(cost) AS c, sum(output_tokens) AS o,
                       sum(total_tokens) AS k, count(*) AS n
                  FROM flightdeck_message WHERE turn_id IS NOT NULL GROUP BY turn_id),
            h AS (
                SELECT t.id, sum(a.busy_hours) AS h
                  FROM flightdeck_agent_activity a JOIN flightdeck_turn t ON t.prompt_id = a.prompt_id
                 GROUP BY t.id),
            v AS (
                SELECT t.id, COALESCE(s.c, 0) AS c, COALESCE(s.o, 0) AS o, COALESCE(s.k, 0) AS k,
                       COALESCE(s.n, 0) AS n, COALESCE(h.h, 0) AS h
                  FROM flightdeck_turn t LEFT JOIN s ON s.turn_id = t.id LEFT JOIN h ON h.id = t.id)
            UPDATE flightdeck_turn t
               SET own_cost = v.c, own_output = v.o, own_tokens = v.k, own_calls = v.n, own_hours = v.h
              FROM v
             WHERE t.id = v.id AND (t.own_cost IS DISTINCT FROM v.c OR t.own_output IS DISTINCT FROM v.o
                   OR t.own_tokens IS DISTINCT FROM v.k OR t.own_calls IS DISTINCT FROM v.n
                   OR t.own_hours IS DISTINCT FROM v.h)
        """)
        cr.execute("""
            WITH s AS (SELECT turn_id, count(*) AS n FROM flightdeck_turn_steer GROUP BY turn_id),
            v AS (SELECT t.id, COALESCE(s.n, 0) AS n FROM flightdeck_turn t LEFT JOIN s ON s.turn_id = t.id)
            UPDATE flightdeck_turn t SET own_steers = v.n
              FROM v
             WHERE t.id = v.id AND t.own_steers IS DISTINCT FROM v.n
        """)
        turns = self._turn_dicts()
        roots = classifier.resolve_tasks(turns)
        rec_of = {t["id"]: t["rec"] for t in turns}
        cr.execute("""
            UPDATE flightdeck_turn t SET task_id = v.task_id
              FROM unnest(%s::int[], %s::int[]) AS v(id, task_id)
             WHERE t.id = v.id AND t.task_id IS DISTINCT FROM v.task_id
        """, ([t["rec"] for t in turns], [rec_of[roots[t["id"]]] for t in turns]))
        # Feedback counts on the task whose result it judges: a prompt's labels
        # on the previous turn's task, a steer message's on its own turn's.
        cr.execute("""
            WITH s AS (
                SELECT task_id, sum(own_cost) AS c, sum(own_output) AS o, sum(own_tokens) AS k,
                       sum(own_calls) AS n, sum(own_hours) AS h, count(*) AS tc,
                       count(*) FILTER (WHERE intent = 'follow_up') AS fu,
                       sum(own_steers) AS st
                  FROM flightdeck_turn WHERE task_id IS NOT NULL GROUP BY task_id),
            f AS (
                SELECT p.task_id, k.family, k.code
                  FROM flightdeck_turn_feedback_rel r
                  JOIN flightdeck_turn t ON t.id = r.turn_id
                  JOIN flightdeck_turn p ON p.id = t.previous_turn_id
                  JOIN flightdeck_feedback_kind k ON k.id = r.kind_id
                 WHERE p.task_id IS NOT NULL
                UNION ALL
                SELECT t.task_id, k.family, k.code
                  FROM flightdeck_steer_feedback_rel r
                  JOIN flightdeck_turn_steer s ON s.id = r.steer_id
                  JOIN flightdeck_turn t ON t.id = s.turn_id
                  JOIN flightdeck_feedback_kind k ON k.id = r.kind_id
                 WHERE t.task_id IS NOT NULL),
            g AS (
                SELECT task_id,
                       count(*) FILTER (WHERE family = 'wrong') AS wr,
                       count(*) FILTER (WHERE family = 'revise') AS rv,
                       count(*) FILTER (WHERE code = 'decision') AS de,
                       count(*) FILTER (WHERE code IN ('explain', 'status', 'handback')) AS pu,
                       count(*) FILTER (WHERE code = 'redirect') AS rd,
                       count(*) FILTER (WHERE code = 'teach') AS te
                  FROM f GROUP BY task_id),
            v AS (
                SELECT t.id, COALESCE(s.c, 0) AS c, COALESCE(s.o, 0) AS o, COALESCE(s.k, 0) AS k,
                       COALESCE(s.n, 0) AS n, COALESCE(s.h, 0) AS h, COALESCE(s.tc, 0) AS tc,
                       COALESCE(s.fu, 0) AS fu, COALESCE(s.st, 0) AS st,
                       COALESCE(g.wr, 0) AS wr, COALESCE(g.rv, 0) AS rv, COALESCE(g.de, 0) AS de,
                       COALESCE(g.pu, 0) AS pu, COALESCE(g.rd, 0) AS rd, COALESCE(g.te, 0) AS te,
                       (t.task_id = t.id AND COALESCE(t.kind, '') <> 'no_work') AS is_task
                  FROM flightdeck_turn t LEFT JOIN s ON s.task_id = t.id LEFT JOIN g ON g.task_id = t.id)
            UPDATE flightdeck_turn t
               SET cost = v.c, output = v.o, tokens = v.k, calls = v.n, hours = v.h,
                   turn_count = v.tc, is_task = v.is_task, follow_ups = v.fu, steers = v.st,
                   wrong_results = v.wr, revisions = v.rv, decisions = v.de, pulls = v.pu,
                   redirects = v.rd, rules_taught = v.te, adjustments = v.wr + v.rd
              FROM v
             WHERE t.id = v.id AND (t.cost IS DISTINCT FROM v.c OR t.calls IS DISTINCT FROM v.n
                   OR t.hours IS DISTINCT FROM v.h OR t.turn_count IS DISTINCT FROM v.tc
                   OR t.output IS DISTINCT FROM v.o OR t.tokens IS DISTINCT FROM v.k
                   OR t.is_task IS DISTINCT FROM v.is_task OR t.follow_ups IS DISTINCT FROM v.fu
                   OR t.steers IS DISTINCT FROM v.st OR t.wrong_results IS DISTINCT FROM v.wr
                   OR t.revisions IS DISTINCT FROM v.rv OR t.decisions IS DISTINCT FROM v.de
                   OR t.pulls IS DISTINCT FROM v.pu OR t.redirects IS DISTINCT FROM v.rd
                   OR t.rules_taught IS DISTINCT FROM v.te
                   OR t.adjustments IS DISTINCT FROM v.wr + v.rd)
        """)
        # The model a person worked with: the main agent's, by output. A
        # subagent on a cheaper model would otherwise name the task.
        cr.execute("""
            WITH m AS (
                SELECT t.task_id, msg.model, sum(msg.output_tokens) AS o
                  FROM flightdeck_message msg JOIN flightdeck_turn t ON t.id = msg.turn_id
                 WHERE t.task_id IS NOT NULL AND msg.agent_key = '' AND msg.model NOT LIKE '<%'
                 GROUP BY 1, 2),
            r AS (SELECT DISTINCT ON (task_id) task_id, model FROM m ORDER BY task_id, o DESC, model),
            v AS (SELECT t.id, r.model FROM flightdeck_turn t LEFT JOIN r ON r.task_id = t.id)
            UPDATE flightdeck_turn t SET model = v.model
              FROM v
             WHERE t.id = v.id AND t.model IS DISTINCT FROM v.model
        """)
        cr.execute("""
            WITH want AS (
                SELECT DISTINCT t.task_id AS turn_id, r.tag_id
                  FROM flightdeck_turn_tag_rel r JOIN flightdeck_turn t ON t.id = r.turn_id
                 WHERE t.task_id IS NOT NULL)
            DELETE FROM flightdeck_turn_task_tag_rel x
             WHERE NOT EXISTS (SELECT 1 FROM want w WHERE w.turn_id = x.turn_id AND w.tag_id = x.tag_id)
        """)
        cr.execute("""
            INSERT INTO flightdeck_turn_task_tag_rel (turn_id, tag_id)
            SELECT DISTINCT t.task_id, r.tag_id
              FROM flightdeck_turn_tag_rel r JOIN flightdeck_turn t ON t.id = r.turn_id
             WHERE t.task_id IS NOT NULL
            ON CONFLICT DO NOTHING
        """)
        self.env["flightdeck.message"].invalidate_model(["turn_id"])
        self.invalidate_model()
        return True

    # --- the panel above the list ---------------------------------------------
    @api.model
    def fd_task_panel(self, domain=None):
        """Figures for the tasks the list shows, and the review queue."""
        domain = list(domain or []) + [("is_task", "=", True)]
        costs = sorted(self.search(domain).mapped("cost"))

        def pct(p):
            return costs[min(len(costs) - 1, int(p * len(costs)))] if costs else 0.0

        # One queue for both labels: a Type an engine was unsure of, or Feedback to review.
        to_review = ["|", "&", ("needs_review", "=", True), ("label_source", "!=", "human"),
                     ("feedback_state", "=", "review")]
        queue = self.search(to_review, order="started_at desc", limit=REVIEW_LIMIT)
        kinds = self.env["flightdeck.feedback.kind"].search([], order="sequence, id")
        return {
            "tasks": len(costs),
            "cost": sum(costs),
            "median": statistics.median(costs) if costs else 0.0,
            "p90": pct(0.9),
            "unlabeled": self.search_count([("label_source", "=", False)]),
            "review_count": self.search_count(to_review),
            "feedback_kinds": [{"code": k.code, "name": k.name, "family": k.family} for k in kinds],
            "review": [{
                "id": t.id,
                "prompt": classifier.title_of(t.prompt_text or t.prompt_head),
                "started_at": fields.Datetime.to_string(t.started_at),
                "project": t.project_id.display_name or "",
                "kind": t.intent or "",
                "source": t.label_source or "",
                "confidence": t.label_confidence,
                "jev_kind": t.jev_kind or "",
                "jev_top1": t.jev_top1,
                "llm_kind": t.llm_kind or "",
                "llm_confidence": t.llm_confidence,
                "reason": t.review_reason or "",
                "parent": t.parent_id.display_name or "",
                "cost": t.own_cost,
                "type_review": t.needs_review and t.label_source != "human",
                "feedback": t.feedback_ids.mapped("code"),
                "feedback_review": t.feedback_state == "review",
                "feedback_reason": (t.feedback_detail or {}).get("reason") or "",
            } for t in queue],
        }

    # --- adjustments per task, by model -------------------------------------------
    @api.model
    def fd_model_stats(self, tz_name=None, weeks=8, model=None, baseline=None):
        """Tasks > Adjustments per task: `weeks` local weeks up to this one."""
        zone = _zone(tz_name or self.env.context.get("tz") or self.env.user.tz or "UTC")
        this_week = model_stats.week_of(datetime.now(zone).date())
        first_week = this_week - timedelta(weeks=max(int(weeks or 8), 1) - 1)
        start = datetime.combine(first_week, time.min, zone).astimezone(timezone.utc)
        rows = self.search_read(
            [("is_task", "=", True), ("started_at", ">=", start.replace(tzinfo=None))],
            ["started_at", "model", "cost", *model_stats.METRICS])
        tasks = [dict(r, model=r["model"] or "",
                      week=model_stats.week_of(_aware(r["started_at"]).astimezone(zone).date()))
                 for r in rows]
        first_seen, counts = {}, {}
        for name, first, count in self._read_group(
                [("is_task", "=", True)], ["model"], ["started_at:min", "__count"]):
            first_seen[name or ""] = first
            counts[name or ""] = count
        weeks_shown = [first_week + timedelta(weeks=i)
                       for i in range((this_week - first_week).days // 7 + 1)]
        return model_stats.summarize(
            tasks, weeks_shown, model_stats.colour_slots(first_seen, counts),
            model=model, baseline=baseline,
            first_used={name: _aware(first).astimezone(zone).date()
                        for name, first in first_seen.items() if first})

    # --- the task's own transcript ------------------------------------------------
    def fd_transcript(self):
        """The turns of this task, and the lines of each read from its file."""
        self.ensure_one()
        task = self.task_id or self
        turns = (task | task.turn_ids).sorted("started_at")
        out = []
        for turn in turns:
            out.append({
                "id": turn.id,
                "label": turn.display_name,
                "kind": dict(INTENTS).get(turn.intent, ""),
                "started_at": fields.Datetime.to_string(turn.started_at),
                "session": turn.session_ref_id.display_name,
                "current": turn == self,
            })
        return {"turns": out}

    def fd_turn_lines(self):
        """This turn's lines, read from the session's file between its offsets."""
        self.ensure_one()
        path = self.session_ref_id._transcript_path()
        if not path or not os.path.exists(path):
            return {"missing": True, "lines": []}
        from . import transcript_reader
        start = self.byte_start or 0
        size = os.path.getsize(path)
        end = self.byte_end or size
        end = min(end, start + transcript_reader.TURN_READ_BYTES)
        try:
            rows = transcript_reader.read_range(path, start, end)
        except OSError as e:
            raise UserError("Cannot read the transcript: %s" % e) from e
        return {"missing": False, "lines": rows, "cut": end < (self.byte_end or size)}
