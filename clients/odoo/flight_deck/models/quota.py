"""Subscription quota, as the CLI's /usage command reports it.

The runner runs `claude -p /usage` on a timer and whenever Odoo asks, and
sends the text back as a `usage` event. It is parsed here, with the rules of
`python/flightdeck/usage_poll.py`, and kept as one snapshot per report, so
the history is there for a trend as well as the latest figure.
"""
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.workflow_studio.workflow import workflow

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

_logger = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# "Current session: 34% used · resets Jul 6, 2:20pm (Asia/Ho_Chi_Minh)"
# "Current week (all models): 46% used · resets Jul 7, 10am (UTC)"
_WINDOW = re.compile(
    r"^Current (session|week \(([^)]+)\)):\s*([\d.]+)% used"
    r"(?:\s*·\s*resets\s*(.+))?$")
_RESET = re.compile(r"^(.+?)\s*\(([^)]+)\)\s*$")
# A month of reports every ten minutes.
KEEP_DAYS = 30


def _parse_reset(text, now):
    """'Jul 6, 2:20pm (Asia/Ho_Chi_Minh)' as a naive UTC datetime, or False."""
    found = _RESET.match(text or "")
    if not found or ZoneInfo is None:
        return False
    try:
        tz = ZoneInfo(found.group(2).strip())
    except Exception:  # noqa: BLE001 - an unknown zone name is simply unparsed
        return False
    for fmt in ("%b %d, %I:%M%p", "%b %d, %I%p"):
        try:
            naive = datetime.strptime(found.group(1).strip(), fmt)
            break
        except ValueError:
            continue
    else:
        return False
    ref = now.astimezone(tz)
    moment = naive.replace(year=ref.year, tzinfo=tz)
    # A reset is in the future; one that reads as far in the past is next year.
    if moment < ref - timedelta(hours=1):
        moment = moment.replace(year=ref.year + 1)
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def parse_usage(text, now=None):
    """The windows in a /usage report: `[(label, used_pct, resets_at)]`.

    `label` is "session" for the five-hour window, "all models" for the weekly
    one, and the model family name for a per-model weekly bucket.
    """
    now = now or datetime.now(timezone.utc)
    windows = []
    for line in (text or "").splitlines():
        found = _WINDOW.match(_ANSI.sub("", line).strip())
        if not found:
            continue
        which, bucket, pct, reset = found.groups()
        label = "session" if which == "session" else (bucket or "").strip().lower()
        windows.append((label, float(pct), _parse_reset(reset or "", now)))
    return windows


class FlightdeckQuotaSnapshot(models.Model):
    _name = "flightdeck.quota.snapshot"
    _description = "Subscription quota report"
    _order = "captured_at desc, id desc"
    _rec_name = "captured_at"

    captured_at = fields.Datetime(required=True, index=True)
    runner_id = fields.Many2one("flightdeck.runner", ondelete="set null")
    # `max`, not the default sum: two reports in one hour are one reading.
    session_pct = fields.Float("Session used %", digits=(5, 1), aggregator="max")
    session_resets_at = fields.Datetime("Session resets")
    week_pct = fields.Float("Week used %", digits=(5, 1), aggregator="max")
    week_resets_at = fields.Datetime("Week resets")
    # Per-model weekly buckets, e.g. [{"label": "fable", "pct": 35.0, "resets_at": "..."}].
    models_json = fields.Json("Model buckets")
    raw = fields.Text()

    @api.model
    @workflow.task(name="Store the quota report")
    def _from_report(self, runner, text, captured_at):
        windows = parse_usage(text)
        if not windows:
            _logger.warning("usage report from runner %s had no quota lines", runner.id)
            return self.browse()
        vals = {"captured_at": captured_at, "runner_id": runner.id, "raw": text,
                "models_json": []}
        for label, pct, resets_at in windows:
            if label == "session":
                vals.update(session_pct=pct, session_resets_at=resets_at)
            elif label == "all models":
                vals.update(week_pct=pct, week_resets_at=resets_at)
            else:
                vals["models_json"].append({
                    "label": label, "pct": pct,
                    "resets_at": fields.Datetime.to_string(resets_at) if resets_at else False,
                })
        snapshot = self.create(vals)
        self.search([("captured_at", "<", captured_at - timedelta(days=KEEP_DAYS))]).unlink()
        return snapshot


class FlightdeckRunnerUsage(models.Model):
    _inherit = "flightdeck.runner"

    reports_usage = fields.Boolean(
        "Reports quota",
        help="Ask this runner for the subscription quota. One runner is enough: "
             "every runner reads the same account.")

    def _apply_event(self, event):
        if event.get("kind") != "usage":
            return super()._apply_event(event)
        payload = event.get("payload") or {}
        if event.get("error") or not payload.get("text"):
            _logger.warning("runner %s could not report usage: %s", self.id, event.get("error"))
            return
        stamp = payload.get("captured_at") or event.get("ts")
        try:
            captured = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            captured = captured.astimezone(timezone.utc).replace(tzinfo=None)
        except (AttributeError, ValueError):
            captured = fields.Datetime.now()
        self.env["flightdeck.quota.snapshot"]._from_report(self, payload["text"], captured)

    def _submit_usage(self):
        """Ask for a quota report now. It arrives on the next events read."""
        self.ensure_one()
        seq = self.next_seq
        self.next_seq = seq + 1
        command = {"cmd_id": str(uuid.uuid4()), "seq": seq, "op": "usage",
                   "session_id": "", "args": {}}
        row = self.env["flightdeck.runner.command"].create({
            "runner_id": self.id, "cmd_id": command["cmd_id"], "seq": seq,
            "op": "usage", "args": "{}",
        })
        try:
            self._connector().submit(command)
        except OSError as e:
            row.write({"state": "failed", "error": str(e)})
            raise UserError("The runner's spool is not writable: %s" % e) from e
        return row

    @api.model
    def fd_refresh_quota(self):
        """The dashboard's refresh button: ask every quota runner.

        A reader may ask too. The command only reads the quota, and writing it
        needs rights on the runner that a reader does not hold, hence sudo
        after the group check.
        """
        if not self.env.user.has_group("flight_deck.group_user"):
            raise UserError("Only FlightDeck users can refresh the quota.")
        runners = self.sudo().search([("reports_usage", "=", True)])
        for runner in runners:
            runner._submit_usage()
        return len(runners)


class FlightdeckRunnerCommandUsage(models.Model):
    _inherit = "flightdeck.runner.command"

    op = fields.Selection(selection_add=[("usage", "Report quota")],
                          ondelete={"usage": "cascade"})
