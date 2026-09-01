#!/usr/bin/env python3
"""
test_exit_from_portfolio.py
============================
Unit tests for the --exit-from-portfolio flow:
  1. auto_portfolio V9-H filter (BOUNCE/CONTINUATION exempt from MinerviniScore)
  2. --exit-from-portfolio merges portfolio.json + auto_portfolio.json positions
  3. refresh_prices auto-closes positions when stop is hit
  4. Day-after exit scenario: position added yesterday, price gaps below stop overnight

Run:
    python test_exit_from_portfolio.py
    python test_exit_from_portfolio.py -v
"""

import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

# Signal-file dates must stay INSIDE auto_portfolio.SIGNAL_MAX_AGE_DAYS.
# These fixtures used a hardcoded 2026-03-18, which was fine only while
# scan_and_add had no age bound at all. With the bound in place a stale
# filename is retired before the V9-H quality filter ever runs — so the
# "accepted" tests broke outright, and (worse) the "rejected" ones kept
# passing for entirely the wrong reason. Deriving the date from today keeps
# every test in this class measuring the quality filter, which is its subject.
_TODAY = datetime.now().strftime('%Y%m%d')
# Position fixtures use this instead of a fixed historical date so they stay
# inside every mode's MAX_HOLD_BARS cap (refresh_prices' MaxHold auto-close,
# added 2026-08-28) regardless of when the suite runs — same aging trap as
# the signal-date fix above, one layer down.
_RECENT_DATE = datetime.now().strftime('%Y-%m-%d')

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ─────────────────────────────────────────────────────────────────────────────
# 1. V9-H filter in auto_portfolio (MinerviniScore exemption for non-breakouts)
# ─────────────────────────────────────────────────────────────────────────────

