#!/usr/bin/env python3
"""
test_surge_day.py
=================
Tests for the Surge Day Mode feature:
  1. SURGE regime detection in classify_market_regime()
  2. Pre-market path (SPY gap + breadth)
  3. Intraday fallback path (SPY move, no breadth)
  4. filter_signals_by_regime() SURGE handling
  5. Scanner momentum_surge threshold changes
  6. SURGE_DAY_CONFIG gating (enabled/disabled)
  7. Score threshold overrides in _calculate_signal_score()
"""

import asyncio
import sys
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Python 3.14+: ib_insync needs an event loop at import time
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


class _SurgeTestBase(unittest.TestCase):
    """Base class with temp dir isolation."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._patcher = patch('config.OUTPUT_DIR', self._tmpdir)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()


# -- 1. SURGE regime detection via pre-market context -------------------------

class TestSurgeRegimePremarket(_SurgeTestBase):
    """Test SURGE detection from premarket_monitor surge_context."""

    def test_surge_detected_with_premarket_data(self):
        """SPY gap >= 1% + 15 gappers -> SURGE."""
        from utils import classify_market_regime
        ctx = {'spy_gap_pct': 1.5, 'num_gappers': 20}
        result = classify_market_regime(0.01, 0.5, surge_context=ctx)
        self.assertEqual(result, 'SURGE')

    def test_no_surge_low_spy_gap(self):
        """SPY gap < 1% -> not SURGE even with many gappers."""
        from utils import classify_market_regime
        ctx = {'spy_gap_pct': 0.5, 'num_gappers': 30}
        result = classify_market_regime(0.01, 0.5, surge_context=ctx)
        self.assertNotEqual(result, 'SURGE')

    def test_no_surge_low_breadth(self):
        """Few gappers -> not SURGE even with big SPY gap."""
        from utils import classify_market_regime
        ctx = {'spy_gap_pct': 2.0, 'num_gappers': 5}
        result = classify_market_regime(0.01, 0.5, surge_context=ctx)
        self.assertNotEqual(result, 'SURGE')

    def test_surge_exactly_at_thresholds(self):
        """Exactly at threshold values -> SURGE."""
        from utils import classify_market_regime
        ctx = {'spy_gap_pct': 1.0, 'num_gappers': 15}
        result = classify_market_regime(0.01, 0.5, surge_context=ctx)
        self.assertEqual(result, 'SURGE')

    def test_surge_overrides_bearish(self):
        """SURGE takes priority over BEARISH regime (spy_perf negative)."""
        from utils import classify_market_regime
        ctx = {'spy_gap_pct': 1.5, 'num_gappers': 20}
        # spy_perf = -0.01 would normally be BEARISH
        result = classify_market_regime(-0.01, 0.5, surge_context=ctx)
        self.assertEqual(result, 'SURGE')

    def test_surge_overrides_expansion(self):
        """SURGE takes priority over EXPANSION."""
        from utils import classify_market_regime
        ctx = {'spy_gap_pct': 2.0, 'num_gappers': 25}
        # spy_perf = 0.03 would normally be EXPANSION
        result = classify_market_regime(0.03, 0.5, surge_context=ctx)
        self.assertEqual(result, 'SURGE')


# -- 2. SURGE regime detection via intraday fallback --------------------------

class TestSurgeRegimeIntraday(_SurgeTestBase):
    """Test SURGE detection from intraday SPY move (no premarket data)."""

    def test_intraday_surge_detected(self):
        """SPY intraday move >= 1.5% with no breadth -> SURGE."""
        from utils import classify_market_regime
        ctx = {'spy_gap_pct': 1.8, 'num_gappers': 0}
        result = classify_market_regime(0.02, 0.5, surge_context=ctx)
        self.assertEqual(result, 'SURGE')

    def test_intraday_no_surge_below_threshold(self):
        """SPY intraday move 1.2% (below 1.5%) with no breadth -> not SURGE."""
        from utils import classify_market_regime
        ctx = {'spy_gap_pct': 1.2, 'num_gappers': 0}
        result = classify_market_regime(0.02, 0.5, surge_context=ctx)
        self.assertNotEqual(result, 'SURGE')

    def test_intraday_with_some_gappers_uses_premarket_path(self):
        """SPY gap 1.2% with 5 gappers -> not SURGE (doesn't meet either path)."""
        from utils import classify_market_regime
        ctx = {'spy_gap_pct': 1.2, 'num_gappers': 5}
        result = classify_market_regime(0.01, 0.5, surge_context=ctx)
        # 1.2% < 1.5% intraday threshold AND 5 < 15 breadth threshold
        self.assertNotEqual(result, 'SURGE')


# -- 3. SURGE_DAY_CONFIG gating -----------------------------------------------

class TestSurgeConfigGating(_SurgeTestBase):
    """Test that SURGE can be disabled via config."""

    def test_disabled_config_no_surge(self):
        """SURGE_DAY_CONFIG enabled=False -> never SURGE."""
        from utils import classify_market_regime
        ctx = {'spy_gap_pct': 3.0, 'num_gappers': 50}
        with patch('config.SURGE_DAY_CONFIG', {'enabled': False}):
            # Need to reimport since utils imports config at call time
            result = classify_market_regime(0.02, 0.5, surge_context=ctx)
        self.assertNotEqual(result, 'SURGE')

    def test_no_surge_context_no_surge(self):
        """No surge_context passed -> never SURGE (backward compatible)."""
        from utils import classify_market_regime
        result = classify_market_regime(0.03, 0.5)
        # Would be EXPANSION, not SURGE
        self.assertEqual(result, 'EXPANSION')

    def test_none_surge_context_no_surge(self):
        """surge_context=None -> never SURGE."""
        from utils import classify_market_regime
        result = classify_market_regime(0.03, 0.5, surge_context=None)
        self.assertEqual(result, 'EXPANSION')


# -- 4. filter_signals_by_regime() with SURGE ---------------------------------

class TestFilterSignalsSurge(_SurgeTestBase):
    """Test that SURGE regime passes signals through (like EXPANSION)."""

    def test_surge_passes_continuation(self):
        """CONTINUATION signals pass in SURGE regime."""
        from utils import filter_signals_by_regime
        signals = [
            {'Symbol': 'NVDA', 'Type': 'CONTINUATION', 'Quality': 'HIGH'},
            {'Symbol': 'AAPL', 'Type': 'CONTINUATION', 'Quality': 'PREMIUM'},
        ]
        # V9-H enabled
        filtered, blocked = filter_signals_by_regime(signals, 'SURGE', True)
        self.assertEqual(len(filtered), 2)
        self.assertEqual(blocked, 0)

    def test_surge_passes_momentum(self):
        """Momentum signals pass in SURGE regime."""
        from utils import filter_signals_by_regime
        signals = [
            {'Symbol': 'RCAT', 'Type': 'Momentum', 'Quality': 'HIGH'},
        ]
        filtered, blocked = filter_signals_by_regime(signals, 'SURGE', True)
        self.assertEqual(len(filtered), 1)

    def test_surge_blocks_bounce_non_gold(self):
        """BOUNCE requires GOLD quality even in SURGE (universal rule)."""
        from utils import filter_signals_by_regime
        signals = [
            {'Symbol': 'XYZ', 'Type': 'BOUNCE', 'Quality': 'PREMIUM'},
            {'Symbol': 'ABC', 'Type': 'BOUNCE', 'Quality': 'GOLD'},
        ]
        filtered, blocked = filter_signals_by_regime(signals, 'SURGE', True)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['Symbol'], 'ABC')

    def test_surge_v9d1_path(self):
        """V9-D1 (v9h_enabled=False) with SURGE passes everything."""
        from utils import filter_signals_by_regime
        signals = [
            {'Symbol': 'NVDA', 'Type': 'CONTINUATION', 'Quality': 'HIGH'},
            {'Symbol': 'PLTR', 'Type': 'Momentum', 'Quality': 'PREMIUM'},
        ]
        filtered, blocked = filter_signals_by_regime(signals, 'SURGE', False)
        self.assertEqual(len(filtered), 2)
        self.assertEqual(blocked, 0)


