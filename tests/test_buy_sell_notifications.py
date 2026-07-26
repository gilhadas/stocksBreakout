#!/usr/bin/env python3
"""
Test that BUY (breakout) and SELL (exit) notifications are sent,
and that all other notification types are disabled.

Run:  venv/bin/python3 -m pytest tests/test_buy_sell_notifications.py -v
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from notifier import Notifier


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_notifier():
    """Create a Notifier with a cleared cache so test messages aren't deduped."""
    n = Notifier()
    n._sent_cache.clear()
    return n


SAMPLE_BUY_SIGNAL = [{
    'Symbol': 'TEST_BUY',
    'Price': 100.00,
    'Stop': 95.00,
    'Target': 115.00,
    'R:R': '3.0',
    'Vol': '2.5',
    'Quality': 'PREMIUM',
    'Sector': 'Technology',
    'Mode': 'swing',
}]

SAMPLE_EXIT = [{
    'Symbol': 'TEST_SELL',
    'Action': 'EXIT_STOP',
    'Price': 92.00,
    'UnrealizedR': -1.5,
    'Quality': 'EXIT_STOP',
}]


# ── BUY (breakout) notification tests ────────────────────────────────────────

class TestBuyNotification:
    """Verify breakout (buy) notifications are dispatched to all channels."""

    @patch('notifier.requests.post')
    def test_buy_sends_discord(self, mock_post):
        """send_all with signals should POST to Discord signals webhook."""
        mock_post.return_value = MagicMock(status_code=204)
        n = _make_notifier()
        n.send_all(
            subject="SWING Signals [test] (1 V9-C of 1 total)",
            message="1 signals (PREMIUM/GOLD)",
            signals=SAMPLE_BUY_SIGNAL,
            notification_type='signals',
        )
        # Discord should have been called (if enabled)
        if n.discord_enabled:
            # Find calls that posted JSON with 'embeds' key (Discord webhook format)
            discord_calls = [
                c for c in mock_post.call_args_list
                if c.kwargs.get('json', {}).get('embeds') or
                   (c.args and isinstance(c.args[0], str) and 'discord' in c.args[0])
            ]
            assert len(discord_calls) >= 1, (
                f"Discord webhook was not called for BUY signal. "
                f"All calls: {[str(c)[:100] for c in mock_post.call_args_list]}"
            )
            # Verify embed is green (signals color)
            payload = discord_calls[0].kwargs.get('json', {})
            if 'embeds' in payload:
                assert payload['embeds'][0]['color'] == 0x00ff00, "BUY embed should be green"

    @patch('notifier.requests.post')
    def test_buy_contains_symbol(self, mock_post):
        """BUY notification should include the symbol name."""
        mock_post.return_value = MagicMock(status_code=204)
        n = _make_notifier()
        n.send_all(
            subject="SWING Signals [test]",
            message="Test buy",
            signals=SAMPLE_BUY_SIGNAL,
            notification_type='signals',
        )
        if n.discord_enabled:
            all_payloads = str(mock_post.call_args_list)
            assert 'TEST_BUY' in all_payloads, "Symbol TEST_BUY not found in notification"


# ── SELL (exit) notification tests ───────────────────────────────────────────

class TestSellNotification:
    """Verify exit (sell) notifications are dispatched."""

    @patch('notifier.requests.post')
    def test_sell_sends_notification(self, mock_post):
        """send_exit_notification should trigger send_all."""
        mock_post.return_value = MagicMock(status_code=204)
        n = _make_notifier()
        n.send_exit_notification(SAMPLE_EXIT)
        if n.discord_enabled:
            assert mock_post.called, "Discord was not called for SELL notification"

    @patch('notifier.requests.post')
    def test_sell_skips_hold(self, mock_post):
        """Exit notification should skip positions with Action=HOLD."""
        mock_post.return_value = MagicMock(status_code=204)
        n = _make_notifier()
        hold_only = [{'Symbol': 'HOLD_SYM', 'Action': 'HOLD', 'Price': 100,
                       'UnrealizedR': 0.5}]
        n.send_exit_notification(hold_only)
        # Should NOT have sent anything
        discord_calls = [c for c in mock_post.call_args_list if 'discord.com' in str(c)]
        assert len(discord_calls) == 0, "HOLD positions should not trigger notifications"


