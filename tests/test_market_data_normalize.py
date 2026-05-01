# EPIC-DATA-001 — market_data.MarketDataHandler._normalize_timeframe() validation
# See: tests/stories/EPIC-DATA-001.md
#
# NOTE: _normalize_timeframe is an instance method on MarketDataHandler.
# ib_insync is stubbed in conftest.py so MarketDataHandler can be imported without
# a live IB Gateway. The handler is instantiated with ib_connection=None.
#
# KNOWN BEHAVIOR (as-implemented):
#   - Unknown strings pass through UNCHANGED (no raise). This is a gap — the
#     implementation should raise ValueError for unrecognized inputs to prevent
#     silent bad data reaching IB. Tests marked TODO reflect aspirational behavior.

import pytest
from market_data import MarketDataHandler


@pytest.fixture
def handler() -> MarketDataHandler:
    """MarketDataHandler with no IB connection — safe for unit tests."""
    return MarketDataHandler(ib_connection=None, yf_fallback=False)


# ── Canonical round-trip cases ────────────────────────────────────────────────

class TestNormalizeTimeframeCanonical:

    def test_1w_passthrough(self, handler):
        """AC1: '1W' is already IB-valid — passes through unchanged."""
        assert handler._normalize_timeframe("1W") == "1W"

    def test_1w_week_maps_to_1w(self, handler):
        """AC5 (alias): '1 week' → '1W' (known alias in tf_map)."""
        assert handler._normalize_timeframe("1 week") == "1W"

    def test_weekly_maps_to_1w(self, handler):
        """'weekly' → '1W' (known alias in tf_map)."""
        assert handler._normalize_timeframe("weekly") == "1W"

    def test_daily_maps_to_1_day(self, handler):
        """'daily' → '1 day' (known alias in tf_map)."""
        assert handler._normalize_timeframe("daily") == "1 day"

    def test_1_hour_passthrough(self, handler):
        """'1 hour' contains 'hour' → recognized as IB-valid, passes through."""
        assert handler._normalize_timeframe("1 hour") == "1 hour"

    def test_4_hours_passthrough(self, handler):
        """'4 hours' contains 'hour' → recognized as IB-valid, passes through."""
        assert handler._normalize_timeframe("4 hours") == "4 hours"

    def test_1_day_passthrough(self, handler):
        """'1 day' contains 'day' → recognized as IB-valid, passes through."""
        assert handler._normalize_timeframe("1 day") == "1 day"

    def test_return_type_is_always_str(self, handler):
        """Return type must be str on valid input."""
        result = handler._normalize_timeframe("1W")
        assert isinstance(result, str)


# ── Known alias mappings (tf_map coverage) ────────────────────────────────────

class TestKnownAliases:

    @pytest.mark.parametrize("alias, expected", [
        ("1 week",    "1W"),
        ("1 month",   "1M"),
        ("weekly",    "1W"),
        ("monthly",   "1M"),
        ("daily",     "1 day"),
        ("1 minute",  "1 min"),
        ("5 minutes", "5 mins"),
        ("15 minutes","15 mins"),
        ("30 minutes","30 mins"),
        ("1 hour",    "1 hour"),
        ("4 hours",   "4 hours"),
    ])
    def test_alias_maps_to_expected(self, handler, alias, expected):
        """All tf_map entries must produce the documented IB-valid output."""
        result = handler._normalize_timeframe(alias)
        assert result == expected, (
            f"_normalize_timeframe({alias!r}) returned {result!r}, expected {expected!r}"
        )


# ── Edge cases and gaps ───────────────────────────────────────────────────────

class TestEdgeCases:

    def test_none_raises(self, handler):
        """AC6: None input raises — AttributeError on .lower() call."""
        with pytest.raises((TypeError, AttributeError)):
            handler._normalize_timeframe(None)

    def test_unknown_string_passthrough(self, handler):
        """
        CURRENT behavior: unknown strings pass through unchanged.
        TODO: This should raise ValueError to prevent silent bad IB requests.
        Test documents actual behavior — not aspirational.
        """
        result = handler._normalize_timeframe("garbage")
        assert result == "garbage", (
            "Current impl passes unknown strings through unchanged. "
            "If this assertion fails, the behavior has been improved to raise — update test."
        )

    def test_empty_string_behavior(self, handler):
        """
        CURRENT behavior: empty string passes through unchanged.
        TODO: Should raise ValueError.
        """
        result = handler._normalize_timeframe("")
        assert isinstance(result, str)

    def test_1d_short_form_passthrough(self, handler):
        """
        '1D' is NOT in tf_map and doesn't contain IB format words.
        Current behavior: passes through unchanged as '1D'.
        NOTE: '1D' is not a valid IB timeframe — use '1 day' or 'daily' instead.
        """
        result = handler._normalize_timeframe("1D")
        # Documents current passthrough behavior
        assert result == "1D"

    def test_case_insensitive_lookup(self, handler):
        """tf_map lookup uses .lower() — 'DAILY' should map to '1 day'."""
        result = handler._normalize_timeframe("DAILY")
        assert result == "1 day"
