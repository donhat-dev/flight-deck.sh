"""Rates read from the pricing page on 2026-09-23, applied to stored turns.

The price file is `noupdate`, so an existing database needs this: Opus 5.5
and Fable/Mythos 5.1 arrive as new rows through the file itself, but Sonnet
5's standard rate changes in place and its introductory row goes. Stored
`cost` was written at import from the old rates, so the turns those rates
priced are priced again, and their sessions' totals with them.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# Families whose rate or cache multiplier changed.
REPRICED = ("claude-opus-5-5%", "claude-fable-5-1%", "claude-mythos-5-1%", "claude-sonnet-5%")


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    sonnet = env.ref("flight_deck.price_sonnet_5", raise_if_not_found=False)
    if sonnet:
        sonnet.write({"input_rate": 2.0, "output_rate": 10.0})
    intro = env.ref("flight_deck.price_sonnet_5_intro", raise_if_not_found=False)
    if intro:
        intro.unlink()

    Price = env["flightdeck.model.price"]
    index = Price.rate_index()
    cr.execute(
        "SELECT id, model, ts, input_tokens, cache_read, cache_create_5m, cache_create_1h,"
        " output_tokens, cost FROM flightdeck_message WHERE model LIKE ANY(%s)",
        [list(REPRICED)])
    changed, sessions = 0, set()
    for mid, model, ts, inp, cread, c5, c1, out, old in cr.fetchall():
        rate = Price.match(index, model, ts)
        cost = Price.cost_of(rate, inp or 0, cread or 0, c5 or 0, c1 or 0, out or 0)
        if abs(cost - (old or 0.0)) > 1e-9:
            cr.execute("UPDATE flightdeck_message SET cost = %s, priced = %s WHERE id = %s",
                       (cost, bool(rate), mid))
            changed += 1
    if changed:
        cr.execute("""
            UPDATE flightdeck_session s SET total_cost = t.cost
              FROM (SELECT session_ref_id, sum(cost) AS cost FROM flightdeck_message
                     WHERE session_ref_id IN (SELECT DISTINCT session_ref_id
                                                FROM flightdeck_message
                                               WHERE model LIKE ANY(%s))
                     GROUP BY session_ref_id) t
             WHERE s.id = t.session_ref_id
         RETURNING s.id""", [list(REPRICED)])
        sessions = {row[0] for row in cr.fetchall()}
    _logger.info("FlightDeck: repriced %s turn(s) in %s session(s)", changed, len(sessions))

    # One runner reports the quota: the one in this container, which has the
    # CLI and its sign-in beside Odoo.
    env["flightdeck.runner"].search([("zone", "=", "container")]).write({"reports_usage": True})
