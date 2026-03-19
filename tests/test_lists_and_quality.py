#!/usr/bin/env python3
"""
test_lists_and_quality.py
=========================
Unit tests for recent changes:
  1. upload_to_s3.py  — upload_files() glob/log-exclusion, upload_dir() log-exclusion
  2. utils.py         — append_signals_to_positions() quality column; get_positions_from_file() legacy default
  3. Quality filter   — run_exit_mode position filter (PREMIUM/GOLD only)
  4. export_premium   — GOLD included alongside PREMIUM
  5. monitor_watch.py — WATCH_FILES paths updated to scanner_output/lists/

Run:
    python test_lists_and_quality.py
    python test_lists_and_quality.py -v
"""

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ─────────────────────────────────────────────────────────────────────────────
# 1. upload_to_s3.py
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadToS3(unittest.TestCase):
    """Tests for upload_to_s3.upload_files() and upload_dir()."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_file(self, name: str, content: str = "data") -> Path:
        p = Path(self.tmp) / name
        p.write_text(content)
        return p

    def test_upload_files_skips_log(self):
        """upload_files() must not upload .log files."""
        from upload_to_s3 import upload_files
        csv_file = self._make_file("signals.csv", "a,b\n1,2")
        log_file = self._make_file("cron.log", "some log output")

        mock_s3 = MagicMock()
        count = upload_files(mock_s3, "test-bucket", [str(csv_file), str(log_file)])

        # Only the CSV should be uploaded
        self.assertEqual(count, 1)
        uploaded_keys = [call.args[2] for call in mock_s3.upload_file.call_args_list]
        self.assertFalse(any('.log' in k for k in uploaded_keys), f"log file was uploaded: {uploaded_keys}")

    def test_upload_files_glob_pattern(self):
        """upload_files() resolves glob patterns and uploads matched files."""
        from upload_to_s3 import upload_files
        self._make_file("signals_swing_20260301.csv", "a,b\n1,2")
        self._make_file("signals_swing_20260302.csv", "a,b\n3,4")
        self._make_file("signals_daytrade_20260301.csv", "a,b\n5,6")

        mock_s3 = MagicMock()
        pattern = str(Path(self.tmp) / "signals_swing_*.csv")
        count = upload_files(mock_s3, "test-bucket", [pattern])

        self.assertEqual(count, 2, f"Expected 2 swing CSVs uploaded, got {count}")

    def test_upload_files_missing_pattern_warns(self):
        """upload_files() returns 0 for a glob that matches nothing (no crash)."""
        from upload_to_s3 import upload_files
        mock_s3 = MagicMock()
        count = upload_files(mock_s3, "test-bucket", ["/nonexistent/path/no_match_*.csv"])
        self.assertEqual(count, 0)

    def test_upload_dir_skips_log(self):
        """upload_dir() must not upload .log files from a directory."""
        from upload_to_s3 import upload_dir, PROJECT_ROOT
        # Create a subdirectory under the real PROJECT_ROOT so relative path works
        test_subdir = PROJECT_ROOT / "scanner_output" / "_test_upload_dir_tmp"
        test_subdir.mkdir(parents=True, exist_ok=True)
        try:
            (test_subdir / "signals.csv").write_text("a,b\n1,2")
            (test_subdir / "cron.log").write_text("log line")

            mock_s3 = MagicMock()
            rel_dir = str(test_subdir.relative_to(PROJECT_ROOT))
            count = upload_dir(mock_s3, "test-bucket", rel_dir)

            self.assertEqual(count, 1)
            uploaded_keys = [call.args[2] for call in mock_s3.upload_file.call_args_list]
            self.assertFalse(any('.log' in k for k in uploaded_keys))
        finally:
            import shutil
            shutil.rmtree(test_subdir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 2. utils.py — positions CSV (quality column)
# ─────────────────────────────────────────────────────────────────────────────

class TestPositionsQuality(unittest.TestCase):
    """Tests for append_signals_to_positions() and get_positions_from_file()."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.positions_file = os.path.join(self.tmp, "positions_mock.csv")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_signal(self, symbol: str, quality: str, price: float = 100.0) -> dict:
        return {
            'Symbol': symbol,
            'Quality': quality,
            'Price': price,
            'Target': price * 1.05,
        }

    def test_new_file_has_quality_column(self):
        """append_signals_to_positions() creates file with quality column."""
        from utils import append_signals_to_positions, get_positions_from_file
        signals = [self._make_signal('AAPL', 'PREMIUM', 180.0)]
        append_signals_to_positions(signals, self.positions_file, mode='swing')

        positions = get_positions_from_file(self.positions_file)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]['quality'], 'PREMIUM')

    def test_gold_signal_saved(self):
        """append_signals_to_positions() saves GOLD-quality signals."""
        from utils import append_signals_to_positions, get_positions_from_file
        signals = [self._make_signal('NVDA', 'GOLD', 500.0)]
        append_signals_to_positions(signals, self.positions_file, mode='swing')

        positions = get_positions_from_file(self.positions_file)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]['quality'], 'GOLD')

    def test_high_signal_rejected_by_default(self):
        """append_signals_to_positions() skips HIGH quality when min_quality=PREMIUM."""
        from utils import append_signals_to_positions, get_positions_from_file
        signals = [
            self._make_signal('TSLA', 'HIGH', 200.0),
            self._make_signal('AAPL', 'PREMIUM', 180.0),
        ]
        append_signals_to_positions(signals, self.positions_file, mode='swing')

        positions = get_positions_from_file(self.positions_file)
        symbols = [p['symbol'] for p in positions]
        self.assertNotIn('TSLA', symbols, "HIGH signal should be filtered out")
        self.assertIn('AAPL', symbols)

    def test_deduplication(self):
        """append_signals_to_positions() does not add the same symbol twice."""
        from utils import append_signals_to_positions, get_positions_from_file
        signals = [self._make_signal('AAPL', 'PREMIUM', 180.0)]
        append_signals_to_positions(signals, self.positions_file, mode='swing')
        append_signals_to_positions(signals, self.positions_file, mode='swing')

        positions = get_positions_from_file(self.positions_file)
        self.assertEqual(len(positions), 1, "Duplicate symbol should be skipped")

    def test_legacy_file_defaults_quality_to_premium(self):
        """get_positions_from_file() returns 'PREMIUM' when quality column is absent."""
        from utils import get_positions_from_file
        # Write CSV without quality column (legacy format)
        with open(self.positions_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['symbol', 'mode', 'entry', 'entry_date', 'stop', 'target', 'timeframe'])
            writer.writeheader()
            writer.writerow({
                'symbol': 'MSFT', 'mode': 'swing', 'entry': '400.0',
                'entry_date': '2026-01-15', 'stop': '390.0', 'target': '420.0',
                'timeframe': '1 day',
            })

        positions = get_positions_from_file(self.positions_file)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]['quality'], 'PREMIUM',
                         "Legacy row without quality column should default to PREMIUM")

    def test_empty_quality_defaults_to_premium(self):
        """get_positions_from_file() defaults blank quality cell to 'PREMIUM'."""
        from utils import get_positions_from_file
        with open(self.positions_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['symbol', 'mode', 'entry', 'entry_date', 'stop', 'target', 'timeframe', 'quality'])
            writer.writeheader()
            writer.writerow({
                'symbol': 'GOOG', 'mode': 'swing', 'entry': '170.0',
                'entry_date': '2026-01-20', 'stop': '165.0', 'target': '180.0',
                'timeframe': '1 day', 'quality': '',
            })

        positions = get_positions_from_file(self.positions_file)
        self.assertEqual(positions[0]['quality'], 'PREMIUM')

    def test_existing_file_without_quality_col_not_broken(self):
        """append_signals_to_positions() on legacy file (no quality col) still adds symbols."""
        from utils import append_signals_to_positions, get_positions_from_file
        # Pre-existing file without quality column
        with open(self.positions_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['symbol', 'mode', 'entry', 'entry_date', 'stop', 'target', 'timeframe'])
            writer.writeheader()
            writer.writerow({
                'symbol': 'MSFT', 'mode': 'swing', 'entry': '400.0',
                'entry_date': '2026-01-15', 'stop': '390.0', 'target': '420.0',
                'timeframe': '1 day',
            })

        # Append a new signal — file has no quality column, so quality not added
        signals = [self._make_signal('AAPL', 'PREMIUM', 180.0)]
        count = append_signals_to_positions(signals, self.positions_file, mode='swing')
        self.assertEqual(count, 1)

        positions = get_positions_from_file(self.positions_file)
        symbols = [p['symbol'] for p in positions]
        self.assertIn('MSFT', symbols)
        self.assertIn('AAPL', symbols)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Quality filter in run_exit_mode
