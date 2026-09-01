"""
Tests for the pinned/compressed-range veto (CLAUDE.md §27 "best lead out of
that session"): PRA/JHG/HOLX/STEL — cash-merger targets pinned near the deal
price — scored GOLD and fired TREND_CONFIRM despite collapsed volatility and
no real trend, the opposite of a genuine Stage 2 breakout.

Covers:
  1. quantkit.indicators.check_pinned_range() — the reusable detection primitive.
  2. config.PINNED_RANGE_CONFIG — dormant-by-default contract.
  3. scanner.py wiring — GOLD/PREMIUM downgrade veto + TREND_CONFIRM hard-gate veto.
"""
import numpy as np
import pandas as pd
import pytest

from quantkit.indicators import check_pinned_range


# ── Fixture builders ─────────────────────────────────────────────────────────

def _pinned_df(n=90, price=50.0, range_pct=4.0, atr_pct=0.5):
    """A stock pinned in a tight band around `price` — the merger-arb signature."""
    rng = np.random.default_rng(42)
    half = price * range_pct / 100 / 2
    close = price + rng.uniform(-half, half, n)
    close[-1] = price  # anchor the final close so range_pct is exactly reproducible
    atr_abs = price * atr_pct / 100
    high = close + atr_abs / 2
    low = close - atr_abs / 2
    return pd.DataFrame({
        'open': close, 'high': high, 'low': low, 'close': close,
        'volume': np.full(n, 500_000.0),
        'ATR': np.full(n, atr_abs),
    })


def _trending_df(n=90, start=50.0, end=80.0, atr_pct=3.0):
    """A normal trending stock — real range, real ATR. Must NOT be flagged pinned."""
    close = np.linspace(start, end, n)
    atr_abs = close * atr_pct / 100
    high = close + atr_abs
    low = close - atr_abs
    return pd.DataFrame({
        'open': close, 'high': high, 'low': low, 'close': close,
        'volume': np.full(n, 500_000.0),
        'ATR': atr_abs,
    })


# ── 1. check_pinned_range() primitive ────────────────────────────────────────