# ── Disabled notification tests ──────────────────────────────────────────────

class TestDisabledNotifications:
    """Verify that non-buy/sell notifications are disabled at their call sites."""

    def test_monitor_alert_disabled(self):
        """breakout_scanner portfolio monitor alert should be commented out."""
        code = Path('breakout_scanner.py').read_text()
        # The active call should be commented out
        assert '# notifier.send_monitor_alert(' in code, \
            "send_monitor_alert should be commented out in breakout_scanner.py"

    def test_premarket_discord_disabled(self):
        """premarket_monitor.py Discord alerts should be commented out."""
        code = Path('premarket_monitor.py').read_text()
        assert '# sent = notifier.send_discord(subject=subject, message=message)' in code or \
               "Premarket alerts disabled" in code, \
            "premarket_monitor Discord should be disabled"

    def test_monitor_watch_discord_disabled(self):
        """monitor_watch.py Discord alerts should be commented out."""
        code = Path('monitor_watch.py').read_text()
        assert '# notifier.send_discord(subject, body)' in code, \
            "monitor_watch Discord should be disabled"

    def test_position_health_disabled(self):
        """position_health.py notifications should be commented out."""
        code = Path('position_health.py').read_text()
        assert '# _send_health_discord(alerts_to_send)' in code, \
            "position_health Discord should be disabled"

    def test_portfolio_daily_report_disabled(self):
        """portfolio.py daily report should be commented out."""
        code = Path('portfolio.py').read_text()
        assert '# notifier.send_all(subject=subject, message=body)' in code, \
            "portfolio daily report should be disabled"

    def test_ib_placed_disabled(self):
        """ib_executor.py PLACED notification should be commented out."""
        code = Path('ib_executor.py').read_text()
        assert "#     _notify_order(order_info, 'PLACED')" in code, \
            "IB PLACED notification should be disabled"

    def test_scanner_error_disabled(self):
        """breakout_scanner.py error notification should be commented out."""
        code = Path('breakout_scanner.py').read_text()
        assert '# Scanner Error' in code or \
               "Scanner Error" in code and "# " in code, \
            "Scanner error notification should be disabled"


# ── Integration: end-to-end BUY → Discord ───────────────────────────────────

class TestEndToEnd:
    """Full integration: simulate what breakout_scanner.py does."""

    @patch('notifier.requests.post')
    def test_full_buy_flow(self, mock_post):
        """Replicate the exact send_all call from breakout_scanner.py line 556."""
        mock_post.return_value = MagicMock(status_code=204)
        n = _make_notifier()

        signals = [{
            'Symbol': 'AAPL', 'Price': 185.50, 'Stop': 178.00,
            'Target': 205.00, 'R:R': '2.6', 'Vol': '1.8',
            'Quality': 'PREMIUM', 'Sector': 'Technology', 'Mode': 'swing',
        }]
        n.send_all(
            subject="SWING Signals [optimizer_watch] (1 V9-C of 3 total)",
            message="1 signals (PREMIUM/GOLD) from 3 total",
            signals=signals,
            csv_path=None,
        )
        if n.discord_enabled:
            assert mock_post.called, "Full buy flow should trigger Discord"
            payload_str = str(mock_post.call_args_list)
            assert 'AAPL' in payload_str
            assert 'PREMIUM' in payload_str

    @patch('notifier.requests.post')
    def test_full_sell_flow(self, mock_post):
        """Replicate the exact send_exit_notification call from breakout_scanner.py line 701."""
        mock_post.return_value = MagicMock(status_code=204)
        n = _make_notifier()

        exits = [
            {'Symbol': 'TSLA', 'Action': 'EXIT_STOP', 'Price': 240.00, 'UnrealizedR': -2.0},
            {'Symbol': 'NVDA', 'Action': 'HOLD', 'Price': 900.00, 'UnrealizedR': 1.5},
        ]
        n.send_exit_notification(exits)
        if n.discord_enabled:
            assert mock_post.called, "Full sell flow should trigger Discord"
            payload_str = str(mock_post.call_args_list)
            assert 'TSLA' in payload_str
            # NVDA is HOLD — should not appear in notification signals
