# EPIC-PAT-001 — pattern_recognition.get_pattern_score() 7-tuple contract
# See: tests/stories/EPIC-PAT-001.md
#
# get_pattern_score() signature:
#   get_pattern_score(df: pd.DataFrame, ticker: str = "", mode: str = "swing") -> tuple
#
# Returns:
#   (has_bullish, has_bearish, best_target, pattern_names, volume_confirmed,
#    vcp_quality, vcp_data)
#
# NOTE: vcp_quality is always a float (0.0 when no VCP detected), NEVER None.
# vcp_data is None when no VCP detected, dict otherwise.

import numpy as np
import pandas as pd
import pytest

from pattern_recognition import get_pattern_score


# ── Contract validator ────────────────────────────────────────────────────────

def _assert_7tuple_contract(result, context: str) -> None:
    """
    Enforce the full type contract on a get_pattern_score() return value.
    Positions:
      [0] has_bullish    — bool
      [1] has_bearish    — bool
      [2] best_target    — float >= 0.0
      [3] pattern_names  — list[str] (may be empty, never None)
      [4] vol_confirmed  — bool
      [5] vcp_quality    — float (0.0 when no VCP — never None)
      [6] vcp_data       — dict or None
    """
    assert isinstance(result, tuple), (
        f"[{context}] Return is not a tuple: {type(result)}"
    )
    assert len(result) == 7, (
        f"[{context}] Expected 7 values, got {len(result)}: {result}"
    )

    has_bullish, has_bearish, best_target, pattern_names, vol_confirmed, vcp_quality, vcp_data = result

    assert type(has_bullish) is bool, (
        f"[{context}] result[0] (has_bullish) must be bool, got {type(has_bullish)}"
    )
    assert type(has_bearish) is bool, (
        f"[{context}] result[1] (has_bearish) must be bool, got {type(has_bearish)}"
    )
    assert isinstance(best_target, (int, float)) and best_target >= 0, (
        f"[{context}] result[2] (best_target) must be float >= 0, got {best_target!r}"
    )
    assert isinstance(pattern_names, list), (
        f"[{context}] result[3] (pattern_names) must be list, got {type(pattern_names)}"
    )
    assert all(isinstance(n, str) for n in pattern_names), (
        f"[{context}] result[3] (pattern_names) must be list[str], got {pattern_names}"
    )
    assert type(vol_confirmed) is bool, (
        f"[{context}] result[4] (vol_confirmed) must be bool, got {type(vol_confirmed)}"
    )
    assert isinstance(vcp_quality, (int, float)), (
        f"[{context}] result[5] (vcp_quality) must be float (0.0 when no VCP), "
        f"got {type(vcp_quality)}"
    )
    assert vcp_data is None or isinstance(vcp_data, dict), (
        f"[{context}] result[6] (vcp_data) must be dict or None, got {type(vcp_data)}"
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


@pytest.fixture
def single_row_ohlcv() -> pd.DataFrame:
    return pd.DataFrame({
        "open":   [100.0],
        "high":   [102.0],
        "low":    [99.0],
        "close":  [101.5],
        "volume": [500_000],
    })


@pytest.fixture
def synthetic_50_ohlcv() -> pd.DataFrame:
    """50-row OHLCV with a forced bullish engulfing at the last two bars."""
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0.1, 0.5, 50))
    high  = close + np.abs(rng.normal(0.3, 0.1, 50))
    low   = close - np.abs(rng.normal(0.3, 0.1, 50))
    open_ = close - rng.normal(0, 0.2, 50)

    # Force bullish engulfing at last two rows
    open_[-1] = close[-2] - 0.5    # opens below previous close
    close[-1] = open_[-2] + 0.5    # closes above previous open
    high[-1]  = close[-1] + 0.2
    low[-1]   = open_[-1] - 0.2

    return pd.DataFrame({
        "open":   open_,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": np.full(50, 1_000_000),
    })


@pytest.fixture
def nan_ohlcv() -> pd.DataFrame:
    return pd.DataFrame({
        "open":   [np.nan] * 10,
        "high":   [np.nan] * 10,
        "low":    [np.nan] * 10,
        "close":  [np.nan] * 10,
        "volume": [0.0] * 10,
    })


# ── Contract tests ────────────────────────────────────────────────────────────

class TestGetPatternScoreContract:

    def test_empty_dataframe_returns_7tuple(self, empty_ohlcv):
        """AC1-AC8: Contract holds for empty DataFrame."""
        result = get_pattern_score(empty_ohlcv)
        _assert_7tuple_contract(result, "empty_ohlcv")

    def test_single_row_returns_7tuple(self, single_row_ohlcv):
        """AC1-AC8: Contract holds for 1-row DataFrame."""
        result = get_pattern_score(single_row_ohlcv)
        _assert_7tuple_contract(result, "single_row_ohlcv")

    def test_50_row_returns_7tuple(self, synthetic_50_ohlcv):
        """AC1-AC8: Contract holds for standard 50-row fixture."""
        result = get_pattern_score(synthetic_50_ohlcv)
        _assert_7tuple_contract(result, "synthetic_50_ohlcv")

    def test_50_row_detects_bullish_pattern(self, synthetic_50_ohlcv):
        """Forced bullish engulfing must set has_bullish=True and pattern_names non-empty."""
        result = get_pattern_score(synthetic_50_ohlcv)
        has_bullish   = result[0]
        pattern_names = result[3]
        assert has_bullish is True, "Expected has_bullish=True for forced bullish engulfing"
        assert len(pattern_names) > 0, "Expected at least one detected pattern name"

    def test_no_exception_on_nan_filled_ohlcv(self, nan_ohlcv):
        """AC9: NaN-filled input must not raise — return a valid 7-tuple."""
        try:
            result = get_pattern_score(nan_ohlcv)
            _assert_7tuple_contract(result, "nan_filled_ohlcv")
        except Exception as exc:
            pytest.fail(f"get_pattern_score raised on NaN input: {exc}")

    def test_pattern_names_is_never_none(self, empty_ohlcv, single_row_ohlcv, synthetic_50_ohlcv):
        """AC5: result[3] must be a list (possibly empty), never None."""
        for df, label in [
            (empty_ohlcv, "empty"),
            (single_row_ohlcv, "1-row"),
            (synthetic_50_ohlcv, "50-row"),
        ]:
            result = get_pattern_score(df)
            assert result[3] is not None, (
                f"[{label}] pattern_names (result[3]) must be a list, not None"
            )

    def test_vcp_quality_is_float_not_none(self, synthetic_50_ohlcv):
        """result[5] (vcp_quality) is always float — 0.0 when no VCP detected."""
        result = get_pattern_score(synthetic_50_ohlcv)
        assert isinstance(result[5], (int, float)), (
            f"vcp_quality must be float, got {type(result[5])}"
        )

    @pytest.mark.parametrize("rows", [2, 5, 14, 26, 49])
    def test_contract_holds_for_various_lengths(self, rows):
        """AC9: Contract holds for DataFrames of various row counts."""
        rng = np.random.default_rng(rows)
        close = 100 + np.cumsum(rng.normal(0, 0.5, rows))
        df = pd.DataFrame({
            "open":   close - 0.1,
            "high":   close + 0.5,
            "low":    close - 0.5,
            "close":  close,
            "volume": np.full(rows, 500_000),
        })
        result = get_pattern_score(df)
        _assert_7tuple_contract(result, f"{rows}-row")