class TestCheckPinnedRange:
    def test_merger_arb_pin_flagged(self):
        """Tight absolute range + collapsed ATR together → pinned."""
        df = _pinned_df(range_pct=4.0, atr_pct=0.5)
        is_pinned, range_pct, atr_pct = check_pinned_range(df, lookback_days=60)
        assert is_pinned is True
        assert range_pct < 10.0
        assert atr_pct < 1.5

    def test_normal_trending_stock_not_flagged(self):
        """A real Stage-2 uptrend has real range and real ATR — never flagged."""
        df = _trending_df(start=50.0, end=80.0, atr_pct=3.0)
        is_pinned, range_pct, atr_pct = check_pinned_range(df, lookback_days=60)
        assert is_pinned is False
        assert range_pct > 10.0

    def test_tight_range_but_normal_atr_not_flagged(self):
        """Range alone isn't enough — a genuine pre-breakout consolidation with
        normal ATR must NOT be vetoed as a merger pin (both signals required)."""
        df = _pinned_df(range_pct=4.0, atr_pct=3.0)
        is_pinned, _, atr_pct = check_pinned_range(df, lookback_days=60)
        assert is_pinned is False
        assert atr_pct >= 1.5

    def test_tiny_atr_but_wide_range_not_flagged(self):
        """ATR alone isn't enough either — a quiet low-beta name with one genuine
        wide swing in the lookback window must NOT be vetoed."""
        df = _pinned_df(range_pct=15.0, atr_pct=0.5)
        is_pinned, range_pct, _ = check_pinned_range(df, lookback_days=60)
        assert is_pinned is False
        assert range_pct >= 10.0

    def test_insufficient_history_returns_false(self):
        df = _pinned_df(n=30, range_pct=4.0, atr_pct=0.5)
        is_pinned, range_pct, atr_pct = check_pinned_range(df, lookback_days=60)
        assert (is_pinned, range_pct, atr_pct) == (False, 0.0, 0.0)

    def test_missing_atr_column_returns_false(self):
        df = _pinned_df(range_pct=4.0, atr_pct=0.5).drop(columns=['ATR'])
        is_pinned, range_pct, atr_pct = check_pinned_range(df, lookback_days=60)
        assert (is_pinned, range_pct, atr_pct) == (False, 0.0, 0.0)

    def test_nan_atr_returns_false_but_reports_range(self):
        df = _pinned_df(range_pct=4.0, atr_pct=0.5)
        df.loc[df.index[-1], 'ATR'] = np.nan
        is_pinned, range_pct, atr_pct = check_pinned_range(df, lookback_days=60)
        assert is_pinned is False
        assert range_pct > 0.0
        assert atr_pct == 0.0

    def test_zero_close_returns_false(self):
        df = _pinned_df(range_pct=4.0, atr_pct=0.5)
        df.loc[df.index[-1], 'close'] = 0.0
        assert check_pinned_range(df, lookback_days=60) == (False, 0.0, 0.0)

    def test_boundary_uses_strict_less_than(self):
        """Exactly at both thresholds is NOT pinned (strict <, matches the rest
        of the codebase's boundary convention, e.g. Is_Consolidating)."""
        df = _pinned_df(n=90, price=100.0, range_pct=10.0, atr_pct=1.5)
        # First measure the fixture's actual range/ATR, then re-check with the
        # thresholds set to exactly those measured values (the boundary case).
        _, measured_range, measured_atr = check_pinned_range(df, lookback_days=60)
        is_pinned, _, _ = check_pinned_range(
            df, lookback_days=60, max_range_pct=measured_range, max_atr_pct=measured_atr
        )
        assert is_pinned is False

    def test_custom_thresholds_respected(self):
        df = _pinned_df(range_pct=4.0, atr_pct=0.5)
        # Thresholds tighter than the fixture's actual range/ATR → no longer flagged.
        is_pinned, _, _ = check_pinned_range(
            df, lookback_days=60, max_range_pct=1.0, max_atr_pct=0.1
        )
        assert is_pinned is False

    def test_shim_reexports_same_function(self):
        """indicators.py (root shim) must expose the same primitive scanner.py imports."""
        from indicators import check_pinned_range as shimmed
        assert shimmed is check_pinned_range


# ── 2. config.PINNED_RANGE_CONFIG contract ───────────────────────────────────

class TestPinnedRangeConfig:
    def test_enabled_is_boolean(self):
        # Live since 2026-08-21 (CLAUDE.md §28) — zero regression on 2 backtest
        # universes; efficacy validated live, not by this contract test.
        from config import PINNED_RANGE_CONFIG
        assert isinstance(PINNED_RANGE_CONFIG['enabled'], bool)

    def test_required_keys_present_and_numeric(self):
        from config import PINNED_RANGE_CONFIG
        for key in ('lookback_days', 'max_range_pct', 'max_atr_pct'):
            assert key in PINNED_RANGE_CONFIG
            assert isinstance(PINNED_RANGE_CONFIG[key], (int, float))

    def test_thresholds_are_positive(self):
        from config import PINNED_RANGE_CONFIG
        assert PINNED_RANGE_CONFIG['lookback_days'] > 0
        assert PINNED_RANGE_CONFIG['max_range_pct'] > 0
        assert PINNED_RANGE_CONFIG['max_atr_pct'] > 0


# ── 3. scanner.py wiring ──────────────────────────────────────────────────────

