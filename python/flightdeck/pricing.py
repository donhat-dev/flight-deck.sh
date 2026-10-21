"""Model pricing and cost math. Rates are per 1,000,000 tokens (list API prices).

Source: https://platform.claude.com/docs/en/about-claude/pricing (read
2026-07-31, re-read 2026-09-23 and 2026-10-01). Every number below is copied from that table — none is inferred from a
neighbouring model, because a missing row reads as "unpriced" (honest) while a
guessed row reads as a real cost (wrong, and invisibly so).

Two facts that shape the structure:

- **Prefix matching must be longest-wins per family, not per generation.** Opus 4.5
  through 4.8 are $5/$25 while Opus 4.1 and Opus 4 are $15/$75, so a single
  `claude-opus-4` key would price a retired Opus 4 at a third of its real rate.
- **Cache reads are not always 0.1x.** Opus 5.5 reads at 0.05x and Fable/Mythos 5.1
  at 0.025x of base input; every other model at 0.1x. In an agent loop cache reads
  are most of the bill, so a missing override misprices the largest term.
- **Promotions are dated by the MESSAGE's timestamp**, not by today's, because the
  ledger keeps history. None is active now: Sonnet 5's introductory $2/$10 became
  its standard price instead of rising to $3/$15 on 2026-09-01.

The 1M-token context window is standard-priced on Claude 4.6 and later (a 900k
request is billed per token exactly like a 9k one), so no long-context tier exists
here. Fast mode and US-only inference ARE priced differently; both are read from
the message's own `usage.speed` / `usage.inference_geo`.
"""
from __future__ import annotations

# model-ID prefix -> (input_per_mtok, output_per_mtok). Longest prefix wins.
RATES: dict[str, tuple[float, float]] = {
    # Opus 5 / 4.8 / 4.7 / 4.6 / 4.5 — all $5 / $25.
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    # Opus 4.1 (deprecated) and Opus 4 (retired) kept their original $15 / $75.
    # `claude-opus-4-1` needs its own key precisely because the shorter
    # `claude-opus-4` key below would otherwise be the only match it has.
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4": (15.0, 75.0),
    # Opus 5.5 is $4 / $20. Needs its own key: `claude-opus-5` would match it.
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5": (2.0, 10.0),
    # Same $2 / $10 as Sonnet 5 (table re-read 2026-10-01). Listed so the price
    # comes from the table, not from `claude-sonnet-5` happening to match it.
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    # Same $10 / $50 as the 5.0 models, but a cheaper cache read (see below).
    "claude-fable-5-1": (10.0, 50.0),
    "claude-mythos-5-1": (10.0, 50.0),
}

# (prefix, first ISO date the rate NO LONGER applies, rate). A message stamped
# before that date is priced at the promotional rate; on or after it, at the
# standard rate in RATES. Encoded as an end date rather than as a "current rate"
# because the ledger keeps history: July's message really did cost $2/MTok, and
# rewriting it on 1 September would silently restate the past.
PROMOTIONS: tuple[tuple[str, str, tuple[float, float]], ...] = ()

CACHE_READ_MULT = 0.1
WRITE_5M_MULT = 1.25
WRITE_1H_MULT = 2.0

# RATES prefix -> cache-read multiplier where it is not CACHE_READ_MULT.
READ_MULT_OVERRIDES: dict[str, float] = {
    "claude-opus-5-5": 0.05,
    "claude-fable-5-1": 0.025,
    "claude-mythos-5-1": 0.025,
}

# RATES prefix -> (input, output) in fast mode. Cache multipliers apply on top.
FAST_RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (8.0, 40.0),
    "claude-opus-5": (10.0, 50.0),
    "claude-opus-4-8": (10.0, 50.0),
}

# `usage.inference_geo` value -> multiplier on every token class.
GEO_MULT: dict[str, float] = {"us": 1.1}


def _prefix_match(model: str) -> str | None:
    """The longest RATES prefix this model ID starts with."""
    best: str | None = None
    for prefix in RATES:
        if model.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return best


def rate_for(model: str, ts: str | None = None) -> tuple[float, float] | None:
    """List rate for a model, as of `ts` (an ISO timestamp).

    `ts` chooses between a promotional and a standard rate. Omitting it uses the
    STANDARD rate — the higher one — so a caller that cannot say when a message was
    sent never has a discount applied on its behalf.
    """
    if not model:
        return None
    prefix = _prefix_match(model)
    if prefix is None:
        return None
    if ts:
        for promo_prefix, until, rate in PROMOTIONS:
            if prefix == promo_prefix and ts < until:
                return rate
    return RATES[prefix]


def read_mult(model: str) -> float:
    """Cache-read multiplier of base input for this model."""
    prefix = _prefix_match(model or "")
    return READ_MULT_OVERRIDES.get(prefix, CACHE_READ_MULT)


def _effective_rate(model, ts, speed, geo):
    """(input, output) after fast mode and inference geography, or None."""
    rate = rate_for(model, ts)
    if rate is None:
        return None
    if speed == "fast":
        rate = FAST_RATES.get(_prefix_match(model), rate)
    m = GEO_MULT.get(geo or "", 1.0)
    return rate[0] * m, rate[1] * m


def message_cost(
    model: str,
    input_tokens: int,
    cache_read: int,
    cache_create_5m: int,
    cache_create_1h: int,
    output_tokens: int,
    ts: str | None = None,
    speed: str | None = None,
    geo: str | None = None,
) -> float | None:
    """API-equivalent USD cost for one message, or None if the model is unpriced."""
    rate = _effective_rate(model, ts, speed, geo)
    if rate is None:
        return None
    r_in, r_out = rate
    return (
        input_tokens * r_in
        + output_tokens * r_out
        + cache_read * read_mult(model) * r_in
        + cache_create_5m * WRITE_5M_MULT * r_in
        + cache_create_1h * WRITE_1H_MULT * r_in
    ) / 1_000_000


def uncached_cost(
    model: str,
    input_tokens: int,
    cache_read: int,
    cache_create_5m: int,
    cache_create_1h: int,
    output_tokens: int,
    ts: str | None = None,
    speed: str | None = None,
    geo: str | None = None,
) -> float | None:
    """What the same tokens would cost with no prompt caching: every input-side
    token at the base input rate. None if the model is unpriced."""
    rate = _effective_rate(model, ts, speed, geo)
    if rate is None:
        return None
    r_in, r_out = rate
    context = input_tokens + cache_read + cache_create_5m + cache_create_1h
    return (context * r_in + output_tokens * r_out) / 1_000_000


def cache_savings(
    model: str,
    input_tokens: int,
    cache_read: int,
    cache_create_5m: int,
    cache_create_1h: int,
    output_tokens: int,
    ts: str | None = None,
    speed: str | None = None,
    geo: str | None = None,
) -> float:
    """Net USD saved by prompt caching: uncached cost minus cached cost.

    Net, not gross: cache writes cost MORE than base input (1.25x / 2x), and
    counting only the read discount overstated savings by the whole write premium.
    0.0 if the model is unpriced."""
    args = (model, input_tokens, cache_read, cache_create_5m, cache_create_1h,
            output_tokens, ts, speed, geo)
    cached = message_cost(*args)
    if cached is None:
        return 0.0
    return uncached_cost(*args) - cached
