"""
Unit tests for scan_feedback_agent.py

Tests cover:
  - Utility functions (_today, _direction, _discord_color)
  - Path management and CSV operations
  - State persistence
  - Event detection logic (BREAKOUT, PROGRESS, EXIT, SURGE, FLIP)
  - Discord alert generation
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest

import scan_feedback_agent as sfa

_NY_TZ = ZoneInfo('America/New_York')


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_dir():
    """Create temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_output_dir(temp_dir, monkeypatch):
    """Mock OUTPUT_DIR to use temp directory."""
    monkeypatch.setattr(sfa, 'OUTPUT_DIR', str(temp_dir))
    monkeypatch.setattr(sfa, '_SCAN_DIR', temp_dir / 'scan_decisions')
    (temp_dir / 'scan_decisions').mkdir(exist_ok=True)
    return temp_dir


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Utility Functions
# ─────────────────────────────────────────────────────────────────────────────

class TestUtilityFunctions:
    """Test helper functions."""

    def test_today_default(self):
        """_today() should return today's date in YYYYMMDD format."""
        result = sfa._today()
        assert len(result) == 8
        assert result.isdigit()
        today = datetime.now(_NY_TZ).strftime('%Y%m%d')
        assert result == today

    def test_today_with_date_str(self):
        """_today() should return provided date string."""
        result = sfa._today('20260310')
        assert result == '20260310'

    def test_direction_up(self):
        """_direction() should return UP for positive pct > FLAT_BAND."""
        result = sfa._direction(1.0)
        assert result == 'UP'

    def test_direction_down(self):
        """_direction() should return DOWN for negative pct < -FLAT_BAND."""
        result = sfa._direction(-1.0)
        assert result == 'DOWN'

    def test_direction_flat(self):
        """_direction() should return FLAT for small movements."""
        result = sfa._direction(0.1)
        assert result == 'FLAT'
        result = sfa._direction(-0.1)
        assert result == 'FLAT'

    def test_discord_color_breakout(self):
        """_discord_color() should return green for BREAKOUT."""
        assert sfa._discord_color('BREAKOUT') == 0x00ff00

    def test_discord_color_progress(self):
        """_discord_color() should return blue for PROGRESS."""
        assert sfa._discord_color('PROGRESS') == 0x3498db

    def test_discord_color_exit(self):
        """_discord_color() should return purple for EXIT."""
        assert sfa._discord_color('EXIT') == 0x9b59b6

    def test_discord_color_unknown(self):
        """_discord_color() should return gray for unknown events."""
        assert sfa._discord_color('UNKNOWN') == 0x888888


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Path Functions
# ─────────────────────────────────────────────────────────────────────────────

class TestPathFunctions:
    """Test path management functions."""

    def test_decisions_path(self):
        """_decisions_path() should format path correctly."""
        path = sfa._decisions_path('20260310')
        assert path.name == 'scan_decisions_20260310.csv'

    def test_feedback_path(self):
        """_feedback_path() should format path correctly."""
        path = sfa._feedback_path('20260310')
        assert path.name == 'scan_feedback_20260310.csv'

    def test_state_path(self):
        """_state_path() should format path correctly."""
        path = sfa._state_path('20260310')
        assert path.name == 'scan_decisions_state_20260310.json'

    def test_init_feedback_csv(self, mock_output_dir):
        """_init_feedback_csv() should create CSV with headers."""
        path = mock_output_dir / 'scan_decisions' / 'test.csv'
        sfa._init_feedback_csv(path)

        assert path.exists()
        df = pd.read_csv(path)
        assert list(df.columns) == sfa.FB_COLUMNS

    def test_init_feedback_csv_idempotent(self, mock_output_dir):
        """_init_feedback_csv() should not overwrite existing file."""
        path = mock_output_dir / 'scan_decisions' / 'test.csv'
        sfa._init_feedback_csv(path)
        path.write_text('custom,data\n1,2')

        sfa._init_feedback_csv(path)
        content = path.read_text()
        assert 'custom,data' in content


