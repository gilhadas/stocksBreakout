#!/usr/bin/env python3
"""
test_regime_cooldown.py
=======================
Tests for the regime persistence counter and post-regime-change cooldown,
covering the HMM-like temporal smoothing layer added in March 2026.

Modules under test:
  - utils.get_smoothed_regime()   — persistence counter + regime change tracking
  - utils.check_regime_cooldown() — cooldown window check
  - config.V9H_REGIME_GATE        — cooldown configuration

Test groups:
  1. Persistence counter (smoothed regime transitions)
  2. Cooldown activation and expiry
  3. GOLD signal exemption
  4. RED_MARKET immediate transition (safety-first)
  5. Edge cases (corrupt state, disabled features, first run)
"""

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _RegimeTestBase(unittest.TestCase):
    """Base class that provides a temporary .regime_state.json for isolation."""

    def setUp(self):
        """Redirect state file to a temp location so tests don't interfere."""
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self._patcher = patch('config.OUTPUT_DIR', self._tmpdir)
        self._patcher.start()
        # Ensure clean state for each test
        state_file = Path(self._tmpdir) / '.regime_state.json'
        if state_file.exists():
            state_file.unlink()

    def tearDown(self):
        self._patcher.stop()

    @property
    def _state_file(self):
        return Path(self._tmpdir) / '.regime_state.json'

    def _read_state(self):
        return json.loads(self._state_file.read_text())

    def _write_state(self, state: dict):
        self._state_file.write_text(json.dumps(state))


# ── 1. Persistence Counter ─────────────────────────────────────────────────

class TestPersistenceCounter(_RegimeTestBase):
    """Verify that regime transitions require N consecutive confirmations."""

    def test_single_scan_does_not_switch(self):
        """One BEARISH scan should NOT switch from NORMAL (pending 1/2)."""
        from utils import get_smoothed_regime
        eff, dbg = get_smoothed_regime('BEARISH')
        self.assertEqual(eff, 'NORMAL')
        self.assertEqual(dbg['pending'], 'BEARISH')
        self.assertEqual(dbg['count'], 1)
        self.assertFalse(dbg['regime_changed'])

    def test_two_scans_confirm_switch(self):
        """Two consecutive BEARISH scans should confirm the transition."""
        from utils import get_smoothed_regime
        get_smoothed_regime('BEARISH')  # pending 1/2
        eff, dbg = get_smoothed_regime('BEARISH')  # confirmed 2/2
        self.assertEqual(eff, 'BEARISH')
        self.assertTrue(dbg['regime_changed'])

    def test_interrupted_pending_resets(self):
        """A different raw regime interrupts the pending counter."""
        from utils import get_smoothed_regime
        get_smoothed_regime('BEARISH')    # pending BEARISH 1/2
        eff, dbg = get_smoothed_regime('CHOPPY')  # interrupts → new pending CHOPPY 1/2
        self.assertEqual(eff, 'NORMAL')
        self.assertEqual(dbg['pending'], 'CHOPPY')
        self.assertEqual(dbg['count'], 1)

    def test_same_regime_resets_pending(self):
        """If raw matches current regime, pending is cleared."""
        from utils import get_smoothed_regime
        get_smoothed_regime('BEARISH')    # pending 1/2
        eff, dbg = get_smoothed_regime('NORMAL')  # matches current → reset
        self.assertEqual(eff, 'NORMAL')
        self.assertIsNone(dbg['pending'])
        self.assertEqual(dbg['count'], 0)

    def test_disabled_persistence_passes_through(self):
        """threshold=0 disables smoothing — raw regime used directly."""
        from utils import get_smoothed_regime
        with patch('config.V9H_REGIME_GATE', {
            'persistence_threshold': 0,
            'cooldown_hours': 0,
        }):
            eff, dbg = get_smoothed_regime('EXPANSION')
            self.assertEqual(eff, 'EXPANSION')

    def test_state_persists_across_calls(self):
        """State file preserves regime across separate function calls."""
        from utils import get_smoothed_regime
        get_smoothed_regime('BEARISH')
        get_smoothed_regime('BEARISH')  # BEARISH confirmed
        eff, _ = get_smoothed_regime('BEARISH')  # same regime, no change
        self.assertEqual(eff, 'BEARISH')
        state = self._read_state()
        self.assertEqual(state['current_regime'], 'BEARISH')

    def test_history_kept_last_10(self):
        """History list should keep at most 10 entries."""
        from utils import get_smoothed_regime
        for _ in range(15):
            get_smoothed_regime('NORMAL')
        state = self._read_state()
        self.assertLessEqual(len(state['history']), 10)


# ── 2. Cooldown Activation & Expiry ────────────────────────────────────────

