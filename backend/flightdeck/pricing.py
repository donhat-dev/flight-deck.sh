"""Model pricing and cost math. Rates are per 1,000,000 tokens (list API prices)."""

# model-ID prefix -> (input_per_mtok, output_per_mtok)
RATES: dict[str, tuple[float, float]] = {
    "claude-opus-4": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
}

CACHE_READ_MULT = 0.1
WRITE_5M_MULT = 1.25
WRITE_1H_MULT = 2.0


def rate_for(model: str) -> tuple[float, float] | None:
    """Longest-prefix match of a model ID against RATES."""
    if not model:
        return None
    best: tuple[str, tuple[float, float]] | None = None
    for prefix, rate in RATES.items():
        if model.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, rate)
    return best[1] if best else None


def message_cost(
    model: str,
    input_tokens: int,
    cache_read: int,
    cache_create_5m: int,
    cache_create_1h: int,
    output_tokens: int,
) -> float | None:
    """API-equivalent USD cost for one message, or None if the model is unpriced."""
    rate = rate_for(model)
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


def cache_savings(model: str, cache_read: int) -> float:
    """USD saved by cache reads vs paying full input price (0.0 if unpriced)."""
    rate = rate_for(model)
    if rate is None:
        return 0.0
    r_in, _ = rate
    return cache_read * (1 - CACHE_READ_MULT) * r_in / 1_000_000