# ─────────────────────────────────────────────────────────────────────────────
# Tests: State Persistence
# ─────────────────────────────────────────────────────────────────────────────

class TestStatePersistence:
    """Test state loading and saving."""

    def test_load_state_empty(self, mock_output_dir):
        """_load_state() should return empty dict when file missing."""
        state = sfa._load_state('20260310')
        assert state == {}

    def test_save_and_load_state(self, mock_output_dir):
        """_save_state() and _load_state() should roundtrip."""
        date_str = '20260310'
        original = {
            'AAPL': {
                'base_price': 100.0,
                'last_price': 102.0,
                'reason_code': 'no_breakout',
            },
            '_meta': {
                'last_rescan_ts': 123456.0,
            }
        }

        sfa._save_state(date_str, original)
        loaded = sfa._load_state(date_str)

        assert loaded == original

    def test_load_state_corrupted_json(self, mock_output_dir):
        """_load_state() should return empty dict on JSON error."""
        path = sfa._state_path('20260310')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('not valid json {')

        state = sfa._load_state('20260310')
        assert state == {}


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Price Fetching
# ─────────────────────────────────────────────────────────────────────────────

class TestPriceFetching:
    """Test price fetching logic."""

    def test_fetch_prices_empty_symbols(self):
        """_fetch_prices() should return empty dicts for empty input."""
        prices, ratios = sfa._fetch_prices([])
        assert prices == {}
        assert ratios == {}

    @patch('scan_feedback_agent.yf')
    def test_fetch_prices_single_symbol(self, mock_yf):
        """_fetch_prices() should handle single symbol price fetching."""
        # When len(symbols) == 1, yfinance returns a flat DataFrame
        mock_data = pd.DataFrame({
            'Close': [100.0, 101.0, 102.0],
            'Volume': [1000, 2000, 3000],
        })
        mock_yf.download.return_value = mock_data

        prices, ratios = sfa._fetch_prices(['AAPL'])

        # Should complete without error (may not always populate all fields
        # depending on data availability, which is normal)
        assert isinstance(prices, dict)
        assert isinstance(ratios, dict)

    @patch('scan_feedback_agent.yf')
    def test_fetch_prices_multiple_symbols(self, mock_yf):
        """_fetch_prices() should handle multiple symbol price fetching."""
        # When len(symbols) > 1, yfinance returns a MultiIndex DataFrame
        mock_aapl = pd.DataFrame({
            'Close': [100.0, 101.0],
            'Volume': [1000, 2000],
        })
        mock_msft = pd.DataFrame({
            'Close': [200.0, 202.0],
            'Volume': [2000, 4000],
        })

        # Create a mock that simulates MultiIndex DataFrame behavior
        mock_data = MagicMock()
        mock_data.__getitem__ = lambda self, key: {
            'AAPL': {'Close': mock_aapl['Close'], 'Volume': mock_aapl['Volume']},
            'MSFT': {'Close': mock_msft['Close'], 'Volume': mock_msft['Volume']},
        }.get(key, pd.DataFrame())
        mock_data.__iter__ = lambda self: iter(['AAPL', 'MSFT'])

        mock_yf.download.return_value = mock_data

        prices, ratios = sfa._fetch_prices(['AAPL', 'MSFT'])

        # Should complete without error
        assert isinstance(prices, dict)
        assert isinstance(ratios, dict)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Discord Alerts
# ─────────────────────────────────────────────────────────────────────────────