class TestV9HFilter(unittest.TestCase):
    """V9-H filter: breakouts need MinerviniScore>=7, BOUNCE/CONTINUATION exempt."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.signals_dir = os.path.join(self.tmp_dir, 'signals')
        os.makedirs(self.signals_dir)
        self.portfolio_path = os.path.join(self.tmp_dir, 'auto_portfolio.json')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_signal_csv(self, filename, rows, has_minervini=True, has_type=True):
        """Write a signal CSV with configurable columns."""
        filepath = os.path.join(self.signals_dir, filename)
        fieldnames = ['Symbol', 'Price', 'Stop', 'Target', 'Quality', 'Mode']
        if has_minervini:
            fieldnames.append('MinerviniScore')
        if has_type:
            fieldnames.append('Type')
        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                filtered = {k: v for k, v in row.items() if k in fieldnames}
                writer.writerow(filtered)

    def _empty_portfolio(self):
        return {
            'capital': 10000,
            'positions': [],
            'closed': [],
            'skipped_cash': [],
            'processed_files': [],
            'last_updated': None,
        }

    @patch('auto_portfolio._fetch_entry_and_current')
    @patch('auto_portfolio._compute_atr_adjustment', return_value=1.0)
    def test_breakout_without_minervini_rejected(self, mock_atr, mock_fetch):
        """Breakout signal with MinerviniScore=NaN should be rejected."""
        mock_fetch.return_value = (100.0, 102.0)

        self._write_signal_csv(f'signals_swing_{_TODAY}_093500.csv', [
            {'Symbol': 'AAPL', 'Price': '100', 'Stop': '95', 'Target': '110',
             'Quality': 'PREMIUM', 'Mode': 'swing', 'MinerviniScore': '',
             'Type': 'Breakout'},
        ])

        import auto_portfolio as ap
        with patch.object(ap, '_SIGNALS_DIR', self.signals_dir), \
             patch.object(ap, '_PORTFOLIO_PATH', self.portfolio_path), \
             patch.object(ap, 'load', return_value=self._empty_portfolio()), \
             patch.object(ap, '_save'):
            result = ap.scan_and_add(notify=False)

        self.assertEqual(result['added'], 0,
                         "Breakout with no MinerviniScore should be rejected")

    @patch('auto_portfolio._fetch_entry_and_current')
    @patch('auto_portfolio._compute_atr_adjustment', return_value=1.0)
    def test_continuation_without_minervini_accepted(self, mock_atr, mock_fetch):
        """CONTINUATION signal (PREMIUM) should be accepted without MinerviniScore."""
        mock_fetch.return_value = (25.0, 26.0)

        self._write_signal_csv(f'signals_swing_{_TODAY}_093500.csv', [
            {'Symbol': 'ELAN', 'Price': '25', 'Stop': '23', 'Target': '28',
             'Quality': 'PREMIUM', 'Mode': 'swing', 'MinerviniScore': '',
             'Type': 'CONTINUATION'},
        ])

        import auto_portfolio as ap
        with patch.object(ap, '_SIGNALS_DIR', self.signals_dir), \
             patch.object(ap, '_PORTFOLIO_PATH', self.portfolio_path), \
             patch.object(ap, 'load', return_value=self._empty_portfolio()), \
             patch.object(ap, '_save'):
            result = ap.scan_and_add(notify=False)

        self.assertEqual(result['added'], 1,
                         "CONTINUATION PREMIUM should pass V9-H without MinerviniScore")
        self.assertIn('ELAN', result['added_symbols'])

    @patch('auto_portfolio._fetch_entry_and_current')
    @patch('auto_portfolio._compute_atr_adjustment', return_value=1.0)
    def test_bounce_without_minervini_accepted(self, mock_atr, mock_fetch):
        """BOUNCE signal (PREMIUM) should be accepted without MinerviniScore."""
        mock_fetch.return_value = (26.0, 27.0)

        self._write_signal_csv(f'signals_swing_{_TODAY}_094800.csv', [
            {'Symbol': 'RGC', 'Price': '26', 'Stop': '21', 'Target': '37',
             'Quality': 'PREMIUM', 'Mode': 'swing', 'MinerviniScore': '',
             'Type': 'BOUNCE'},
        ])

        import auto_portfolio as ap
        with patch.object(ap, '_SIGNALS_DIR', self.signals_dir), \
             patch.object(ap, '_PORTFOLIO_PATH', self.portfolio_path), \
             patch.object(ap, 'load', return_value=self._empty_portfolio()), \
             patch.object(ap, '_save'):
            result = ap.scan_and_add(notify=False)

        self.assertEqual(result['added'], 1,
                         "BOUNCE PREMIUM should pass V9-H without MinerviniScore")

    @patch('auto_portfolio._fetch_entry_and_current')
    @patch('auto_portfolio._compute_atr_adjustment', return_value=1.0)
    def test_breakout_with_minervini_7_accepted(self, mock_atr, mock_fetch):
        """Breakout signal with MinerviniScore=8 should be accepted."""
        mock_fetch.return_value = (150.0, 155.0)

        self._write_signal_csv(f'signals_swing_{_TODAY}_093500.csv', [
            {'Symbol': 'NVDA', 'Price': '150', 'Stop': '140', 'Target': '170',
             'Quality': 'GOLD', 'Mode': 'swing', 'MinerviniScore': '8',
             'Type': 'Breakout'},
        ])

        import auto_portfolio as ap
        with patch.object(ap, '_SIGNALS_DIR', self.signals_dir), \
             patch.object(ap, '_PORTFOLIO_PATH', self.portfolio_path), \
             patch.object(ap, 'load', return_value=self._empty_portfolio()), \
             patch.object(ap, '_save'):
            result = ap.scan_and_add(notify=False)

        self.assertEqual(result['added'], 1,
                         "GOLD breakout with MinerviniScore=8 should pass V9-H")

    @patch('auto_portfolio._fetch_entry_and_current')
    @patch('auto_portfolio._compute_atr_adjustment', return_value=1.0)
    def test_no_type_column_uses_minervini(self, mock_atr, mock_fetch):
        """When Type column is absent, MinerviniScore filter applies to all."""
        mock_fetch.return_value = (50.0, 52.0)

        self._write_signal_csv(f'signals_swing_{_TODAY}_093500.csv', [
            {'Symbol': 'XYZ', 'Price': '50', 'Stop': '47', 'Target': '55',
             'Quality': 'PREMIUM', 'Mode': 'swing', 'MinerviniScore': '3'},
        ], has_type=False)

        import auto_portfolio as ap
        with patch.object(ap, '_SIGNALS_DIR', self.signals_dir), \
             patch.object(ap, '_PORTFOLIO_PATH', self.portfolio_path), \
             patch.object(ap, 'load', return_value=self._empty_portfolio()), \
             patch.object(ap, '_save'):
            result = ap.scan_and_add(notify=False)

        self.assertEqual(result['added'], 0,
                         "Without Type column, MinerviniScore=3 should be rejected")

    @patch('auto_portfolio._fetch_entry_and_current')
    @patch('auto_portfolio._compute_atr_adjustment', return_value=1.0)
    def test_high_quality_rejected(self, mock_atr, mock_fetch):
        """HIGH quality signals should always be rejected (only GOLD/PREMIUM)."""
        mock_fetch.return_value = (70.0, 72.0)

        self._write_signal_csv(f'signals_swing_{_TODAY}_093500.csv', [
            {'Symbol': 'VIST', 'Price': '70', 'Stop': '65', 'Target': '80',
             'Quality': 'HIGH', 'Mode': 'swing', 'MinerviniScore': '8',
             'Type': 'CONTINUATION'},
        ])

        import auto_portfolio as ap
        with patch.object(ap, '_SIGNALS_DIR', self.signals_dir), \
             patch.object(ap, '_PORTFOLIO_PATH', self.portfolio_path), \
             patch.object(ap, 'load', return_value=self._empty_portfolio()), \
             patch.object(ap, '_save'):
            result = ap.scan_and_add(notify=False)

        self.assertEqual(result['added'], 0,
                         "HIGH quality should be rejected regardless of MinerviniScore")


# ─────────────────────────────────────────────────────────────────────────────
# 2. --exit-from-portfolio merges portfolio.json + auto_portfolio.json
# ─────────────────────────────────────────────────────────────────────────────

class TestExitFromPortfolioMerge(unittest.TestCase):
    """--exit-from-portfolio should load positions from both portfolio sources."""

    def test_merge_portfolio_and_auto_portfolio(self):
        """Positions from portfolio.json and auto_portfolio.json are merged."""
        from config import MODES

        # Mock portfolio.json positions
        portfolio_positions = [
            {'symbol': 'AAPL', 'mode': 'swing', 'entry': 180.0,
             'stop': 170.0, 'target': 200.0,
             'timeframe': MODES['swing']['default_timeframe']},
        ]

        # Mock auto_portfolio.json data
        auto_data = {
            'positions': [
                {'symbol': 'RGC', 'mode': 'swing', 'entry_price': 26.38,
                 'stop': 21.3, 'target': 37.47, 'date_added': _RECENT_DATE,
                 'quality': 'PREMIUM'},
                {'symbol': 'AAPL', 'mode': 'swing', 'entry_price': 185.0,
                 'stop': 175.0, 'target': 205.0, 'date_added': '2026-03-17',
                 'quality': 'GOLD'},
            ]
        }

        # Simulate the merge logic from breakout_scanner.py
        exit_positions = list(portfolio_positions)  # start with portfolio.json
        existing_symbols = {ep['symbol'] for ep in exit_positions}

        for pos in auto_data.get('positions', []):
            if pos['symbol'] not in existing_symbols:
                mode = pos.get('mode', 'swing')
                exit_positions.append({
                    'symbol': pos['symbol'],
                    'mode': mode,
                    'entry': pos['entry_price'],
                    'entry_date': pos.get('date_added', ''),
                    'stop': pos['stop'],
                    'target': pos['target'],
                    'timeframe': MODES.get(mode, MODES['swing'])['default_timeframe'],
                    'quality': pos.get('quality', 'PREMIUM'),
                })

        symbols = [p['symbol'] for p in exit_positions]
        self.assertEqual(len(exit_positions), 2, "Should have 2 unique positions")
        self.assertIn('AAPL', symbols, "AAPL from portfolio.json")
        self.assertIn('RGC', symbols, "RGC from auto_portfolio.json")
        # AAPL should come from portfolio.json (first), not auto_portfolio (deduped)
        aapl_pos = next(p for p in exit_positions if p['symbol'] == 'AAPL')
        self.assertEqual(aapl_pos['entry'], 180.0,
                         "AAPL should use portfolio.json entry (180), not auto's (185)")


# ─────────────────────────────────────────────────────────────────────────────
# 3. refresh_prices auto-closes positions when stop is hit
# ─────────────────────────────────────────────────────────────────────────────

class TestRefreshPricesStopHit(unittest.TestCase):
    """refresh_prices() should auto-close positions where current_price <= stop."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.portfolio_path = os.path.join(self.tmp_dir, 'auto_portfolio.json')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_portfolio(self, positions):
        data = {
            'capital': 10000,
            'positions': positions,
            'closed': [],
            'skipped_cash': [],
            'processed_files': [],
            'last_updated': None,
        }
        with open(self.portfolio_path, 'w') as f:
            json.dump(data, f)
        return data

    @patch('auto_portfolio._find_stop_hit_date', return_value='2026-03-19')
    def test_stop_hit_closes_position(self, mock_find_date):
        """Position below stop should be auto-closed."""
        import auto_portfolio as ap

        data = self._make_portfolio([
            {'symbol': 'TEST', 'date_added': _RECENT_DATE, 'mode': 'swing',
             'quality': 'PREMIUM', 'minervini_score': 0,
             'entry_price': 100.0, 'stop': 95.0, 'target': 115.0,
             'shares': 100, 'cost': 10000.0, 'current_price': 100.0,
             'sector': 'Technology'},
        ])

        # Mock yfinance to return price below stop
        mock_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = lambda self, key: MagicMock(
            dropna=lambda: MagicMock(iloc=MagicMock(__getitem__=lambda s, i: 90.0))
        )
        mock_ticker.history.return_value = mock_hist

        with patch.object(ap, '_PORTFOLIO_PATH', self.portfolio_path), \
             patch.object(ap, 'load', return_value=data), \
             patch.object(ap, '_save') as mock_save, \
             patch('yfinance.Ticker', return_value=mock_ticker):
            result = ap.refresh_prices()

        self.assertIn('TEST', result['closed'],
                      "Position below stop should be closed")
        # Verify _save was called with position moved to closed
        saved_data = mock_save.call_args[0][0]
        self.assertEqual(len(saved_data['positions']), 0,
                         "No open positions should remain")
        self.assertEqual(len(saved_data['closed']), 1,
                         "One closed position should exist")
        self.assertEqual(saved_data['closed'][0]['close_reason'], 'atr_trail_stop')

    def test_above_stop_stays_open(self):
        """Position above stop should remain open."""
        import auto_portfolio as ap

        data = self._make_portfolio([
            {'symbol': 'TEST', 'date_added': _RECENT_DATE, 'mode': 'swing',
             'quality': 'PREMIUM', 'minervini_score': 0,
             'entry_price': 100.0, 'stop': 95.0, 'target': 115.0,
             'shares': 100, 'cost': 10000.0, 'current_price': 100.0,
             'sector': 'Technology'},
        ])

        # Mock yfinance to return price above stop
        mock_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = lambda self, key: MagicMock(
            dropna=lambda: MagicMock(iloc=MagicMock(__getitem__=lambda s, i: 102.0))
        )
        mock_ticker.history.return_value = mock_hist

        with patch.object(ap, '_PORTFOLIO_PATH', self.portfolio_path), \
             patch.object(ap, 'load', return_value=data), \
             patch.object(ap, '_save') as mock_save, \
             patch('yfinance.Ticker', return_value=mock_ticker):
            result = ap.refresh_prices()

        self.assertEqual(result['closed'], [],
                         "Position above stop should NOT be closed")
        saved_data = mock_save.call_args[0][0]
        self.assertEqual(len(saved_data['positions']), 1,
                         "Position should remain open")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Day-after exit scenario (yesterday's stock gaps below stop overnight)
# ─────────────────────────────────────────────────────────────────────────────

class TestDayAfterExit(unittest.TestCase):
    """
    Scenario: stock added to auto_portfolio yesterday at entry=$26.38,
    stop=$21.30. Overnight it gaps to $20.00 (below stop).
    Morning refresh_prices should auto-close it.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.portfolio_path = os.path.join(self.tmp_dir, 'auto_portfolio.json')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    @patch('auto_portfolio._find_stop_hit_date', return_value='2026-03-19')
    def test_overnight_gap_below_stop_closes(self, mock_find_date):
        """RGC added yesterday at 26.38, gaps to 20.00 overnight → closed at stop."""
        import auto_portfolio as ap

        data = {
            'capital': 10000,
            'positions': [
                {'symbol': 'RGC', 'date_added': _RECENT_DATE, 'mode': 'swing',
                 'quality': 'PREMIUM', 'minervini_score': 0,
                 'entry_price': 26.38, 'stop': 21.3, 'target': 37.47,
                 'shares': 75, 'cost': 1978.5, 'current_price': 26.38,
                 'sector': 'Healthcare'},
            ],
            'closed': [],
            'skipped_cash': [],
            'processed_files': [],
            'last_updated': None,
        }
        with open(self.portfolio_path, 'w') as f:
            json.dump(data, f)

        # Simulate overnight gap: price = $20.00 (below stop $21.30)
        mock_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = lambda self, key: MagicMock(
            dropna=lambda: MagicMock(iloc=MagicMock(__getitem__=lambda s, i: 20.0))
        )
        mock_ticker.history.return_value = mock_hist

        with patch.object(ap, '_PORTFOLIO_PATH', self.portfolio_path), \
             patch.object(ap, 'load', return_value=data), \
             patch.object(ap, '_save') as mock_save, \
             patch('yfinance.Ticker', return_value=mock_ticker):
            result = ap.refresh_prices()

        self.assertIn('RGC', result['closed'],
                      "RGC should be closed after overnight gap below stop")
        saved_data = mock_save.call_args[0][0]
        closed_pos = saved_data['closed'][0]
        self.assertEqual(closed_pos['symbol'], 'RGC')
        self.assertEqual(closed_pos['exit_price'], 21.3,
                         "Exit should be at stop price, not gap price")
        self.assertEqual(closed_pos['close_reason'], 'atr_trail_stop')
        expected_pnl = round((21.3 - 26.38) * 75, 2)
        self.assertEqual(closed_pos['pnl'], expected_pnl,
                         f"P&L should be (stop - entry) * shares = {expected_pnl}")

    @patch('auto_portfolio._find_stop_hit_date', return_value='2026-03-19')
    def test_overnight_gap_above_stop_holds(self, mock_find_date):
        """RGC added yesterday at 26.38, opens at 25.00 (above stop 21.30) → holds."""
        import auto_portfolio as ap

        data = {
            'capital': 10000,
            'positions': [
                {'symbol': 'RGC', 'date_added': _RECENT_DATE, 'mode': 'swing',
                 'quality': 'PREMIUM', 'minervini_score': 0,
                 'entry_price': 26.38, 'stop': 21.3, 'target': 37.47,
                 'shares': 75, 'cost': 1978.5, 'current_price': 26.38,
                 'sector': 'Healthcare'},
            ],
            'closed': [],
            'skipped_cash': [],
            'processed_files': [],
            'last_updated': None,
        }

        # Price dips but still above stop
        mock_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = lambda self, key: MagicMock(
            dropna=lambda: MagicMock(iloc=MagicMock(__getitem__=lambda s, i: 25.0))
        )
        mock_ticker.history.return_value = mock_hist

        with patch.object(ap, '_PORTFOLIO_PATH', self.portfolio_path), \
             patch.object(ap, 'load', return_value=data), \
             patch.object(ap, '_save') as mock_save, \
             patch('yfinance.Ticker', return_value=mock_ticker):
            result = ap.refresh_prices()

        self.assertEqual(result['closed'], [],
                         "RGC above stop should NOT be closed")
        saved_data = mock_save.call_args[0][0]
        self.assertEqual(len(saved_data['positions']), 1)
        self.assertEqual(saved_data['positions'][0]['current_price'], 25.0,
                         "Price should be updated to 25.0")

    @patch('auto_portfolio._find_stop_hit_date', return_value='2026-03-19')
    def test_overnight_gap_pnl_flows_to_capital(self, mock_find_date):
        """When stop is hit, realized P&L should be added to capital."""
        import auto_portfolio as ap

        initial_capital = 10000
        data = {
            'capital': initial_capital,
            'positions': [
                {'symbol': 'LOSER', 'date_added': _RECENT_DATE, 'mode': 'swing',
                 'quality': 'PREMIUM', 'minervini_score': 0,
                 'entry_price': 50.0, 'stop': 45.0, 'target': 60.0,
                 'shares': 200, 'cost': 10000.0, 'current_price': 50.0,
                 'sector': 'Technology'},
            ],
            'closed': [],
            'skipped_cash': [],
            'processed_files': [],
            'last_updated': None,
        }

        # Price crashes to $40 (below stop $45)
        mock_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = False
        mock_hist.__getitem__ = lambda self, key: MagicMock(
            dropna=lambda: MagicMock(iloc=MagicMock(__getitem__=lambda s, i: 40.0))
        )
        mock_ticker.history.return_value = mock_hist

        with patch.object(ap, '_PORTFOLIO_PATH', self.portfolio_path), \
             patch.object(ap, 'load', return_value=data), \
             patch.object(ap, '_save') as mock_save, \
             patch('yfinance.Ticker', return_value=mock_ticker):
            result = ap.refresh_prices()

        saved_data = mock_save.call_args[0][0]
        expected_pnl = round((45.0 - 50.0) * 200, 2)  # -1000.0
        self.assertEqual(saved_data['capital'], initial_capital + expected_pnl,
                         f"Capital should be {initial_capital} + {expected_pnl} = {initial_capital + expected_pnl}")


if __name__ == '__main__':
    unittest.main()
