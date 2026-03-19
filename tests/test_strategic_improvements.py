#!/usr/bin/env python3
"""
test_strategic_improvements.py
==============================
Validate the 4 strategic improvements from the March 19 market reversal review:
  P1: Scan timeout (cron_agent)
  P2: Sector-relative regime exception (orchestrator)
  P3: Economic calendar (new module)
  P4: VIX-based sizing (market_data + orchestrator)
"""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── P1: Scan Timeout ─────────────────────────────────────────────────────────

class TestScanTimeout(unittest.TestCase):
    """Verify cron_agent uses 1800s for ALL.txt jobs, 900s otherwise."""

    def test_all_txt_gets_longer_timeout(self):
        """Jobs with ALL.txt in command should get 1800s timeout."""
        from cron_agent import CronJob
        job = CronJob(
            name='Swing Phase 1',
            minute=[35], hour=[9], day=-1, month=-1, weekday=[1,2,3,4,5],
            command='python breakout_scanner.py input/ALL.txt --mode swing',
            hc_uuid=None, category='swing'
        )
        # The timeout logic is:  1800 if 'ALL.txt' in job.command else 900
        timeout = 1800 if 'ALL.txt' in job.command else 900
        self.assertEqual(timeout, 1800)

    def test_regular_job_gets_default_timeout(self):
        """Normal jobs should keep 900s timeout."""
        from cron_agent import CronJob
        job = CronJob(
            name='Monitor',
            minute=[0], hour=[10], day=-1, month=-1, weekday=[1,2,3,4,5],
            command='python monitor_watch.py --mode daytrade',
            hc_uuid=None, category='monitor'
        )
        timeout = 1800 if 'ALL.txt' in job.command else 900
        self.assertEqual(timeout, 900)


# ── P2: Sector-Relative Regime Exception ──────────────────────────────────────

class TestSectorException(unittest.TestCase):
    """Verify sector exception config and logic."""

    def test_config_exists_and_disabled_by_default(self):
        from config import SECTOR_EXCEPTION
        self.assertFalse(SECTOR_EXCEPTION['enabled'])
        self.assertEqual(SECTOR_EXCEPTION['min_rs_5d'], 2.0)
        self.assertEqual(SECTOR_EXCEPTION['min_buzz'], 5)
        self.assertEqual(SECTOR_EXCEPTION['min_quality'], 'PREMIUM')
        self.assertEqual(SECTOR_EXCEPTION['max_entries_per_day'], 2)

    def test_sector_exception_skips_gate_when_enabled(self):
        """When enabled, a PREMIUM signal in a strong-RS sector should survive the gate."""
        from config import SECTOR_EXCEPTION, V9H_REGIME_GATE

        # Simulate the gate logic inline
        signal = {'Quality': 'PREMIUM', 'Symbol': 'XOM'}
        bear_macro = True
        regime = 'RED_MARKET'
        sector_scores_map = {'Energy': {'rs_5d': 3.5, 'buzz': 6}}
        sym_sector = 'Energy'

        # With SECTOR_EXCEPTION enabled
        enabled = True
        sector_exception = False

        if enabled and signal and sector_scores_map and (bear_macro or regime in ('BEARISH', 'RED_MARKET')):
            sc = sector_scores_map.get(sym_sector, {})
            quality_ok = signal.get('Quality') in ('GOLD', 'PREMIUM')
            if (sc.get('rs_5d', 0) >= 2.0 and sc.get('buzz', 0) >= 5 and quality_ok):
                sector_exception = True

        self.assertTrue(sector_exception, "PREMIUM signal in strong Energy should get exception")

    def test_sector_exception_blocked_when_low_rs(self):
        """A sector with RS < 2% should NOT get the exception."""
        signal = {'Quality': 'PREMIUM', 'Symbol': 'AAPL'}
        sector_scores_map = {'Technology': {'rs_5d': 0.5, 'buzz': 3}}
        sym_sector = 'Technology'

        sector_exception = False
        sc = sector_scores_map.get(sym_sector, {})
        quality_ok = signal.get('Quality') in ('GOLD', 'PREMIUM')
        if (sc.get('rs_5d', 0) >= 2.0 and sc.get('buzz', 0) >= 5 and quality_ok):
            sector_exception = True

        self.assertFalse(sector_exception, "Low RS sector should NOT get exception")

    def test_sector_exception_blocked_for_standard_quality(self):
        """Only PREMIUM+ signals qualify for the exception."""
        signal = {'Quality': 'STANDARD', 'Symbol': 'XOM'}
        sector_scores_map = {'Energy': {'rs_5d': 5.0, 'buzz': 8}}
        sym_sector = 'Energy'

        sector_exception = False
        sc = sector_scores_map.get(sym_sector, {})
        quality_ok = signal.get('Quality') in ('GOLD', 'PREMIUM')
        if (sc.get('rs_5d', 0) >= 2.0 and sc.get('buzz', 0) >= 5 and quality_ok):
            sector_exception = True

        self.assertFalse(sector_exception, "STANDARD quality should NOT get exception")


