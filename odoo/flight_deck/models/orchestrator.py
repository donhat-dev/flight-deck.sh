"""Orchestrator: the live Claude sessions on this machine and the agents each runs.

The first level is the CLI's own session roster, one file per running process
under ~/.claude/sessions (mounted at ROSTER_ROOT). It holds what `/list-agents`
prints — the name other sessions use, busy or idle, the working directory,
when it started — and the session id that `/list-agents` leaves out, which is
what ties an entry to its logbook. Only the `.json` files are read: the
`.key` files beside them are the sessions' messaging keys.

The levels below are the agent rows the follow tick builds from each live
transcript (`flightdeck.session.agent`), finished ones included.

An entry with no logbook is left out: the CLI writes the transcript at the
first message, so such an entry is a window opened and never used, or one
left behind.

The container cannot see the host's processes, so an entry left behind by a
killed session still shows here, as idle, until the CLI removes it.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from odoo import api, fields, models

from .session import ROSTER_ROOT

ROSTER_KEYS = ("sessionId", "name", "nameSource", "status", "cwd", "startedAt", "kind")
AGENT_FIELDS = ["parent_agent_id", "description", "agent_type", "model_name", "status",
                "waiting", "is_background", "duration_seconds", "total_tokens",
                "transcript_path"]
TURN_FIELDS = ["started_at", "ended_at", "prompt_head", "title", "intent", "cost", "git_branch"]
RECENT_TURNS = 6
# Enough of a prompt or a report to recognise it; the logbook has the rest.
TEXT_CAP = 1200


def read_roster(root=ROSTER_ROOT):
    """Every readable roster entry, the most recently started first."""
    rows = []
    try:
        names = os.listdir(root)
    except OSError:
        return rows
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("sessionId"):
            rows.append({key: data.get(key) for key in ROSTER_KEYS})
    rows.sort(key=lambda r: -(r.get("startedAt") or 0))
    return rows


def _from_ms(value):
    try:
        moment = datetime.fromtimestamp(int(value) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return False
    return fields.Datetime.to_string(moment.replace(tzinfo=None))


class FlightdeckSessionOrchestrator(models.Model):
    _inherit = "flightdeck.session"

    @api.model
    def fd_orchestrator_tree(self):
        """The Orchestrator page in one read: roster entries, each with its agents."""
        roster = read_roster()
        rows = self.search([("session_id", "in", [r["sessionId"] for r in roster])])
        by_key = {s.session_id: s for s in rows}
        agents = {}
        for row in self.env["flightdeck.session.agent"].search_read(
                [("session_ref_id", "in", rows.ids)], AGENT_FIELDS + ["session_ref_id"],
                order="start_ts, id"):
            agents.setdefault(row["session_ref_id"][0], []).append({
                "id": row["id"],
                "parent_id": row["parent_agent_id"] and row["parent_agent_id"][0],
                "description": row["description"] or row["agent_type"] or "Agent",
                "agent_type": row["agent_type"] or "",
                "model": row["model_name"] or "",
                "status": "waiting" if row["waiting"] else row["status"],
                "background": row["is_background"],
                "seconds": row["duration_seconds"] or 0,
                "tokens": row["total_tokens"] or 0,
                "has_transcript": bool(row["transcript_path"]),
            })
        model_of = self._fd_main_models(rows.ids)
        sessions = []
        for entry in roster:
            session = by_key.get(entry["sessionId"])
            if not session:
                continue
            name = entry["name"] or entry["sessionId"][:8]
            sessions.append({
                "key": entry["sessionId"],
                "id": session.id,
                "name": name,
                # The logbook's title, when it says more than the handle does.
                "title": session.name if session.name not in (name, session.session_id) else "",
                "status": "busy" if entry["status"] == "busy" else "idle",
                "cwd": entry["cwd"] or "",
                "started_at": _from_ms(entry["startedAt"]),
                "model": model_of.get(session.id, ""),
                "agents": agents.get(session.id, []),
            })
        return {"sessions": sessions, "read_at": fields.Datetime.to_string(fields.Datetime.now())}

    @api.model
    def fd_orchestrator_detail(self, kind, record_id):
        """What the side panel shows for one session or one agent."""
        if kind == "agent":
            return self._fd_agent_detail(record_id)
        session = self.browse(record_id).exists()
        if not session:
            return False
        Turn = self.env["flightdeck.turn"]
        turns = Turn.search_read([("session_ref_id", "=", session.id)], TURN_FIELDS,
                                 order="started_at desc, id desc", limit=RECENT_TURNS)
        latest = turns and Turn.browse(turns[0]["id"])
        intents = dict(Turn._fields["intent"].selection)
        since = fields.Datetime.now() - timedelta(hours=24)
        [(count, cost)] = Turn._read_group(
            [("session_ref_id", "=", session.id), ("started_at", ">=", since)], [],
            ["__count", "cost:sum"])
        return {
            "name": session.name,
            "cwd": latest.cwd if latest else "",
            "branch": (turns[0]["git_branch"] or "") if turns else "",
            "day_turns": count,
            "day_cost": cost or 0.0,
            "latest": latest and {
                "started_at": fields.Datetime.to_string(latest.started_at),
                "ended_at": latest.ended_at and fields.Datetime.to_string(latest.ended_at),
                "prompt": (latest.prompt_text or latest.prompt_head or "")[:TEXT_CAP],
                "reply": (latest.final_head or "")[:TEXT_CAP],
                "intent": intents.get(latest.intent, ""),
                "title": latest.title or "",
                "cost": latest.cost,
            },
            "turns": [{
                "id": t["id"],
                "started_at": fields.Datetime.to_string(t["started_at"]),
                "running": not t["ended_at"],
                "text": t["title"] or t["prompt_head"] or "",
                "intent": intents.get(t["intent"], ""),
                "cost": t["cost"],
            } for t in turns],
        }

    @api.model
    def _fd_agent_detail(self, record_id):
        agent = self.env["flightdeck.session.agent"].browse(record_id).exists()
        if not agent:
            return False
        return {
            "description": agent.description or agent.agent_type or "Agent",
            "agent_type": agent.agent_type or "",
            "model": agent.model_name or "",
            "status": "waiting" if agent.waiting else agent.status,
            "background": agent.is_background,
            "started_at": agent.start_ts and fields.Datetime.to_string(agent.start_ts),
            "ended_at": agent.end_ts and fields.Datetime.to_string(agent.end_ts),
            "seconds": agent.duration_seconds,
            "tokens": agent.total_tokens,
            "tool_uses": agent.tool_uses,
            "parent": agent.parent_agent_id.description or "",
            "prompt": (agent.prompt or "")[:TEXT_CAP],
            "summary": (agent.summary or "")[:TEXT_CAP],
        }

    @api.model
    def _fd_main_models(self, ids):
        """The model of each session's latest billed call by its main agent."""
        if not ids:
            return {}
        self.env.cr.execute("""
            SELECT DISTINCT ON (session_ref_id) session_ref_id, model
              FROM flightdeck_message
             WHERE session_ref_id = ANY(%s) AND agent_key = '' AND model NOT LIKE '<%%'
             ORDER BY session_ref_id, ts DESC
        """, (list(ids),))
        return dict(self.env.cr.fetchall())
