"""Pricing table correctness.

Every rate here is checked against the published table
(https://platform.claude.com/docs/en/docs/about-claude/pricing, read 2026-07-31),
because the failure mode is silent: a wrong rate produces a plausible dollar figure
and nothing on the dashboard looks broken. An UNPRICED model at least declares
itself — which is why the last group asserts that a model we have no rate for stays
unpriced instead of inheriting a neighbour's.
"""
import pytest

from flightdeck import pricing

JULY = "2026-07-31T10:00:00Z"      # inside Sonnet 5's introductory window
SEPT = "2026-09-01T00:00:00Z"      # the first day it ends


class TestOpusFamilies:
    def test_opus_5_is_priced_at_all(self):
        # It was missing entirely, so 14.5B tokens of real usage read as $0 and the
        # dashboard's totals excluded the most-used model on the account.
        assert pricing.rate_for("claude-opus-5") == (5.0, 25.0)

    def test_a_dated_opus_5_id_still_matches(self):
        assert pricing.rate_for("claude-opus-5-20260514") == (5.0, 25.0)

    @pytest.mark.parametrize("model", [
        "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5",
    ])
    def test_opus_4_5_through_4_8_are_five_and_twentyfive(self, model):
        assert pricing.rate_for(model + "-20260101") == (5.0, 25.0)

    def test_retired_opus_4_and_4_1_keep_their_higher_rate(self):
        # These are $15/$75. Under a single `claude-opus-4` key priced at $5/$25
        # they were understated threefold — the same defect as Opus 5's, in the
        # opposite direction and harder to notice.
        assert pricing.rate_for("claude-opus-4-1-20250805") == (15.0, 75.0)
        assert pricing.rate_for("claude-opus-4-20250514") == (15.0, 75.0)

    def test_longest_prefix_wins_so_4_8_is_not_read_as_plain_4(self):
        assert pricing.rate_for("claude-opus-4-8") != pricing.rate_for("claude-opus-4")


class TestDatedRates:
    def test_sonnet_5_uses_introductory_pricing_before_september(self):
        assert pricing.rate_for("claude-sonnet-5", JULY) == (2.0, 10.0)

    def test_sonnet_5_reverts_to_standard_on_the_first_of_september(self):
        assert pricing.rate_for("claude-sonnet-5", SEPT) == (3.0, 15.0)

    def test_an_undated_call_gets_the_standard_rate_not_the_discount(self):
        # A caller that cannot say WHEN must not have a discount applied for it.
        assert pricing.rate_for("claude-sonnet-5") == (3.0, 15.0)

    def test_the_promotion_does_not_leak_onto_another_model(self):
        # Prefix equality, not startswith: sonnet-4-6 must not pick up sonnet-5's
        # promotion, and neither must a model whose ID merely contains the string.
        assert pricing.rate_for("claude-sonnet-4-6", JULY) == (3.0, 15.0)
        assert pricing.rate_for("claude-haiku-4-5", JULY) == (1.0, 5.0)

    def test_message_cost_follows_the_date(self):
        args = ("claude-sonnet-5", 1_000_000, 0, 0, 0, 0)
        assert pricing.message_cost(*args, JULY) == pytest.approx(2.0)
        assert pricing.message_cost(*args, SEPT) == pytest.approx(3.0)

    def test_cache_savings_follows_the_date(self):
        # 1M cache reads saved at 0.9 x input price: $1.80 vs $2.70.
        assert pricing.cache_savings("claude-sonnet-5", 1_000_000, JULY) == pytest.approx(1.8)
        assert pricing.cache_savings("claude-sonnet-5", 1_000_000, SEPT) == pytest.approx(2.7)


class TestUnpricedStaysUnpriced:
    @pytest.mark.parametrize("model", ["", None, "<synthetic>", "gpt-4o", "claude-opus-9"])
    def test_no_rate_is_invented_for_an_unknown_model(self, model):
        assert pricing.rate_for(model) is None
        assert pricing.message_cost(model, 1000, 1000, 0, 0, 1000) is None

    def test_an_unpriced_model_saves_nothing_rather_than_guessing(self):
        assert pricing.cache_savings("claude-opus-9", 1_000_000) == 0.0


class TestCacheMultipliers:
    def test_the_multipliers_match_the_published_ones(self):
        # Read 0.1x, 5m write 1.25x, 1h write 2x of base input.
        assert (pricing.CACHE_READ_MULT, pricing.WRITE_5M_MULT,
                pricing.WRITE_1H_MULT) == (0.1, 1.25, 2.0)

    def test_opus_5_cache_columns_come_out_at_the_published_dollar_rates(self):
        # Published: $6.25/MTok 5m write, $10/MTok 1h write, $0.50/MTok cache hit.
        one_m = 1_000_000
        assert pricing.message_cost("claude-opus-5", 0, 0, one_m, 0, 0) == pytest.approx(6.25)
        assert pricing.message_cost("claude-opus-5", 0, 0, 0, one_m, 0) == pytest.approx(10.0)
        assert pricing.message_cost("claude-opus-5", 0, one_m, 0, 0, 0) == pytest.approx(0.50)
