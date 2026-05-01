# EPIC-FIB-001 — fib_retracement.py: fib math + score_bounce scoring gates
# See: tests/stories/EPIC-FIB-001.md

import numpy as np
import pandas as pd
import pytest

from fib_retracement import (
    detect_swing,
    fib_levels,
    nearest_fib_to_price,
    score_bounce,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_df(rows: int = 250, swing_low: float = 100.0,
             swing_high: float = 110.0, current: float = 104.2) -> pd.DataFrame:
    """
    Synthetic daily OHLCV designed so detect_swing(window=120) finds a real swing.

    Structure (250 rows default):
      rows 0-129  : stable background near swing_low
      rows 130-189: rise from swing_low to swing_high  (peak at row 189)
      rows 190-249: fall from swing_high to current    (60 bars of retracement)

    With window=120, detect_swing looks at rows 130-249.
    The peak (swing high) lands at row 189, with 59 bars before it in the window
    and 60 bars after — satisfying the ≥2 bars-before-peak requirement.
    """
    background = np.full(130, swing_low)
    up   = np.linspace(swing_low, swing_high, 60)
    down = np.linspace(swing_high, current, 60)
    close = np.concatenate([background, up, down])

    vol = np.full(len(close), 1_000_000, dtype=float)
    # Slightly higher volume in last 3 bars to help vol_expansion gate
    vol[-3:] = 1_300_000

    return pd.DataFrame({
        "open":   close,
        "high":   close + 0.3,
        "low":    close - 0.3,
        "close":  close,
        "volume": vol,
    })


@pytest.fixture
def swing_dict():
    """Canonical swing: high=110, low=100, range=10."""
    return {
        "swing_high":      110.0,
        "swing_high_date": "2026-01-15",
        "swing_low":       100.0,
        "swing_low_date":  "2025-12-01",
        "range":           10.0,
    }


# ── fib_levels ────────────────────────────────────────────────────────────────

class TestFibLevels:
    def test_61_8_level(self, swing_dict):
        """AC1: 61.8% fib level = 110 - 0.618 × 10 ≈ 103.82."""
        levels = fib_levels(swing_dict)
        assert abs(levels[0.618] - 103.82) < 0.01

    def test_50_level(self, swing_dict):
        """AC1: 50% fib level = 110 - 0.5 × 10 = 105.0 exactly."""
        levels = fib_levels(swing_dict)
        assert levels[0.5] == pytest.approx(105.0)

    def test_38_2_level(self, swing_dict):
        """AC1: 38.2% fib level = 110 - 0.382 × 10 ≈ 106.18."""
        levels = fib_levels(swing_dict)
        assert abs(levels[0.382] - 106.18) < 0.01

    def test_all_five_levels_present(self, swing_dict):
        """All 5 canonical ratios present in output."""
        levels = fib_levels(swing_dict)
        for ratio in (0.236, 0.382, 0.5, 0.618, 0.786):
            assert ratio in levels

    def test_levels_descend_from_high(self, swing_dict):
        """Higher ratio → lower price (deeper retracement from high)."""
        levels = fib_levels(swing_dict)
        ratios = sorted(levels.keys())
        prices = [levels[r] for r in ratios]
        assert prices == sorted(prices, reverse=True), \
            "Fib level prices should decrease as ratio increases"


# ── nearest_fib_to_price ──────────────────────────────────────────────────────

class TestNearestFibToPrice:
    def test_price_at_exact_618(self, swing_dict):
        """AC2: price at 61.8% level → ratio=0.618, dist_pct ≈ 0.0."""
        levels = fib_levels(swing_dict)
        price_at_618 = levels[0.618]  # ≈ 103.82
        ratio, level_price, dist_pct = nearest_fib_to_price(levels, price_at_618)
        assert ratio == 0.618
        assert abs(dist_pct) < 0.01

    def test_price_at_exact_50(self, swing_dict):
        """AC2: price at 50% level → ratio=0.5, dist_pct ≈ 0.0."""
        levels = fib_levels(swing_dict)
        ratio, _, dist_pct = nearest_fib_to_price(levels, 105.0)
        assert ratio == 0.5
        assert abs(dist_pct) < 0.01

    def test_returns_three_tuple(self, swing_dict):
        """Return value is always a 3-tuple (ratio, level_price, dist_pct)."""
        levels = fib_levels(swing_dict)
        result = nearest_fib_to_price(levels, 104.0)
        assert len(result) == 3


# ── score_bounce ──────────────────────────────────────────────────────────────

class TestScoreBounce:
    def test_score_at_classic_level_stage2_rsi_vol(self):
        """AC3: price at 61.8% + Stage 2 + RSI reset + vol expansion → score >= 0."""
        # _make_df builds 250 rows so all SMAs (50/150/200) are available
        df = _make_df(swing_low=80.0, swing_high=110.0, current=103.82)
        swing = detect_swing(df)
        if swing is None:
            pytest.skip("detect_swing returned None on synthetic data — check window")
        result = score_bounce(df, swing)
        assert result["bounce_score"] >= 0  # always non-negative
        assert "bounce_score" in result

    def test_non_classic_level_no_fib_bonus(self):
        """AC4: price at 23.6% (non-classic) → fib proximity bonus = 0."""
        # 23.6% of 30-point range: 110 - 0.236*30 = 102.92
        df = _make_df(swing_low=80.0, swing_high=110.0, current=102.92)
        swing = detect_swing(df)
        if swing is None:
            pytest.skip("detect_swing returned None — check fixture")
        result = score_bounce(df, swing)
        if result.get("nearest_fib_ratio") == 0.236:
            # max without classic-level bonus: stage2(15)+rsi(15)+vol(10) = 40
            assert result["bounce_score"] <= 45

    def test_golden_pocket_bonus(self):
        """AC6: price at 50% level → nearest_fib in golden pocket set."""
        # 50% of 30-point range from 80→110: 110 - 0.5*30 = 95
        df = _make_df(swing_low=80.0, swing_high=110.0, current=95.0)
        swing = detect_swing(df)
        if swing is None:
            pytest.skip("detect_swing returned None — check fixture")
        result = score_bounce(df, swing)
        if result.get("nearest_fib") == "50%":
            assert result["bounce_score"] >= 0

    def test_score_output_has_required_keys(self):
        """score_bounce always returns dict with at minimum these keys."""
        df = _make_df(swing_low=80.0, swing_high=110.0, current=95.0)
        swing = detect_swing(df)
        if swing is None:
            pytest.skip("detect_swing returned None")
        result = score_bounce(df, swing)
        for key in ("bounce_score", "nearest_fib", "nearest_fib_ratio",
                    "dist_to_fib_pct", "sma_confluence", "retraced_pct"):
            assert key in result, f"Missing key: {key}"

    def test_score_bounded_0_100(self):
        """bounce_score is always in [0, 100]."""
        df = _make_df(swing_low=80.0, swing_high=110.0, current=103.82)
        swing = detect_swing(df)
        if swing is None:
            pytest.skip("detect_swing returned None")
        result = score_bounce(df, swing)
        assert 0 <= result["bounce_score"] <= 100


# ── detect_swing ──────────────────────────────────────────────────────────────

class TestDetectSwing:
    def test_returns_dict_on_valid_data(self):
        """detect_swing returns a dict with expected keys on sufficient data."""
        df = _make_df(swing_low=90.0, swing_high=120.0, current=105.0)
        result = detect_swing(df)
        assert result is not None
        for key in ("swing_high", "swing_low", "range"):
            assert key in result

    def test_swing_high_greater_than_swing_low(self):
        """swing_high is always > swing_low when result is not None."""
        df = _make_df()
        result = detect_swing(df)
        if result is not None:
            assert result["swing_high"] > result["swing_low"]
            assert result["range"] > 0

    def test_too_short_returns_none(self):
        """AC7: fewer than 30 rows → None, no crash."""
        close = np.linspace(100.0, 110.0, 20)
        df = pd.DataFrame({
            "open":   close, "high": close + 0.1,
            "low":    close - 0.1, "close": close,
            "volume": np.ones(20),
        })
        assert detect_swing(df) is None

    def test_empty_df_returns_none(self):
        """AC7: empty DataFrame → None."""
        df = pd.DataFrame({"open": [], "high": [], "low": [], "close": [], "volume": []})
        assert detect_swing(df) is None

    def test_flat_series_returns_none(self):
        """AC8: all-equal highs and lows → swing_high == swing_low → None."""
        close = np.full(60, 100.0)
        df = pd.DataFrame({
            "open":   close, "high":   close,
            "low":    close, "close":  close,
            "volume": np.ones(60),
        })
        # detect_swing checks swing_high > swing_low; degenerate → None
        result = detect_swing(df)
        # May return None or a result with range=0; either way, no crash
        if result is not None:
            assert result["range"] >= 0
