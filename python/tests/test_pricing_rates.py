"""Pricing table correctness.

Every rate here is checked against the published table
(https://platform.claude.com/docs/en/about-claude/pricing, read 2026-07-31 and
2026-09-23),
because the failure mode is silent: a wrong rate produces a plausible dollar figure
and nothing on the dashboard looks broken. An UNPRICED model at least declares
itself — which is why the last group asserts that a model we have no rate for stays
unpriced instead of inheriting a neighbour's.
"""
import pytest

from flightdeck import pricing

JULY = "2026-07-31T10:00:00Z"
SEPT = "2026-09-01T00:00:00Z"      # the day Sonnet 5 was to rise to $3/$15, and did not


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


class TestSeptemberRates:
    """Rates re-read on 2026-09-23 after the Opus 5.5 launch."""

    def test_sonnet_5_stays_at_two_and_ten_after_september_first(self):
        # The introductory price became the standard one; the $3/$15 rise never came.
        assert pricing.rate_for("claude-sonnet-5", JULY) == (2.0, 10.0)
        assert pricing.rate_for("claude-sonnet-5", SEPT) == (2.0, 10.0)
        assert pricing.rate_for("claude-sonnet-5") == (2.0, 10.0)

    def test_no_promotion_is_active(self):
        assert pricing.PROMOTIONS == ()

    def test_opus_5_5_is_not_priced_as_opus_5(self):
        # `claude-opus-5` is a prefix of `claude-opus-5-5`; without its own key the
        # newer, cheaper model inherited the older rate and its 0.1x cache read.
        assert pricing.rate_for("claude-opus-5-5") == (4.0, 20.0)
        one_m = 1_000_000
        assert pricing.message_cost("claude-opus-5-5", 0, one_m, 0, 0, 0) == pytest.approx(0.20)
        assert pricing.message_cost("claude-opus-5-5", 0, 0, one_m, 0, 0) == pytest.approx(5.0)
        assert pricing.message_cost("claude-opus-5-5", 0, 0, 0, one_m, 0) == pytest.approx(8.0)

    def test_fable_5_1_reads_cache_at_a_fortieth_of_input(self):
        assert pricing.message_cost("claude-fable-5-1", 0, 1_000_000, 0, 0, 0) == pytest.approx(0.25)
        # Fable 5 (no .1) keeps the standard 0.1x.
        assert pricing.message_cost("claude-fable-5", 0, 1_000_000, 0, 0, 0) == pytest.approx(1.0)

    def test_fast_mode_uses_its_own_rate_and_keeps_cache_multipliers(self):
        one_m = 1_000_000
        assert pricing.message_cost("claude-opus-5-5", one_m, 0, 0, 0, one_m, speed="fast") == pytest.approx(48.0)
        assert pricing.message_cost("claude-opus-5", 0, one_m, 0, 0, 0, speed="fast") == pytest.approx(1.0)
        # A model without a fast rate is billed at its standard rate.
        assert pricing.message_cost("claude-sonnet-5", one_m, 0, 0, 0, 0, speed="fast") == pytest.approx(2.0)

    def test_us_inference_costs_a_tenth_more_on_every_class(self):
        one_m = 1_000_000
        assert pricing.message_cost("claude-opus-5", one_m, one_m, 0, 0, one_m, geo="us") == pytest.approx(
            (5 + 0.5 + 25) * 1.1)
        assert pricing.message_cost("claude-opus-5", one_m, 0, 0, 0, 0, geo="global") == pytest.approx(5.0)


class TestUnpricedStaysUnpriced:
    @pytest.mark.parametrize("model", ["", None, "<synthetic>", "gpt-4o", "claude-opus-9"])
    def test_no_rate_is_invented_for_an_unknown_model(self, model):
        assert pricing.rate_for(model) is None
        assert pricing.message_cost(model, 1000, 1000, 0, 0, 1000) is None

    def test_an_unpriced_model_saves_nothing_rather_than_guessing(self):
        assert pricing.cache_savings("claude-opus-9", 0, 1_000_000, 0, 0, 0) == 0.0


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
