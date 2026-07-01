# V15 — quantkit.calculate_supertrend (ATR-band trend filter) validation.
# Canonical scalping whipsaw filter: bullish (+1) when price rides above the
# sticky lower band, bearish (-1) when it breaks below the upper band.

import numpy as np
import pandas as pd
import pytest

from quantkit import calculate_supertrend


def _ohlcv(close, wick=0.5):
    close = np.asarray(close, dtype=float)
    idx = pd.date_range("2026-01-01", periods=len(close), freq="D")
    return pd.DataFrame(
        {"open": close, "high": close + wick, "low": close - wick, "close": close},
        index=idx,
    )


def test_returns_aligned_series():
    df = _ohlcv(np.linspace(100, 120, 60))
    line, direction = calculate_supertrend(df)
    assert len(line) == len(df) and len(direction) == len(df)
    assert list(line.index) == list(df.index)
    assert set(np.unique(direction.values)).issubset({-1, 1})


def test_sustained_uptrend_is_bullish():
    df = _ohlcv(np.linspace(100, 160, 80))
    _, direction = calculate_supertrend(df, period=10, multiplier=3.0)
    # After the ATR warmup the trend is unambiguously up.
    assert (direction.iloc[20:] == 1).all()


def test_sustained_downtrend_is_bearish():
    df = _ohlcv(np.linspace(160, 100, 80))
    _, direction = calculate_supertrend(df, period=10, multiplier=3.0)
    assert (direction.iloc[20:] == -1).all()


def test_line_trails_below_price_in_uptrend():
    close = np.linspace(100, 160, 80)
    df = _ohlcv(close)
    line, direction = calculate_supertrend(df, period=10, multiplier=3.0)
    bull = direction == 1
    # In a bullish regime the Supertrend line is a trailing stop below price.
    assert (line[bull].iloc[20:] < pd.Series(close, index=df.index)[bull].iloc[20:]).all()


def test_flips_on_trend_reversal():
    up = np.linspace(100, 160, 60)
    down = np.linspace(160, 110, 60)
    df = _ohlcv(np.concatenate([up, down]))
    _, direction = calculate_supertrend(df, period=10, multiplier=3.0)
    assert direction.iloc[40] == 1          # mid-uptrend
    assert direction.iloc[-1] == -1         # after reversal
    assert int((direction.diff().abs() > 0).sum()) >= 1


def test_tighter_multiplier_flips_at_least_as_often():
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1.0, 200))
    df = _ohlcv(close)
    _, d_tight = calculate_supertrend(df, period=10, multiplier=1.5)
    _, d_wide = calculate_supertrend(df, period=10, multiplier=4.0)
    flips_tight = int((d_tight.diff().abs() > 0).sum())
    flips_wide = int((d_wide.diff().abs() > 0).sum())
    # Wider ATR bands are stickier -> never more flips than tighter bands.
    assert flips_tight >= flips_wide
