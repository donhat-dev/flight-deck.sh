"""Every call the label pass made to Jev or Luna, and the pages that read them.

A run is stored whether or not its answer was used, so the analytics pages can
say how often each engine was sure, how often the two agreed, and how often a
person later changed what they said. A probe is a call made by hand from one of
those pages: stored so it can be looked at again, left out of the figures, and
never written onto a turn.
"""
import json
import statistics
from datetime import timedelta
from html import escape

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.workflow_studio.workflow import workflow

from . import feedback_classifier as feedback
from . import task_classifier as classifier

ENGINES = [("jev", "Jev"), ("llm", "Luna")]
STATS_DAYS = 30


class FlightdeckLabelRun(models.Model):
    _name = "flightdeck.label.run"
    _inherit = ["workflow.run.linked.mixin"]
    _description = "Label engine call"
    _order = "create_date desc, id desc"

    engine = fields.Selection(ENGINES, required=True, index=True)
    turn_id = fields.Many2one("flightdeck.turn", "Turn", index=True, ondelete="cascade")
    probe = fields.Boolean(index=True, help="Made by hand from an analytics page; not used as a label.")
    kind = fields.Char("Answer", index=True)
    top1 = fields.Float("Top probability", digits=(4, 3), aggregator="avg")
    margin = fields.Float(digits=(4, 3), aggregator="avg")
    confidence = fields.Float(digits=(4, 3), aggregator="avg")
    work_tag = fields.Char("Work tag")
    accepted = fields.Boolean(help="Clear enough to be used as the label.")
    latency_ms = fields.Integer("Latency (ms)", aggregator="avg")
    error = fields.Char()
    sent = fields.Text("Sent")
    answer = fields.Json("Raw answer")
    human_kind = fields.Selection(related="turn_id.intent", string="Label now")
    question = fields.Selection([("type", "Type"), ("feedback", "Feedback")], default="type",
                                required=True, index=True)
    steer_id = fields.Many2one("flightdeck.turn.steer", "Steer message", ondelete="cascade")

    @api.model
    def _store_runs(self, runs, rec_of, probe=False):
        """Store engine calls; a call stored already (`call_id`, by the Ask Jev step)
        only gets its `accepted`, decided after the call."""
        vals = []
        known = [run for run in runs if run.get("call_id")]
        for accepted in (True, False):
            ids = [run["call_id"] for run in known if bool(run.get("accepted")) is accepted]
            self.browse(ids).write({"accepted": accepted})
        for run in runs:
            if run.get("call_id"):
                continue
            question = run.get("question") or "type"
            v = {"engine": run["engine"], "turn_id": rec_of.get(run.get("turn")) or False,
                 "probe": probe, "sent": run.get("sent"), "error": run.get("error") or False,
                 "latency_ms": run.get("latency_ms") or 0, "accepted": bool(run.get("accepted")),
                 "question": question, "steer_id": run.get("steer") or False}
            if question == "feedback":
                if run["engine"] == "jev":
                    answer = (run.get("answers") or {}).get("feedback") or {}
                    v.update(kind=answer.get("kind") or False, top1=answer.get("p1") or 0.0,
                             margin=answer.get("margin") or 0.0,
                             answer=run.get("answers") or run.get("raw") or False)
                else:
                    raw = run.get("raw") or {}
                    said = raw.get("feedback")
                    v.update(kind=(said[0] if said else "none") if raw else False,
                             confidence=float(raw.get("confidence") or 0.0), answer=raw or False)
            elif run["engine"] == "jev":
                kind = (run.get("answers") or {}).get("kind") or {}
                work = (run.get("answers") or {}).get("work") or {}
                # A work-tag call asks no kind question; its spread is the tag's.
                spread = kind or work
                v.update(kind=kind.get("kind") or False, top1=spread.get("p1") or 0.0,
                         margin=spread.get("margin") or 0.0, work_tag=work.get("kind") or False,
                         answer=run.get("answers") or run.get("raw") or False)
            else:
                raw = run.get("raw") or {}
                v.update(kind=raw.get("intent") or raw.get("kind") or False,
                         confidence=float(raw.get("confidence") or 0.0),
                         work_tag=raw.get("work_tag") or False, answer=raw or False)
            vals.append(v)
        stored = self.create(vals)
        # In a label run, each call's form reaches that run (ADR-015).
        workflow.link(stored)
        return stored


