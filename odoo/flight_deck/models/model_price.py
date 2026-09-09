from odoo import api, fields, models


class FlightdeckModelPrice(models.Model):
    """List API rates, one record per model-ID prefix.

    Two rules the flat number cannot carry on its own. The longest prefix wins,
    so `claude-opus-4-1` is not priced by the shorter `claude-opus-4` row. And a
    rate can expire: a record with `date_end` set applies only to messages sent
    before that date, which is why history stays correct when a promotion ends.
    """

    _name = "flightdeck.model.price"
    _description = "Model price"
    _order = "name"

    name = fields.Char("Model prefix", required=True, index=True)
    input_rate = fields.Float("Input per MTok", required=True, digits=(12, 4))
    output_rate = fields.Float("Output per MTok", required=True, digits=(12, 4))
    date_end = fields.Date(
        "In effect before",
        help="Leave empty for the standard rate. A date here means the rate "
             "applies only to messages sent before that day.",
    )
    cache_read_mult = fields.Float("Cache read multiplier", default=0.1)
    cache_write_5m_mult = fields.Float("Cache write 5m multiplier", default=1.25)
    cache_write_1h_mult = fields.Float("Cache write 1h multiplier", default=2.0)
    active = fields.Boolean(default=True)

    @api.model
    def rate_index(self):
        """All rates as plain dicts, longest prefix first.

        Returned once and reused by the importer: matching 103k messages one
        `search` at a time is the difference between minutes and hours.
        """
        rows = self.search_read(
            [],
            ["name", "input_rate", "output_rate", "date_end",
             "cache_read_mult", "cache_write_5m_mult", "cache_write_1h_mult"],
        )
        return sorted(rows, key=lambda r: len(r["name"]), reverse=True)

    @api.model
    def match(self, index, model, ts=None):
        """The rate row for `model` as of `ts`, or None when the model is unpriced.

        An unpriced model returns nothing rather than a neighbour's rate: a
        missing number reads as unknown, a guessed one reads as a real cost.
        """
        if not model:
            return None
        prefix = None
        for row in index:
            if model.startswith(row["name"]):
                prefix = row["name"]
                break
        if prefix is None:
            return None
        candidates = [r for r in index if r["name"] == prefix]
        day = ts.date() if ts is not None and hasattr(ts, "date") else ts
        for row in candidates:
            if row["date_end"] and day and day < row["date_end"]:
                return row
        for row in candidates:
            if not row["date_end"]:
                return row
        return None

    @api.model
    def cost_of(self, row, input_tokens, cache_read, cache_5m, cache_1h, output_tokens):
        if not row:
            return 0.0
        r_in, r_out = row["input_rate"], row["output_rate"]
        return (
            input_tokens * r_in
            + output_tokens * r_out
            + cache_read * row["cache_read_mult"] * r_in
            + cache_5m * row["cache_write_5m_mult"] * r_in
            + cache_1h * row["cache_write_1h_mult"] * r_in
        ) / 1_000_000.0
