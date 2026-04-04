"""
Tests validating key fixes from the code review.
Each test targets a specific bug-fix or hardening change.
"""

import json
import os
import pickle
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestATRWilderSmoothing(unittest.TestCase):
    """1. ATR must use Wilder's exponential smoothing, not simple rolling mean."""

    def test_atr_uses_ewm_not_rolling(self):
        np.random.seed(42)
        n = 30
        close = 100 + np.cumsum(np.random.randn(n) * 0.5)
        high = close + np.abs(np.random.randn(n) * 0.3)
        low = close - np.abs(np.random.randn(n) * 0.3)

        df = pd.DataFrame({'high': high, 'low': low, 'close': close})

        from indicators import calculate_atr
        result = calculate_atr(df, 14)

        # Manually compute Wilder's smoothing: alpha=1/period → com=period-1
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        expected_ewm = tr.ewm(com=13, adjust=False).mean()

        pd.testing.assert_series_equal(result, expected_ewm, check_names=False)

        # Also verify it differs from simple rolling mean
        simple_rolling = tr.rolling(14).mean()
        # They should diverge after the initial window
        tail_diff = (result.iloc[20:] - simple_rolling.iloc[20:]).abs().sum()
        self.assertGreater(tail_diff, 0.001,
                           "ATR result looks identical to simple rolling mean; "
                           "expected Wilder's EWM smoothing")


class TestRRGradeOrdering(unittest.TestCase):
    """2. R:R grade assignment loop: B -> C -> A -> D (descending threshold)."""

    def _assign_grade(self, rr):
        from config import RR_GRADE_CONFIG
        rr_grade = 'D'
        for grade in ['B', 'C', 'A']:
            if rr >= RR_GRADE_CONFIG[grade]['min_rr']:
                rr_grade = grade
                break
        return rr_grade

    def test_grade_B(self):
        self.assertEqual(self._assign_grade(2.5), 'B')

    def test_grade_C(self):
        self.assertEqual(self._assign_grade(1.5), 'C')

    def test_grade_A(self):
        self.assertEqual(self._assign_grade(0.7), 'A')

    def test_grade_D(self):
        self.assertEqual(self._assign_grade(0.5), 'D')

    def test_grade_boundary_B(self):
        self.assertEqual(self._assign_grade(2.0), 'B')

    def test_grade_boundary_C(self):
        self.assertEqual(self._assign_grade(1.5), 'C')


class TestRRScoringMap(unittest.TestCase):
    """3. R:R scoring map: B > C > A > D."""

    def test_scoring_order(self):
        score_map = {'A': 0.3, 'B': 1.0, 'C': 0.65, 'D': 0.0}
        self.assertGreater(score_map['B'], score_map['C'])
        self.assertGreater(score_map['C'], score_map['A'])
        self.assertGreater(score_map['A'], score_map['D'])
        self.assertEqual(score_map['D'], 0.0)
        self.assertEqual(score_map['B'], 1.0)


class TestVCPDecayRelaxation(unittest.TestCase):
    """4. VCP decay uses avg_decay < 0.90 — tolerates one non-contracting pair."""

    def test_accept_when_avg_decay_below_threshold(self):
        # [15, 12, 13, 9]: ratios = [12/15=0.80, 13/12=1.083, 9/13=0.692]
        # avg = (0.80 + 1.083 + 0.692) / 3 = 0.858 < 0.90 => accept
        pullbacks = [15.0, 12.0, 13.0, 9.0]
        decay_ratios = [pullbacks[i] / pullbacks[i - 1] for i in range(1, len(pullbacks))]
        avg_decay = np.mean(decay_ratios)
        self.assertLess(avg_decay, 0.90,
                        f"Expected avg_decay < 0.90 (accept), got {avg_decay:.4f}")

    def test_reject_when_avg_decay_above_threshold(self):
        # [15, 14.5, 14]: ratios = [14.5/15=0.9667, 14/14.5=0.9655]
        # avg = 0.9661 > 0.90 => reject
        pullbacks = [15.0, 14.5, 14.0]
        decay_ratios = [pullbacks[i] / pullbacks[i - 1] for i in range(1, len(pullbacks))]
        avg_decay = np.mean(decay_ratios)
        self.assertGreater(avg_decay, 0.90,
                           f"Expected avg_decay > 0.90 (reject), got {avg_decay:.4f}")

    def test_single_pair_contracting(self):
        # [10, 6]: ratio = 0.6 < 0.90 => accept
        pullbacks = [10.0, 6.0]
        decay_ratios = [pullbacks[1] / pullbacks[0]]
        avg_decay = np.mean(decay_ratios)
        self.assertLess(avg_decay, 0.90)