class FlightdeckLabelProbe(models.TransientModel):
    """The Jev analytics and LLM analytics pages: figures, and a try-it panel."""

    _name = "flightdeck.label.probe"
    _description = "Label engine analytics"

    engine = fields.Selection(ENGINES, required=True, readonly=True,
                              default=lambda self: self.env.context.get("default_engine", "jev"))
    question = fields.Selection([("type", "Type"), ("feedback", "Feedback")], default="type",
                                required=True,
                                help="Which label the figures and the try-it panel are about.")

    # --- figures -----------------------------------------------------------------
    calls = fields.Integer(compute="_compute_stats")
    accepted_share = fields.Float("Accepted", compute="_compute_stats")
    unsure_share = fields.Float(compute="_compute_stats",
                                help="Jev: a flat spread. Luna: confidence below the review line.")
    error_count = fields.Integer("Errors", compute="_compute_stats")
    other_agree = fields.Float(compute="_compute_stats",
                               help="Turns both engines answered, where they gave the same kind.")
    other_pairs = fields.Integer(compute="_compute_stats")
    human_agree = fields.Float(compute="_compute_stats",
                               help="Turns a person labelled, where this engine had said the same.")
    human_pairs = fields.Integer(compute="_compute_stats")
    latency_median = fields.Integer("Median latency (ms)", compute="_compute_stats")
    kind_mix = fields.Html(compute="_compute_stats", sanitize=False)

    # --- try it --------------------------------------------------------------------
    source = fields.Selection([("turn", "A turn"), ("custom", "My own text")],
                              default="turn", required=True)
    turn_id = fields.Many2one("flightdeck.turn", "Turn")
    custom_prompt = fields.Text("Prompt")
    custom_previous = fields.Text(
        "Earlier turns", help="One earlier prompt per paragraph, the most recent first.")
    custom_cwd = fields.Char("Working directory")
    custom_state = fields.Text(
        "Jev state", help="Sent to Jev as it is written, in place of the one built from the fields above.")
    result_kind = fields.Char("Answer", readonly=True)
    result_html = fields.Html("Result", readonly=True, sanitize=False)
    result_sent = fields.Text("What was sent", readonly=True)
    result_raw = fields.Text("Raw answer", readonly=True)
    result_latency = fields.Integer("Latency (ms)", readonly=True)
    result_error = fields.Char("Error", readonly=True)

    @api.depends("engine")
    def _compute_display_name(self):
        for probe in self:
            probe.display_name = "Jev analytics" if probe.engine == "jev" else "LLM analytics"

    @api.depends("engine", "question")
    def _compute_stats(self):
        Run = self.env["flightdeck.label.run"]
        since = fields.Datetime.now() - timedelta(days=STATS_DAYS)
        for probe in self:
            # A work-tag call asks no kind question, so it says nothing about
            # how sure the engine is of a kind.
            runs = Run.search([("engine", "=", probe.engine), ("probe", "=", False),
                               ("question", "=", probe.question), ("steer_id", "=", False),
                               ("create_date", ">=", since),
                               "|", ("kind", "!=", False), ("error", "!=", False)])
            ok = runs.filtered(lambda r: not r.error)
            probe.calls = len(runs)
            probe.error_count = len(runs) - len(ok)
            probe.accepted_share = len(ok.filtered("accepted")) / len(ok) if ok else 0.0
            if probe.engine == "jev":
                unsure = ok.filtered(lambda r: r.top1 < classifier.JEV_ACCEPT_TOP1
                                     or r.margin < classifier.JEV_ACCEPT_MARGIN)
            else:
                unsure = ok.filtered(lambda r: r.confidence < classifier.LLM_REVIEW_BELOW)
            probe.unsure_share = len(unsure) / len(ok) if ok else 0.0
            latencies = [r.latency_ms for r in ok if r.latency_ms]
            probe.latency_median = int(statistics.median(latencies)) if latencies else 0
            mine = {r.turn_id.id: r.kind for r in ok.sorted("id") if r.turn_id}
            other = {r.turn_id.id: r.kind for r in Run.search([
                ("engine", "!=", probe.engine), ("probe", "=", False), ("error", "=", False),
                ("question", "=", probe.question), ("steer_id", "=", False),
                ("turn_id", "in", list(mine)), ("create_date", ">=", since)]).sorted("id")}
            pairs = [t for t in mine if t in other]
            probe.other_pairs = len(pairs)
            probe.other_agree = (sum(mine[t] == other[t] for t in pairs) / len(pairs)) if pairs else 0.0
            if probe.question == "feedback":
                human = self.env["flightdeck.turn"].search(
                    [("id", "in", list(mine)), ("feedback_source", "=", "human")])
                said = {h.id: h.feedback_main_id.code or feedback.NONE for h in human}
            else:
                human = self.env["flightdeck.turn"].search_read(
                    [("id", "in", list(mine)), ("label_source", "=", "human")], ["intent"])
                said = {h["id"]: h["intent"] for h in human}
            probe.human_pairs = len(said)
            probe.human_agree = (sum(mine[t] == k for t, k in said.items()) / len(said)
                                 if said else 0.0)
            counts = {}
            for r in ok:
                counts[r.kind or "?"] = counts.get(r.kind or "?", 0) + 1
            probe.kind_mix = "".join(
                '<span class="badge text-bg-light me-1">%s %s</span>' % (escape(k), n)
                for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))

    # --- running it -------------------------------------------------------------------
    def _context_for(self):
        """The turn and its context, as the label pass would build them."""
        Turn = self.env["flightdeck.turn"]
        if self.source == "turn":
            if not self.turn_id:
                raise UserError("Pick a turn first.")
            turns = Turn._turn_dicts()
            contexts = classifier.build_contexts(turns)
            turn = next((t for t in turns if t["rec"] == self.turn_id.id), None)
            if turn is None:
                raise UserError("That turn has no start time, so it has no context.")
            return turn, contexts[turn["id"]]
        if not (self.custom_prompt or self.custom_state):
            raise UserError("Write a prompt first.")
        now = fields.Datetime.now()
        previous = [p.strip() for p in (self.custom_previous or "").split("\n\n") if p.strip()]
        prev = [{"id": "earlier-%s" % n, "prompt": text, "final": "", "cwd": self.custom_cwd or ""}
                for n, text in enumerate(previous, 1)]
        turn = {"id": "custom", "prompt": self.custom_prompt or "", "cwd": self.custom_cwd or "",
                "branch": "", "final": "", "start": now, "end": None, "calls": 1}
        return turn, {"prev": prev, "tasks": [], "others": [], "gap_min": 5.0 if prev else None,
                      "moved": False}

    def action_run(self):
        self.ensure_one()
        turn, context = self._context_for()
        work_tags = self.env["flightdeck.turn"]._work_tags()
        if self.question == "feedback":
            run, vals = self._run_feedback(turn, context)
        elif self.engine == "jev":
            key = classifier.jev_key()
            if not key:
                raise UserError("No TypeSafe key: set TYPESAFE_API_KEY or TYPESAFE_ENV_FILE "
                                "for the Odoo process.")
            state = self.custom_state if self.source == "custom" and self.custom_state \
                else classifier.state_text(turn, context)
            run = classifier.jev_call(state, key, work_tags)
            answers = run["answers"]
            kind = answers.get("kind")
            run["accepted"] = classifier.clear(kind)
            vals = {
                "result_kind": kind and kind["kind"] or False,
                "result_html": self._jev_html(answers),
                "result_raw": json.dumps(run["raw"], ensure_ascii=False, indent=1) if run["raw"] else False,
            }
        else:
            item = classifier.llm_item(turn, context)
            run = classifier.llm_call([item], work_tags)
            raw = run["labels"].get(turn["id"])
            checked = classifier.check_llm(raw, context) if raw else None
            one = {"engine": "llm", "sent": run["sent"], "raw": raw, "error": run["error"],
                   "latency_ms": run["latency_ms"], "accepted": bool(checked and not checked["review"])}
            run = one
            vals = {
                "result_kind": raw and raw.get("intent") or False,
                "result_html": self._llm_html(raw, checked),
                "result_raw": json.dumps(raw, ensure_ascii=False, indent=1) if raw else False,
            }
        run["turn"] = turn["id"] if self.source == "turn" else None
        rec_of = {turn["id"]: self.turn_id.id} if self.source == "turn" else {}
        self.env["flightdeck.label.run"]._store_runs([run], rec_of, probe=True)
        vals.update(result_sent=run["sent"], result_latency=run["latency_ms"],
                    result_error=run["error"] or False)
        self.write(vals)
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
            "context": {"default_engine": self.engine},
        }

    def _run_feedback(self, turn, context):
        """Try the Feedback question on one prompt: `(run, vals)` as `action_run` stores them."""
        kinds = self.env["flightdeck.feedback.kind"]._criteria()
        if self.engine == "jev":
            key = classifier.jev_key()
            if not key:
                raise UserError("No TypeSafe key: set TYPESAFE_API_KEY or TYPESAFE_ENV_FILE "
                                "for the Odoo process.")
            state = self.custom_state if self.source == "custom" and self.custom_state \
                else classifier.state_text(turn, context)
            run = classifier.jev_call(state, key, questions={"feedback": feedback.question(kinds)})
            jev = feedback.read_jev(run)
            run.update(question="feedback", accepted=bool(jev and jev["clear"]))
            return run, {
                "result_kind": jev and jev["main"] or False,
                "result_html": ('<div class="fw-semibold mt-2">Feedback <span class="text-muted '
                                'fw-normal">top %.2f, margin %.2f</span></div>%s' % (
                                    jev["p1"], jev["margin"], self._bars(jev["probs"], jev["clear"])))
                               if jev else False,
                "result_raw": json.dumps(run["raw"], ensure_ascii=False, indent=1) if run["raw"] else False,
            }
        item = feedback.turn_item(dict(turn, rec=turn.get("rec") or "custom"), context)
        call = feedback.luna_call([item], kinds)
        raw = call["labels"].get(item["id"])
        checked = feedback.check_luna(raw, kinds) if raw else None
        run = {"engine": "llm", "question": "feedback", "sent": call["sent"], "raw": raw,
               "error": call["error"], "latency_ms": call["latency_ms"],
               "accepted": bool(checked and not checked["review"])}
        html = False
        if checked:
            html = ('<div class="small"><span class="text-muted d-inline-block" style="width:6rem">'
                    'Feedback</span>%s</div><div class="small"><span class="text-muted d-inline-block" '
                    'style="width:6rem">Confidence</span>%.2f</div>' % (
                        escape(", ".join(checked["codes"]) or "none"), checked["confidence"]))
            html += ('<div class="small text-warning mt-1">Would go to review: %s</div>'
                     % escape(checked["reason"]) if checked["review"] else
                     '<div class="small text-success mt-1">The feedback pass would use this answer.</div>')
        return run, {
            "result_kind": ",".join(checked["codes"]) or feedback.NONE if checked else False,
            "result_html": html,
            "result_raw": json.dumps(raw, ensure_ascii=False, indent=1) if raw else False,
        }

    @staticmethod
    def _bars(probs, accept=None):
        rows = []
        for name, p in sorted(probs.items(), key=lambda kv: -kv[1]):
            rows.append(
                '<div class="d-flex align-items-center gap-2 small">'
                '<span style="width:9rem" class="text-truncate">%s</span>'
                '<div class="progress flex-grow-1" style="height:.5rem">'
                '<div class="progress-bar" style="width:%.1f%%"></div></div>'
                '<span style="width:3.5rem" class="text-end">%.0f%%</span></div>'
                % (escape(str(name)), p * 100, p * 100))
        note = ""
        if accept is not None:
            note = ('<div class="small %s mt-1">%s</div>' % (
                "text-success" if accept else "text-warning",
                "Clear: the label pass would use this answer." if accept
                else "Flat: the label pass would ask Luna."))
        return "".join(rows) + note

    def _jev_html(self, answers):
        parts = []
        for name, title in (("kind", "Type"), ("work", "Work tag")):
            answer = answers.get(name)
            if not answer:
                continue
            parts.append('<div class="fw-semibold mt-2">%s <span class="text-muted fw-normal">'
                         'top %.2f, margin %.2f</span></div>' % (title, answer["p1"], answer["margin"]))
            parts.append(self._bars(answer["probs"], classifier.clear(answer)))
        return "".join(parts) or False

    @staticmethod
    def _llm_html(raw, checked):
        if not raw:
            return False
        rows = [("Type", raw.get("intent")), ("Continues", raw.get("parent_id") or "—"),
                ("Title", raw.get("title") or "—"), ("Work tag", raw.get("work_tag") or "—"),
                ("Confidence", "%.2f" % float(raw.get("confidence") or 0))]
        html = "".join('<div class="small"><span class="text-muted d-inline-block" style="width:6rem">'
                       '%s</span>%s</div>' % (k, escape(str(v))) for k, v in rows)
        if checked and checked["review"]:
            html += ('<div class="small text-warning mt-1">Would go to review: %s</div>'
                     % escape(checked["reason"]))
        elif checked:
            html += '<div class="small text-success mt-1">The label pass would use this answer.</div>'
        return html
