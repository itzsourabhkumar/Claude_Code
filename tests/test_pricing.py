"""Cost calculation: per-dimension rates, per-model aggregation, INR output.

The central correctness property under test is that the four token dimensions
are priced at four *different* rates and the total is the sum of the parts that
are displayed beside it - never a blended figure derived from ``total_tokens``,
which would overstate a cache-heavy Claude Code bill by roughly ten times.
"""

from __future__ import annotations

import json

import pytest

from tracker.config import load_config
from tracker.pricing import (
    DEFAULT_USD_TO_INR,
    PRICES,
    PRICING_AS_OF,
    PriceBook,
    format_inr,
    normalise_model,
    rupee_symbol,
)

MILLION = 1_000_000


@pytest.fixture
def prices():
    """A price book with a round exchange rate, so arithmetic is checkable."""
    return PriceBook(usd_to_inr=100.0)


def usage(inp=0, out=0, read=0, write=0):
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write,
    }


# --------------------------------------------------------------------------
# The published price table
# --------------------------------------------------------------------------
class TestPriceTable:
    """Rates verified against platform.claude.com on the PRICING_AS_OF date."""

    @pytest.mark.parametrize("model,inp,out", [
        ("claude-opus-5", 5.0, 25.0),
        ("claude-opus-4-8", 5.0, 25.0),
        ("claude-sonnet-5", 2.0, 10.0),
        ("claude-sonnet-4-6", 3.0, 15.0),
        ("claude-haiku-4-5", 1.0, 5.0),
        ("claude-fable-5-1", 10.0, 50.0),
    ])
    def test_base_rates(self, model, inp, out):
        assert PRICES[model]["input"] == inp
        assert PRICES[model]["output"] == out

    @pytest.mark.parametrize("model", [m for m in PRICES if m not in
                                       ("claude-fable-5-1", "claude-mythos-5-1")])
    def test_cache_write_follows_the_standard_multipliers(self, model):
        row = PRICES[model]
        assert row["cache_write_5m"] == pytest.approx(row["input"] * 1.25)
        assert row["cache_write_1h"] == pytest.approx(row["input"] * 2.0)

    @pytest.mark.parametrize("model", [
        "claude-opus-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5",
    ])
    def test_cache_read_is_a_tenth_of_input(self, model):
        row = PRICES[model]
        assert row["cache_read"] == pytest.approx(row["input"] * 0.1)

    @pytest.mark.parametrize("model", ["claude-fable-5-1", "claude-mythos-5-1"])
    def test_fable_5_1_reads_cache_at_a_fortieth_not_a_tenth(self, model):
        """The documented exception to the 0.1x rule - $0.25, not $1.00."""
        row = PRICES[model]
        assert row["cache_read"] == 0.25
        assert row["cache_read"] == pytest.approx(row["input"] * 0.025)

    def test_every_model_prices_every_dimension(self):
        for model, row in PRICES.items():
            for field in ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read"):
                assert isinstance(row.get(field), (int, float)), "%s.%s" % (model, field)
                assert row[field] > 0

    def test_the_table_records_when_it_was_checked(self):
        assert len(PRICING_AS_OF) == 10 and PRICING_AS_OF[4] == "-"


# --------------------------------------------------------------------------
# Model resolution
# --------------------------------------------------------------------------
class TestModelResolution:
    @pytest.mark.parametrize("given,expected", [
        ("claude-opus-5", "claude-opus-5"),
        ("  claude-opus-5  ", "claude-opus-5"),
        ("anthropic.claude-opus-5", "claude-opus-5"),          # Bedrock
        ("claude-haiku-4-5@20251001", "claude-haiku-4-5"),     # Vertex
    ])
    def test_platform_decorations_are_stripped(self, given, expected):
        assert normalise_model(given) == expected

    @pytest.mark.parametrize("given", [None, "", "   ", 42, {}])
    def test_junk_resolves_to_nothing(self, given):
        assert normalise_model(given) is None

    def test_a_dated_snapshot_prices_as_its_undated_model(self, prices):
        assert prices.prices_for("claude-haiku-4-5-20251001") == PRICES["claude-haiku-4-5"]

    def test_an_unknown_model_has_no_price(self, prices):
        assert prices.prices_for("some-other-llm") is None
        assert prices.is_priced("some-other-llm") is False

    def test_synthetic_turns_are_never_priced(self, prices):
        """Claude Code writes <synthetic> for turns that never hit the API."""
        assert prices.is_priced("<synthetic>") is False