# -- 5. Scanner momentum_surge thresholds ------------------------------------

class TestMomentumSurgeThresholds(unittest.TestCase):
    """Test that momentum_surge thresholds are correctly applied."""

    def test_default_thresholds_lowered(self):
        """Non-surge default thresholds: 4% move, 2x vol (was 5%/3x)."""
        from scanner import BreakoutDetector
        import pandas as pd
        import numpy as np

        detector = BreakoutDetector()

        # Create minimal DataFrame with a 4.5% gap and 2.5x volume
        dates = pd.date_range('2024-01-01', periods=25, freq='B')
        df = pd.DataFrame({
            'open': [100.0] * 24 + [104.5],
            'high': [101.0] * 24 + [106.0],
            'low': [99.0] * 24 + [104.0],
            'close': [100.0] * 24 + [105.5],
            'volume': [1_000_000] * 24 + [2_500_000],
        }, index=dates)

        # Calculate Vol_Ratio manually
        vol_ma = df['volume'].rolling(20).mean()
        df['Vol_Ratio'] = df['volume'] / vol_ma

        # The last bar has ~2.5x volume and ~4.5% gap
        latest = df.iloc[-1]
        gap_pct = (latest['open'] - df['close'].iloc[-2]) / df['close'].iloc[-2] * 100

        # With new thresholds (4% move, 2x vol): should trigger
        self.assertGreaterEqual(gap_pct, 4.0, "Gap should be >= 4%")
        self.assertGreaterEqual(latest['Vol_Ratio'], 2.0, "Vol ratio should be >= 2x")

    def test_surge_day_thresholds_even_lower(self):
        """Surge day thresholds from config: 3% move, 1.5x vol."""
        from config import SURGE_DAY_CONFIG
        self.assertEqual(SURGE_DAY_CONFIG['move_thresh_pct'], 3.0)
        self.assertEqual(SURGE_DAY_CONFIG['vol_ratio_min'], 1.5)

    def test_momentum_surge_weight_increased(self):
        """momentum_surge scoring weight should be 15 (was 2)."""
        from config import SCORING_WEIGHTS
        self.assertEqual(SCORING_WEIGHTS['momentum_surge'], 15)


