"""
Tests for utils.pace_adjust_volume_ratio() — the intraday Vol_Ratio pacing
fix (2026-09-03).

Live intraday scans (docker/crontab's 9:35 ET swing scan is 5 minutes after
the open) fetch a daily bar via yfinance whose last row is today's still-
forming bar. calculate_all_indicators() computes Vol_Ratio as raw volume
over a 20-day average — correct for a completed bar, but a structural
mismatch intraday: a few minutes of volume against a denominator built from
full trading days. Every detector decides its signal off exactly this value
(`vol_confirm = latest['Vol_Ratio'] >= vol_thresh`), so an early scan either
suppresses real breakouts (numerator understated) or, for a genuine opening
gap-and-go, can overstate it.

Unlike close_basis_history, this function must NOT drop today's row — that
would silence new-signal detection until 15:30 ET on every intraday scan,
defeating the point of an early scan. Only Vol_Ratio is rescaled; price and
candle-shape checks keep reading the real, developing bar.
"""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from utils import pace_adjust_volume_ratio


def _df(dates, volumes, vol_ma, vol_ratio=None):
    n = len(dates)
    if vol_ratio is None:
        vol_ratio = [v / m if m else np.nan for v, m in zip(volumes, vol_ma)]
    return pd.DataFrame({
        'open':   [100.0] * n,
        'high':   [101.0] * n,
        'low':    [99.0] * n,
        'close':  [100.0] * n,
        'volume': volumes,
        'Vol_MA': vol_ma,
        'Vol_Ratio': vol_ratio,
    }, index=pd.DatetimeIndex(pd.to_datetime(dates)))


TODAY = '2026-09-03'   # a Thursday
YDAY  = '2026-09-02'
DAYS  = ['2026-08-28', '2026-09-01', YDAY, TODAY]


# ─── Guard clauses: nothing to project, pass through unchanged ───────────────

def test_none_and_empty_pass_through():
    now = datetime(2026, 9, 3, 9, 35)
    assert pace_adjust_volume_ratio(None, now) is None
    empty = _df([], [], [])
    assert pace_adjust_volume_ratio(empty, now) is empty


def test_missing_columns_pass_through():
    now = datetime(2026, 9, 3, 9, 35)
    df = _df(DAYS, [1_000_000] * 4, [800_000] * 4)
    no_vol_ma = df.drop(columns=['Vol_MA'])
    assert pace_adjust_volume_ratio(no_vol_ma, now) is no_vol_ma
    no_ratio = df.drop(columns=['Vol_Ratio'])
    assert pace_adjust_volume_ratio(no_ratio, now) is no_ratio


def test_last_bar_not_today_passes_through():
    # Last bar is yesterday (pre-open cache, or data hasn't rolled to today) —
    # nothing to project.
    df = _df(DAYS[:-1], [1_000_000, 1_100_000, 900_000], [800_000] * 3)
    now = datetime(2026, 9, 3, 9, 35)
    out = pace_adjust_volume_ratio(df, now)
    assert out is df


def test_after_close_bar_is_final_unchanged():
    df = _df(DAYS, [1_000_000] * 4, [800_000] * 4)
    original_ratio = float(df['Vol_Ratio'].iloc[-1])
    for hh, mm in [(16, 0), (16, 30), (20, 0)]:
        out = pace_adjust_volume_ratio(df, datetime(2026, 9, 3, hh, mm))
        assert out is df
        assert float(out['Vol_Ratio'].iloc[-1]) == original_ratio


def test_before_open_unchanged():
    df = _df(DAYS, [1_000_000] * 4, [800_000] * 4)
    original_ratio = float(df['Vol_Ratio'].iloc[-1])
    out = pace_adjust_volume_ratio(df, datetime(2026, 9, 3, 9, 0))
    assert out is df
    assert float(out['Vol_Ratio'].iloc[-1]) == original_ratio


def test_too_few_bars_passes_through():
    df = _df([TODAY], [1_000_000], [np.nan])  # no prior bar for a baseline
    now = datetime(2026, 9, 3, 9, 35)
    out = pace_adjust_volume_ratio(df, now)
    assert out is df


def test_zero_or_nan_baseline_passes_through():
    now = datetime(2026, 9, 3, 9, 35)
    zero_baseline = _df(DAYS, [1_000_000] * 4, [800_000, 800_000, 0.0, 800_000])
    out = pace_adjust_volume_ratio(zero_baseline, now)
    assert out is zero_baseline

    nan_baseline = _df(DAYS, [1_000_000] * 4, [800_000, 800_000, np.nan, 800_000])
    out2 = pace_adjust_volume_ratio(nan_baseline, now)
    assert out2 is nan_baseline


# ─── Core projection math ──────────────────────────────────────────────────

def test_midsession_projects_against_yesterdays_clean_baseline():
    # Yesterday's Vol_MA (clean 20-day trailing avg) = 800,000.
    # Today (still forming) raw volume = 100,000, 65 minutes into the session
    # (10:35 ET) out of 390 session minutes -> projection = 390/65 = 6.0,
    # capped at the default max_projection=4.0.
    df = _df(DAYS, [900_000, 950_000, 800_000, 100_000],
             [780_000, 790_000, 800_000, np.nan])
    now = datetime(2026, 9, 3, 10, 35)
    out = pace_adjust_volume_ratio(df, now)
    assert out is not df  # returns a copy, not a mutation of the caller's df
    expected = (100_000 * 4.0) / 800_000  # capped projection
    assert out['Vol_Ratio'].iloc[-1] == pytest.approx(expected)
    # Earlier rows and the raw volume column are untouched
    assert out['Vol_Ratio'].iloc[-2] == df['Vol_Ratio'].iloc[-2]
    assert out['volume'].iloc[-1] == 100_000


def test_projection_uncapped_when_below_ceiling():
    # 195 minutes elapsed (12:45 ET) -> raw projection = 390/195 = 2.0,
    # comfortably under the 4.0 cap, so the exact value should be used.
    df = _df(DAYS, [900_000, 950_000, 800_000, 300_000],
              [780_000, 790_000, 800_000, np.nan])
    now = datetime(2026, 9, 3, 12, 45)
    out = pace_adjust_volume_ratio(df, now)
    expected = (300_000 * 2.0) / 800_000
    assert out['Vol_Ratio'].iloc[-1] == pytest.approx(expected)


def test_custom_max_projection_respected():
    df = _df(DAYS, [900_000, 950_000, 800_000, 50_000],
              [780_000, 790_000, 800_000, np.nan])
    now = datetime(2026, 9, 3, 9, 35)  # 5 min in -> raw projection = 78x
    out = pace_adjust_volume_ratio(df, now, max_projection=2.5)
    expected = (50_000 * 2.5) / 800_000
    assert out['Vol_Ratio'].iloc[-1] == pytest.approx(expected)


def test_elapsed_floor_prevents_division_blowup_at_open():
    # At exactly 9:30:00 the elapsed-minutes floor (1.0) still applies —
    # must not divide by zero or blow up beyond the cap.
    df = _df(DAYS, [900_000, 950_000, 800_000, 10_000],
              [780_000, 790_000, 800_000, np.nan])
    now = datetime(2026, 9, 3, 9, 30, 0)
    out = pace_adjust_volume_ratio(df, now)
    expected = (10_000 * 4.0) / 800_000  # still capped at max_projection
    assert out['Vol_Ratio'].iloc[-1] == pytest.approx(expected)
