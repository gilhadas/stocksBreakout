# EPIC-TEN-001 — quantkit/tension.py Tension Index composite validation
# Compression ("point of silence") + Value-Area volume consensus +
# market/sector confirmation (rolling corr/beta) + fractal (LTF↔daily) alignment.

import numpy as np
import pandas as pd
import pytest

from quantkit.tension import (
    compute_tension_index,
    compression_score,
    volume_consensus_score,
    confirmation_score,
    fractal_alignment_score,
    TensionConfig,
)

DEFAULT = TensionConfig()


# ── Fixtures / builders ─────────────────────────────────────────────────────────
def _ohlcv(close, *, vol=1_000_000.0, wick=0.4, seed=42, index=True):
    """Build an OHLCV frame from a close array (deterministic wicks/volume)."""
    rng = np.random.default_rng(seed)
    close = np.asarray(close, dtype=float)
    n = len(close)
    high = close + np.abs(rng.normal(wick, 0.05, n))
    low = close - np.abs(rng.normal(wick, 0.05, n))
    volume = np.full(n, vol) if np.isscalar(vol) else np.asarray(vol, dtype=float)
    df = pd.DataFrame({"open": close, "high": high, "low": low,
                       "close": close, "volume": volume})
    if index:
        df.index = pd.date_range("2024-01-01", periods=n, freq="B")
    return df


@pytest.fixture
def compressed_df():
    """A gradual volatility taper into a tight coil (VCP-like, no breakout).

    Amplitude (both close swing and bar range) decays monotonically, so BB
    width stays below its own rolling average — the project's definition of
    contraction — for a sustained run of bars, and ATR shrinks too. This is the
    textbook 'point of silence' that precedes a breakout.
    """
    rng = np.random.default_rng(7)
    n = 200
    amp = np.linspace(6.0, 0.08, n)                       # decaying amplitude
    close = 100 + amp * np.sin(np.linspace(0, 9 * np.pi, n)) + rng.normal(0, 0.04, n)
    rng2 = np.random.default_rng(8)
    rng3 = np.random.default_rng(9)
    span = 0.05 + 0.3 * amp                                # bar range shrinks with amp
    high = close + np.abs(rng2.normal(span, 0.02))
    low = close - np.abs(rng3.normal(span, 0.02))
    df = pd.DataFrame({"open": close, "high": high, "low": low,
                       "close": close, "volume": 1_000_000.0})
    df.index = pd.date_range("2024-01-01", periods=n, freq="B")
    return df


@pytest.fixture
def volatile_df():
    """Volatility expanding into the last bar → low compression."""
    rng = np.random.default_rng(11)
    amp = np.linspace(0.5, 12, 200)
    close = 100 + amp * np.sin(np.linspace(0, 10 * np.pi, 200)) + rng.normal(0, 1, 200)
    return _ohlcv(close)


@pytest.fixture
def uptrend_market():
    """SPY + sector ETF in a steady uptrend, datetime-indexed for 200 B-days."""
    rng = np.random.default_rng(3)
    idx = pd.date_range("2024-01-01", periods=200, freq="B")
    spy = _ohlcv(np.linspace(400, 470, 200) + rng.normal(0, 1, 200))
    sect = _ohlcv(np.linspace(100, 135, 200) + rng.normal(0, 0.5, 200), vol=5e6)
    spy.index = idx
    sect.index = idx
    return spy, sect


# ── Compression ──────────────────────────────────────────────────────────────────
class TestCompression:
    def test_compressed_tail_high_C(self, compressed_df):
        out = compression_score(compressed_df, DEFAULT)
        assert out["C"] >= 0.6, f"expected high compression, got {out}"
        assert out["bb_comp"] >= 0.7
        assert out["point_of_silence"] is True

    def test_volatile_low_C(self, volatile_df):
        out = compression_score(volatile_df, DEFAULT)
        assert out["C"] <= 0.4, f"expected low compression, got {out}"
        assert out["point_of_silence"] is False

    def test_compressed_beats_volatile(self, compressed_df, volatile_df):
        assert compression_score(compressed_df)["C"] > compression_score(volatile_df)["C"]


# ── Volume consensus vs the Value Area ────────────────────────────────────────────
class TestVolumeConsensus:
    def test_breakout_above_vah_high_V(self):
        # Long base near 100, then a strong push above the value area on 3× volume,
        # closing near the high of the bar.
        base = np.concatenate([np.full(80, 100.0), np.linspace(100, 112, 5)])
        df = _ohlcv(base, vol=np.concatenate([np.full(80, 1e6), np.full(5, 3e6)]))
        # Force the last bar to close in its upper third
        df.loc[df.index[-1], "low"] = df["close"].iloc[-1] - 2.0
        df.loc[df.index[-1], "high"] = df["close"].iloc[-1] + 0.1
        out = volume_consensus_score(df)
        assert out["va_accept"] == 1.0
        assert out["vol_expand"] >= 0.9
        assert out["close_pos"] >= 0.8
        assert out["V"] >= 0.6

    def test_wick_rejection_low_close_pos(self):
        base = np.concatenate([np.full(80, 100.0), np.linspace(100, 108, 5)])
        df = _ohlcv(base, vol=1e6)
        # Last bar: long upper wick, close back near the low (rejection)
        last = df.index[-1]
        df.loc[last, "high"] = df["close"].iloc[-1] + 5.0
        df.loc[last, "low"] = df["close"].iloc[-1] - 0.2
        out = volume_consensus_score(df)
        assert out["close_pos"] <= 0.4