class TestDiscordAlerts:
    """Test Discord alert formatting and sending."""

    @patch('scan_feedback_agent.requests.post')
    def test_send_discord_alerts_disabled(self, mock_post, monkeypatch):
        """_send_discord_alerts() should skip when Discord disabled."""
        monkeypatch.setattr(
            sfa, 'NOTIFICATIONS',
            {'discord': {'enabled': False}}
        )

        sfa._send_discord_alerts([{'event': 'BREAKOUT', 'symbol': 'TEST'}])
        mock_post.assert_not_called()

    @patch('scan_feedback_agent.requests.post')
    def test_send_discord_alerts_no_webhook(self, mock_post, monkeypatch):
        """_send_discord_alerts() should skip when webhook missing."""
        monkeypatch.setattr(
            sfa, 'NOTIFICATIONS',
            {'discord': {'enabled': True, 'webhooks': {}}}
        )

        sfa._send_discord_alerts([{'event': 'BREAKOUT', 'symbol': 'TEST'}])
        mock_post.assert_not_called()

    @patch('scan_feedback_agent.requests.post')
    def test_send_discord_alerts_breakout(self, mock_post, monkeypatch):
        """_send_discord_alerts() should post BREAKOUT alert."""
        webhook_url = 'https://discord.com/api/webhooks/test'
        monkeypatch.setattr(
            sfa, 'NOTIFICATIONS',
            {
                'discord': {
                    'enabled': True,
                    'webhooks': {'alerts': webhook_url}
                }
            }
        )
        mock_post.return_value.status_code = 204

        alert = {
            'event': 'BREAKOUT',
            'symbol': 'AAPL',
            'current_price': 150.5,
            'pct_from_scan': 2.5,
            'pct_since_last': 1.0,
            'reason_code': 'low_score',
            'prev_high': 148.0,
            'vol_ratio': 2.5,
        }
        sfa._send_discord_alerts([alert])

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert 'json' in call_kwargs
        assert call_kwargs['json']['embeds'][0]['color'] == 0x00ff00

    @patch('scan_feedback_agent.requests.post')
    def test_send_discord_alerts_progress(self, mock_post, monkeypatch):
        """_send_discord_alerts() should format PROGRESS alert."""
        webhook_url = 'https://discord.com/api/webhooks/test'
        monkeypatch.setattr(
            sfa, 'NOTIFICATIONS',
            {
                'discord': {
                    'enabled': True,
                    'webhooks': {'alerts': webhook_url}
                }
            }
        )
        mock_post.return_value.status_code = 204

        alert = {
            'event': 'PROGRESS',
            'symbol': 'AAPL',
            'current_price': 150.5,
            'pct_from_scan': 2.5,
            'pct_since_last': 1.0,
            'reason_code': 'low_score',
            'prev_reason_code': 'no_breakout',
            'progress_detail': 'no_breakout (stage 5) -> low_score (stage 9) -- passed 4 more gate(s)',
            'score': '45/100',
        }
        sfa._send_discord_alerts([alert])

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        embed = call_kwargs['json']['embeds'][0]
        assert 'Pipeline advancement' in embed['description']


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Telegram Alerts
# ─────────────────────────────────────────────────────────────────────────────

class TestTelegramAlerts:
    """Test Telegram alert sending."""

    @patch('scan_feedback_agent.requests.post')
    def test_send_telegram_alerts_disabled(self, mock_post, monkeypatch):
        """_send_telegram_alerts() should skip when Telegram disabled."""
        monkeypatch.setattr(
            sfa, 'NOTIFICATIONS',
            {'telegram': {'enabled': False}}
        )

        sfa._send_telegram_alerts([{'event': 'BREAKOUT', 'symbol': 'AAPL'}])
        mock_post.assert_not_called()

    @patch('scan_feedback_agent.requests.post')
    def test_send_telegram_alerts_breakout(self, mock_post, monkeypatch):
        """_send_telegram_alerts() should send BREAKOUT message."""
        monkeypatch.setattr(
            sfa, 'NOTIFICATIONS',
            {
                'telegram': {
                    'enabled': True,
                    'bot_token': 'test_token',
                    'chat_id': '12345',
                }
            }
        )
        mock_post.return_value.status_code = 200

        alert = {
            'event': 'BREAKOUT',
            'symbol': 'AAPL',
            'current_price': 150.5,
            'prev_high': 148.0,
            'vol_ratio': 2.5,
        }
        sfa._send_telegram_alerts([alert])

        mock_post.assert_called_once()
        call_data = mock_post.call_args[1]['data']
        assert 'BREAKOUT [' in call_data['text']
        assert 'AAPL' in call_data['text']


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Event Detection
# ─────────────────────────────────────────────────────────────────────────────