# --------------------------------------------------------------------------
# The arithmetic
# --------------------------------------------------------------------------
class TestCostForOneInteraction:
    def test_input_tokens_price_at_the_input_rate(self, prices):
        cost = prices.cost_for(usage(inp=MILLION), "claude-opus-5")
        assert cost["input_cost_usd"] == pytest.approx(5.0)
        assert cost["input_cost_inr"] == pytest.approx(500.0)

    def test_output_tokens_price_at_the_output_rate(self, prices):
        cost = prices.cost_for(usage(out=MILLION), "claude-opus-5")
        assert cost["output_cost_usd"] == pytest.approx(25.0)

    def test_cache_reads_price_at_a_tenth_of_input(self, prices):
        cost = prices.cost_for(usage(read=MILLION), "claude-opus-5")
        assert cost["cache_read_cost_usd"] == pytest.approx(0.50)
        assert cost["cached_input_cost_usd"] == pytest.approx(0.50)

    def test_cache_writes_price_above_input(self, prices):
        cost = prices.cost_for(usage(write=MILLION), "claude-opus-5")
        assert cost["cache_write_cost_usd"] == pytest.approx(6.25)

    def test_the_cached_bucket_is_reads_plus_writes(self, prices):
        cost = prices.cost_for(usage(read=MILLION, write=MILLION), "claude-opus-5")
        assert cost["cached_input_cost_usd"] == pytest.approx(0.50 + 6.25)
        assert cost["cached_input_cost_usd"] == pytest.approx(
            cost["cache_read_cost_usd"] + cost["cache_write_cost_usd"])

    def test_the_total_is_the_sum_of_the_three_displayed_buckets(self, prices):
        cost = prices.cost_for(
            usage(inp=MILLION, out=MILLION, read=MILLION, write=MILLION), "claude-opus-5")
        assert cost["total_cost_usd"] == pytest.approx(
            cost["input_cost_usd"] + cost["cached_input_cost_usd"] + cost["output_cost_usd"])
        assert cost["total_cost_usd"] == pytest.approx(5.0 + 25.0 + 0.50 + 6.25)

    def test_the_total_is_not_total_tokens_at_the_input_rate(self, prices):
        """The bug this whole module exists to avoid.

        A cache-heavy turn priced as "all tokens at the input rate" costs many
        times what it really does, because cache reads are a tenth of input.
        """
        cache_heavy = usage(inp=100, out=1_000, read=10 * MILLION)
        cost = prices.cost_for(cache_heavy, "claude-opus-5")
        naive = (100 + 1_000 + 10 * MILLION) * 5.0 / MILLION
        assert cost["total_cost_usd"] < naive / 8

    def test_inr_is_usd_times_the_configured_rate(self, prices):
        cost = prices.cost_for(usage(inp=MILLION), "claude-opus-5")
        for bucket in ("input", "cached_input", "output", "total"):
            usd = cost[bucket + "_cost_usd"]
            assert cost[bucket + "_cost_inr"] == pytest.approx(usd * 100.0)

    def test_token_counts_are_echoed_so_a_figure_can_be_audited(self, prices):
        cost = prices.cost_for(usage(inp=7, out=9), "claude-opus-5")
        assert cost["tokens"]["input_tokens"] == 7
        assert cost["tokens"]["output_tokens"] == 9

    def test_the_rates_used_are_reported(self, prices):
        cost = prices.cost_for(usage(inp=1), "claude-opus-5")
        assert cost["rates"]["input"] == 5.0
        assert cost["usd_to_inr"] == 100.0


class TestCacheWriteTtl:
    def test_five_minute_writes_are_the_default(self):
        assert PriceBook().cache_write_ttl == "5m"

    def test_one_hour_writes_cost_twice_input(self):
        book = PriceBook(usd_to_inr=100.0, cache_write_ttl="1h")
        cost = book.cost_for(usage(write=MILLION), "claude-opus-5")
        assert cost["cache_write_cost_usd"] == pytest.approx(10.0)

    def test_the_ttl_in_force_is_reported(self):
        assert PriceBook(cache_write_ttl="1h").cost_for(usage(), "claude-opus-5")[
            "cache_write_ttl"] == "1h"

    @pytest.mark.parametrize("given", ["nonsense", "", None, "10m"])
    def test_an_unrecognised_ttl_falls_back_to_the_cheaper_assumption(self, given):
        assert PriceBook(cache_write_ttl=given).cache_write_ttl == "5m"