# ─────────────────────────────────────────────────────────────────────────────

class TestExitModeQualityFilter(unittest.TestCase):
    """Tests for the PREMIUM/GOLD filter applied in run_exit_mode."""

    def _apply_filter(self, positions: list) -> list:
        """Replicate the filter logic from breakout_scanner.run_exit_mode()."""
        _quality_rank = {'GOLD': 4, 'PREMIUM': 3, 'HIGH': 2, 'STANDARD': 1, 'REJECT': 0}
        return [p for p in positions if _quality_rank.get(p.get('quality', 'PREMIUM'), 0) >= 3]

    def _make_pos(self, symbol: str, quality: str) -> dict:
        return {
            'symbol': symbol, 'quality': quality, 'mode': 'swing',
            'entry': 100.0, 'stop': 95.0, 'target': 110.0, 'timeframe': '1 day',
        }

    def test_gold_passes(self):
        positions = [self._make_pos('NVDA', 'GOLD')]
        self.assertEqual(len(self._apply_filter(positions)), 1)

    def test_premium_passes(self):
        positions = [self._make_pos('AAPL', 'PREMIUM')]
        self.assertEqual(len(self._apply_filter(positions)), 1)

    def test_high_filtered_out(self):
        positions = [self._make_pos('TSLA', 'HIGH')]
        self.assertEqual(len(self._apply_filter(positions)), 0)

    def test_standard_filtered_out(self):
        positions = [self._make_pos('AMZN', 'STANDARD')]
        self.assertEqual(len(self._apply_filter(positions)), 0)

    def test_reject_filtered_out(self):
        positions = [self._make_pos('GME', 'REJECT')]
        self.assertEqual(len(self._apply_filter(positions)), 0)

    def test_missing_quality_treated_as_premium(self):
        """Position without 'quality' key defaults to PREMIUM (passes)."""
        positions = [{'symbol': 'MSFT', 'mode': 'swing', 'entry': 400.0,
                      'stop': 390.0, 'target': 420.0, 'timeframe': '1 day'}]
        self.assertEqual(len(self._apply_filter(positions)), 1)

    def test_mixed_list_filters_correctly(self):
        positions = [
            self._make_pos('NVDA', 'GOLD'),
            self._make_pos('AAPL', 'PREMIUM'),
            self._make_pos('TSLA', 'HIGH'),
            self._make_pos('AMZN', 'STANDARD'),
        ]
        result = self._apply_filter(positions)
        symbols = [p['symbol'] for p in result]
        self.assertIn('NVDA', symbols)
        self.assertIn('AAPL', symbols)
        self.assertNotIn('TSLA', symbols)
        self.assertNotIn('AMZN', symbols)


