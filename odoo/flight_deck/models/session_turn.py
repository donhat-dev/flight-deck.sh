"""Turns: one person's prompt and everything the session did about it.

A transcript is a flat list of lines, and the unit a reader actually thinks in
is the turn — what was asked, and what happened next. The split is a property
of the lines, not something stored: a turn starts at a prompt a person wrote
and runs to the line before the next one.

Kept out of session.py so the file that follows transcripts and the file that
reads them apart stay separate.
"""
from odoo import api, models

# What separates a person's prompt from the machinery that shares its role.
# Everything the CLI injects arrives as a user line too: task notifications,
# editor state, slash commands, system reminders, the caveat blocks, and the
# `[Image: ...]` line that trails a prompt carrying a screenshot.
PROMPT_WHERE = """
    role = 'user' AND tool_name IS NULL
    AND text IS NOT NULL AND text <> ''
    AND text NOT LIKE '<%%'
    AND text NOT LIKE '[Request interrupted%%'
    AND text NOT LIKE '[Image:%%'
    AND text NOT LIKE 'Caveat:%%'
"""
# A picker is a list someone reads, so it holds a session's recent work rather
# than all of it: a long session passes a thousand turns.
TURN_LIMIT = 60
# A turn's own lines, for the chart. Enough for the longest turns measured and
# short of the runaway one.
STEP_LIMIT = 600
PROMPT_CHARS = 120


class FlightdeckSessionTurns(models.Model):
    _inherit = "flightdeck.session"

    def _turn_scope(self, agent_id=None):
        """The lines a turn may be made of, as a SQL condition and its values."""
        self.ensure_one()
        if agent_id:
            return "agent_ref_id = %s", [agent_id]
        return "session_ref_id = %s AND agent_ref_id IS NULL", [self.id]

    def fd_turns(self, agent_id=None, limit=TURN_LIMIT):
        """The most recent turns, newest last, as the picker lists them.

        `index` counts from the start of the transcript, so it keeps meaning
        when only the tail is returned.
        """
        self.ensure_one()
        scope, args = self._turn_scope(agent_id)
        self.env.cr.execute(
            "SELECT seq, ts, left(text, %s) FROM flightdeck_message_text"
            " WHERE " + scope + " AND " + PROMPT_WHERE + " ORDER BY seq",
            [PROMPT_CHARS] + args)
        prompts = self.env.cr.fetchall()
        if not prompts:
            return []
        self.env.cr.execute(
            "SELECT max(seq) FROM flightdeck_message_text WHERE " + scope, args)
        last_seq = self.env.cr.fetchone()[0] or prompts[-1][0]

        turns = []
        for at, (seq, ts, text) in enumerate(prompts):
            turns.append({
                "index": at + 1,
                "seq_start": seq,
                "seq_end": prompts[at + 1][0] - 1 if at + 1 < len(prompts) else last_seq,
                "ts_start": ts and ts.isoformat(sep=" ", timespec="seconds"),
                "prompt": " ".join((text or "").split())[:PROMPT_CHARS],
            })
        turns = turns[-limit:] if limit else turns

        # One query for every turn on screen rather than one each: the ranges go
        # in as three arrays and come back joined against the lines.
        self.env.cr.execute(
            "SELECT t.idx, count(*) FILTER (WHERE m.tool_name IS NOT NULL),"
            "       count(*), max(m.ts)"
            "  FROM unnest(%s::int[], %s::int[], %s::int[]) AS t(idx, lo, hi)"
            "  JOIN flightdeck_message_text m"
            "    ON m.seq BETWEEN t.lo AND t.hi AND " + scope +
            " GROUP BY t.idx",
            [[t["index"] for t in turns], [t["seq_start"] for t in turns],
             [t["seq_end"] for t in turns]] + args)
        counted = {row[0]: row for row in self.env.cr.fetchall()}
        for turn in turns:
            _idx, steps, lines, ts_end = counted.get(
                turn["index"], (0, 0, 0, None))
            turn["steps"] = steps or 0
            turn["lines"] = lines or 0
            turn["ts_end"] = ts_end and ts_end.isoformat(sep=" ", timespec="seconds")
        return turns

    def fd_turn_steps(self, seq_start, seq_end, agent_id=None):
        """What ran inside one turn, in the order it was written.

        `gap_ms` is the time from a line to the one after it, which is not the
        same as how long a tool ran: a tool result is written back onto the line
        that called it, so the transcript carries no end of its own. It is the
        best measure the file supports, and it is what a reader is looking for —
        where the turn spent its minutes.

        `repeat` marks a call made with the same tool and the same whole input
        as an earlier one in this turn, which is how a loop shows itself. A
        shared prefix is not a repeat: most commands in one folder start alike.

        Once the turn's tool calls are read (`flightdeck.turn.step`), each call
        also carries how long the tool itself ran (`tool_ms`, part of its gap),
        and, when it failed, whose failure it was (`family`, `error_class`).
        """
        self.ensure_one()
        scope, args = self._turn_scope(agent_id)
        self.env.cr.execute(
            "SELECT seq, ts, role, tool_name, coalesce(tool_input, ''), tool_use_id"
            "  FROM flightdeck_message_text"
            " WHERE " + scope + " AND seq BETWEEN %s AND %s"
            " ORDER BY seq LIMIT %s",
            args + [seq_start, seq_end, STEP_LIMIT])
        rows = self.env.cr.fetchall()
        read = {}
        ids = [r[5] for r in rows if r[5]]
        if ids:
            self.env.cr.execute(
                "SELECT tool_use_id, duration, outcome, family, error_class, error_head"
                "  FROM flightdeck_turn_step WHERE tool_use_id = ANY(%s)", [ids])
            read = {r[0]: r[1:] for r in self.env.cr.fetchall()}
        steps, seen = [], set()
        for at, (seq, ts, role, tool_name, tool_input, tool_use_id) in enumerate(rows):
            after = rows[at + 1][1] if at + 1 < len(rows) else None
            gap = max(int((after - ts).total_seconds() * 1000), 0) if (ts and after) else 0
            key = (tool_name or "", tool_input)
            repeat = bool(tool_name) and key in seen
            seen.add(key)
            duration, outcome, family, error_class, error_head = read.get(tool_use_id) or (
                None, None, None, None, None)
            steps.append({
                "seq": seq,
                "ts": ts and ts.isoformat(sep=" ", timespec="seconds"),
                "role": role,
                "tool_name": tool_name or ("text" if role == "assistant" else "prompt"),
                "head": " ".join(tool_input.split())[:80],
                "gap_ms": gap,
                "tool_ms": min(int((duration or 0) * 1000), gap) if duration is not None else None,
                "outcome": outcome or "",
                "family": family or "",
                "error_class": error_class or "",
                "error_head": error_head or "",
                "repeat": repeat,
            })
        return steps

    @api.model
    def fd_agent_turns(self, agent_id, limit=TURN_LIMIT):
        """The same two calls, addressed by agent rather than by session."""
        agent = self.env["flightdeck.session.agent"].browse(agent_id)
        return agent.session_ref_id.fd_turns(agent_id=agent_id, limit=limit)
