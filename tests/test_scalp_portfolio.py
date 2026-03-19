#!/usr/bin/env python3
"""
test_scalp_portfolio.py
========================
Unit tests for the scalping portfolio module and its integration with the
feedback agent.

Run:
    python test_scalp_portfolio.py
    python test_scalp_portfolio.py -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scalp_portfolio as sp


# ─────────────────────────────────────────────────────────────────────────────
# Helper: redirect persistence to a temp file so tests don't touch real data
# ─────────────────────────────────────────────────────────────────────────────

class ScalpPortfolioTestCase(unittest.TestCase):
    """Base class that redirects scalp_portfolio to a temp JSON file."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self._tmp.close()
        self._orig_path = sp._PORTFOLIO_PATH
        sp._PORTFOLIO_PATH = self._tmp.name
        # Start fresh
        sp.reset()

    def tearDown(self):
        sp._PORTFOLIO_PATH = self._orig_path
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 1. Empty state
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyPortfolio(ScalpPortfolioTestCase):

    def test_initial_state(self):
        data = sp.load()
        self.assertEqual(data['capital'], 100_000)
        self.assertEqual(data['positions'], [])
        self.assertEqual(data['closed'], [])

    def test_summary_empty(self):
        data = sp.load()
        s = sp.get_summary(data)
        self.assertEqual(s['cash'], 100_000)
        self.assertEqual(s['market_value'], 0)
        self.assertEqual(s['unrealized'], 0)
        self.assertEqual(s['realized'], 0)
        self.assertEqual(s['open_count'], 0)
        self.assertEqual(s['closed_count'], 0)

    def test_available_cash_empty(self):
        data = sp.load()
        self.assertEqual(sp.available_cash(data), 100_000)

    def test_open_symbols_empty(self):
        data = sp.load()
        self.assertEqual(sp.open_symbols(data), set())


# ─────────────────────────────────────────────────────────────────────────────
# 2. Add position (buy)
# ─────────────────────────────────────────────────────────────────────────────