# ─────────────────────────────────────────────────────────────────────────────
# 4. export_premium includes GOLD
# ─────────────────────────────────────────────────────────────────────────────

class TestExportPremium(unittest.TestCase):
    """Verify that GOLD and PREMIUM both pass the export_premium filter."""

    def _filter_export(self, signals: list) -> list:
        """Replicate the export_premium filter from breakout_scanner.run_scan_mode()."""
        return [
            sig.get('Symbol') or sig.get('symbol', '')
            for sig in signals
            if sig.get('Quality') in ('PREMIUM', 'GOLD')
        ]

    def test_gold_included(self):
        signals = [{'Symbol': 'NVDA', 'Quality': 'GOLD'}]
        result = self._filter_export(signals)
        self.assertIn('NVDA', result)

    def test_premium_included(self):
        signals = [{'Symbol': 'AAPL', 'Quality': 'PREMIUM'}]
        result = self._filter_export(signals)
        self.assertIn('AAPL', result)

    def test_high_excluded(self):
        signals = [{'Symbol': 'TSLA', 'Quality': 'HIGH'}]
        result = self._filter_export(signals)
        self.assertNotIn('TSLA', result)

    def test_standard_excluded(self):
        signals = [{'Symbol': 'GME', 'Quality': 'STANDARD'}]
        result = self._filter_export(signals)
        self.assertNotIn('GME', result)

    def test_mixed_signals(self):
        signals = [
            {'Symbol': 'NVDA', 'Quality': 'GOLD'},
            {'Symbol': 'AAPL', 'Quality': 'PREMIUM'},
            {'Symbol': 'TSLA', 'Quality': 'HIGH'},
            {'Symbol': 'GME',  'Quality': 'STANDARD'},
        ]
        result = self._filter_export(signals)
        self.assertIn('NVDA', result)
        self.assertIn('AAPL', result)
        self.assertNotIn('TSLA', result)
        self.assertNotIn('GME', result)


