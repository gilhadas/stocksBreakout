# EPIC-IND-001 — indicators.py RSI Wilder EMA + ATR + MACD math validation
# See: tests/stories/EPIC-IND-001.md

import numpy as np
import pandas as pd
import pytest

from indicators import calculate_rsi, calculate_atr, calculate_macd


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mixed_ohlcv() -> pd.DataFrame:
    """60-row sinusoidal close; deterministic via fixed seed, no NaN."""
    rng = np.random.default_rng(42)
    t = np.linspace(0, 4 * np.pi, 60)
    close = 100 + 10 * np.sin(t) + rng.normal(0, 0.2, 60)
    high  = close + np.abs(rng.normal(0.5, 0.1, 60))
    low   = close - np.abs(rng.normal(0.5, 0.1, 60))
    return pd.DataFrame({
        "open": close, "high": high, "low": low,
        "close": close, "volume": 1_000_000,
    })


@pytest.fixture
def all_gains_ohlcv() -> pd.DataFrame:
    """60-row monotonically rising close — RSI must be ~100."""
    close = np.linspace(100, 160, 60)
    return pd.DataFrame({
        "open": close, "high": close + 0.1,
        "low": close - 0.1, "close": close, "volume": 1_000_000,
    })


@pytest.fixture
def all_losses_ohlcv() -> pd.DataFrame:
    """60-row monotonically falling close — RSI must be exactly 0."""
    close = np.linspace(160, 100, 60)
    return pd.DataFrame({
        "open": close, "high": close + 0.1,
        "low": close - 0.1, "close": close, "volume": 1_000_000,
    })


# ── RSI ───────────────────────────────────────────────────────────────────────

class TestRSI:
    def test_rsi_strictly_bounded_mixed(self, mixed_ohlcv):
        """AC1: RSI within (0, 100) exclusively for a mixed-direction series."""
        rsi = calculate_rsi(mixed_ohlcv, period=14).dropna()
        assert len(rsi) > 0, "RSI returned all NaN"
        assert (rsi > 0).all(), f"RSI ≤ 0 found: {rsi[rsi <= 0]}"
        assert (rsi < 100).all(), f"RSI ≥ 100 found: {rsi[rsi >= 100]}"

    def test_rsi_near_100_all_gains(self, all_gains_ohlcv):
        """AC2: All-gains series → RSI approaches 100 (avg_loss→0 replaced by 1e-10)."""
        rsi = calculate_rsi(all_gains_ohlcv, period=14).dropna()
        # Due to 1e-10 floor on avg_loss, RSI is ~99.9999 not exactly 100.0
        assert (rsi >= 99.99).all(), f"Expected RSI≈100 for all gains, got: {rsi.describe()}"

    def test_rsi_equals_0_all_losses(self, all_losses_ohlcv):
        """AC3: All-losses series → RSI = 0.0 exactly (avg_gain = 0, RS = 0)."""
        rsi = calculate_rsi(all_losses_ohlcv, period=14).dropna()
        assert (rsi == 0.0).all(), f"Expected RSI=0 for all losses, got: {rsi.unique()}"

    def test_rsi_no_nan_after_warmup(self, mixed_ohlcv):
        """AC7: No NaN in RSI after warmup period (row >= 14)."""
        rsi = calculate_rsi(mixed_ohlcv, period=14)
        assert rsi.iloc[14:].isna().sum() == 0, "NaN found after warmup period"

    def test_rsi_empty_dataframe(self):
        """Edge: empty input returns empty or all-NaN series without crash."""
        empty = pd.DataFrame({"open": [], "high": [], "low": [], "close": [], "volume": []})
        try:
            rsi = calculate_rsi(empty, period=14)
            assert rsi.empty or rsi.isna().all()
        except Exception as exc:
            pytest.fail(f"calculate_rsi raised on empty DataFrame: {exc}")


# ── ATR ───────────────────────────────────────────────────────────────────────

class TestATR:
    def test_atr_matches_wilder_ewm(self, mixed_ohlcv):
        """AC4: ATR matches Wilder EWM (alpha=1/period, com=period-1) applied to True Range."""
        df = mixed_ohlcv.copy()
        atr_series = calculate_atr(df, period=14)

        # Manual True Range (same as implementation)
        prev_close = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)

        # Implementation uses ewm(com=period-1, adjust=False) — replicate exactly
        expected_atr = tr.ewm(com=13, adjust=False).mean()

        valid = ~(atr_series.isna() | expected_atr.isna())
        np.testing.assert_allclose(
            atr_series[valid].values, expected_atr[valid].values,
            rtol=1e-6, err_msg="ATR != Wilder EWM(TR, com=13)"
        )

    def test_atr_always_positive(self, mixed_ohlcv):
        """ATR must be positive for all valid rows."""
        atr = calculate_atr(mixed_ohlcv, period=14).dropna()
        assert (atr > 0).all()


# ── MACD ──────────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    """Reference EMA — same ewm(span, adjust=False) algorithm as indicators.py."""
    return series.ewm(span=span, adjust=False).mean()


class TestMACD:
    def test_macd_line_equals_ema_fast_minus_slow(self, mixed_ohlcv):
        """AC5: MACD line == EMA(12) - EMA(26) at every non-NaN row."""
        macd_line, signal_line, _ = calculate_macd(mixed_ohlcv, fast=12, slow=26, signal=9)

        close = mixed_ohlcv["close"]
        expected_macd = _ema(close, 12) - _ema(close, 26)
        valid = ~(macd_line.isna() | expected_macd.isna())
        np.testing.assert_allclose(
            macd_line[valid].values, expected_macd[valid].values,
            rtol=1e-6, err_msg="MACD line != EMA_fast - EMA_slow"
        )

    def test_signal_equals_ema9_of_macd(self, mixed_ohlcv):
        """AC6: Signal line == EMA(9) of MACD line at every non-NaN row."""
        macd_line, signal_line, _ = calculate_macd(mixed_ohlcv, fast=12, slow=26, signal=9)

        expected_signal = _ema(macd_line, 9)
        valid = ~(signal_line.isna() | expected_signal.isna())
        np.testing.assert_allclose(
            signal_line[valid].values, expected_signal[valid].values,
            rtol=1e-6, err_msg="Signal line != EMA9(MACD)"
        )

    def test_histogram_equals_macd_minus_signal(self, mixed_ohlcv):
        """Histogram = MACD - Signal at every row."""
        macd_line, signal_line, histogram = calculate_macd(
            mixed_ohlcv, fast=12, slow=26, signal=9
        )
        valid = ~(histogram.isna())
        np.testing.assert_allclose(
            histogram[valid].values,
            (macd_line - signal_line)[valid].values,
            rtol=1e-6,
        )

    def test_macd_no_nan_after_warmup(self, mixed_ohlcv):
        """AC7: No NaN after warmup — MACD valid from row 25, signal from row 33."""
        macd_line, signal_line, _ = calculate_macd(mixed_ohlcv, fast=12, slow=26, signal=9)
        assert macd_line.iloc[25:].isna().sum() == 0, "NaN in MACD after warmup"
        assert signal_line.iloc[33:].isna().sum() == 0, "NaN in signal line after warmup"