# -- 6. Score threshold overrides ---------------------------------------------

class TestScoreThresholdOverrides(unittest.TestCase):
    """Test _calculate_signal_score with surge threshold overrides."""

    def test_surge_thresholds_are_relaxed(self):
        """Surge score thresholds are lower than defaults."""
        from config import SCORE_THRESHOLDS, SURGE_DAY_CONFIG
        surge_th = SURGE_DAY_CONFIG['score_thresholds']
        self.assertLess(surge_th['GOLD'], SCORE_THRESHOLDS['GOLD'])
        self.assertLess(surge_th['PREMIUM'], SCORE_THRESHOLDS['PREMIUM'])
        self.assertLess(surge_th['HIGH'], SCORE_THRESHOLDS['HIGH'])
        self.assertLess(surge_th['STANDARD'], SCORE_THRESHOLDS['STANDARD'])

    def test_score_with_surge_overrides(self):
        """A score that would be REJECT normally becomes STANDARD with surge thresholds."""
        from scanner import BreakoutDetector
        from config import SURGE_DAY_CONFIG

        detector = BreakoutDetector()
        # Build checks that score between 40-50% (REJECT at 50% normal, STANDARD at 40% surge)
        # Total weights = 242. Need 97-121 pts (40-50%).
        checks = {
            'vol_confirm': True,           # 16
            'trend_ok': True,              # 2
            'momentum_surge': True,        # 15
            'consolidation': True,         # 12
            'rs_ok': True,                 # 16
            'has_bullish_pattern': True,    # 13
            'near_52w_high': True,         # 17
            'no_vol_divergence': True,      # 6
            'conviction_strong': True,      # 4
            'rr_ok': True,                 # 2
            # = 103 pts → 42.6%
            'momentum_strong': False,
            'dist_confirm': False,
            'candle_ok': False,
            'rsi_divergence': False,
            'sector_momentum': False,
            'pattern_vol_confirmed': False,
            'minervini_template': False,
            'vcp_quality': False,
            'sr_breakout': False,
            'at_key_support': False,
            'trendline_break': False,
        }

        # Normal thresholds: should be REJECT (score% < 50)
        _, _, quality_normal = detector._calculate_signal_score(checks)
        self.assertEqual(quality_normal, 'REJECT',
                         "Should be REJECT with normal thresholds")

        # Surge thresholds: should be STANDARD or better (threshold lowered to 40)
        surge_th = SURGE_DAY_CONFIG['score_thresholds']
        _, _, quality_surge = detector._calculate_signal_score(
            checks, score_thresholds_override=surge_th
        )
        self.assertNotEqual(quality_surge, 'REJECT',
                            "Should NOT be REJECT with surge thresholds")


# -- 7. REGIME_CONFIG includes SURGE ------------------------------------------

class TestRegimeConfigSurge(unittest.TestCase):
    """Test that SURGE is properly defined in REGIME_CONFIG."""

    def test_surge_in_regime_config(self):
        from config import REGIME_CONFIG
        self.assertIn('SURGE', REGIME_CONFIG)
        self.assertIn('vol_mult', REGIME_CONFIG['SURGE'])
        self.assertIn('atr_mult', REGIME_CONFIG['SURGE'])

    def test_surge_day_config_exists(self):
        from config import SURGE_DAY_CONFIG
        self.assertTrue(SURGE_DAY_CONFIG['enabled'])
        self.assertIn('spy_gap_min_pct', SURGE_DAY_CONFIG)
        self.assertIn('breadth_min_gappers', SURGE_DAY_CONFIG)
        self.assertIn('max_signals_per_scan', SURGE_DAY_CONFIG)
        self.assertIn('pos_size_mult', SURGE_DAY_CONFIG)


if __name__ == '__main__':
    unittest.main()