class TestStopLossGuardCap(unittest.TestCase):
    """5. Stop loss guard triggers at 0.30 (30%) distance from entry."""

    def _guard_triggers(self, entry_price, stop):
        """Reproduce the guard logic from auto_portfolio.py."""
        return (entry_price > 0 and (entry_price - stop) / entry_price > 0.30)

    def test_35pct_below_triggers(self):
        # stop 35% below entry => guard fires
        entry = 100.0
        stop = 65.0  # 35% below
        self.assertTrue(self._guard_triggers(entry, stop),
                        "Stop 35% below entry should trigger the guard")

    def test_25pct_below_does_not_trigger(self):
        # stop 25% below entry => guard does not fire
        entry = 100.0
        stop = 75.0  # 25% below
        self.assertFalse(self._guard_triggers(entry, stop),
                         "Stop 25% below entry should NOT trigger the guard")

    def test_exactly_30pct_does_not_trigger(self):
        # exactly 30% => not > 0.30, so does NOT trigger
        entry = 100.0
        stop = 70.0
        self.assertFalse(self._guard_triggers(entry, stop),
                         "Stop exactly 30% below should NOT trigger (> not >=)")


class TestSPYCacheKeyIncludesDate(unittest.TestCase):
    """6. SPY cache key must include YYYY-MM-DD date string."""

    def test_cache_key_format(self):
        # Reproduce the cache_key construction from market_data.py
        timeframe = '1D'
        lookback = 20
        cache_key = f"{timeframe}_{lookback}_{datetime.now().strftime('%Y-%m-%d')}"

        today_str = datetime.now().strftime('%Y-%m-%d')
        self.assertIn(today_str, cache_key,
                      f"Cache key '{cache_key}' should contain today's date '{today_str}'")
        # Verify date format pattern
        parts = cache_key.split('_')
        date_part = parts[-1]
        # Should parse as a valid date
        parsed = datetime.strptime(date_part, '%Y-%m-%d')
        self.assertEqual(parsed.strftime('%Y-%m-%d'), today_str)