# --------------------------------------------------------------------------
# Missing, zero and unavailable values
# --------------------------------------------------------------------------
class TestSafeValues:
    def test_zero_tokens_cost_zero_not_none(self, prices):
        cost = prices.cost_for(usage(), "claude-opus-5")
        assert cost["total_cost_inr"] == 0
        assert cost["priced"] is True

    def test_missing_dimensions_are_treated_as_zero_for_costing(self, prices):
        """A turn that reported no cache usage still has a real input cost."""
        cost = prices.cost_for({"input_tokens": MILLION}, "claude-opus-5")
        assert cost["input_cost_usd"] == pytest.approx(5.0)
        assert cost["cached_input_cost_usd"] == 0.0

    def test_null_dimensions_do_not_raise(self, prices):
        cost = prices.cost_for(
            {f: None for f in ("input_tokens", "output_tokens",
                               "cache_read_input_tokens",
                               "cache_creation_input_tokens")}, "claude-opus-5")
        assert cost["total_cost_usd"] == 0.0

    @pytest.mark.parametrize("bad", [None, [], "nonsense", 5])
    def test_a_non_mapping_usage_is_survivable(self, prices, bad):
        assert prices.cost_for(bad, "claude-opus-5")["total_cost_usd"] == 0.0

    def test_negative_counts_are_ignored_rather_than_credited(self, prices):
        cost = prices.cost_for(usage(inp=-500), "claude-opus-5")
        assert cost["total_cost_usd"] == 0.0

    def test_an_unpriced_model_yields_null_costs_not_zero(self, prices):
        """Unknown must never render as free."""
        cost = prices.cost_for(usage(inp=MILLION), "mystery-model")
        assert cost["priced"] is False
        for field in ("input_cost_inr", "cached_input_cost_inr",
                      "output_cost_inr", "total_cost_inr", "total_cost_usd"):
            assert cost[field] is None
        assert cost["unpriced_tokens"] == MILLION


# --------------------------------------------------------------------------
# Aggregation across models
# --------------------------------------------------------------------------
class TestAggregation:
    def test_each_model_is_priced_at_its_own_rate(self, prices):
        """The reason the database groups by model before anything is costed."""
        rows = [
            dict(usage(inp=MILLION), model="claude-opus-5"),    # $5
            dict(usage(inp=MILLION), model="claude-haiku-4-5"),  # $1
        ]
        total = prices.aggregate(rows)
        assert total["input_cost_usd"] == pytest.approx(6.0)

    def test_a_blended_rate_would_have_been_wrong(self, prices):
        """Same tokens, very different money - proof a single rate cannot work."""
        opus = prices.aggregate([dict(usage(inp=MILLION), model="claude-opus-5")])
        haiku = prices.aggregate([dict(usage(inp=MILLION), model="claude-haiku-4-5")])
        assert opus["total_cost_usd"] == pytest.approx(haiku["total_cost_usd"] * 5)

    def test_the_aggregate_total_is_the_sum_of_the_aggregate_parts(self, prices):
        rows = [
            dict(usage(inp=1000, out=2000, read=30000, write=400), model="claude-opus-5"),
            dict(usage(inp=500, out=100, read=90000, write=50), model="claude-sonnet-5"),
        ]
        total = prices.aggregate(rows)
        assert total["total_cost_inr"] == pytest.approx(
            total["input_cost_inr"] + total["cached_input_cost_inr"]
            + total["output_cost_inr"])

    def test_unpriced_rows_are_excluded_and_counted(self, prices):
        rows = [
            dict(usage(inp=MILLION), model="claude-opus-5"),
            dict(usage(inp=7777), model="mystery-model"),
        ]
        total = prices.aggregate(rows)
        assert total["input_cost_usd"] == pytest.approx(5.0)
        assert total["unpriced_tokens"] == 7777
        assert total["unpriced_models"] == ["mystery-model"]
        assert total["priced"] is True

    def test_an_entirely_unpriced_group_reports_unknown_not_zero(self, prices):
        total = prices.aggregate([dict(usage(inp=100), model="mystery-model")])
        assert total["priced"] is False
        assert total["total_cost_inr"] is None
        assert total["total_cost_usd"] is None

    def test_an_empty_group_is_not_an_error(self, prices):
        total = prices.aggregate([])
        assert total["priced"] is False
        assert total["unpriced_tokens"] == 0

    def test_nested_usage_objects_are_accepted(self, prices):
        """Stored records nest tokens under "usage"; grouped rows do not."""
        row = {"model": "claude-opus-5", "usage": usage(inp=MILLION)}
        assert prices.aggregate([row])["input_cost_usd"] == pytest.approx(5.0)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