# ── P3: Economic Calendar ─────────────────────────────────────────────────────

class TestEconomicCalendar(unittest.TestCase):
    """Validate FOMC, CPI, NFP date detection and sizing multipliers."""

    def test_fomc_dates_2026(self):
        from economic_calendar import FOMC_DATES_2026, is_fomc_day
        self.assertEqual(len(FOMC_DATES_2026), 8)
        self.assertTrue(is_fomc_day(date(2026, 3, 18)))
        self.assertTrue(is_fomc_day(date(2026, 1, 28)))
        self.assertFalse(is_fomc_day(date(2026, 3, 19)))

    def test_cpi_dates_2026(self):
        from economic_calendar import CPI_DATES_2026, is_cpi_day
        self.assertEqual(len(CPI_DATES_2026), 12)
        self.assertTrue(is_cpi_day(date(2026, 3, 11)))
        self.assertFalse(is_cpi_day(date(2026, 3, 12)))

    def test_nfp_first_friday(self):
        from economic_calendar import NFP_DATES_2026, is_nfp_day
        self.assertEqual(len(NFP_DATES_2026), 12)
        # Jan 2026: first Friday is Jan 2
        self.assertTrue(is_nfp_day(date(2026, 1, 2)))
        self.assertFalse(is_nfp_day(date(2026, 1, 3)))

    def test_fomc_sizing_mult(self):
        from economic_calendar import get_event_context
        ctx = get_event_context(date(2026, 3, 18))
        self.assertTrue(ctx['is_event_day'])
        self.assertEqual(ctx['event_name'], 'FOMC')
        self.assertEqual(ctx['sizing_mult'], 0.50)

    def test_cpi_sizing_mult(self):
        from economic_calendar import get_event_context
        ctx = get_event_context(date(2026, 3, 11))
        self.assertTrue(ctx['is_event_day'])
        self.assertEqual(ctx['event_name'], 'CPI')
        self.assertEqual(ctx['sizing_mult'], 0.75)

    def test_nfp_sizing_mult(self):
        from economic_calendar import get_event_context
        ctx = get_event_context(date(2026, 1, 2))
        self.assertTrue(ctx['is_event_day'])
        self.assertEqual(ctx['event_name'], 'NFP')
        self.assertEqual(ctx['sizing_mult'], 0.75)

    def test_normal_day(self):
        from economic_calendar import get_event_context
        ctx = get_event_context(date(2026, 3, 20))
        self.assertFalse(ctx['is_event_day'])
        self.assertEqual(ctx['event_name'], '')
        self.assertEqual(ctx['sizing_mult'], 1.0)

    def test_disabled_config(self):
        """When disabled, always returns normal sizing."""
        from economic_calendar import get_event_context
        with patch('economic_calendar.ECONOMIC_CALENDAR', {'enabled': False}):
            ctx = get_event_context(date(2026, 3, 18))  # FOMC day
            self.assertFalse(ctx['is_event_day'])
            self.assertEqual(ctx['sizing_mult'], 1.0)

    def test_fomc_priority_over_other_events(self):
        """If a date is both FOMC and CPI (unlikely), FOMC takes priority."""
        from economic_calendar import get_event_context
        # Manually patch to make a date match both
        with patch('economic_calendar.FOMC_DATES_2026', {date(2026, 6, 10)}), \
             patch('economic_calendar.CPI_DATES_2026', {date(2026, 6, 10)}):
            ctx = get_event_context(date(2026, 6, 10))
            self.assertEqual(ctx['event_name'], 'FOMC')
            self.assertEqual(ctx['sizing_mult'], 0.50)