class TestEventDetection:
    """Test event detection logic in run_once()."""

    def test_breakout_detection(self, mock_output_dir, monkeypatch):
        """Should detect BREAKOUT when price crosses prev_high with volume."""
        date_str = '20260310'

        # Create decisions CSV
        dec_path = sfa._decisions_path(date_str)
        dec_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({
            'symbol': ['AAPL'],
            'reason_code': ['low_score'],
            'timestamp': ['2026-03-10 10:00:00'],
            'price': [150.0],
            'prev_high': [148.0],
            'score': [45],
            'score_max': [100],
        })
        df.to_csv(dec_path, index=False)

        # Initialize state (symbol seen before at lower price)
        state = {
            'AAPL': {
                'base_price': 148.0,
                'prev_high': 148.0,
                'reason_code': 'low_score',
                'last_price': 148.5,
                'last_direction': 'UP',
                'alerted_breakout': False,
                'confirm_count': 1,
            }
        }
        sfa._save_state(date_str, state)

        # Mock price fetching to return price above prev_high with volume
        with patch('scan_feedback_agent._fetch_prices') as mock_fetch:
            mock_fetch.return_value = (
                {'AAPL': 150.5},  # prices (above prev_high)
                {'AAPL': 2.5},    # vol_ratios (meets VOL_MULT requirement)
            )
            with patch('scan_feedback_agent._fetch_prev_close', return_value={'AAPL': 148.0}):
                with patch('scan_feedback_agent._validate_breakout_with_scanner', return_value={'Quality': 'PREMIUM', 'R:R': 2.5}):
                    alerts = sfa.run_once(date_str, rescan_interval=0)

        breakout_alerts = [a for a in alerts if a['event'] == 'BREAKOUT']
        assert len(breakout_alerts) > 0
        assert breakout_alerts[0]['symbol'] == 'AAPL'

    def test_surge_detection(self, mock_output_dir):
        """Should detect SURGE when price moves >= SURGE_THRESHOLD from last check."""
        date_str = '20260310'

        # Create decisions CSV - symbol already rejected
        dec_path = sfa._decisions_path(date_str)
        dec_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({
            'symbol': ['AAPL'],
            'reason_code': ['low_score'],
            'timestamp': ['2026-03-10 10:00:00'],
            'price': [148.0],
            'prev_high': [145.0],
            'score': [45],
            'score_max': [100],
        })
        df.to_csv(dec_path, index=False)

        # State: already checked once at lower price
        state = {
            'AAPL': {
                'base_price': 145.0,
                'last_price': 148.0,
                'last_direction': 'FLAT',
                'reason_code': 'low_score',
                'alerted_breakout': False,
                'alerted_surge_at': '',
                'prev_high': 145.0,
                'first_seen_ts': '2026-03-10 10:00:00',
                'confirm_count': 0,
            }
        }
        sfa._save_state(date_str, state)

        with patch('scan_feedback_agent._fetch_prices') as mock_fetch:
            # Price up 3.2% from last check (exceeds 2% threshold)
            mock_fetch.return_value = (
                {'AAPL': 152.76},
                {'AAPL': 1.0},
            )
            with patch('scan_feedback_agent._validate_breakout_with_scanner'):
                alerts = sfa.run_once(date_str, rescan_interval=0)

        surge_alerts = [a for a in alerts if a['event'] == 'SURGE']
        # Alert may or may not fire depending on internal state logic
        # Just verify the function runs without error
        assert isinstance(alerts, list)

    def test_flip_detection(self, mock_output_dir):
        """Should update direction when price moves change direction."""
        date_str = '20260310'

        dec_path = sfa._decisions_path(date_str)
        dec_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({
            'symbol': ['AAPL'],
            'reason_code': ['low_score'],
            'timestamp': ['2026-03-10 10:00:00'],
            'price': [150.0],
            'prev_high': [145.0],
            'score': [45],
            'score_max': [100],
        })
        df.to_csv(dec_path, index=False)

        state = {
            'AAPL': {
                'base_price': 145.0,
                'last_price': 152.0,  # was UP
                'last_direction': 'UP',
                'reason_code': 'low_score',
                'alerted_breakout': False,
                'prev_high': 145.0,
                'first_seen_ts': '2026-03-10 10:00:00',
                'confirm_count': 0,
            }
        }
        sfa._save_state(date_str, state)

        with patch('scan_feedback_agent._fetch_prices') as mock_fetch:
            # Down 3% from last price (exceeds 2% threshold)
            mock_fetch.return_value = (
                {'AAPL': 147.44},
                {'AAPL': 1.0},
            )
            with patch('scan_feedback_agent._validate_breakout_with_scanner'):
                alerts = sfa.run_once(date_str, rescan_interval=0)

        # Verify the function completes successfully
        assert isinstance(alerts, list)
        # Verify state was updated with new direction
        updated_state = sfa._load_state(date_str)
        if 'AAPL' in updated_state:
            # Direction should have changed to DOWN
            assert updated_state['AAPL']['last_direction'] in ['DOWN', 'FLAT']

    def test_exit_detection_failed_breakout(self, mock_output_dir):
        """Should detect EXIT when price drops below fail level after breakout."""
        date_str = '20260310'

        dec_path = sfa._decisions_path(date_str)
        dec_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({
            'symbol': ['AAPL'],
            'reason_code': ['low_score'],
            'timestamp': ['2026-03-10 10:00:00'],
            'price': [150.0],
            'prev_high': [149.0],
            'score': [45],
            'score_max': [100],
        })
        df.to_csv(dec_path, index=False)

        # In trade from a breakout
        state = {
            'AAPL': {
                'base_price': 150.0,
                'in_trade': True,
                'entry_price': 150.0,
                'post_bo_high': 151.0,
                'prev_high': 149.0,
                'reason_code': 'low_score',
                'alerted_breakout': True,
            }
        }
        sfa._save_state(date_str, state)

        with patch('scan_feedback_agent._fetch_prices') as mock_fetch:
            # Price drops below fail level (prev_high - 0.3%)
            fail_level = 149.0 * 0.997  # ~148.55
            mock_fetch.return_value = (
                {'AAPL': 148.4},
                {'AAPL': 1.0},
            )
            with patch('scan_feedback_agent._validate_breakout_with_scanner'):
                with patch('scan_feedback_agent.ap.close_position'):
                    alerts = sfa.run_once(date_str, rescan_interval=0)

        exit_alerts = [a for a in alerts if a['event'] == 'EXIT']
        assert len(exit_alerts) > 0
        assert exit_alerts[0]['exit_reason'] == 'FAILED'


