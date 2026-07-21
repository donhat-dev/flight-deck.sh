from flightdeck import pricing


def test_rate_for_opus_prefix():
    assert pricing.rate_for("claude-opus-4-8") == (5.0, 25.0)
    assert pricing.rate_for("claude-opus-4-6") == (5.0, 25.0)


def test_rate_for_unknown_is_none():
    assert pricing.rate_for("gpt-4o") is None


def test_rate_for_none_or_empty_is_none():
    assert pricing.rate_for(None) is None
    assert pricing.rate_for("") is None


def test_message_cost_full_formula():
    # 1M input, 1M output, 1M cache_read, 1M 5m-write, 1M 1h-write on opus (5/25)
    cost = pricing.message_cost(
        "claude-opus-4-8",
        input_tokens=1_000_000,
        cache_read=1_000_000,
        cache_create_5m=1_000_000,
        cache_create_1h=1_000_000,
        output_tokens=1_000_000,
    )
    # 5 + 25 + 0.1*5 + 1.25*5 + 2.0*5 = 5+25+0.5+6.25+10 = 46.75
    assert round(cost, 4) == 46.75


def test_message_cost_unknown_model_none():
    assert pricing.message_cost("gpt-4o", 1000, 0, 0, 0, 1000) is None


def test_cache_savings():
    # 1M cache_read on opus: 1M * 0.9 * 5/1e6 = 4.5
    assert round(pricing.cache_savings("claude-opus-4-8", 1_000_000), 4) == 4.5
    assert pricing.cache_savings("gpt-4o", 1_000_000) == 0.0