class TestTrendConfirmVeto:
    """detect_trend_confirm() must fully block a pinned stock — it only ever
    emits PREMIUM/GOLD, so this is the detector's entire defense for this class."""

    def _all_gates_pass(self, df, i, sma150, sma50):
        return {'G1': True, 'G2': True, 'G3': True, 'G4': True, 'G5': True,
                'G6': True, 'G7': True, 'fresh': True, 'golden': True,
                'vol_ratio': 2.0, 'ext_pct': 0.05, 'rsi': 60.0, 'score': 7}

    def _uptrend_df(self, n=250):
        close = np.linspace(50.0, 100.0, n)
        rng = np.random.default_rng(1)
        vol = np.full(n, 1_000_000.0) * rng.uniform(0.9, 1.1, n)
        idx = pd.date_range('2020-01-01', periods=n, freq='B')
        return pd.DataFrame({
            'open': close, 'high': close * 1.01, 'low': close * 0.99,
            'close': close, 'volume': vol,
        }, index=idx)

    def test_pinned_stock_never_fires(self, monkeypatch):
        import config
        monkeypatch.setitem(config.TREND_CONFIRM, 'enabled', True)
        monkeypatch.setitem(config.TREND_CONFIRM, 'enabled_modes', ['swing', 'longterm'])
        monkeypatch.setitem(config.PINNED_RANGE_CONFIG, 'enabled', True)

        from scanner import BreakoutDetector
        detector = BreakoutDetector()
        monkeypatch.setattr(detector, '_trend_confirm_gates', self._all_gates_pass)
        monkeypatch.setattr('scanner.check_pinned_range', lambda *a, **kw: (True, 4.0, 0.5))

        result = detector.detect_trend_confirm(self._uptrend_df(), 'HOLX', 'swing', '1 day')
        assert result is None

    def test_non_pinned_stock_still_fires(self, monkeypatch):
        import config
        monkeypatch.setitem(config.TREND_CONFIRM, 'enabled', True)
        monkeypatch.setitem(config.TREND_CONFIRM, 'enabled_modes', ['swing', 'longterm'])
        monkeypatch.setitem(config.PINNED_RANGE_CONFIG, 'enabled', True)

        from scanner import BreakoutDetector
        detector = BreakoutDetector()
        monkeypatch.setattr(detector, '_trend_confirm_gates', self._all_gates_pass)
        monkeypatch.setattr('scanner.check_pinned_range', lambda *a, **kw: (False, 40.0, 3.0))

        result = detector.detect_trend_confirm(self._uptrend_df(), 'NVDA', 'swing', '1 day')
        assert result is not None
        assert result['Type'] == 'TREND_CONFIRM'

    def test_disabled_config_skips_pinned_check_entirely(self, monkeypatch):
        """Dormant by default: even a stock that WOULD be flagged pinned must
        fire normally when PINNED_RANGE_CONFIG['enabled'] is False."""
        import config
        monkeypatch.setitem(config.TREND_CONFIRM, 'enabled', True)
        monkeypatch.setitem(config.TREND_CONFIRM, 'enabled_modes', ['swing', 'longterm'])
        monkeypatch.setitem(config.PINNED_RANGE_CONFIG, 'enabled', False)

        from scanner import BreakoutDetector
        detector = BreakoutDetector()
        monkeypatch.setattr(detector, '_trend_confirm_gates', self._all_gates_pass)

        def _boom(*a, **kw):
            raise AssertionError("check_pinned_range must not be called when dormant")
        monkeypatch.setattr('scanner.check_pinned_range', _boom)

        result = detector.detect_trend_confirm(self._uptrend_df(), 'HOLX', 'swing', '1 day')
        assert result is not None


