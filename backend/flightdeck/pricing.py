"""Model pricing and cost math. Rates are per 1,000,000 tokens (list API prices).

Source: https://platform.claude.com/docs/en/docs/about-claude/pricing (read
2026-07-31). Every number below is copied from that table — none is inferred from a
neighbouring model, because a missing row reads as "unpriced" (honest) while a
guessed row reads as a real cost (wrong, and invisibly so).

Two facts that shape the structure:

- **Prefix matching must be longest-wins per family, not per generation.** Opus 4.5
  through 4.8 are $5/$25 while Opus 4.1 and Opus 4 are $15/$75, so a single
  `claude-opus-4` key would price a retired Opus 4 at a third of its real rate.
- **One rate is promotional and expires.** Sonnet 5 is $2/$10 through 2026-08-31
  and $3/$15 from 2026-09-01. A ledger holds messages from both sides of that
  date, so the rate has to be chosen by the MESSAGE's timestamp, not by today's.

The 1M-token context window is standard-priced on Claude 4.6 and later (a 900k
request is billed per token exactly like a 9k one), so no long-context tier exists
here. Fast mode IS priced differently ($10/$50 on Opus 5 and 4.8), but the ledger
records no speed field, so fast-mode turns are billed at standard rates below.
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
    "claude-sonnet-5": (3.0, 15.0),      # standard rate; see PROMOTIONS
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
}

# (prefix, first ISO date the rate NO LONGER applies, rate). A message stamped
# before that date is priced at the promotional rate; on or after it, at the
# standard rate in RATES. Encoded as an end date rather than as a "current rate"
# because the ledger keeps history: July's message really did cost $2/MTok, and
# rewriting it on 1 September would silently restate the past.
PROMOTIONS: tuple[tuple[str, str, tuple[float, float]], ...] = (
    # Sonnet 5 introductory pricing, in effect through 2026-08-31 inclusive.
    ("claude-sonnet-5", "2026-09-01", (2.0, 10.0)),
)

CACHE_READ_MULT = 0.1
WRITE_5M_MULT = 1.25
WRITE_1H_MULT = 2.0


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


def message_cost(
    model: str,
    input_tokens: int,
    cache_read: int,
    cache_create_5m: int,
    cache_create_1h: int,
    output_tokens: int,
    ts: str | None = None,
) -> float | None:
    """API-equivalent USD cost for one message, or None if the model is unpriced."""
    rate = rate_for(model, ts)
    if rate is None:
        return None
    r_in, r_out = rate
    return (
        input_tokens * r_in
        + output_tokens * r_out
        + cache_read * CACHE_READ_MULT * r_in
        + cache_create_5m * WRITE_5M_MULT * r_in
        + cache_create_1h * WRITE_1H_MULT * r_in
    ) / 1_000_000


def cache_savings(model: str, cache_read: int, ts: str | None = None) -> float:
    """USD saved by cache reads vs paying full input price (0.0 if unpriced)."""
    rate = rate_for(model, ts)
    if rate is None:
        return 0.0
    r_in, _ = rate
    return cache_read * (1 - CACHE_READ_MULT) * r_in / 1_000_000
