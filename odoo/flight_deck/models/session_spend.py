"""Correct a stored turn when a later block of the same response says more.

`transcript_reader.read_usage` keeps the block with the most output, but a
spend pass stops at a byte budget, so the blocks of one response can land in
two passes, and rows stored before that rule kept the first block's count.
`_store_spend` skips an id it already holds; this raises the held row first.

It also writes down the prompt and the agent each row came from, on rows
stored before those were read too.
"""
from odoo import models


class FlightdeckSessionSpend(models.Model):
    _inherit = "flightdeck.session"

    def _store_spend(self, rows):
        self.ensure_one()
        self._raise_held_output(rows)
        added = super()._store_spend(rows)
        self._link_spend(rows)
        return added

    def _link_spend(self, rows):
        """Fill in prompt and agent on this session's rows that lack them."""
        if not rows:
            return
        agent = self.env.context.get("fd_agent_key") or ""
        self.env.cr.execute("""
            UPDATE flightdeck_message m
               SET prompt_id = COALESCE(m.prompt_id, v.prompt_id),
                   agent_key = COALESCE(m.agent_key, %s)
              FROM unnest(%s::varchar[], %s::varchar[]) AS v(uuid, prompt_id)
             WHERE m.uuid = v.uuid AND m.session_ref_id = %s
               AND (m.prompt_id IS NULL OR m.agent_key IS NULL)
        """, (agent, [r["uuid"] for r in rows], [r.get("prompt_id") for r in rows], self.id))
        self.env["flightdeck.message"].invalidate_model(["prompt_id", "agent_key"])

    def _raise_held_output(self, rows):
        Message = self.env["flightdeck.message"]
        by_uuid = {r["uuid"]: r for r in rows if r["output_tokens"]}
        if not by_uuid:
            return 0
        held = Message.search([("uuid", "in", list(by_uuid))])
        Price = self.env["flightdeck.model.price"]
        index = None
        moved = {}
        for message in held:
            row = by_uuid[message.uuid]
            if row["output_tokens"] <= message.output_tokens:
                continue
            index = index or Price.rate_index()
            rate = Price.match(index, message.model, message.ts)
            cost = Price.cost_of(
                rate, message.input_tokens, message.cache_read, message.cache_create_5m,
                message.cache_create_1h, row["output_tokens"])
            extra = row["output_tokens"] - message.output_tokens
            delta = moved.setdefault(message.session_ref_id, [0, 0.0])
            delta[0] += extra
            delta[1] += cost - message.cost
            message.write({
                "output_tokens": row["output_tokens"],
                "total_tokens": message.total_tokens + extra,
                "cost": cost,
            })
        for session, (extra, cost) in moved.items():
            if session:
                session.write({"output_tokens": session.output_tokens + extra,
                               "total_cost": session.total_cost + cost})
        return len(moved)