class TestAddPosition(ScalpPortfolioTestCase):

    def test_basic_buy(self):
        result = sp.add_position('AAPL', 150.00, 149.98, 150.06, quality='HIGH')
        self.assertTrue(result['added'])
        self.assertEqual(result['reason'], 'ok')
        data = result['data']
        self.assertEqual(len(data['positions']), 1)
        pos = data['positions'][0]
        self.assertEqual(pos['symbol'], 'AAPL')
        self.assertEqual(pos['entry_price'], 150.00)
        self.assertEqual(pos['stop'], 149.98)
        self.assertEqual(pos['target'], 150.06)
        self.assertEqual(pos['mode'], 'scalping')
        self.assertEqual(pos['quality'], 'HIGH')

    def test_position_sizing(self):
        result = sp.add_position('AAPL', 100.00, 99.98, 100.06)
        pos = result['data']['positions'][0]
        # 10% of $100K = $10K → 100 shares at $100
        self.assertEqual(pos['shares'], 100)
        self.assertEqual(pos['cost'], 10_000.00)

    def test_custom_position_pct(self):
        result = sp.add_position('AAPL', 100.00, 99.98, 100.06, position_pct=0.05)
        pos = result['data']['positions'][0]
        # 5% of $100K = $5K → 50 shares at $100
        self.assertEqual(pos['shares'], 50)
        self.assertEqual(pos['cost'], 5_000.00)

    def test_cash_deducted(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        data = sp.load()
        self.assertEqual(sp.available_cash(data), 90_000)

    def test_vol_ratio_stored(self):
        result = sp.add_position('AAPL', 100.00, 99.98, 100.06, vol_ratio=3.5)
        pos = result['data']['positions'][0]
        self.assertEqual(pos['vol_ratio'], 3.5)

    def test_duplicate_rejected(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        result = sp.add_position('AAPL', 101.00, 100.98, 101.06)
        self.assertFalse(result['added'])
        self.assertEqual(result['reason'], 'duplicate')
        # Still only 1 position
        data = sp.load()
        self.assertEqual(len(data['positions']), 1)

    def test_different_symbols_ok(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        result = sp.add_position('MSFT', 200.00, 199.98, 200.06)
        self.assertTrue(result['added'])
        data = sp.load()
        self.assertEqual(len(data['positions']), 2)

    def test_no_cash_rejected(self):
        # Fill up all capital: 10 positions at 10% each
        for i, sym in enumerate(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']):
            sp.add_position(sym, 100.00, 99.98, 100.06)
        result = sp.add_position('K', 100.00, 99.98, 100.06)
        self.assertFalse(result['added'])
        self.assertEqual(result['reason'], 'no_cash')

    def test_min_1_share(self):
        # Very expensive stock — should still get at least 1 share
        result = sp.add_position('BRK', 50000.00, 49999.98, 50000.06)
        pos = result['data']['positions'][0]
        self.assertEqual(pos['shares'], 1)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Close position (sell)
# ─────────────────────────────────────────────────────────────────────────────

class TestClosePosition(ScalpPortfolioTestCase):

    def test_close_winning_trade(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        result = sp.close_position('AAPL', 100.05, reason='trail_stop')
        self.assertTrue(result['closed'])
        self.assertGreater(result['pnl'], 0)
        data = result['data']
        self.assertEqual(len(data['positions']), 0)
        self.assertEqual(len(data['closed']), 1)
        trade = data['closed'][0]
        self.assertEqual(trade['exit_price'], 100.05)
        self.assertEqual(trade['close_reason'], 'trail_stop')
        self.assertAlmostEqual(trade['pnl_pct'], 0.05, places=1)

    def test_close_losing_trade(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        result = sp.close_position('AAPL', 99.97, reason='failed')
        self.assertTrue(result['closed'])
        self.assertLess(result['pnl'], 0)
        trade = result['data']['closed'][0]
        self.assertEqual(trade['close_reason'], 'failed')

    def test_close_nonexistent_symbol(self):
        result = sp.close_position('AAPL', 100.00)
        self.assertFalse(result['closed'])
        self.assertEqual(result['pnl'], 0)

    def test_cash_restored_after_close(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        data = sp.load()
        cash_before = sp.available_cash(data)

        sp.close_position('AAPL', 100.00)
        data = sp.load()
        cash_after = sp.available_cash(data)
        # Cash should be fully restored (position removed from invested)
        self.assertEqual(cash_after, 100_000)
        self.assertGreater(cash_after, cash_before)

    def test_close_only_one_when_multiple(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        sp.add_position('MSFT', 200.00, 199.98, 200.06)
        sp.close_position('AAPL', 100.05)
        data = sp.load()
        self.assertEqual(len(data['positions']), 1)
        self.assertEqual(data['positions'][0]['symbol'], 'MSFT')
        self.assertEqual(len(data['closed']), 1)

    def test_reentry_after_close(self):
        """After closing AAPL, should be able to re-enter."""
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        sp.close_position('AAPL', 100.05)
        result = sp.add_position('AAPL', 101.00, 100.98, 101.06)
        self.assertTrue(result['added'])
        data = sp.load()
        self.assertEqual(len(data['positions']), 1)
        self.assertEqual(len(data['closed']), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 4. PnL calculations
# ─────────────────────────────────────────────────────────────────────────────

class TestPnLCalculations(ScalpPortfolioTestCase):

    def test_pnl_math(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        data = sp.load()
        shares = data['positions'][0]['shares']  # 100

        result = sp.close_position('AAPL', 100.03)
        # PnL = (100.03 - 100.00) * 100 = $3.00
        self.assertAlmostEqual(result['pnl'], 3.00, places=2)
        trade = result['data']['closed'][0]
        self.assertAlmostEqual(trade['pnl_pct'], 0.03, places=2)

    def test_realized_pnl_in_summary(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        sp.close_position('AAPL', 100.10)

        sp.add_position('MSFT', 200.00, 199.98, 200.06)
        sp.close_position('MSFT', 199.90)

        data = sp.load()
        s = sp.get_summary(data)
        # AAPL: +$10, MSFT: -$5 → realized = +$5
        aapl_pnl = (100.10 - 100.00) * 100  # $10
        msft_pnl = (199.90 - 200.00) * 50   # -$5
        self.assertAlmostEqual(s['realized'], round(aapl_pnl + msft_pnl, 2), places=0)

    def test_unrealized_pnl_in_summary(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        # current_price is set to entry_price on add
        data = sp.load()
        s = sp.get_summary(data)
        self.assertEqual(s['unrealized'], 0)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Close all (EOD)
# ─────────────────────────────────────────────────────────────────────────────

class TestCloseAllEOD(ScalpPortfolioTestCase):

    @patch('scalp_portfolio.load')
    @patch('scalp_portfolio._save')
    def test_eod_closes_all_positions(self, mock_save, mock_load):
        mock_load.return_value = {
            'capital': 100_000,
            'positions': [
                {'symbol': 'AAPL', 'entry_price': 100.0, 'shares': 100,
                 'cost': 10_000, 'current_price': 100.05, 'stop': 99.98,
                 'target': 100.06, 'mode': 'scalping', 'quality': 'HIGH',
                 'date_added': '2026-03-10 10:00', 'vol_ratio': 2.0},
                {'symbol': 'MSFT', 'entry_price': 200.0, 'shares': 50,
                 'cost': 10_000, 'current_price': 199.90, 'stop': 199.98,
                 'target': 200.06, 'mode': 'scalping', 'quality': 'HIGH',
                 'date_added': '2026-03-10 10:05', 'vol_ratio': 1.5},
            ],
            'closed': [],
            'last_updated': None,
        }

        with patch('yfinance.Ticker') as mock_ticker:
            mock_hist = MagicMock()
            mock_hist.empty = True
            mock_ticker.return_value.history.return_value = mock_hist

            result = sp.close_all_eod()

        self.assertEqual(sorted(result['closed']), ['AAPL', 'MSFT'])
        saved_data = mock_save.call_args[0][0]
        self.assertEqual(len(saved_data['positions']), 0)
        self.assertEqual(len(saved_data['closed']), 2)
        for trade in saved_data['closed']:
            self.assertEqual(trade['close_reason'], 'eod')

    def test_eod_empty_portfolio(self):
        result = sp.close_all_eod()
        self.assertEqual(result['closed'], [])


# ─────────────────────────────────────────────────────────────────────────────
# 6. Summary calculations
# ─────────────────────────────────────────────────────────────────────────────

class TestSummary(ScalpPortfolioTestCase):

    def test_market_value_with_positions(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        data = sp.load()
        s = sp.get_summary(data)
        # 100 shares × $100 = $10,000
        self.assertEqual(s['market_value'], 10_000)
        self.assertEqual(s['cash'], 90_000)
        self.assertEqual(s['open_count'], 1)

    def test_summary_after_multiple_trades(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        sp.add_position('MSFT', 200.00, 199.98, 200.06)
        sp.close_position('AAPL', 100.10)

        data = sp.load()
        s = sp.get_summary(data)
        self.assertEqual(s['open_count'], 1)
        self.assertEqual(s['closed_count'], 1)
        self.assertGreater(s['realized'], 0)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Reset
# ─────────────────────────────────────────────────────────────────────────────

class TestReset(ScalpPortfolioTestCase):

    def test_reset_clears_everything(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        sp.close_position('AAPL', 100.05)
        sp.add_position('MSFT', 200.00, 199.98, 200.06)

        sp.reset()
        data = sp.load()
        self.assertEqual(data['capital'], 100_000)
        self.assertEqual(data['positions'], [])
        self.assertEqual(data['closed'], [])


# ─────────────────────────────────────────────────────────────────────────────
# 8. Persistence (load/save round-trip)
# ─────────────────────────────────────────────────────────────────────────────

class TestPersistence(ScalpPortfolioTestCase):

    def test_data_survives_reload(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06, quality='GOLD', vol_ratio=2.5)
        # Force reload from disk
        data = sp.load()
        self.assertEqual(len(data['positions']), 1)
        pos = data['positions'][0]
        self.assertEqual(pos['symbol'], 'AAPL')
        self.assertEqual(pos['quality'], 'GOLD')
        self.assertEqual(pos['vol_ratio'], 2.5)
        self.assertIsNotNone(data['last_updated'])

    def test_closed_trades_persist(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        sp.close_position('AAPL', 100.05, reason='trail_stop')
        data = sp.load()
        self.assertEqual(len(data['closed']), 1)
        self.assertEqual(data['closed'][0]['close_reason'], 'trail_stop')


# ─────────────────────────────────────────────────────────────────────────────
# 9. Refresh prices
# ─────────────────────────────────────────────────────────────────────────────

class TestRefreshPrices(ScalpPortfolioTestCase):

    def test_refresh_empty(self):
        result = sp.refresh_prices()
        self.assertEqual(result['updated'], 0)

    @patch('scalp_portfolio.load')
    @patch('scalp_portfolio._save')
    def test_refresh_updates_current_price(self, mock_save, mock_load):
        import pandas as pd
        mock_load.return_value = {
            'capital': 100_000,
            'positions': [
                {'symbol': 'AAPL', 'entry_price': 100.0, 'shares': 100,
                 'cost': 10_000, 'current_price': 100.0, 'stop': 99.98,
                 'target': 100.06, 'mode': 'scalping', 'quality': 'HIGH',
                 'date_added': '2026-03-10 10:00', 'vol_ratio': 2.0},
            ],
            'closed': [],
            'last_updated': None,
        }

        mock_hist = pd.DataFrame({'Close': [101.50]})
        with patch('yfinance.Ticker') as mock_ticker:
            mock_ticker.return_value.history.return_value = mock_hist
            result = sp.refresh_prices()

        self.assertEqual(result['updated'], 1)
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved['positions'][0]['current_price'], 101.5)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Integration: feedback agent → scalp portfolio
# ─────────────────────────────────────────────────────────────────────────────

class TestFeedbackAgentIntegration(ScalpPortfolioTestCase):
    """Test that the feedback agent's BREAKOUT/EXIT events trigger portfolio actions."""

    def test_breakout_buys_into_portfolio(self):
        """Simulate what scan_feedback_agent does on BREAKOUT."""
        symbol = 'AAPL'
        price = 150.00
        scalp_fail_cents = 2
        scalp_trail_cents = 3

        stop_px = price - (scalp_fail_cents / 100.0)
        target_px = price + (scalp_trail_cents * 2 / 100.0)

        result = sp.add_position(symbol, price, stop_px, target_px, vol_ratio=2.5)
        self.assertTrue(result['added'])

        data = sp.load()
        pos = data['positions'][0]
        self.assertEqual(pos['symbol'], 'AAPL')
        self.assertEqual(pos['stop'], 149.98)    # 2 cents below
        self.assertEqual(pos['target'], 150.06)   # 6 cents above

    def test_exit_sells_from_portfolio(self):
        """Simulate what scan_feedback_agent does on EXIT."""
        sp.add_position('AAPL', 150.00, 149.98, 150.06)

        result = sp.close_position('AAPL', 149.97, reason='failed')
        self.assertTrue(result['closed'])
        self.assertLess(result['pnl'], 0)

        data = sp.load()
        self.assertEqual(len(data['positions']), 0)
        self.assertEqual(data['closed'][0]['close_reason'], 'failed')

    def test_full_trade_lifecycle(self):
        """BREAKOUT → price moves up → TRAIL_STOP exit."""
        # Buy
        sp.add_position('AAPL', 150.00, 149.98, 150.06, quality='HIGH', vol_ratio=3.0)

        # Sell at profit (trail stop)
        result = sp.close_position('AAPL', 150.04, reason='trail_stop')
        self.assertTrue(result['closed'])
        self.assertGreater(result['pnl'], 0)

        data = sp.load()
        s = sp.get_summary(data)
        self.assertEqual(s['open_count'], 0)
        self.assertEqual(s['closed_count'], 1)
        self.assertGreater(s['realized'], 0)

        # Can re-enter same symbol
        result2 = sp.add_position('AAPL', 151.00, 150.98, 151.06)
        self.assertTrue(result2['added'])


# ─────────────────────────────────────────────────────────────────────────────
# 11. Edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases(ScalpPortfolioTestCase):

    def test_penny_stock(self):
        result = sp.add_position('PENNY', 0.50, 0.48, 0.56)
        self.assertTrue(result['added'])
        pos = result['data']['positions'][0]
        # $10K / $0.50 = 20,000 shares
        self.assertEqual(pos['shares'], 20_000)

    def test_expensive_stock(self):
        """Stock at $500K exceeds $100K capital → rejected with no_cash."""
        result = sp.add_position('BRK', 500_000.00, 499_999.98, 500_000.06)
        self.assertFalse(result['added'])
        self.assertEqual(result['reason'], 'no_cash')

    def test_close_position_with_zero_pnl(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        result = sp.close_position('AAPL', 100.00)
        self.assertTrue(result['closed'])
        self.assertEqual(result['pnl'], 0)

    def test_summary_with_no_closed(self):
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        data = sp.load()
        s = sp.get_summary(data)
        self.assertEqual(s['realized'], 0)
        self.assertEqual(s['closed_count'], 0)


# ─────────────────────────────────────────────────────────────────────────────
# 12. Capital updates on close (bug fix validation)
# ─────────────────────────────────────────────────────────────────────────────

class TestCapitalUpdatesOnClose(ScalpPortfolioTestCase):

    def test_capital_increases_on_profitable_close(self):
        """Capital should grow when a position is closed at profit."""
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        data = sp.load()
        shares = data['positions'][0]['shares']  # 100

        sp.close_position('AAPL', 100.10)
        data = sp.load()
        expected_pnl = (100.10 - 100.00) * shares  # $10
        self.assertAlmostEqual(data['capital'], 100_000 + expected_pnl, places=2)

    def test_capital_decreases_on_loss(self):
        """Capital should shrink when a position is closed at a loss."""
        sp.add_position('MSFT', 200.00, 199.98, 200.06)
        data = sp.load()
        shares = data['positions'][0]['shares']  # 50

        sp.close_position('MSFT', 199.90)
        data = sp.load()
        expected_pnl = (199.90 - 200.00) * shares  # -$5
        self.assertAlmostEqual(data['capital'], 100_000 + expected_pnl, places=2)

    def test_cash_reflects_realized_pnl(self):
        """After closing all positions, cash should equal capital (including realized P&L)."""
        sp.add_position('AAPL', 100.00, 99.98, 100.06)
        sp.close_position('AAPL', 100.10)

        data = sp.load()
        s = sp.get_summary(data)
        # No open positions → cash should equal capital
        self.assertEqual(s['cash'], s['capital'])
        self.assertGreater(s['cash'], 100_000)


if __name__ == '__main__':
    unittest.main()