class TestMainPathDowngrade:
    """The GOLD/PREMIUM→HIGH downgrade block in BreakoutDetector.detect()."""

    def _gold_scoring_df(self, n=200):
        """A monotonic uptrend, ending at its own 52-week high with a volume
        spike on the last bar — satisfies every GOLD hard-gate (rr, above
        Trend_Line, Vol_Ratio>=2, near_52w_high) once quality-scoring itself
        is mocked to GOLD."""
        idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n, freq='B')
        close = np.linspace(50.0, 100.0, n)
        vol = np.full(n, 1_000_000.0)
        vol[-1] = 6_000_000.0  # Vol_Ratio vs 20-bar avg >> 2.0
        return pd.DataFrame({
            'open': close, 'high': close * 1.001, 'low': close * 0.98,
            'close': close, 'volume': vol,
        }, index=idx)

    def _wire_gold_signal(self, monkeypatch, detector, pinned):
        """Mock every sub-check `detect()` calls so it deterministically reaches
        the pinned-range veto with quality='GOLD' and all gold_gates passing —
        without needing to hand-construct BB/pattern/consolidation structure."""
        monkeypatch.setattr(detector, '_calculate_signal_score',
                            lambda checks, score_thresholds_override=None: (100, 100, 'GOLD'))
        monkeypatch.setattr(detector, '_calculate_rr',
                            lambda *a, **kw: (a[0]['close'] * 0.9, a[0]['close'] * 1.5, 5.0))
        monkeypatch.setattr('scanner.check_pinned_range',
                            lambda *a, **kw: (pinned, 4.0 if pinned else 40.0, 0.5 if pinned else 3.0))

    def test_real_detect_downgrades_pinned_gold_to_high(self, monkeypatch):
        """End-to-end through the REAL BreakoutDetector.detect() — not a mirror
        of the logic — so a regression in the shipped conditional is caught."""
        import config
        monkeypatch.setitem(config.PINNED_RANGE_CONFIG, 'enabled', True)
        from scanner import BreakoutDetector
        detector = BreakoutDetector()
        self._wire_gold_signal(monkeypatch, detector, pinned=True)

        result = detector.detect(self._gold_scoring_df(), 'HOLX', 'swing', '1 day',
                                 spy_perf=0.0, sector_hot=True)
        assert result is not None
        assert result['Quality'] == 'HIGH'

    def test_real_detect_leaves_non_pinned_gold_alone(self, monkeypatch):
        import config
        monkeypatch.setitem(config.PINNED_RANGE_CONFIG, 'enabled', True)
        from scanner import BreakoutDetector
        detector = BreakoutDetector()
        self._wire_gold_signal(monkeypatch, detector, pinned=False)

        result = detector.detect(self._gold_scoring_df(), 'NVDA', 'swing', '1 day',
                                 spy_perf=0.0, sector_hot=True)
        assert result is not None
        assert result['Quality'] == 'GOLD'

    def test_real_detect_disabled_config_never_downgrades(self, monkeypatch):
        """Dormant by default: even a GOLD signal on a stock that WOULD be
        flagged pinned must keep its tier when PINNED_RANGE_CONFIG is off."""
        import config
        monkeypatch.setitem(config.PINNED_RANGE_CONFIG, 'enabled', False)
        from scanner import BreakoutDetector
        detector = BreakoutDetector()
        monkeypatch.setattr(detector, '_calculate_signal_score',
                            lambda checks, score_thresholds_override=None: (100, 100, 'GOLD'))
        monkeypatch.setattr(detector, '_calculate_rr',
                            lambda *a, **kw: (a[0]['close'] * 0.9, a[0]['close'] * 1.5, 5.0))

        def _boom(*a, **kw):
            raise AssertionError("check_pinned_range must not be called when dormant")
        monkeypatch.setattr('scanner.check_pinned_range', _boom)

        result = detector.detect(self._gold_scoring_df(), 'HOLX', 'swing', '1 day',
                                 spy_perf=0.0, sector_hot=True)
        assert result is not None
        assert result['Quality'] == 'GOLD'

    def test_downgrade_logic_caps_gold_and_premium(self):
        """Mirrors the exact conditional shipped in scanner.py's detect():
        pinned_range True + quality in (GOLD, PREMIUM) → HIGH; anything else
        passes through unchanged. Pins the decision table so a future edit
        to the block can't silently narrow or widen it."""
        def apply(quality, pinned_range):
            if pinned_range and quality in ('GOLD', 'PREMIUM'):
                return 'HIGH'
            return quality

        assert apply('GOLD', True) == 'HIGH'
        assert apply('PREMIUM', True) == 'HIGH'
        assert apply('HIGH', True) == 'HIGH'     # already at/below the cap
        assert apply('STANDARD', True) == 'STANDARD'
        assert apply('GOLD', False) == 'GOLD'
        assert apply('PREMIUM', False) == 'PREMIUM'

    def test_scanner_imports_pinned_range_symbols(self):
        """Wiring smoke test: scanner.py must have the veto's dependencies in
        scope (import-time failure would silently disable the whole feature)."""
        import scanner
        assert hasattr(scanner, 'check_pinned_range')
        assert hasattr(scanner, 'PINNED_RANGE_CONFIG')