class TestConfiguration:
    def test_defaults_are_sane(self, config):
        book = PriceBook.from_config(config)
        assert book.usd_to_inr == DEFAULT_USD_TO_INR
        assert book.cache_write_ttl == "5m"
        assert book.enabled is True

    def test_the_exchange_rate_is_configurable(self, tracker_root):
        (tracker_root / "config.json").write_text(
            json.dumps({"pricing": {"usd_to_inr": 90.5}}), encoding="utf-8")
        assert PriceBook.from_config(load_config()).usd_to_inr == 90.5

    @pytest.mark.parametrize("bad", [0, -5, "abc", None])
    def test_a_nonsense_rate_falls_back_to_the_default(self, tracker_root, bad):
        (tracker_root / "config.json").write_text(
            json.dumps({"pricing": {"usd_to_inr": bad}}), encoding="utf-8")
        assert load_config().usd_to_inr == DEFAULT_USD_TO_INR

    def test_the_rate_can_come_from_the_environment(self, tracker_root, monkeypatch):
        monkeypatch.setenv("CCTRACKER_USD_TO_INR", "92.25")
        assert load_config().usd_to_inr == 92.25

    def test_pricing_can_be_switched_off(self, tracker_root):
        (tracker_root / "config.json").write_text(
            json.dumps({"pricing": {"enabled": False}}), encoding="utf-8")
        assert PriceBook.from_config(load_config()).enabled is False

    def test_a_model_price_can_be_overridden(self, tracker_root):
        (tracker_root / "config.json").write_text(json.dumps({"pricing": {
            "model_prices": {"claude-opus-5": {"input": 1.0, "output": 2.0}}}}),
            encoding="utf-8")
        book = PriceBook.from_config(load_config())
        assert book.prices_for("claude-opus-5")["input"] == 1.0
        # Cache prices the override omitted fall back to the standard multipliers.
        assert book.prices_for("claude-opus-5")["cache_read"] == pytest.approx(0.1)

    def test_a_new_model_can_be_added(self, tracker_root):
        (tracker_root / "config.json").write_text(json.dumps({"pricing": {
            "model_prices": {"future-model": {"input": 7.0, "output": 21.0}}}}),
            encoding="utf-8")
        book = PriceBook.from_config(load_config())
        assert book.is_priced("future-model")
        assert book.cost_for(usage(inp=MILLION), "future-model")["input_cost_usd"] == 7.0

    @pytest.mark.parametrize("bad", [
        {"input": "free"}, {"output": 5.0}, {"input": -1, "output": 2}, "nonsense", None, 42,
    ])
    def test_a_malformed_override_is_ignored_not_fatal(self, bad):
        book = PriceBook(overrides={"claude-opus-5": bad})
        assert book.prices_for("claude-opus-5") == PRICES["claude-opus-5"]

    def test_overriding_never_mutates_the_shared_table(self):
        PriceBook(overrides={"claude-opus-5": {"input": 999.0, "output": 999.0}})
        assert PRICES["claude-opus-5"]["input"] == 5.0


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
class TestRupeeFormatting:
    @pytest.mark.parametrize("amount,expected", [
        (0, "Rs.0.00"),
        (5, "Rs.5.00"),
        (999.99, "Rs.999.99"),
        (1234.5, "Rs.1,234.50"),
        (123456.78, "Rs.1,23,456.78"),          # lakh: 2,2,3 grouping
        (12345678.9, "Rs.1,23,45,678.90"),      # crore
        (1234567890.0, "Rs.1,23,45,67,890.00"),
    ])
    def test_indian_digit_grouping(self, amount, expected):
        """India groups 2,2,3 from the right, not 3,3,3."""
        assert format_inr(amount, symbol="Rs.") == expected

    def test_negative_amounts_keep_the_sign_outside_the_symbol(self):
        assert format_inr(-1234.5, symbol="Rs.") == "-Rs.1,234.50"

    @pytest.mark.parametrize("bad", [None, "abc", [], {}])
    def test_unrenderable_values_show_a_dash(self, bad):
        assert format_inr(bad) == "-"

    def test_decimals_can_be_dropped(self):
        assert format_inr(1234.56, decimals=0, symbol="Rs.") == "Rs.1,234"

    def test_the_rupee_sign_is_used_where_it_can_be_encoded(self):
        class Utf8Stream:
            encoding = "utf-8"

        assert rupee_symbol(Utf8Stream()) == "₹"

    def test_a_legacy_codepage_console_falls_back_to_ascii(self):
        """A Windows cp1252 console cannot encode the rupee sign at all."""
        class Cp1252Stream:
            encoding = "cp1252"

        assert rupee_symbol(Cp1252Stream()) == "Rs."

    def test_a_stream_with_no_encoding_is_survivable(self):
        class Bare:
            encoding = None

        assert rupee_symbol(Bare()) == "Rs."