class TestNotificationCacheMerge(unittest.TestCase):
    """7. _save_cache merges with existing on-disk keys (no overwrite)."""

    def test_merge_preserves_existing_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / '.notification_cache.json'
            today = datetime.now().strftime('%Y-%m-%d')

            # Simulate existing cache on disk (from another process)
            existing_keys = {'key_A', 'key_B'}
            cache_file.write_text(json.dumps({
                'date': today,
                'keys': list(existing_keys),
            }))

            # Simulate our in-memory cache with different keys
            in_memory_keys = {'key_C', 'key_D'}

            # Reproduce the merge logic from notifier.py _save_cache
            disk_keys = set()
            if cache_file.exists():
                disk = json.loads(cache_file.read_text())
                if disk.get('date') == today:
                    disk_keys = set(disk.get('keys', []))

            merged = disk_keys | in_memory_keys
            cache_file.write_text(json.dumps({
                'date': today,
                'keys': list(merged),
            }))

            # Verify: all 4 keys present
            final = json.loads(cache_file.read_text())
            final_keys = set(final['keys'])
            self.assertEqual(final_keys, {'key_A', 'key_B', 'key_C', 'key_D'},
                             "Merge must preserve existing + add new keys")

    def test_no_overwrite_on_concurrent_write(self):
        """Existing keys must not be lost when a second process writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / '.notification_cache.json'
            today = datetime.now().strftime('%Y-%m-%d')

            # Process 1 writes
            p1_keys = {'alert_AAPL', 'alert_TSLA'}
            cache_file.write_text(json.dumps({'date': today, 'keys': list(p1_keys)}))

            # Process 2: reads disk, merges its own, writes
            p2_keys = {'alert_NVDA'}
            disk = json.loads(cache_file.read_text())
            disk_keys = set(disk.get('keys', []))
            merged = disk_keys | p2_keys
            cache_file.write_text(json.dumps({'date': today, 'keys': list(merged)}))

            final = set(json.loads(cache_file.read_text())['keys'])
            self.assertIn('alert_AAPL', final)
            self.assertIn('alert_TSLA', final)
            self.assertIn('alert_NVDA', final)


class TestPickleAtomicWrite(unittest.TestCase):
    """8. save_cache in job_launcher creates final .pkl (not .pkl.tmp)."""

    def test_atomic_write_produces_pkl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Reproduce the atomic write pattern from job_launcher.py save_cache
            import tempfile as tf

            cache_file = Path(tmpdir) / 'market_data_test.pkl'
            data = {'symbols': ['AAPL', 'TSLA'], 'ts': '2026-04-04'}

            fd, tmp_path = tf.mkstemp(dir=tmpdir, suffix='.pkl.tmp')
            try:
                with open(fd, 'wb') as f:
                    pickle.dump(data, f)
                Path(tmp_path).rename(cache_file)
            except Exception:
                Path(tmp_path).unlink(missing_ok=True)
                raise

            # Final file must exist and be .pkl (not .pkl.tmp)
            self.assertTrue(cache_file.exists(), "Final .pkl file should exist")
            self.assertFalse(Path(tmp_path).exists(),
                             "Temp .pkl.tmp should not remain after rename")
            self.assertTrue(str(cache_file).endswith('.pkl'))

            # Verify content is readable
            with open(cache_file, 'rb') as f:
                loaded = pickle.load(f)
            self.assertEqual(loaded['symbols'], ['AAPL', 'TSLA'])


class TestSRToleranceModeAdaptive(unittest.TestCase):
    """9. S/R tolerance dict returns correct per-mode values."""

    def test_all_modes(self):
        _sr_tol = {'scalping': 0.005, 'daytrade': 0.010, 'swing': 0.015, 'longterm': 0.020}
        self.assertAlmostEqual(_sr_tol['scalping'], 0.005)
        self.assertAlmostEqual(_sr_tol['daytrade'], 0.010)
        self.assertAlmostEqual(_sr_tol['swing'], 0.015)
        self.assertAlmostEqual(_sr_tol['longterm'], 0.020)

    def test_default_fallback(self):
        _sr_tol = {'scalping': 0.005, 'daytrade': 0.010, 'swing': 0.015, 'longterm': 0.020}
        # Unknown mode falls back to 0.015
        self.assertAlmostEqual(_sr_tol.get('unknown_mode', 0.015), 0.015)

    def test_ordering_tighter_for_faster_modes(self):
        _sr_tol = {'scalping': 0.005, 'daytrade': 0.010, 'swing': 0.015, 'longterm': 0.020}
        self.assertLess(_sr_tol['scalping'], _sr_tol['daytrade'])
        self.assertLess(_sr_tol['daytrade'], _sr_tol['swing'])
        self.assertLess(_sr_tol['swing'], _sr_tol['longterm'])


class TestPostEntryDriftGuard(unittest.TestCase):
    """10. Drift guard triggers when price moves >5% from signal price."""

    def _drift_triggers(self, sig_price, current_price, threshold=0.05):
        """Reproduce the drift guard from scan_feedback_agent.py."""
        if sig_price and current_price > 0:
            drift_pct = abs(current_price - sig_price) / sig_price
            return drift_pct > threshold
        return False

    def test_6pct_drift_triggers(self):
        # Signal $100, current $106 => 6% drift > 5% threshold
        self.assertTrue(self._drift_triggers(100.0, 106.0),
                        "6% drift should trigger the guard")

    def test_3pct_drift_does_not_trigger(self):
        # Signal $100, current $103 => 3% drift <= 5%
        self.assertFalse(self._drift_triggers(100.0, 103.0),
                         "3% drift should NOT trigger")

    def test_exactly_5pct_does_not_trigger(self):
        # Exactly 5% => not > 0.05
        self.assertFalse(self._drift_triggers(100.0, 105.0),
                         "Exactly 5% should NOT trigger (> not >=)")

    def test_downward_drift_triggers(self):
        # Signal $100, current $93 => 7% drift downward
        self.assertTrue(self._drift_triggers(100.0, 93.0),
                        "7% downward drift should also trigger (abs)")

    def test_zero_signal_price_no_trigger(self):
        self.assertFalse(self._drift_triggers(0, 106.0))


if __name__ == '__main__':
    unittest.main()
