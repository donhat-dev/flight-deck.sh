"""Feedback labels: what a prompt says about the agent's result just before it.

Spec 2026-09-28-turn-feedback-labels-design, step 1: stored beside the Type
label and read by nothing yet. `feedback_classifier` holds the stages; this
file holds the labels, the fields on turns and steers, the job that labels
them, and the label check a person fills in before the labels are trusted.
"""
import logging

from odoo import api, fields, models

from odoo.addons.workflow_studio.workflow import workflow

from . import feedback_classifier as feedback
from . import task_classifier as classifier
from .task import SOURCES

_logger = logging.getLogger(__name__)

FAMILIES = [("wrong", "Agent wrong"), ("revise", "Revision"), ("control", "Control")]
FEEDBACK_STATES = [("todo", "Not labelled"), ("done", "Labelled"), ("review", "To review")]
# The fields a person sets; writing one from the UI makes the feedback theirs.
FEEDBACK_FIELDS = {"feedback_ids", "feedback_main_id", "feedback_no_fit", "check_intent"}
# Labels no engine overwrites: a person's, and the reference labels Opus gave the
# label check (kept apart from a person's, so an AI's judgement is never shown as one).
FEEDBACK_SOURCES = SOURCES + [("reference", "Opus (reference)")]
KEPT = ("human", "reference")
# The Type values of the spec's step 3, for the label check's own Type answer.
CHECK_TYPES = [("task", "Task"), ("follow_up", "Follow-up"), ("ask", "Question about the work"),
               ("side", "Side question"), ("no_work", "No work")]
FEEDBACK_LIMIT = 300


def _mark_human(records, vals):
    """`vals` for a write, marked as a person's feedback when a person sets it."""
    if FEEDBACK_FIELDS & set(vals) and not records.env.context.get("fd_label_engine"):
        return dict(vals, feedback_source="human", feedback_state="done",
                    feedback_confidence=1.0), True
    return vals, False


def _main_from_labels(records):
    """Keep a person's main label among their labels: the only one when there is one."""
    for rec in records:
        if rec.feedback_main_id and rec.feedback_main_id in rec.feedback_ids:
            continue
        main = rec.feedback_ids[:1] if len(rec.feedback_ids) == 1 else rec.feedback_ids.browse()
        rec.with_context(fd_label_engine=True).write({"feedback_main_id": main.id or False})


class FlightdeckFeedbackKind(models.Model):
    _name = "flightdeck.feedback.kind"
    _description = "Feedback label"
    _order = "sequence, id"

    code = fields.Char(required=True, help="What the engines answer with. Never renamed.")
    name = fields.Char(required=True)
    family = fields.Selection(FAMILIES, "Group", required=True)
    description = fields.Text(
        "Criterion", help="What Jev and Luna read when they pick a label. "
                          "A label without one is never picked.")
    fix_layer = fields.Char("Where to fix", help="Where a repeated case of this label is fixed.")
    sequence = fields.Integer(default=10)
    color = fields.Integer()
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint("unique(code)", "One label per code.")

    @api.model
    def _criteria(self):
        """`{code: criterion}` of the active labels the engines may pick."""
        return {k.code: k.description for k in self.search([]) if k.description}