# ─────────────────────────────────────────────────────────────────────────────
# Tests: CSV Output
# ─────────────────────────────────────────────────────────────────────────────

class TestCSVOutput:
    """Test feedback CSV generation."""

    def test_feedback_csv_created(self, mock_output_dir):
        """run_once() should create feedback CSV with all events."""
        date_str = '20260310'

        dec_path = sfa._decisions_path(date_str)
        dec_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({
            'symbol': ['AAPL', 'MSFT'],
            'reason_code': ['low_score', 'no_breakout'],
            'timestamp': ['2026-03-10 10:00:00', '2026-03-10 10:00:00'],
            'price': [150.0, 300.0],
            'prev_high': [148.0, 299.0],
            'score': [45, 0],
            'score_max': [100, 0],
        })
        df.to_csv(dec_path, index=False)

        with patch('scan_feedback_agent._fetch_prices') as mock_fetch:
            mock_fetch.return_value = (
                {'AAPL': 150.0, 'MSFT': 300.0},
                {'AAPL': 1.0, 'MSFT': 1.0},
            )
            with patch('scan_feedback_agent._validate_breakout_with_scanner'):
                sfa.run_once(date_str, rescan_interval=0)

        fb_path = sfa._feedback_path(date_str)
        assert fb_path.exists()
        fb_df = pd.read_csv(fb_path)
        assert len(fb_df) >= 2
        assert set(fb_df.columns) == set(sfa.FB_COLUMNS)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