# ── Confirmation (rolling correlation / beta / RS) ────────────────────────────────
class TestConfirmation:
    def test_rs_and_market_ok_high_F(self, uptrend_market):
        spy, sect = uptrend_market
        # Ticker that strongly outperforms a rising market
        ticker = _ohlcv(np.linspace(50, 110, 200))
        ticker.index = spy.index
        out = confirmation_score(ticker, spy_df=spy, sector_df=sect,
                                 regime="NORMAL", spy_perf=0.02)
        assert out["rs_ok"] is True
        assert out["market_ok"] is True
        assert out["F"] >= 0.6

    def test_risk_off_flag_set(self, uptrend_market):
        spy, sect = uptrend_market
        ticker = _ohlcv(np.linspace(50, 110, 200))
        ticker.index = spy.index
        out = confirmation_score(ticker, spy_df=spy, sector_df=sect,
                                 regime="BEARISH", spy_perf=0.02)
        assert out["market_risk_off"] is True
        assert out["market_ok"] is False


# ── Fractal alignment (LTF ↔ daily) ───────────────────────────────────────────────
class TestFractal:
    def test_uptrend_breakout_no_contradiction(self):
        df = _ohlcv(np.linspace(60, 120, 200))  # clean daily uptrend
        # Ensure a breakout bar
        df.loc[df.index[-1], "close"] = df["high"].iloc[-30:-1].max() + 2.0
        out = fractal_alignment_score(df, daily_df=None)
        assert out["htf_trend"] >= 0.5
        assert out["fractal_contradiction"] is False

    def test_downtrend_breakout_contradiction(self):
        # Daily downtrend, but the last bar pops above the recent (low) high.
        df = _ohlcv(np.linspace(200, 100, 200))
        df.loc[df.index[-1], "close"] = df["high"].iloc[-30:-1].max() + 1.0
        out = fractal_alignment_score(df, daily_df=None)
        assert out["ltf_break_up"] is True
        assert out["fractal_contradiction"] is True


# ── Composite + gates + safety ────────────────────────────────────────────────────
class TestComposite:
    def test_bounds_random(self):
        rng = np.random.default_rng(99)
        df = _ohlcv(100 + np.cumsum(rng.normal(0, 1, 200)))
        ti = compute_tension_index(df)["tension_index"]
        assert 0.0 <= ti <= 1.0

    def test_fractal_contradiction_penalizes(self, uptrend_market):
        spy, sect = uptrend_market
        # Ticker in a daily downtrend that prints a breakout bar → contradiction gate
        df = _ohlcv(np.linspace(200, 100, 200))
        df.loc[df.index[-1], "close"] = df["high"].iloc[-30:-1].max() + 1.0
        df.index = spy.index
        res = compute_tension_index(df, spy_df=spy, sector_df=sect, regime="NORMAL")
        assert res["fractal_contradiction"] is True
        # Raw blend without the gate must exceed the gated index
        cfg = DEFAULT
        raw = (cfg.w_compression * res["compression"] + cfg.w_volume * res["volume_consensus"]
               + cfg.w_confirmation * res["confirmation"] + cfg.w_fractal * res["fractal_alignment"])
        assert res["tension_index"] <= raw + 1e-9
        assert res["tension_index"] == pytest.approx(min(1.0, raw * cfg.gate_fractal), abs=1e-3)

    def test_state_releasing_on_breakout(self):
        base = np.concatenate([np.full(120, 100.0) + np.random.default_rng(1).normal(0, 0.1, 120),
                               np.linspace(100, 110, 5)])
        df = _ohlcv(base, vol=np.concatenate([np.full(120, 1e6), np.full(5, 3e6)]))
        df.loc[df.index[-1], "low"] = df["close"].iloc[-1] - 1.0
        df.loc[df.index[-1], "high"] = df["close"].iloc[-1] + 0.1
        res = compute_tension_index(df)
        assert res["breakout_bar"] is True
        assert res["state"] == "RELEASING"

    def test_short_history_safe(self):
        df = _ohlcv(np.linspace(100, 101, 8))
        res = compute_tension_index(df)
        assert res["tension_index"] == 0.0
        assert res["state"] == "NONE"

    def test_all_nan_safe(self):
        df = _ohlcv(np.linspace(100, 110, 200))
        df["close"] = np.nan
        res = compute_tension_index(df)  # must not raise
        assert res["tension_index"] == 0.0

    def test_missing_context_degrades(self):
        # No spy/sector/regime: confirmation falls back to a neutral baseline,
        # the index is still computed and bounded.
        df = _ohlcv(np.linspace(80, 120, 200))
        res = compute_tension_index(df)
        assert 0.0 <= res["tension_index"] <= 1.0
        assert res["confirmation"] >= 0.0