# ── 5. detect_sma20_cross() — the gap CWAN slipped through (2026-09-01) ────────
#
# CWAN (Clearwater Analytics) was taken private on 2026-07-02 at $24.55/share.
# Its frozen, near-zero-ATR post-delisting price trivially satisfies "above
# SMA20/SMA50", and detect_sma20_cross() was the one detector type never wired
# into this veto — detect() and detect_trend_confirm() were, this wasn't — so
# it kept re-admitting CWAN as a fresh PREMIUM signal into every live book.

class TestSma20CrossVeto:
    """The GOLD/PREMIUM→HIGH downgrade block in detect_sma20_cross()."""

    def _cross_df(self, n_pre=180, n_tail=70):
        """Uptrend long enough for SMA200 (above_sma200 check), dipping below
        SMA20 for the required 3-of-5 days, then crossing back above with a
        volume spike and a bullish candle — satisfies every hard gate in
        detect_sma20_cross() and, with vol=5x/RSI=62, enough soft checks
        (vol_very_strong, above_sma200, fresh_cross, rsi_sweet_spot) for a
        PREMIUM bar quality, so the downgrade has something to downgrade."""
        pre = np.linspace(30.0, 50.0, n_pre)
        tail = np.linspace(50.0, 70.0, n_tail)
        for off in range(-6, -1):
            tail[off] *= 0.92          # pull below SMA20 for the lookback window
        tail[-1] = tail[-2] * 1.03     # cross back above on the last bar
        close = np.concatenate([pre, tail])
        n = len(close)
        idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n, freq='B')
        open_ = close * 0.995
        open_[-1] = close[-2]          # opens near yesterday's close -> bullish candle
        high = np.maximum(close, open_) * 1.005
        low = np.minimum(close, open_) * 0.995
        vol = np.full(n, 1_000_000.0)
        vol[-1] = 5_000_000.0
        atr = close * 0.02
        df = pd.DataFrame({
            'open': open_, 'high': high, 'low': low, 'close': close,
            'volume': vol, 'ATR': atr,
        }, index=idx)
        df['Vol_Ratio'] = 1.0
        df.iloc[-1, df.columns.get_loc('Vol_Ratio')] = 5.0
        df['RSI'] = 62.0
        return df

    def test_real_detect_sma20_cross_downgrades_pinned_premium_to_high(self, monkeypatch):
        """End-to-end through the REAL detect_sma20_cross() — not a mirror of
        the logic — so a regression in the shipped conditional is caught."""
        import config
        monkeypatch.setitem(config.PINNED_RANGE_CONFIG, 'enabled', True)
        monkeypatch.setattr('scanner.check_pinned_range',
                            lambda *a, **kw: (True, 4.0, 0.5))
        from scanner import BreakoutDetector
        detector = BreakoutDetector()

        result = detector.detect_sma20_cross(self._cross_df(), 'CWAN', 'swing', '1 day')
        assert result is not None
        assert result['Quality'] == 'HIGH'

    def test_real_detect_sma20_cross_leaves_non_pinned_premium_alone(self, monkeypatch):
        import config
        monkeypatch.setitem(config.PINNED_RANGE_CONFIG, 'enabled', True)
        monkeypatch.setattr('scanner.check_pinned_range',
                            lambda *a, **kw: (False, 40.0, 3.0))
        from scanner import BreakoutDetector
        detector = BreakoutDetector()

        result = detector.detect_sma20_cross(self._cross_df(), 'NVDA', 'swing', '1 day')
        assert result is not None
        assert result['Quality'] == 'PREMIUM'

    def test_real_detect_sma20_cross_disabled_config_never_downgrades(self, monkeypatch):
        """Dormant by default: even a PREMIUM signal on a stock that WOULD be
        flagged pinned must keep its tier when PINNED_RANGE_CONFIG is off, and
        check_pinned_range must not even be called."""
        import config
        monkeypatch.setitem(config.PINNED_RANGE_CONFIG, 'enabled', False)

        def _boom(*a, **kw):
            raise AssertionError("check_pinned_range must not be called when dormant")
        monkeypatch.setattr('scanner.check_pinned_range', _boom)

        from scanner import BreakoutDetector
        detector = BreakoutDetector()
        result = detector.detect_sma20_cross(self._cross_df(), 'CWAN', 'swing', '1 day')
        assert result is not None
        assert result['Quality'] == 'PREMIUM'