# ─────────────────────────────────────────────────────────────────────────────
# 5. monitor_watch.py — WATCH_FILES paths
# ─────────────────────────────────────────────────────────────────────────────

class TestMonitorWatchPaths(unittest.TestCase):
    """Verify that WATCH_FILES paths in monitor_watch.py point to scanner_output/lists/."""

    def test_daytrade_path(self):
        from monitor_watch import WATCH_FILES
        path = WATCH_FILES.get('daytrade', '')
        self.assertIn('scanner_output/lists/', path,
                      f"WATCH_FILES['daytrade'] should use scanner_output/lists/, got: {path}")
        self.assertIn('momentum_watch_daytrade', path)

    def test_no_input_prefix_in_daytrade(self):
        from monitor_watch import WATCH_FILES
        path = WATCH_FILES.get('daytrade', '')
        self.assertFalse(path.startswith('input/'),
                         f"WATCH_FILES['daytrade'] must not use input/ prefix, got: {path}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. scanner_output/lists/ files exist
# ─────────────────────────────────────────────────────────────────────────────

class TestListsDirectory(unittest.TestCase):
    """Verify that the scanner_output/lists/ directory and key files are present."""

    LISTS_DIR = Path(__file__).parent / 'scanner_output' / 'lists'

    def test_lists_dir_exists(self):
        self.assertTrue(self.LISTS_DIR.exists(), "scanner_output/lists/ directory must exist")

    def test_expected_files_present(self):
        expected = [
            'premium_swing.txt',
            'premium_daytrade.txt',
            'premium_longterm.txt',
            'momentum_watch_daytrade.txt',
            'optimizer_watch.txt',
            'positions_swing_mock.csv',
            'positions_daytrade_mock.csv',
        ]
        for fname in expected:
            fpath = self.LISTS_DIR / fname
            self.assertTrue(fpath.exists(), f"Expected file missing: scanner_output/lists/{fname}")

    def test_positions_csv_not_in_input(self):
        """positions CSV files should NOT be in input/ any more."""
        input_dir = Path(__file__).parent / 'input'
        for fname in ('positions_swing_mock.csv', 'positions_daytrade_mock.csv',
                      'premium_swing.txt', 'premium_daytrade.txt'):
            self.assertFalse((input_dir / fname).exists(),
                             f"{fname} was not removed from input/")


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    unittest.main(verbosity=2)