class FlightdeckTurnFeedback(models.Model):
    _inherit = "flightdeck.turn"

    previous_turn_id = fields.Many2one(
        "flightdeck.turn", "Turn before", index=True, ondelete="set null", readonly=True,
        help="The session's turn before this one: the result this prompt's feedback is about.")
    previous_prompt = fields.Text(related="previous_turn_id.prompt_text", string="Prompt before")
    previous_final = fields.Text(related="previous_turn_id.final_head", string="Reply before")
    feedback_ids = fields.Many2many(
        "flightdeck.feedback.kind", "flightdeck_turn_feedback_rel", "turn_id", "kind_id", "Feedback",
        help="What this prompt says about the agent's result just before it.")
    feedback_main_id = fields.Many2one("flightdeck.feedback.kind", "Main feedback", index=True,
                                       ondelete="set null")
    feedback_source = fields.Selection(FEEDBACK_SOURCES, "Feedback by", index=True, readonly=True)
    feedback_confidence = fields.Float("Feedback confidence", digits=(4, 2), readonly=True)
    feedback_state = fields.Selection(FEEDBACK_STATES, "Feedback state", default="todo", index=True,
                                      readonly=True)
    feedback_detail = fields.Json(readonly=True)
    feedback_jev = fields.Json(
        "Jev said, waiting for Luna", readonly=True,
        help="Jev's unclear answer, kept so the next pass goes straight to Luna.")
    feedback_gold = fields.Boolean(
        "In the label check", index=True, readonly=True,
        help="One of the prompts a person labels by hand before the engines' labels are trusted.")
    feedback_no_fit = fields.Boolean(
        "No label fits", help="For the label check: none of the labels describes this prompt.")
    check_intent = fields.Selection(
        CHECK_TYPES, "Type (label check)",
        help="The label check's answer for Type, in the values Type will have after step 3.")
    feedback_run_ids = fields.One2many(
        "flightdeck.label.run", "turn_id", "Feedback engine calls",
        domain=[("question", "=", "feedback"), ("steer_id", "=", False), ("probe", "=", False)])

    # --- a person's label --------------------------------------------------------
    def write(self, vals):
        vals, mine = _mark_human(self, vals)
        result = super().write(vals)
        if mine:
            _main_from_labels(self)
        return result

    @api.model
    def fd_set_feedback(self, turn_id, codes):
        """The review queue's Save feedback: a person's labels, the first one main."""
        kinds = self.env["flightdeck.feedback.kind"].search([("code", "in", list(codes))])
        by_code = {k.code: k for k in kinds}
        ordered = [by_code[c] for c in codes if c in by_code]
        self.browse(turn_id).write({"feedback_ids": [(6, 0, [k.id for k in ordered])],
                                    "feedback_main_id": ordered[0].id if ordered else False})
        return True

    # --- the pass ------------------------------------------------------------------
    @api.model
    def _cron_label_feedback(self):
        """Queue the feedback pass. As a job it runs one at a time with the label jobs."""
        self.with_delay(description="FlightDeck: label feedback",
                        identity_key="fd-feedback-pass")._label_feedback_pass()
        return True

    @api.model
    @workflow.flow("flight_deck.label_feedback", name="Label feedback")
    def _label_feedback_pass(self, limit=FEEDBACK_LIMIT):
        """Queue job: label turns and steer messages that have no feedback yet.

        Its job function allows commits: the engine calls take minutes, and a
        write held through them collides with the follow tick's writes of live
        turns. So the calls end the transaction, and the labels are written and
        committed on a fresh one.
        """
        self._link_previous_turns()
        # The newest turn is often live, and the follow tick writes it every few seconds.
        self.env.cr.commit()
        llm = self._label_settings()["llm"] and classifier.llm_ready()[0]
        turns = self._find_feedback_turns(limit, llm=llm)
        steers = self._find_feedback_steers(limit, llm=llm)
        if not turns and not steers:
            return {"summary": "No turn or steer message without feedback"}
        items, recs = self._feedback_items(turns, steers)
        self.env.cr.commit()
        kinds = self.env["flightdeck.feedback.kind"]._criteria()
        labels, runs, pending = feedback.classify(
            items, kinds, key=classifier.jev_key(), llm=llm, log=_logger.info,
            ask_jev=self._ask_jev_feedback, ask_luna=lambda i: self._ask_luna_feedback(i, kinds))
        self.env.cr.commit()
        self._store_feedback(labels, recs, runs=runs, pending=pending)
        self.env.cr.commit()
        return self._feedback_report(labels, recs)

    @api.model
    @workflow.task(name="Link each turn to the one before")
    def _link_previous_turns(self):
        """Store each turn's previous turn in its session; returns how many changed."""
        self.env.cr.execute("""
            WITH o AS (SELECT id, lag(id) OVER (PARTITION BY session_ref_id
                                                ORDER BY started_at, id) AS prev
                         FROM flightdeck_turn WHERE started_at IS NOT NULL)
            UPDATE flightdeck_turn t SET previous_turn_id = o.prev
              FROM o WHERE t.id = o.id AND t.previous_turn_id IS DISTINCT FROM o.prev
        """)
        changed = self.env.cr.rowcount
        self.invalidate_model(["previous_turn_id"])
        return changed

    @api.model
    @workflow.task(name="Find turns without feedback",
                   summary=lambda turns: "%d turn(s)" % len(turns))
    def _find_feedback_turns(self, limit, llm=True):
        """Newest first, so live turns are labelled before the backlog. A turn in
        the label check waits for the person; with Luna off, a turn that waits for
        Luna is left out, so it does not hold a place the backlog needs."""
        domain = [("feedback_state", "=", "todo"), ("feedback_gold", "=", False),
                  ("started_at", "!=", False)]
        if not llm:
            domain.append(("feedback_jev", "=", False))
        turns = self.search(domain, order="started_at desc", limit=limit)
        workflow.link(turns)
        return turns

    @api.model
    @workflow.task(name="Find steer messages without feedback",
                   summary=lambda steers: "%d steer message(s)" % len(steers))
    def _find_feedback_steers(self, limit, llm=True):
        domain = [("feedback_state", "=", "todo")]
        if not llm:
            domain.append(("feedback_jev", "=", False))
        return self.env["flightdeck.turn.steer"].search(domain, order="sent_at desc", limit=limit)

    @api.model
    def _feedback_items(self, turns, steers):
        """What each stage reads, keyed "#<turn id>" and "steer-<id>", and the record of each key."""
        items, recs = {}, {}
        if turns:
            dicts = self._turn_dicts()
            contexts = classifier.build_contexts(dicts)
            wanted = set(turns.ids)
            for t in dicts:
                if t["rec"] not in wanted:
                    continue
                c = contexts[t["id"]]
                key = "#%d" % t["rec"]
                recs[key] = self.browse(t["rec"])
                items[key] = {"state": classifier.state_text(t, c), "luna": feedback.turn_item(t, c),
                              "rule": feedback.rule_feedback(t, c), "jev": recs[key].feedback_jev}
        for steer in steers:
            key = "steer-%d" % steer.id
            prompt = steer.turn_id.prompt_text or steer.turn_id.prompt_head or ""
            reply = self._reply_before(steer)
            items[key] = {"state": feedback.steer_state(prompt, reply, steer.text or ""),
                          "luna": feedback.steer_item(key, prompt, reply, steer.text or ""),
                          "rule": None, "jev": steer.feedback_jev}
            recs[key] = steer
        return items, recs

    @api.model
    def _reply_before(self, steer):
        """The agent's last text in the steer's turn before the steer was sent."""
        if not (steer.turn_id.started_at and steer.sent_at):
            return ""
        self.env.cr.execute("""
            SELECT text FROM flightdeck_message_text
             WHERE session_ref_id = %s AND agent_ref_id IS NULL AND role = 'assistant'
               AND tool_name IS NULL AND coalesce(text, '') <> ''
               AND ts >= %s AND ts <= %s
             ORDER BY ts DESC, seq DESC LIMIT 1
        """, [steer.session_ref_id.id, steer.turn_id.started_at, steer.sent_at])
        row = self.env.cr.fetchone()
        return row[0] if row else ""

    @api.model
    @workflow.task(name="Ask Jev", input_summary=lambda self, states: {
        k: "%d chars" % len(v) for k, v in list(states.items())[:20]},
        summary=lambda runs: {k: (r.get("error") or "%s %.2f" % (
            (r["answers"].get("feedback") or {}).get("kind"),
            (r["answers"].get("feedback") or {}).get("p1") or 0)) for k, r in list(runs.items())[:20]})
    def _ask_jev_feedback(self, states):
        """Ask Jev (TypeSafe System One) what each prompt says about the result before it.

        Input: per turn ("#<id>") or steer ("steer-<id>"), the redacted text Jev
        reads. Output: per key, the main label, its probability and margin; the
        full request and answer are in Engine calls.
        """
        kinds = self.env["flightdeck.feedback.kind"]._criteria()
        return classifier.jev_batch(states, classifier.jev_key(),
                                    questions={"feedback": feedback.question(kinds)})

    @api.model
    @workflow.task(name="Ask Luna", input_summary=lambda self, items, kinds: {
        k: (v["prompt"] or "")[:80] for k, v in list(items.items())[:20]},
        summary=lambda answers: {k: (a.get("error") or ",".join((a.get("raw") or {}).get("feedback") or [])
                                     or "none") for k, a in list(answers.items())[:20]})
    def _ask_luna_feedback(self, items, kinds):
        """Ask Luna (GPT-6 Luna through codex exec) for every label that applies, 15 items to a call."""
        return feedback.luna_batch(items, kinds)

    @api.model
    @workflow.task(name="Store feedback", summary=lambda n: "%d record(s) labelled" % n)
    def _store_feedback(self, labels, recs, runs=None, pending=None):
        """Write each label; a person's is kept. A record with no label waits for the next
        pass, with Jev's unclear answer kept (`pending`) so that pass goes straight to Luna."""
        if runs:
            rec_of = {k: (r.turn_id.id if r._name == "flightdeck.turn.steer" else r.id)
                      for k, r in recs.items()}
            for run in runs:
                steer = recs.get(run.get("turn"))
                if steer is not None and steer._name == "flightdeck.turn.steer":
                    run["steer"] = steer.id
            self.env["flightdeck.label.run"]._store_runs(runs, rec_of)
        by_code = {k.code: k for k in self.env["flightdeck.feedback.kind"].with_context(
            active_test=False).search([])}
        written = 0
        for key, lab in labels.items():
            rec = recs.get(key)
            if rec is None or rec.feedback_source in KEPT:
                continue
            kinds = [by_code[c] for c in lab["codes"] if c in by_code]
            rec.with_context(fd_label_engine=True).write({
                "feedback_ids": [(6, 0, [k.id for k in kinds])],
                "feedback_main_id": kinds[0].id if kinds else False,
                "feedback_source": lab["source"],
                "feedback_confidence": lab["confidence"],
                "feedback_state": "review" if lab["review"] else "done",
                "feedback_detail": {"reason": lab["reason"], "jev": lab.get("jev")},
                "feedback_jev": False,
            })
            written += 1
        self.browse([r.id for k, r in recs.items() if k in labels and r._name == "flightdeck.turn"]
                    )._retype_from_feedback()
        for key, jev in (pending or {}).items():
            rec = recs.get(key)
            if rec is not None and rec.feedback_source not in KEPT:
                rec.with_context(fd_label_engine=True).write({"feedback_jev": jev})
        return written

    def _retype_from_feedback(self):
        """An engine's follow-up whose Feedback only asks to explain or report becomes `ask`.

        Type and Feedback come from separate calls; this is where they are made to
        agree. A person's Type is kept, and a reference label never feeds it: it is
        what the label check measures the engines against.
        """
        moved = self.browse()
        for turn in self.filtered(lambda t: t.label_source in ("rule", "jev", "llm")
                                  and t.feedback_source != "reference"):
            if feedback.type_from_feedback(turn.intent, turn.feedback_ids.mapped("code")):
                turn.with_context(fd_label_engine=True).write({
                    "intent": "ask",
                    "label_detail": dict(turn.label_detail or {},
                                         retyped="Feedback only asks to explain or report")})
                moved |= turn
        return moved

    # --- the label check ------------------------------------------------------------
    def action_save_label(self):
        """The label check's Save: the person's label as it stands, "no feedback" included."""
        for turn in self:
            turn.write({"feedback_no_fit": turn.feedback_no_fit})
        return True

    def action_compare_label_check(self):
        """Queue the comparison; the figures come from the Show the figures button."""
        self.env["flightdeck.turn"].with_delay(
            description="FlightDeck: compare the label check",
            identity_key="fd-compare-label-check")._compare_label_check()
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": "Label check", "type": "info",
            "message": "Asking Jev and Luna about the prompts you labelled. "
                       "Press Show the figures in a minute or two."}}

    def action_label_check_figures(self):
        report = self._label_check_report()
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": "Label check", "sticky": True,
            "type": "info" if report["labelled"] else "warning", "message": report["summary"]}}

    @api.model
    @workflow.flow("flight_deck.compare_label_check", name="Compare the label check")
    def _compare_label_check(self):
        """Queue job: ask the engines again about every label-check prompt the person
        labelled, and store their calls; the person's labels are never written.

        Asked again on every run, so a criterion tuned since the last one is
        what the figures measure. Commits around the engine calls, as the pass does.
        """
        done = self._find_label_check_done()
        if not done:
            return {"summary": "No label-check prompt labelled by the person yet"}
        items, recs = self._feedback_items(done, self.env["flightdeck.turn.steer"])
        for item in items.values():
            item["rule"] = item["jev"] = None
        self.env.cr.commit()
        kinds = self.env["flightdeck.feedback.kind"]._criteria()
        llm = self._label_settings()["llm"] and classifier.llm_ready()[0]
        _labels, runs, _pending = feedback.classify(
            items, kinds, key=classifier.jev_key(), llm=llm, log=_logger.info,
            ask_jev=self._ask_jev_feedback, ask_luna=lambda i: self._ask_luna_feedback(i, kinds))
        self.env.cr.commit()
        self._store_feedback({}, recs, runs=runs)
        self.env.cr.commit()
        return {"summary": self._label_check_report()["summary"]}

    @api.model
    @workflow.task(name="Find the prompts the person labelled",
                   summary=lambda turns: "%d prompt(s)" % len(turns))
    def _find_label_check_done(self):
        turns = self.search([("feedback_gold", "=", True), ("feedback_source", "in", KEPT)])
        workflow.link(turns)
        return turns

    @api.model
    def _store_reference(self, turn, label):
        """Write Opus's reference label on a label-check turn; a person's label is kept.

        `label` is `{"codes": [...], "type": a CHECK_TYPES value, "no_fit": bool, "note": str}`,
        codes main first.
        """
        if turn.feedback_source == "human":
            return False
        kinds = self.env["flightdeck.feedback.kind"].with_context(active_test=False).search(
            [("code", "in", label["codes"])])
        by_code = {k.code: k for k in kinds}
        ordered = [by_code[c] for c in label["codes"] if c in by_code]
        turn.with_context(fd_label_engine=True).write({
            "feedback_ids": [(6, 0, [k.id for k in ordered])],
            "feedback_main_id": ordered[0].id if ordered else False,
            "feedback_no_fit": bool(label.get("no_fit")),
            "check_intent": label.get("type") or False,
            "feedback_source": "reference",
            "feedback_confidence": 1.0,
            "feedback_state": "done",
            "feedback_detail": {"reason": "reference label by Opus", "note": label.get("note") or ""},
        })
        return True

    @api.model
    def _label_check_report(self):
        """The spec's pass figures for the label check."""
        gold = self.search([("feedback_gold", "=", True)])
        done = gold.filtered(lambda t: t.feedback_source in KEPT)
        no_fit = done.filtered("feedback_no_fit")
        family_of = {k.code: k.family for k in self.env["flightdeck.feedback.kind"].with_context(
            active_test=False).search([])}
        pairs = jev_pairs = jev_main = jev_family = pipe_main = 0
        for t in done - no_fit:
            runs = t.feedback_run_ids.sorted("id")
            jev = runs.filtered(lambda r: r.engine == "jev" and not r.error)[-1:]
            luna = runs.filtered(lambda r: r.engine == "llm" and not r.error)[-1:]
            main, codes = t.feedback_main_id.code, t.feedback_ids.mapped("code")
            if jev and jev.accepted:
                jev_pairs += 1
                jev_main += feedback.agrees(main, codes, jev.kind)
                jev_family += feedback.agrees_family(main, codes, jev.kind, family_of)
            said = jev if jev and jev.accepted else luna
            if said:
                pairs += 1
                pipe_main += feedback.agrees(main, codes, said.kind)

        # Type: the engines' label against the label check's own Type answer.
        typed = done.filtered(lambda t: t.check_intent and t.intent
                              and t.label_source in ("rule", "jev", "llm"))
        type_match = len(typed.filtered(lambda t: t.intent == t.check_intent))

        def pct(n, d):
            return "%d%%" % round(100.0 * n / d) if d else "—"
        return {
            "labelled": len(done), "gold": len(gold),
            "summary": ("%d of %d labelled (%d by you, %d by Opus). No label fits: %s (pass ≤ 10%%). "
                        "Jev clear on %d: main %s (pass ≥ 80%%), group %s (pass ≥ 90%%). "
                        "Jev, else Luna, on %d: main %s. Type: engines match %s on %d (pass ≥ 80%%).") % (
                len(done), len(gold), len(done.filtered(lambda t: t.feedback_source == "human")),
                len(done.filtered(lambda t: t.feedback_source == "reference")),
                pct(len(no_fit), len(done)), jev_pairs, pct(jev_main, jev_pairs),
                pct(jev_family, jev_pairs), pairs, pct(pipe_main, pairs),
                pct(type_match, len(typed)), len(typed)),
        }

    @api.model
    def _feedback_report(self, labels, recs):
        by = {}
        for lab in labels.values():
            by[lab["source"]] = by.get(lab["source"], 0) + 1
        report = {"summary": "%d of %d labelled: %s; %d to review, %d waiting" % (
            len(labels), len(recs),
            ", ".join("%d by %s" % (n, s) for s, n in sorted(by.items())) or "none",
            sum(1 for lab in labels.values() if lab["review"]), len(recs) - len(labels))}
        for key, lab in list(labels.items())[:19]:
            report[key] = "%s by %s" % (",".join(lab["codes"]) or "none", lab["source"])
        return report


class FlightdeckSteerFeedback(models.Model):
    _inherit = "flightdeck.turn.steer"

    feedback_ids = fields.Many2many(
        "flightdeck.feedback.kind", "flightdeck_steer_feedback_rel", "steer_id", "kind_id", "Feedback",
        help="What this message says about the agent's work before it.")
    feedback_main_id = fields.Many2one("flightdeck.feedback.kind", "Main feedback",
                                       ondelete="set null")
    feedback_source = fields.Selection(FEEDBACK_SOURCES, "Feedback by", readonly=True)
    feedback_confidence = fields.Float("Feedback confidence", digits=(4, 2), readonly=True)
    feedback_state = fields.Selection(FEEDBACK_STATES, "Feedback state", default="todo", index=True,
                                      readonly=True)
    feedback_detail = fields.Json(readonly=True)
    feedback_jev = fields.Json("Jev said, waiting for Luna", readonly=True)

    def write(self, vals):
        vals, mine = _mark_human(self, vals)
        result = super().write(vals)
        if mine:
            _main_from_labels(self)
        return result