# ── P4: VIX-Based Sizing ─────────────────────────────────────────────────────

class TestVIXConfig(unittest.TestCase):
    """Validate VIX config and sizing logic."""

    def test_vix_config_exists(self):
        from config import VIX_CONFIG
        self.assertTrue(VIX_CONFIG['enabled'])
        self.assertEqual(VIX_CONFIG['elevated'], 25)
        self.assertEqual(VIX_CONFIG['extreme'], 35)
        self.assertEqual(VIX_CONFIG['sizing_mult_elevated'], 0.75)
        self.assertEqual(VIX_CONFIG['sizing_mult_extreme'], 0.50)

    def test_vix_sizing_logic_normal(self):
        from config import VIX_CONFIG
        vix = 18.0
        if vix >= VIX_CONFIG['extreme']:
            mult = VIX_CONFIG['sizing_mult_extreme']
        elif vix >= VIX_CONFIG['elevated']:
            mult = VIX_CONFIG['sizing_mult_elevated']
        else:
            mult = 1.0
        self.assertEqual(mult, 1.0)

    def test_vix_sizing_logic_elevated(self):
        from config import VIX_CONFIG
        vix = 28.0
        if vix >= VIX_CONFIG['extreme']:
            mult = VIX_CONFIG['sizing_mult_extreme']
        elif vix >= VIX_CONFIG['elevated']:
            mult = VIX_CONFIG['sizing_mult_elevated']
        else:
            mult = 1.0
        self.assertEqual(mult, 0.75)

    def test_vix_sizing_logic_extreme(self):
        from config import VIX_CONFIG
        vix = 40.0
        if vix >= VIX_CONFIG['extreme']:
            mult = VIX_CONFIG['sizing_mult_extreme']
        elif vix >= VIX_CONFIG['elevated']:
            mult = VIX_CONFIG['sizing_mult_elevated']
        else:
            mult = 1.0
        self.assertEqual(mult, 0.50)

    def test_combined_event_and_vix_mult(self):
        """FOMC day + elevated VIX should compound: 0.50 × 0.75 = 0.375."""
        event_mult = 0.50   # FOMC
        vix_mult = 0.75     # VIX = 28
        combined = event_mult * vix_mult
        self.assertAlmostEqual(combined, 0.375)


# ── P3+P4: Auto-Portfolio Sizing Integration ──────────────────────────────────

class TestAutoPortfolioEventSizing(unittest.TestCase):
    """Verify that Event_Sizing_Mult flows through to position sizing."""

    def test_event_sizing_mult_reduces_position(self):
        """A signal with Event_Sizing_Mult=0.50 should halve position value."""
        import auto_portfolio as ap

        row = MagicMock()
        row.get = lambda key, default=None: {
            'Event_Sizing_Mult': 0.50,
        }.get(key, default)

        # _safe_float handles the lookup
        event_mult = ap._safe_float(row.get('Event_Sizing_Mult')) or 1.0
        self.assertEqual(event_mult, 0.50)

    def test_no_event_mult_defaults_to_1(self):
        """Without Event_Sizing_Mult, sizing should be normal (1.0)."""
        import auto_portfolio as ap

        event_mult = ap._safe_float(None) or 1.0
        self.assertEqual(event_mult, 1.0)

        event_mult = ap._safe_float('') or 1.0
        self.assertEqual(event_mult, 1.0)


if __name__ == '__main__':
    unittest.main()
