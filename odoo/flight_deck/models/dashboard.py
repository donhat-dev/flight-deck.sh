"""The numbers above the logbook, and the trend charts.

One read for the whole panel: API-equivalent value, output tokens and agent
work-hours for today, this week and this month, each against the same stretch
of the period before; a daily series for the charts; and the latest quota
report. Periods are the viewer's own days, from the time zone the browser
sends, because a day in UTC is not the day a person worked.
"""
from datetime import datetime, time, timedelta, timezone

from odoo import api, fields, models

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

# How old a quota report may be before the panel says so. The runner reports
# every ten minutes.
QUOTA_STALE_MINUTES = 30
# How long a due job may wait before the panel says the queue has stopped. Jobs
# run one at a time and most take seconds; the long label passes take minutes.
QUEUE_STALL_MINUTES = 10
SERIES_DAYS = (14, 30, 90)


def _zone(name):
    try:
        return ZoneInfo(name) if name and ZoneInfo else timezone.utc
    except Exception:  # noqa: BLE001 - an unknown zone falls back to UTC
        return timezone.utc


def _utc(moment):
    """A zone-aware moment as the naive UTC value Odoo stores."""
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


class FlightdeckSessionDashboard(models.Model):
    _inherit = "flightdeck.session"

    @api.model
    def fd_dashboard(self, tz_name=None, days=30):
        tz_name = tz_name or self.env.context.get("tz") or self.env.user.tz or "UTC"
        zone = _zone(tz_name)
        days = days if days in SERIES_DAYS else 30
        now = datetime.now(zone)
        midnight = datetime.combine(now.date(), time.min, zone)
        week_start = midnight - timedelta(days=now.weekday())
        month_start = midnight.replace(day=1)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
        elapsed_month = now - month_start
        windows = {
            "today": (midnight, now, midnight - timedelta(days=1), now - timedelta(days=1)),
            "week": (week_start, now, week_start - timedelta(days=7), now - timedelta(days=7)),
            # The same number of days and hours into last month, capped at its end.
            "month": (month_start, now, prev_month_start,
                      min(prev_month_start + elapsed_month, month_start)),
        }
        periods = {}
        for key, (start, end, prev_start, prev_end) in windows.items():
            current = self._fd_totals(start, end)
            previous = self._fd_totals(prev_start, prev_end)
            periods[key] = {"current": current, "previous": previous,
                            "start": fields.Datetime.to_string(_utc(start))}
        return {
            "tz": tz_name,
            "now": fields.Datetime.to_string(_utc(now)),
            "periods": periods,
            "series": self._fd_series(midnight - timedelta(days=days - 1), now, tz_name),
            "quota": self._fd_quota(),
            "hours_since": self._fd_hours_since(),
            "queue": self._fd_queue(),
        }

    @api.model
    def _fd_queue(self):
        """The background queue: when it stops, labels, spend and work-hours all stop."""
        Job = self.env["queue.job"].sudo()
        now = fields.Datetime.now()
        due = [("state", "=", "pending"), "|", ("eta", "=", False), ("eta", "<=", now)]
        oldest = Job.search(due, order="date_created asc", limit=1)
        running = Job.search([("state", "in", ("enqueued", "started"))], order="date_created asc", limit=1)
        waited = (now - oldest.date_created).total_seconds() / 60.0 if oldest else 0.0
        return {"waiting": Job.search_count(due), "oldest": oldest.name or "",
                "oldest_minutes": round(waited), "running": running.name or "",
                "stalled": waited > QUEUE_STALL_MINUTES}

    @api.model
    def _fd_totals(self, start, end):
        domain = [("ts", ">=", _utc(start)), ("ts", "<", _utc(end))]
        [(cost, output, calls)] = self.env["flightdeck.message"]._read_group(
            domain, [], ["cost:sum", "output_tokens:sum", "__count"])
        hour_domain = [("hour", ">=", _utc(start).replace(minute=0, second=0, microsecond=0)),
                       ("hour", "<", _utc(end))]
        Activity = self.env["flightdeck.agent.activity"]
        [(hours,)] = Activity._read_group(hour_domain, [], ["busy_hours:sum"])
        [(sub_hours,)] = Activity._read_group(
            hour_domain + [("is_subagent", "=", True)], [], ["busy_hours:sum"])
        return {"cost": cost or 0.0, "output": output or 0, "calls": calls,
                "hours": hours or 0.0, "sub_hours": sub_hours or 0.0}

    @api.model
    def _fd_series(self, start, end, tz_name):
        """One row per local day from `start` to `end`, empty days included."""
        Message = self.env["flightdeck.message"].with_context(tz=tz_name)
        Activity = self.env["flightdeck.agent.activity"].with_context(tz=tz_name)
        Turn = self.env["flightdeck.turn"].with_context(tz=tz_name)
        domain = [("ts", ">=", _utc(start)), ("ts", "<", _utc(end))]
        rows = {}
        for day, cost, out, inp, cread, c5, c1 in Message._read_group(
                domain, ["ts:day"],
                ["cost:sum", "output_tokens:sum", "input_tokens:sum", "cache_read:sum",
                 "cache_create_5m:sum", "cache_create_1h:sum"]):
            rows[day.date()] = {"cost": cost or 0.0, "output": out or 0, "input": inp or 0,
                                "cache_read": cread or 0,
                                "cache_write": (c5 or 0) + (c1 or 0)}
        for day, sub, hours in Activity._read_group(
                [("hour", ">=", _utc(start)), ("hour", "<", _utc(end))],
                ["hour:day", "is_subagent"], ["busy_hours:sum"]):
            row = rows.setdefault(day.date(), {})
            row["sub_hours" if sub else "main_hours"] = (
                row.get("sub_hours" if sub else "main_hours", 0.0) + (hours or 0.0))
        for day, count in Turn._read_group(
                [("is_task", "=", True), ("started_at", ">=", _utc(start)),
                 ("started_at", "<", _utc(end))],
                ["started_at:day"], ["__count"]):
            rows.setdefault(day.date(), {})["tasks"] = count
        series, day = [], start.date()
        while day <= end.date():
            row = rows.get(day, {})
            series.append({
                "day": day.isoformat(),
                "cost": row.get("cost", 0.0),
                "output": row.get("output", 0),
                "input": row.get("input", 0),
                "cache_read": row.get("cache_read", 0),
                "cache_write": row.get("cache_write", 0),
                "main_hours": row.get("main_hours", 0.0),
                "sub_hours": row.get("sub_hours", 0.0),
                "tasks": row.get("tasks", 0),
            })
            day += timedelta(days=1)
        return series

    @api.model
    def _fd_quota(self):
        Snapshot = self.env["flightdeck.quota.snapshot"]
        latest = Snapshot.search([], limit=1)
        if not latest:
            return False
        age = (fields.Datetime.now() - latest.captured_at).total_seconds() / 60.0
        since = latest.captured_at - timedelta(hours=24)
        history = Snapshot.search_read(
            [("captured_at", ">=", since)], ["captured_at", "session_pct", "week_pct"],
            order="captured_at asc")
        return {
            "captured_at": fields.Datetime.to_string(latest.captured_at),
            "age_minutes": round(age, 1),
            "stale": age > QUOTA_STALE_MINUTES,
            "runner": latest.runner_id.name or "",
            "session": {"pct": latest.session_pct,
                        "resets_at": fields.Datetime.to_string(latest.session_resets_at) or False},
            "week": {"pct": latest.week_pct,
                     "resets_at": fields.Datetime.to_string(latest.week_resets_at) or False},
            "models": latest.models_json or [],
            "history": [{"t": fields.Datetime.to_string(h["captured_at"]),
                         "session": h["session_pct"], "week": h["week_pct"]} for h in history],
        }

    @api.model
    def _fd_hours_since(self):
        """The first hour busy time is known for: older transcripts are gone."""
        first = self.env["flightdeck.agent.activity"].search([], order="hour asc", limit=1)
        return fields.Datetime.to_string(first.hour) if first else False