class TestCooldownActivation(_RegimeTestBase):
    """Verify cooldown activates after regime change and expires correctly."""

    def test_no_cooldown_without_regime_change(self):
        """Cooldown should be inactive when no regime change has occurred."""
        from utils import check_regime_cooldown
        active, remaining = check_regime_cooldown(12)
        self.assertFalse(active)
        self.assertEqual(remaining, 0.0)

    def test_cooldown_activates_after_regime_change(self):
        """Cooldown should be active immediately after a confirmed change."""
        from utils import get_smoothed_regime, check_regime_cooldown
        get_smoothed_regime('BEARISH')
        get_smoothed_regime('BEARISH')  # confirm change
        active, remaining = check_regime_cooldown(12)
        self.assertTrue(active)
        self.assertGreater(remaining, 11.9)

    def test_cooldown_expires_after_window(self):
        """Cooldown should be inactive after the window elapses."""
        from utils import get_smoothed_regime, check_regime_cooldown
        get_smoothed_regime('BEARISH')
        get_smoothed_regime('BEARISH')
        # Backdate the regime change to 13 hours ago
        state = self._read_state()
        state['last_regime_change'] = (
            datetime.now() - timedelta(hours=13)
        ).isoformat()
        self._write_state(state)
        active, remaining = check_regime_cooldown(12)
        self.assertFalse(active)

    def test_cooldown_disabled_when_zero(self):
        """cooldown_hours=0 should always return inactive."""
        from utils import get_smoothed_regime, check_regime_cooldown
        get_smoothed_regime('BEARISH')
        get_smoothed_regime('BEARISH')
        active, _ = check_regime_cooldown(0)
        self.assertFalse(active)

    def test_cooldown_disabled_when_negative(self):
        """Negative cooldown_hours should be treated as disabled."""
        from utils import check_regime_cooldown
        active, _ = check_regime_cooldown(-5)
        self.assertFalse(active)


# ── 3. GOLD Signal Exemption ───────────────────────────────────────────────

class TestCooldownExemption(_RegimeTestBase):
    """Verify GOLD signals are exempt from cooldown suppression."""

    def test_gold_in_exempt_list(self):
        """Config should list GOLD as cooldown-exempt."""
        from config import V9H_REGIME_GATE
        exempt = V9H_REGIME_GATE.get('cooldown_exempt_quality', [])
        self.assertIn('GOLD', exempt)

    def test_premium_not_in_exempt_list(self):
        """PREMIUM should NOT be exempt by default."""
        from config import V9H_REGIME_GATE
        exempt = V9H_REGIME_GATE.get('cooldown_exempt_quality', [])
        self.assertNotIn('PREMIUM', exempt)


# ── 4. RED_MARKET Immediate Transition ─────────────────────────────────────

class TestRedMarketImmediate(_RegimeTestBase):
    """RED_MARKET transitions must bypass the persistence counter."""

    def test_red_market_immediate_from_normal(self):
        """RED_MARKET should switch in a single scan, no waiting."""
        from utils import get_smoothed_regime
        eff, dbg = get_smoothed_regime('RED_MARKET')
        self.assertEqual(eff, 'RED_MARKET')
        self.assertTrue(dbg['regime_changed'])

    def test_red_market_triggers_cooldown(self):
        """Even immediate RED_MARKET should set last_regime_change for cooldown."""
        from utils import get_smoothed_regime, check_regime_cooldown
        get_smoothed_regime('RED_MARKET')
        active, _ = check_regime_cooldown(12)
        self.assertTrue(active)

    def test_recovery_from_red_market_needs_persistence(self):
        """Exiting RED_MARKET back to NORMAL requires N confirmations."""
        from utils import get_smoothed_regime
        get_smoothed_regime('RED_MARKET')  # immediate
        eff, _ = get_smoothed_regime('NORMAL')  # pending 1/2
        self.assertEqual(eff, 'RED_MARKET')  # still RED_MARKET
        eff, _ = get_smoothed_regime('NORMAL')  # confirmed 2/2
        self.assertEqual(eff, 'NORMAL')


# ── 5. Edge Cases ──────────────────────────────────────────────────────────

class TestEdgeCases(_RegimeTestBase):
    """Edge cases: corrupt state, missing file, first run."""

    def test_corrupt_state_file_resets(self):
        """Corrupt JSON should reset to NORMAL defaults."""
        from utils import get_smoothed_regime
        self._state_file.write_text('NOT VALID JSON {{{')
        eff, dbg = get_smoothed_regime('NORMAL')
        self.assertEqual(eff, 'NORMAL')
        self.assertFalse(dbg['regime_changed'])

    def test_corrupt_state_cooldown_inactive(self):
        """Corrupt state file should not activate cooldown."""
        from utils import check_regime_cooldown
        self._state_file.write_text('CORRUPT')
        active, _ = check_regime_cooldown(12)
        self.assertFalse(active)

    def test_first_run_no_state_file(self):
        """First run (no state file) should default to NORMAL."""
        from utils import get_smoothed_regime
        eff, dbg = get_smoothed_regime('NORMAL')
        self.assertEqual(eff, 'NORMAL')
        self.assertFalse(dbg['regime_changed'])

    def test_regime_change_records_previous(self):
        """On regime change, previous_regime should be saved in state."""
        from utils import get_smoothed_regime
        get_smoothed_regime('BEARISH')
        get_smoothed_regime('BEARISH')  # confirm
        state = self._read_state()
        self.assertEqual(state['previous_regime'], 'NORMAL')
        self.assertEqual(state['current_regime'], 'BEARISH')

    def test_last_regime_change_is_iso_timestamp(self):
        """last_regime_change should be a parseable ISO timestamp."""
        from utils import get_smoothed_regime
        get_smoothed_regime('RED_MARKET')
        state = self._read_state()
        ts = state['last_regime_change']
        parsed = datetime.fromisoformat(ts)
        self.assertIsInstance(parsed, datetime)

    def test_debug_info_contains_all_fields(self):
        """debug_info should contain all documented fields."""
        from utils import get_smoothed_regime
        _, dbg = get_smoothed_regime('NORMAL')
        expected_keys = {
            'raw', 'effective', 'pending', 'count',
            'threshold', 'regime_changed', 'last_regime_change',
        }
        self.assertEqual(set(dbg.keys()), expected_keys)


if __name__ == '__main__':
    unittest.main()
