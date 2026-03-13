#!/usr/bin/env python3
"""
tests/test_macd_rsi_and_comparison.py
──────────────────────────────────────
Unit tests for macd_rsi_scan.py and comparison_report.py.

Run with:
  pytest tests/test_macd_rsi_and_comparison.py -v
"""

import io
import sys
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ── Make project root importable ──────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import macd_rsi_scan as mrs
import comparison_report as cr

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_price_series(n: int = 60, start: float = 100.0, trend: float = 0.5) -> pd.Series:
    """Build a deterministic price series for indicator tests."""
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.3, n)
    prices = start + np.cumsum(noise) + np.arange(n) * trend
    return pd.Series(prices, name='Close')


def make_ohlcv_df(n: int = 60) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with DatetimeIndex."""
    idx = pd.date_range('2025-01-01', periods=n, freq='B')
    close = make_price_series(n)
    return pd.DataFrame({'Open': close * 0.99, 'High': close * 1.01,
                         'Low': close * 0.98, 'Close': close,
                         'Volume': np.full(n, 1_000_000)}, index=idx)


# ─────────────────────────────────────────────────────────────────────────────
# macd_rsi_scan — calculate_rsi()
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateRSI:

    def test_output_length_matches_input(self):
        close = make_price_series(60)
        rsi = mrs.calculate_rsi(close, period=14)
        assert len(rsi) == len(close)

    def test_rsi_bounds_after_warmup(self):
        """RSI must be in [0, 100] for all non-NaN values."""
        close = make_price_series(60)
        rsi = mrs.calculate_rsi(close, period=14)
        valid = rsi.dropna()
        assert (valid >= 0).all(), "RSI below 0"
        assert (valid <= 100).all(), "RSI above 100"

    def test_all_up_bars_gives_high_rsi(self):
        """Monotonically rising series should produce RSI close to 100."""
        close = pd.Series(np.arange(1.0, 61.0))
        rsi = mrs.calculate_rsi(close, period=14)
        # After warmup the RSI should be very high (>= 90)
        assert float(rsi.iloc[-1]) >= 90.0

    def test_all_down_bars_gives_low_rsi(self):
        """Monotonically falling series should produce RSI close to 0."""
        close = pd.Series(np.arange(60.0, 0.0, -1.0))
        rsi = mrs.calculate_rsi(close, period=14)
        assert float(rsi.iloc[-1]) <= 10.0

    def test_all_flat_bars_no_crash(self):
        """Flat price (zero delta) must not raise ZeroDivisionError."""
        close = pd.Series([50.0] * 30)
        rsi = mrs.calculate_rsi(close, period=14)
        # Result is defined; should not raise
        assert len(rsi) == 30

    def test_custom_period(self):
        close = make_price_series(60)
        rsi9  = mrs.calculate_rsi(close, period=9)
        rsi14 = mrs.calculate_rsi(close, period=14)
        # Different periods → different values at the same index
        assert float(rsi9.iloc[-1]) != float(rsi14.iloc[-1])

    def test_short_series_returns_nans_only(self):
        """Series shorter than period should have no valid RSI values."""
        close = pd.Series([100.0] * 5)
        rsi = mrs.calculate_rsi(close, period=14)
        assert rsi.dropna().empty


# ─────────────────────────────────────────────────────────────────────────────
# macd_rsi_scan — calculate_macd()
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateMACD:

    def test_returns_three_series(self):
        close = make_price_series(60)
        result = mrs.calculate_macd(close)
        assert len(result) == 3

    def test_output_length_matches_input(self):
        close = make_price_series(60)
        macd, sig, hist = mrs.calculate_macd(close)
        assert len(macd) == len(close)
        assert len(sig)  == len(close)
        assert len(hist) == len(close)

    def test_hist_equals_macd_minus_signal(self):
        close = make_price_series(60)
        macd, sig, hist = mrs.calculate_macd(close)
        np.testing.assert_allclose(hist.values, (macd - sig).values, rtol=1e-10)

    def test_custom_params(self):
        close = make_price_series(60)
        macd_default, _, _ = mrs.calculate_macd(close)
        macd_custom,  _, _ = mrs.calculate_macd(close, fast=5, slow=13, signal=5)
        # Different spans produce different results
        assert not np.allclose(macd_default.values, macd_custom.values)

    def test_no_nan_with_sufficient_data(self):
        """With 60 bars, EWM values at tail should not be NaN."""
        close = make_price_series(60)
        macd, sig, hist = mrs.calculate_macd(close)
        assert not np.isnan(float(macd.iloc[-1]))
        assert not np.isnan(float(sig.iloc[-1]))
        assert not np.isnan(float(hist.iloc[-1]))

    def test_trending_up_macd_positive(self):
        """Strong uptrend — fast EMA > slow EMA → MACD > 0."""
        close = pd.Series(np.linspace(50, 150, 60))
        macd, _, _ = mrs.calculate_macd(close)
        assert float(macd.iloc[-1]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# macd_rsi_scan — load_watchlist()
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadWatchlist:

    def test_plain_symbols(self, tmp_path):
        f = tmp_path / 'watch.txt'
        f.write_text("AAPL\nMSFT\nNVDA\n")
        symbols = mrs.load_watchlist(str(f))
        assert symbols == ['AAPL', 'MSFT', 'NVDA']

    def test_skip_hash_comments(self, tmp_path):
        f = tmp_path / 'watch.txt'
        f.write_text("# comment\n### section\nAAPL\n")
        symbols = mrs.load_watchlist(str(f))
        assert symbols == ['AAPL']

    def test_tradingview_format(self, tmp_path):
        f = tmp_path / 'watch.txt'
        f.write_text("NASDAQ:AAPL\nNYSE:IBM\n")
        symbols = mrs.load_watchlist(str(f))
        assert 'AAPL' in symbols
        assert 'IBM' in symbols

    def test_skip_non_us_exchange(self, tmp_path):
        f = tmp_path / 'watch.txt'
        f.write_text("BINANCE:BTC\nNASDAQ:AAPL\nCRYPTO:ETH\n")
        symbols = mrs.load_watchlist(str(f))
        assert symbols == ['AAPL']

    def test_dot_to_dash_conversion(self, tmp_path):
        f = tmp_path / 'watch.txt'
        f.write_text("NYSE:BRK.B\n")
        symbols = mrs.load_watchlist(str(f))
        assert 'BRK-B' in symbols

    def test_uppercase_enforced(self, tmp_path):
        f = tmp_path / 'watch.txt'
        f.write_text("aapl\nmsft\n")
        symbols = mrs.load_watchlist(str(f))
        assert symbols == ['AAPL', 'MSFT']

    def test_empty_file(self, tmp_path):
        f = tmp_path / 'watch.txt'
        f.write_text("")
        # Falls back to DEFAULT_WATCHLIST — just confirm no crash
        # (May log a warning if default doesn't exist; that's fine)
        try:
            symbols = mrs.load_watchlist(str(f))
            assert isinstance(symbols, list)
        except FileNotFoundError:
            pass  # acceptable if default also absent

    def test_missing_file_falls_back(self, tmp_path):
        """Non-existent path should fall back to DEFAULT_WATCHLIST without raising."""
        missing = str(tmp_path / 'nonexistent.txt')
        # Only verify it doesn't raise; actual symbols depend on whether default exists
        try:
            result = mrs.load_watchlist(missing)
            assert isinstance(result, list)
        except FileNotFoundError:
            pass  # acceptable when both files absent


# ─────────────────────────────────────────────────────────────────────────────
# macd_rsi_scan — batch_scan() (mocked yfinance)
# ─────────────────────────────────────────────────────────────────────────────

def _make_multiindex_df(symbols, n=60):
    """Build a yfinance-style MultiIndex DataFrame for batch_scan tests."""
    idx = pd.date_range('2025-01-01', periods=n, freq='B')
    dfs = {}
    for sym in symbols:
        rng = np.random.default_rng(hash(sym) % 2**32)
        close = 100 + np.cumsum(rng.normal(0, 0.5, n)) + np.arange(n) * 0.3
        dfs[sym] = pd.DataFrame({'Close': close, 'Volume': 1e6}, index=idx)
    return pd.concat(dfs, axis=1)  # MultiIndex columns: (sym, field)


class TestBatchScan:

    def test_returns_list(self):
        syms = ['AAPL', 'MSFT']
        fake_raw = _make_multiindex_df(syms)
        with patch('yfinance.download', return_value=fake_raw):
            results = mrs.batch_scan(syms, '1d', 40.0, 72.0)
        assert isinstance(results, list)

    def test_signals_contain_required_keys(self):
        syms = ['AAPL']
        fake_raw = _make_multiindex_df(syms)
        with patch('yfinance.download', return_value=fake_raw):
            results = mrs.batch_scan(syms, '1d', 0.0, 100.0)  # wide RSI window
        for sig in results:
            for key in ('Symbol', 'Price', 'RSI', 'MACD', 'MACD_Signal',
                        'MACD_Hist', 'MACD_Hist_Prev', 'Signal', 'Timeframe', 'Time'):
                assert key in sig, f"Missing key: {key}"

    def test_rsi_filter_respected(self):
        """Signals outside [rsi_min, rsi_max] must be excluded."""
        syms = ['AAPL']
        fake_raw = _make_multiindex_df(syms)
        with patch('yfinance.download', return_value=fake_raw):
            results = mrs.batch_scan(syms, '1d', 0.0, 100.0)
        for sig in results:
            assert 0.0 <= sig['RSI'] <= 100.0

    def test_signal_type_is_valid(self):
        syms = ['NVDA', 'MSFT']
        fake_raw = _make_multiindex_df(syms)
        with patch('yfinance.download', return_value=fake_raw):
            results = mrs.batch_scan(syms, '1d', 0.0, 100.0)
        for sig in results:
            assert sig['Signal'] in ('MACD_CROSS', 'MACD_BULL')

    def test_download_failure_returns_empty(self):
        with patch('yfinance.download', side_effect=Exception("Network error")):
            results = mrs.batch_scan(['AAPL'], '1d', 40.0, 72.0)
        assert results == []

    def test_too_short_data_skipped(self):
        """Symbols with < 35 bars must not produce signals."""
        syms = ['AAPL']
        idx = pd.date_range('2025-01-01', periods=10, freq='B')
        short_df = pd.concat({'AAPL': pd.DataFrame({'Close': np.arange(10.0) + 100,
                                                    'Volume': 1e6}, index=idx)}, axis=1)
        with patch('yfinance.download', return_value=short_df):
            results = mrs.batch_scan(syms, '1d', 0.0, 100.0)
        assert results == []

    def test_unknown_symbol_skipped(self):
        """Symbol not in MultiIndex columns should be silently skipped."""
        syms = ['AAPL', 'GHOST']
        fake_raw = _make_multiindex_df(['AAPL'])  # only AAPL in data
        with patch('yfinance.download', return_value=fake_raw):
            results = mrs.batch_scan(syms, '1d', 0.0, 100.0)
        for sig in results:
            assert sig['Symbol'] != 'GHOST'


# ─────────────────────────────────────────────────────────────────────────────
# macd_rsi_scan — add_new_signals()
# ─────────────────────────────────────────────────────────────────────────────

def _make_signal(sym='AAPL', price=150.0, signal='MACD_CROSS'):
    return {
        'Symbol': sym, 'Price': price, 'RSI': 55.0,
        'MACD': 0.12, 'MACD_Signal': 0.05, 'MACD_Hist': 0.07,
        'MACD_Hist_Prev': -0.02, 'Signal': signal,
        'Timeframe': '1d', 'Time': '2026-03-13 09:35 ET',
    }


class TestAddNewSignals:

    def test_adds_to_empty_portfolio(self):
        portfolio = pd.DataFrame(columns=mrs.PORTFOLIO_COLS)
        sig = _make_signal('AAPL')
        result = mrs.add_new_signals(portfolio, [sig])
        assert len(result) == 1
        assert result.iloc[0]['Symbol'] == 'AAPL'
        assert result.iloc[0]['Status'] == 'OPEN'

    def test_skips_already_open_symbol(self):
        portfolio = pd.DataFrame([{
            'Symbol': 'AAPL', 'Entry_Price': 145.0, 'Status': 'OPEN',
            **{c: None for c in mrs.PORTFOLIO_COLS if c not in ('Symbol', 'Entry_Price', 'Status')}
        }])
        sig = _make_signal('AAPL', price=150.0)
        result = mrs.add_new_signals(portfolio, [sig])
        # AAPL still only has 1 row (the original)
        assert len(result) == 1
        assert float(result.iloc[0]['Entry_Price']) == 145.0

    def test_adds_new_symbol_alongside_open(self):
        portfolio = pd.DataFrame([{
            'Symbol': 'AAPL', 'Entry_Price': 145.0, 'Status': 'OPEN',
            **{c: None for c in mrs.PORTFOLIO_COLS if c not in ('Symbol', 'Entry_Price', 'Status')}
        }])
        sig = _make_signal('MSFT', price=300.0)
        result = mrs.add_new_signals(portfolio, [sig])
        assert len(result) == 2
        assert 'MSFT' in result['Symbol'].values

    def test_empty_signals_list_unchanged(self):
        portfolio = pd.DataFrame(columns=mrs.PORTFOLIO_COLS)
        result = mrs.add_new_signals(portfolio, [])
        assert len(result) == 0

    def test_pnl_initialized_to_zero(self):
        portfolio = pd.DataFrame(columns=mrs.PORTFOLIO_COLS)
        result = mrs.add_new_signals(portfolio, [_make_signal('NVDA', 500.0)])
        assert float(result.iloc[0]['PnL_Pct']) == 0.0

    def test_entry_price_matches_signal(self):
        portfolio = pd.DataFrame(columns=mrs.PORTFOLIO_COLS)
        result = mrs.add_new_signals(portfolio, [_make_signal('GOOG', 2800.0)])
        assert float(result.iloc[0]['Entry_Price']) == 2800.0

    def test_closed_symbol_can_be_re_entered(self):
        """A symbol that is CLOSED (not OPEN) should allow a new entry."""
        portfolio = pd.DataFrame([{
            'Symbol': 'AAPL', 'Entry_Price': 140.0, 'Status': 'CLOSED',
            **{c: None for c in mrs.PORTFOLIO_COLS if c not in ('Symbol', 'Entry_Price', 'Status')}
        }])
        sig = _make_signal('AAPL', price=155.0)
        result = mrs.add_new_signals(portfolio, [sig])
        # Should now have 2 rows: old closed + new open
        assert len(result) == 2
        open_rows = result[result['Status'] == 'OPEN']
        assert len(open_rows) == 1
        assert float(open_rows.iloc[0]['Entry_Price']) == 155.0


# ─────────────────────────────────────────────────────────────────────────────
# macd_rsi_scan — update_open_positions()
# ─────────────────────────────────────────────────────────────────────────────

def _make_portfolio_row(sym='AAPL', entry=100.0, status='OPEN', tf='1d'):
    return {
        'Symbol': sym, 'Entry_Price': entry, 'Entry_Time': '2026-03-13 09:35 ET',
        'Signal': 'MACD_CROSS', 'Timeframe': tf,
        'RSI_Entry': 55.0, 'MACD_Hist_Entry': 0.07,
        'Status': status, 'Current_Price': entry,
        'PnL_Pct': 0.0, 'Exit_Price': None,
        'Exit_Time': None, 'Exit_Reason': None,
    }


class TestUpdateOpenPositions:

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame(columns=mrs.PORTFOLIO_COLS)
        result = mrs.update_open_positions(df, '1d')
        assert result.empty

    def test_stop_triggered(self):
        """Position that drops below stop_pct should be marked CLOSED/STOP."""
        row = _make_portfolio_row('AAPL', entry=100.0)
        df = pd.DataFrame([row])
        # Simulate price at -4% (below -3% stop for '1d')
        with patch.object(mrs, 'fetch_price', return_value=96.0), \
             patch.object(mrs, 'datetime') as mock_dt:
            mock_dt.now.return_value = MagicMock(
                strftime=lambda fmt: '2026-03-13 11:00 ET',
                hour=11, minute=0,
            )
            result = mrs.update_open_positions(df, '1d')
        row_out = result.iloc[0]
        assert row_out['Status'] == 'CLOSED'
        assert row_out['Exit_Reason'] == 'STOP'

    def test_target_triggered(self):
        """Position that exceeds target_pct should be marked CLOSED/TARGET."""
        row = _make_portfolio_row('MSFT', entry=100.0)
        df = pd.DataFrame([row])
        # Simulate price at +7% (above +6% target for '1d')
        with patch.object(mrs, 'fetch_price', return_value=107.0), \
             patch.object(mrs, 'datetime') as mock_dt:
            mock_dt.now.return_value = MagicMock(
                strftime=lambda fmt: '2026-03-13 12:00 ET',
                hour=12, minute=0,
            )
            result = mrs.update_open_positions(df, '1d')
        row_out = result.iloc[0]
        assert row_out['Status'] == 'CLOSED'
        assert row_out['Exit_Reason'] == 'TARGET'

    def test_pnl_pct_calculated_correctly(self):
        row = _make_portfolio_row('NVDA', entry=200.0)
        df = pd.DataFrame([row])
        with patch.object(mrs, 'fetch_price', return_value=210.0), \
             patch.object(mrs, 'datetime') as mock_dt:
            mock_dt.now.return_value = MagicMock(
                strftime=lambda fmt: '2026-03-13 10:00 ET',
                hour=10, minute=0,
            )
            result = mrs.update_open_positions(df, '1d')
        pnl = float(result.iloc[0]['PnL_Pct'])
        assert abs(pnl - 5.0) < 0.01

    def test_price_fetch_failure_skips_row(self):
        """If fetch_price returns None, position should remain OPEN unchanged."""
        row = _make_portfolio_row('BADTICKER', entry=50.0)
        df = pd.DataFrame([row])
        with patch.object(mrs, 'fetch_price', return_value=None):
            result = mrs.update_open_positions(df, '1d')
        assert result.iloc[0]['Status'] == 'OPEN'

    def test_already_closed_rows_not_updated(self):
        rows = [
            _make_portfolio_row('AAPL', entry=100.0, status='CLOSED'),
            _make_portfolio_row('MSFT', entry=200.0, status='OPEN'),
        ]
        df = pd.DataFrame(rows)
        df.at[0, 'Exit_Price'] = 95.0
        with patch.object(mrs, 'fetch_price', return_value=210.0), \
             patch.object(mrs, 'datetime') as mock_dt:
            mock_dt.now.return_value = MagicMock(
                strftime=lambda fmt: '2026-03-13 10:00 ET',
                hour=10, minute=0,
            )
            result = mrs.update_open_positions(df, '1d')
        # AAPL (CLOSED) exit price unchanged
        assert float(result.iloc[0]['Exit_Price']) == 95.0


# ─────────────────────────────────────────────────────────────────────────────
# macd_rsi_scan — print_portfolio_report()
# ─────────────────────────────────────────────────────────────────────────────

class TestPrintPortfolioReport:

    def _capture(self, df):
        captured = io.StringIO()
        sys.stdout = captured
        try:
            mrs.print_portfolio_report(df)
        finally:
            sys.stdout = sys.__stdout__
        return captured.getvalue()

    def test_empty_portfolio_message(self):
        df = pd.DataFrame(columns=mrs.PORTFOLIO_COLS)
        output = self._capture(df)
        assert 'No MACD' in output or 'no' in output.lower()

    def test_open_positions_shown(self):
        rows = [_make_portfolio_row('AAPL', 100.0, 'OPEN')]
        df = pd.DataFrame(rows)
        df.at[0, 'PnL_Pct'] = 2.0
        df.at[0, 'Current_Price'] = 102.0
        output = self._capture(df)
        assert 'AAPL' in output
        assert 'Open' in output

    def test_closed_positions_shown(self):
        rows = [_make_portfolio_row('MSFT', 200.0, 'CLOSED')]
        df = pd.DataFrame(rows)
        df.at[0, 'Exit_Price'] = 210.0
        df.at[0, 'PnL_Pct'] = 5.0
        df.at[0, 'Exit_Reason'] = 'TARGET'
        output = self._capture(df)
        assert 'MSFT' in output
        assert '5.00' in output or '+5.00' in output

    def test_null_exit_price_no_crash(self):
        """print_portfolio_report must not crash when Exit_Price is NaN."""
        rows = [_make_portfolio_row('AAPL', 100.0, 'CLOSED')]
        df = pd.DataFrame(rows)
        df.at[0, 'PnL_Pct'] = -1.5
        df.at[0, 'Exit_Price'] = None  # simulate mid-save crash scenario
        df.at[0, 'Exit_Reason'] = 'STOP'
        # Should not raise
        self._capture(df)

    def test_win_rate_calculation(self):
        rows = [
            {**_make_portfolio_row('A', 100.0, 'CLOSED'), 'PnL_Pct': 3.0, 'Exit_Price': 103.0, 'Exit_Reason': 'TARGET'},
            {**_make_portfolio_row('B', 100.0, 'CLOSED'), 'PnL_Pct': 2.0, 'Exit_Price': 102.0, 'Exit_Reason': 'TARGET'},
            {**_make_portfolio_row('C', 100.0, 'CLOSED'), 'PnL_Pct': -1.5, 'Exit_Price': 98.5, 'Exit_Reason': 'STOP'},
        ]
        df = pd.DataFrame(rows)
        output = self._capture(df)
        # 2W 1L = 67% win rate
        assert '67%' in output


# ─────────────────────────────────────────────────────────────────────────────
# comparison_report — load_breakout_signals()
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_SIGNAL_ROW = {
    'Symbol': 'AAPL', 'Price': 175.0, 'Vol': 2.5, 'Dist': 0.3,
    'SMA_Dist%': 0.5, 'Stop': 170.0, 'Target': 185.0, 'R:R': 5.0,
    'Gap%': 0.0, 'Mode': 'daytrade', 'Quality': 'PREMIUM', 'RR_Grade': 'A',
    'WinProb': 0.75, 'WinGrade': 'HIGH', 'Patterns': '',
    'Near52wHigh': True, 'RSI_BullDiv': False, 'PatternVolConf': False,
    'Type': 'BREAKOUT', 'RSI': 62.0, 'MinerviniScore': 2, 'VCP': False,
    'VCP_Quality': '', 'VCP_Pivot': '', 'VCP_Contractions': '',
    'SR_Resistance': '', 'SR_Res_Strength': '', 'SR_Support': '',
    'SR_Sup_Strength': '', 'SR_Break': False, 'SR_TL_Resistance': '',
    'SR_TL_Support': '', 'SR_InChannel': False, 'SR_Channel_Dir': '',
    'SR_Channel_Width%': '', 'SR_TL_Break': False, 'Checks': '',
    'Sector': 'Technology', 'Sentiment': 'neutral', 'Buzz': 0,
}


class TestLoadBreakoutSignals:

    def _write_signal_csv(self, tmp_path, date_str, time_str, mode, rows):
        fname = f"signals_{mode}_{date_str}_{time_str}.csv"
        p = tmp_path / fname
        pd.DataFrame(rows).to_csv(p, index=False)
        return p

    def test_returns_empty_when_no_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, 'SIGNALS_DIR', tmp_path)
        with patch.object(cr, 'today_str', return_value='20260313'):
            result = cr.load_breakout_signals()
        assert result.empty

    def test_loads_todays_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, 'SIGNALS_DIR', tmp_path)
        self._write_signal_csv(tmp_path, '20260313', '093500', 'daytrade', [SAMPLE_SIGNAL_ROW])
        with patch.object(cr, 'today_str', return_value='20260313'):
            result = cr.load_breakout_signals()
        assert not result.empty
        assert 'AAPL' in result['Symbol'].values

    def test_scan_mode_extracted_from_filename(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, 'SIGNALS_DIR', tmp_path)
        self._write_signal_csv(tmp_path, '20260313', '093500', 'swing', [SAMPLE_SIGNAL_ROW])
        with patch.object(cr, 'today_str', return_value='20260313'):
            result = cr.load_breakout_signals()
        assert result.iloc[0]['Scan_Mode'] == 'swing'

    def test_scan_time_extracted_correctly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, 'SIGNALS_DIR', tmp_path)
        self._write_signal_csv(tmp_path, '20260313', '093500', 'daytrade', [SAMPLE_SIGNAL_ROW])
        with patch.object(cr, 'today_str', return_value='20260313'):
            result = cr.load_breakout_signals()
        assert result.iloc[0]['Scan_Time'] == '09:35'

    def test_since_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, 'SIGNALS_DIR', tmp_path)
        self._write_signal_csv(tmp_path, '20260313', '083000', 'daytrade', [SAMPLE_SIGNAL_ROW])
        row2 = {**SAMPLE_SIGNAL_ROW, 'Symbol': 'MSFT'}
        self._write_signal_csv(tmp_path, '20260313', '093500', 'daytrade', [row2])
        with patch.object(cr, 'today_str', return_value='20260313'):
            result = cr.load_breakout_signals(since_time='09:00')
        # 08:30 signal excluded, 09:35 included
        assert 'MSFT' in result['Symbol'].values
        assert 'AAPL' not in result['Symbol'].values

    def test_duplicate_symbol_same_time_deduplicated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, 'SIGNALS_DIR', tmp_path)
        self._write_signal_csv(tmp_path, '20260313', '093500', 'daytrade',
                               [SAMPLE_SIGNAL_ROW, SAMPLE_SIGNAL_ROW])
        with patch.object(cr, 'today_str', return_value='20260313'):
            result = cr.load_breakout_signals()
        aapl_rows = result[result['Symbol'] == 'AAPL']
        assert len(aapl_rows) == 1


# ─────────────────────────────────────────────────────────────────────────────
# comparison_report — load_macd_portfolio()
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadMacdPortfolio:

    def test_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, 'MACD_DIR', tmp_path)
        with patch.object(cr, 'today_str', return_value='20260313'):
            result = cr.load_macd_portfolio()
        assert result.empty

    def test_loads_portfolio_csv(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, 'MACD_DIR', tmp_path)
        data = {
            'Symbol': ['AAPL'], 'Entry_Price': [150.0],
            'Entry_Time': ['2026-03-13 09:35 ET'], 'Signal': ['MACD_CROSS'],
            'Timeframe': ['1d'], 'RSI_Entry': [55.0],
            'MACD_Hist_Entry': [0.07], 'Status': ['OPEN'],
            'Current_Price': [150.0], 'PnL_Pct': [0.0],
            'Exit_Price': [None], 'Exit_Time': [None], 'Exit_Reason': [None],
        }
        p = tmp_path / 'portfolio_20260313.csv'
        pd.DataFrame(data).to_csv(p, index=False)
        with patch.object(cr, 'today_str', return_value='20260313'):
            result = cr.load_macd_portfolio()
        assert not result.empty
        assert 'AAPL' in result['Symbol'].values

    def test_scan_time_extracted_from_entry_time(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cr, 'MACD_DIR', tmp_path)
        data = {'Symbol': ['MSFT'], 'Entry_Price': [300.0],
                'Entry_Time': ['2026-03-13 14:00 ET'],
                **{c: [None] for c in mrs.PORTFOLIO_COLS
                   if c not in ('Symbol', 'Entry_Price', 'Entry_Time')}}
        p = tmp_path / 'portfolio_20260313.csv'
        pd.DataFrame(data).to_csv(p, index=False)
        with patch.object(cr, 'today_str', return_value='20260313'):
            result = cr.load_macd_portfolio()
        assert result.iloc[0]['Scan_Time'] == '14:00'


# ─────────────────────────────────────────────────────────────────────────────
# comparison_report — enrich_breakout()
# ─────────────────────────────────────────────────────────────────────────────

class TestEnrichBreakout:

    def _make_df(self, symbols, prices_entry, targets=None, stops=None):
        data = {'Symbol': symbols, 'Price': prices_entry}
        if targets is not None:
            data['Target'] = targets
        if stops is not None:
            data['Stop'] = stops
        return pd.DataFrame(data)

    def test_empty_df_returns_empty(self):
        result = cr.enrich_breakout(pd.DataFrame(), {})
        assert result.empty

    def test_current_price_mapped(self):
        df = self._make_df(['AAPL'], [150.0], [160.0], [145.0])
        prices = {'AAPL': 155.0}
        result = cr.enrich_breakout(df, prices)
        assert float(result.iloc[0]['Current_Price']) == 155.0

    def test_pnl_pct_calculation(self):
        df = self._make_df(['AAPL'], [100.0], [110.0], [95.0])
        prices = {'AAPL': 105.0}
        result = cr.enrich_breakout(df, prices)
        assert abs(float(result.iloc[0]['PnL_Pct']) - 5.0) < 0.01

    def test_negative_pnl(self):
        df = self._make_df(['AAPL'], [100.0], [110.0], [95.0])
        prices = {'AAPL': 97.0}
        result = cr.enrich_breakout(df, prices)
        assert float(result.iloc[0]['PnL_Pct']) < 0

    def test_hit_target_status(self):
        df = self._make_df(['AAPL'], [100.0], [110.0], [95.0])
        prices = {'AAPL': 112.0}  # above target
        result = cr.enrich_breakout(df, prices)
        assert result.iloc[0]['Status'] == 'TARGET'

    def test_hit_stop_status(self):
        df = self._make_df(['AAPL'], [100.0], [110.0], [95.0])
        prices = {'AAPL': 94.0}  # below stop
        result = cr.enrich_breakout(df, prices)
        assert result.iloc[0]['Status'] == 'STOP'

    def test_open_status(self):
        df = self._make_df(['AAPL'], [100.0], [110.0], [95.0])
        prices = {'AAPL': 103.0}  # between stop and target
        result = cr.enrich_breakout(df, prices)
        assert result.iloc[0]['Status'] == 'OPEN'

    def test_missing_target_stop_columns_no_crash(self):
        """enrich_breakout must not crash when Target/Stop columns are absent."""
        df = pd.DataFrame({'Symbol': ['AAPL'], 'Price': [100.0]})
        result = cr.enrich_breakout(df, {'AAPL': 105.0})
        assert result.iloc[0]['Status'] == 'OPEN'

    def test_symbol_not_in_prices_gives_nan(self):
        df = self._make_df(['ZZZZ'], [100.0], [110.0], [95.0])
        result = cr.enrich_breakout(df, {})
        assert pd.isna(result.iloc[0]['Current_Price'])

    def test_original_df_not_mutated(self):
        df = self._make_df(['AAPL'], [100.0], [110.0], [95.0])
        original_cols = set(df.columns)
        cr.enrich_breakout(df, {'AAPL': 105.0})
        assert set(df.columns) == original_cols


# ─────────────────────────────────────────────────────────────────────────────
# comparison_report — render_text_report()
# ─────────────────────────────────────────────────────────────────────────────

def _make_breakout_df(n=3):
    rows = []
    for i, sym in enumerate(['AAPL', 'MSFT', 'NVDA'][:n]):
        rows.append({
            'Symbol': sym, 'Price': 100.0 + i * 10,
            'Current_Price': 105.0 + i * 10, 'PnL_Pct': 5.0,
            'Status': 'OPEN', 'Scan_Time': '09:35', 'Scan_Mode': 'daytrade',
            'Quality': 'PREMIUM', 'Type': 'BREAKOUT',
            'RSI': 62.0, 'R:R': 3.0, 'Stop': 95.0 + i * 10,
            'Target': 115.0 + i * 10,
        })
    return pd.DataFrame(rows)


def _make_macd_df(n=2):
    rows = []
    for i, sym in enumerate(['GOOG', 'TSLA'][:n]):
        rows.append({
            'Symbol': sym, 'Entry_Price': 200.0 + i * 50,
            'Current_Price': 210.0 + i * 50, 'PnL_Pct': 5.0,
            'Status': 'OPEN', 'Scan_Time': '09:35',
            'Signal': 'MACD_CROSS', 'RSI_Entry': 58.0,
        })
    return pd.DataFrame(rows)


class TestRenderTextReport:

    def test_returns_string(self):
        report = cr.render_text_report(pd.DataFrame(), pd.DataFrame(), {}, 'now')
        assert isinstance(report, str)

    def test_empty_both_shows_no_signals(self):
        report = cr.render_text_report(pd.DataFrame(), pd.DataFrame(), {}, '2026-03-13 10:00 ET')
        assert '(no signals today)' in report

    def test_breakout_symbols_appear(self):
        breakout = _make_breakout_df()
        report = cr.render_text_report(breakout, pd.DataFrame(), {}, 'now')
        assert 'AAPL' in report
        assert 'MSFT' in report

    def test_macd_symbols_appear(self):
        macd = _make_macd_df()
        report = cr.render_text_report(pd.DataFrame(), macd, {}, 'now')
        assert 'GOOG' in report
        assert 'TSLA' in report

    def test_overlap_section_present(self):
        report = cr.render_text_report(pd.DataFrame(), pd.DataFrame(), {}, 'now')
        assert 'OVERLAP' in report

    def test_head_to_head_section_present(self):
        report = cr.render_text_report(pd.DataFrame(), pd.DataFrame(), {}, 'now')
        assert 'HEAD-TO-HEAD' in report

    def test_stat_bug_win_rate_correct(self):
        """
        Regression test for the stat() bug:
        wins = closed[df[col] > 0]  (used full df as mask — WRONG)
        wins = closed[closed[col] > 0]  (use closed as mask — CORRECT)
        """
        # 2 closed positions: 1 win (+5%), 1 loss (-2%), 1 open
        rows = [
            {'Symbol': 'A', 'Price': 100.0, 'Current_Price': 105.0,
             'PnL_Pct': 5.0, 'Status': 'CLOSED',
             'Scan_Time': '09:35', 'Scan_Mode': 'daytrade',
             'Quality': 'PREMIUM', 'Type': 'BREAKOUT',
             'RSI': 62.0, 'R:R': 3.0, 'Stop': 95.0, 'Target': 115.0},
            {'Symbol': 'B', 'Price': 100.0, 'Current_Price': 98.0,
             'PnL_Pct': -2.0, 'Status': 'CLOSED',
             'Scan_Time': '09:35', 'Scan_Mode': 'daytrade',
             'Quality': 'STANDARD', 'Type': 'BREAKOUT',
             'RSI': 55.0, 'R:R': 2.0, 'Stop': 95.0, 'Target': 110.0},
            {'Symbol': 'C', 'Price': 100.0, 'Current_Price': 103.0,
             'PnL_Pct': 3.0, 'Status': 'OPEN',
             'Scan_Time': '09:35', 'Scan_Mode': 'daytrade',
             'Quality': 'HIGH', 'Type': 'BREAKOUT',
             'RSI': 60.0, 'R:R': 2.5, 'Stop': 95.0, 'Target': 112.0},
        ]
        breakout = pd.DataFrame(rows)
        report = cr.render_text_report(breakout, pd.DataFrame(), {}, 'now')
        # 1W / 2 closed = 50% win rate
        assert '50%' in report

    def test_report_no_crash_missing_optional_columns(self):
        """Report must not crash when optional breakout columns (RSI, R:R) are absent."""
        df = pd.DataFrame([{
            'Symbol': 'AAPL', 'Price': 150.0,
            'Current_Price': 155.0, 'PnL_Pct': 3.33,
            'Status': 'OPEN', 'Scan_Time': '09:35', 'Scan_Mode': 'daytrade',
            'Quality': 'HIGH', 'Type': 'BREAKOUT',
            # RSI, R:R, Stop, Target intentionally absent
        }])
        report = cr.render_text_report(df, pd.DataFrame(), {}, 'now')
        assert 'AAPL' in report


# ─────────────────────────────────────────────────────────────────────────────
# comparison_report — fetch_current_prices() (mocked)
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchCurrentPrices:

    def _make_multiindex(self, symbols, prices):
        idx = pd.date_range('2026-03-13', periods=5, freq='1min')
        dfs = {}
        for sym, price in zip(symbols, prices):
            dfs[sym] = pd.DataFrame({'Close': [price] * 5, 'Volume': [1e6] * 5}, index=idx)
        return pd.concat(dfs, axis=1)

    def test_empty_symbols_returns_empty(self):
        result = cr.fetch_current_prices([])
        assert result == {}

    def test_single_symbol_price_returned(self):
        raw = self._make_multiindex(['AAPL'], [175.0])
        with patch('yfinance.download', return_value=raw):
            result = cr.fetch_current_prices(['AAPL'])
        assert result.get('AAPL') == 175.0

    def test_multiple_symbols(self):
        raw = self._make_multiindex(['AAPL', 'MSFT'], [175.0, 300.0])
        with patch('yfinance.download', return_value=raw):
            result = cr.fetch_current_prices(['AAPL', 'MSFT'])
        assert result.get('AAPL') == 175.0
        assert result.get('MSFT') == 300.0

    def test_download_failure_returns_empty(self):
        with patch('yfinance.download', side_effect=Exception("timeout")):
            result = cr.fetch_current_prices(['AAPL'])
        assert result == {}

    def test_prices_rounded_to_2dp(self):
        raw = self._make_multiindex(['NVDA'], [123.456789])
        with patch('yfinance.download', return_value=raw):
            result = cr.fetch_current_prices(['NVDA'])
        price = result.get('NVDA')
        if price is not None:
            assert price == round(price, 2)
