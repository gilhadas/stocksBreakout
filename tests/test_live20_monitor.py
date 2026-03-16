"""
tests/test_live20_monitor.py
============================
Unit tests for live20_monitor.py

Covers:
  - CSV loading / forecast parsing
  - Alert logic: BREAKING (UP & DOWN), BOUNCING, DIRECTION_CHANGE
  - Deduplication (state prevents repeated alerts)
  - Discord notification dispatch (BREAKING + BOUNCING both sent)
  - Market hours gating
  - yfinance / IB price source selection
"""

import asyncio
import io
import json
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

# ── make sure repo root is on path ───────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import live20_monitor as m

NY_TZ = ZoneInfo('America/New_York')

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_records(**overrides) -> list[dict]:
    """Return a single forecast record, with optional overrides."""
    base = {
        'ticker': 'AAPL',
        'start_price': 100.0,
        'sr_level': 100.0,
        'current_price': None,
        'bullish': True,
        'is_predicted': False,
        'wait_direction': False,
        'bounce_value': None,
        'remarks': '',
        'forecast_date': None,
    }
    base.update(overrides)
    return [base]


def _make_csv_text(rows: list[dict]) -> str:
    """Build a minimal CSV string from a list of row dicts."""
    cols = ['Ticker', '3/13/2026', 'start price', 'forcast', 'current price',
            'Bullish', 'is predicted', 'WAIT FOR CHANGE DIRECTION',
            'bounce Value', 'Remarks']
    lines = [','.join(cols)]
    for r in rows:
        lines.append(','.join([
            str(r.get('Ticker', '')),
            str(r.get('date', '')),
            str(r.get('start price', '')),
            str(r.get('forcast', '')),
            str(r.get('current price', '')),
            str(r.get('Bullish', '')),
            str(r.get('is predicted', '')),
            str(r.get('WAIT FOR CHANGE DIRECTION', '')),
            str(r.get('bounce Value', '')),
            str(r.get('Remarks', '')),
        ]))
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CSV / load_forecast
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadForecast:
    def test_parses_bullish_yes(self, tmp_path):
        csv = tmp_path / 'test.csv'
        # Write CSV with explicit column alignment, no empty cells that become NaN
        csv.write_text(
            'Ticker,3/13/2026,start price,forcast,current price,Bullish,is predicted,WAIT FOR CHANGE DIRECTION,bounce Value,Remarks\n'
            'AAPL,,145,150,148,YES,TRUE,NO,,\n'
        )
        records = m.load_forecast(csv)
        assert len(records) == 1
        rec = records[0]
        assert rec['ticker'] == 'AAPL'
        assert rec['start_price'] == 145.0
        assert rec['sr_level'] == 150.0
        assert rec['current_price'] == 148.0
        assert rec['bullish'] is True
        assert rec['is_predicted'] is True
        assert rec['wait_direction'] is False
        assert rec['bounce_value'] is None
        assert rec['forecast_date'] == '3/13/2026'

    def test_parses_bearish_no(self, tmp_path):
        csv = tmp_path / 'test.csv'
        csv.write_text(_make_csv_text([{'Ticker': 'TSLA', 'start price': '391.2', 'forcast': '385',
                                        'current price': '395', 'Bullish': 'FALSE',
                                        'is predicted': 'FALSE',
                                        'WAIT FOR CHANGE DIRECTION': 'YES',
                                        'bounce Value': '416', 'Remarks': 'entry'}]))
        records = m.load_forecast(csv)
        rec = records[0]
        assert rec['start_price'] == 391.2
        assert rec['current_price'] == 395.0
        assert rec['bullish'] is False
        assert rec['is_predicted'] is False
        assert rec['wait_direction'] is True
        assert rec['bounce_value'] == 416.0
        assert rec['remarks'] == 'entry'

    def test_skips_empty_ticker(self, tmp_path):
        csv = tmp_path / 'test.csv'
        # Empty first field → pandas reads as NaN → should be skipped
        csv.write_text(
            'Ticker,3/13/2026,start price,forcast,current price,Bullish,is predicted,WAIT FOR CHANGE DIRECTION,bounce Value,Remarks\n'
            ',,100,100,,YES,,,,\n'            # empty Ticker cell → NaN → skipped
            'AMZN,,207,212,,YES,,,,\n'
        )
        records = m.load_forecast(csv)
        assert len(records) == 1
        assert records[0]['ticker'] == 'AMZN'

    def test_sr_level_non_numeric_becomes_none(self, tmp_path):
        csv = tmp_path / 'test.csv'
        csv.write_text(
            'Ticker,3/13/2026,start price,forcast,current price,Bullish,is predicted,WAIT FOR CHANGE DIRECTION,bounce Value,Remarks\n'
            'X,,100,n/a,,NO,,,,\n'
        )
        records = m.load_forecast(csv)
        assert records[0]['sr_level'] is None

    def test_nan_remarks_normalised_to_empty_string(self, tmp_path):
        csv = tmp_path / 'test.csv'
        csv.write_text(_make_csv_text([{'Ticker': 'X', 'start price': '100', 'forcast': '',
                                        'current price': '', 'Bullish': 'YES',
                                        'is predicted': '', 'WAIT FOR CHANGE DIRECTION': '',
                                        'bounce Value': '', 'Remarks': 'nan'}]))
        records = m.load_forecast(csv)
        assert records[0]['remarks'] == ''

    def test_whitespace_trimmed_from_ticker(self, tmp_path):
        csv = tmp_path / 'test.csv'
        csv.write_text('Ticker,3/13/2026,start price,forcast,current price,Bullish,is predicted,'
                       'WAIT FOR CHANGE DIRECTION,bounce Value,Remarks\n  aapl  ,,145,100,,YES,,,,\n')
        records = m.load_forecast(csv)
        assert records[0]['ticker'] == 'AAPL'

    def test_start_price_loaded_from_csv(self, tmp_path):
        """Verify start_price column is read and stored."""
        csv = tmp_path / 'test.csv'
        csv.write_text(
            'Ticker,3/13/2026,start price,forcast,current price,Bullish,is predicted,WAIT FOR CHANGE DIRECTION,bounce Value,Remarks\n'
            'AAPL,,145.50,150,148,YES,TRUE,NO,,\n'
        )
        records = m.load_forecast(csv)
        assert len(records) == 1
        assert records[0]['start_price'] == 145.50

    def test_start_price_missing_becomes_none(self, tmp_path):
        """When start_price is missing, it should be None."""
        csv = tmp_path / 'test.csv'
        csv.write_text(
            'Ticker,3/13/2026,start price,forcast,current price,Bullish,is predicted,WAIT FOR CHANGE DIRECTION,bounce Value,Remarks\n'
            'AAPL,,,150,,YES,,NO,,\n'
        )
        records = m.load_forecast(csv)
        assert records[0]['start_price'] is None


# ─────────────────────────────────────────────────────────────────────────────
# check_forecasts — alert logic
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckForecasts:

    # ── BREAKING UP ──────────────────────────────────────────────────────────

    def test_breaking_up_fires_when_bullish_above_threshold(self):
        records = _make_records(sr_level=100.0, bullish=True)
        # threshold = 100 * (1 + 0.15/100) = 100.15
        prices = {'AAPL': 101.2}  # >1% above 100 to trigger with 1.0% tolerance
        alerts = m.check_forecasts(records, prices, {}, state := {})
        assert len(alerts) == 1
        assert alerts[0]['type'] == 'BREAKING'
        assert alerts[0]['direction'] == 'UP'
        assert alerts[0]['ticker'] == 'AAPL'

    def test_breaking_up_does_not_fire_below_threshold(self):
        records = _make_records(sr_level=100.0, bullish=True)
        prices = {'AAPL': 100.90}  # 0.9% above 100 — below 1% threshold
        alerts = m.check_forecasts(records, prices, {}, {})
        assert alerts == []

    def test_breaking_up_does_not_fire_exactly_at_sr(self):
        records = _make_records(sr_level=100.0, bullish=True)
        prices = {'AAPL': 100.0}
        alerts = m.check_forecasts(records, prices, {}, {})
        assert alerts == []

    # ── BREAKING DOWN ────────────────────────────────────────────────────────

    def test_breaking_down_fires_when_bearish_below_threshold(self):
        records = _make_records(sr_level=100.0, bullish=False)
        # threshold = 100 * (1 - 0.15/100) = 99.85
        prices = {'AAPL': 98.9}  # >1% below 100
        alerts = m.check_forecasts(records, prices, {}, state := {})
        assert len(alerts) == 1
        assert alerts[0]['type'] == 'BREAKING'
        assert alerts[0]['direction'] == 'DOWN'

    def test_breaking_down_does_not_fire_above_threshold(self):
        records = _make_records(sr_level=100.0, bullish=False)
        prices = {'AAPL': 99.90}  # 0.1% below 100 — above 99% threshold (doesn't trigger)
        alerts = m.check_forecasts(records, prices, {}, {})
        assert alerts == []

    # ── BOUNCING ─────────────────────────────────────────────────────────────

    def test_bouncing_fires_when_price_near_bounce_value(self):
        # sr_level=None so no BREAKING alert interferes; only BOUNCING fires
        records = _make_records(sr_level=None, bullish=False, bounce_value=40.0)
        prices = {'AAPL': 40.15}   # 0.375% away — within 0.5% tolerance
        alerts = m.check_forecasts(records, prices, {}, state := {})
        assert len(alerts) == 1
        assert alerts[0]['type'] == 'BOUNCING'
        assert alerts[0]['level'] == 40.0

    def test_bouncing_does_not_fire_when_too_far(self):
        records = _make_records(sr_level=None, bullish=False, bounce_value=40.0)
        prices = {'AAPL': 40.30}   # 0.75% away — outside 0.5% tolerance
        alerts = m.check_forecasts(records, prices, {}, {})
        assert alerts == []

    def test_bouncing_fires_both_above_and_below_target(self):
        records = _make_records(sr_level=None, bounce_value=100.0)
        for p in (99.60, 100.40):   # within ±0.5%
            alerts = m.check_forecasts(records, {records[0]['ticker']: p}, {}, {})
            assert any(a['type'] == 'BOUNCING' for a in alerts), f"Expected BOUNCING at ${p}"

    # ── DIRECTION CHANGE ─────────────────────────────────────────────────────

    def test_direction_change_bullish_to_bearish_fires_when_near_sr(self):
        records = _make_records(sr_level=100.0, bullish=True, wait_direction=True)
        prices = {'AAPL': 99.0}   # -1% from SR — within 1.5% proximity, bullish side below
        alerts = m.check_forecasts(records, prices, {}, state := {})
        dc = [a for a in alerts if a['type'] == 'DIRECTION_CHANGE']
        assert len(dc) == 1
        assert dc[0]['direction'] == 'BULLISH→BEARISH'

    def test_direction_change_bearish_to_bullish_fires_when_near_sr(self):
        records = _make_records(sr_level=100.0, bullish=False, wait_direction=True)
        prices = {'AAPL': 101.0}  # +1% from SR — within 1.5%, bearish crossing up
        alerts = m.check_forecasts(records, prices, {}, state := {})
        dc = [a for a in alerts if a['type'] == 'DIRECTION_CHANGE']
        assert len(dc) == 1
        assert dc[0]['direction'] == 'BEARISH→BULLISH'

    def test_direction_change_does_not_fire_when_far_from_sr(self):
        records = _make_records(sr_level=100.0, bullish=False, wait_direction=True)
        prices = {'AAPL': 105.0}  # +5% — too far from SR
        alerts = m.check_forecasts(records, prices, {}, {})
        assert not any(a['type'] == 'DIRECTION_CHANGE' for a in alerts)

    def test_direction_change_does_not_fire_when_wait_direction_false(self):
        records = _make_records(sr_level=100.0, bullish=False, wait_direction=False)
        prices = {'AAPL': 101.0}
        alerts = m.check_forecasts(records, prices, {}, {})
        assert not any(a['type'] == 'DIRECTION_CHANGE' for a in alerts)

    # ── Deduplication ────────────────────────────────────────────────────────

    def test_break_alert_only_fires_once(self):
        records = _make_records(sr_level=100.0, bullish=True)
        prices = {'AAPL': 101.2}  # >1% above 100 to trigger with 1.0% tolerance
        state = {}
        # First check — should fire
        alerts1 = m.check_forecasts(records, prices, {}, state)
        assert len(alerts1) == 1
        assert 'AAPL_break' in state
        # Second check — state persists, should NOT fire again
        alerts2 = m.check_forecasts(records, prices, {}, state)
        assert alerts2 == []

    def test_bounce_alert_only_fires_once(self):
        records = _make_records(bounce_value=50.0)
        prices = {'AAPL': 50.20}
        state = {}
        m.check_forecasts(records, prices, {}, state)
        assert 'AAPL_bounce' in state
        alerts2 = m.check_forecasts(records, prices, {}, state)
        assert alerts2 == []

    def test_direction_change_only_fires_once(self):
        records = _make_records(sr_level=100.0, bullish=False, wait_direction=True)
        prices = {'AAPL': 101.0}
        state = {}
        m.check_forecasts(records, prices, {}, state)
        assert 'AAPL_direction' in state
        alerts2 = m.check_forecasts(records, prices, {}, state)
        assert not any(a['type'] == 'DIRECTION_CHANGE' for a in alerts2)

    # ── Missing price ────────────────────────────────────────────────────────

    def test_no_alert_when_price_missing(self):
        records = _make_records(sr_level=100.0, bullish=True, bounce_value=50.0)
        alerts = m.check_forecasts(records, {}, {}, {})  # empty prices
        assert alerts == []

    # ── Multiple tickers ─────────────────────────────────────────────────────

    def test_multiple_tickers_independent(self):
        records = [
            {'ticker': 'A', 'sr_level': 100.0, 'bullish': True,
             'wait_direction': False, 'bounce_value': None, 'remarks': ''},
            {'ticker': 'B', 'sr_level': 200.0, 'bullish': False,
             'wait_direction': False, 'bounce_value': None, 'remarks': ''},
        ]
        prices = {'A': 101.2, 'B': 198.0}  # A breaks >1% up, B breaks >1% down
        alerts = m.check_forecasts(records, prices, {}, {})
        types = {a['ticker']: a for a in alerts}
        assert types['A']['direction'] == 'UP'
        assert types['B']['direction'] == 'DOWN'

    # ── as_predicted flag ─────────────────────────────────────────────────────

    def test_as_predicted_true_when_bullish_breaks_up(self):
        """Forecast: bullish=YES, price breaks UP → as_predicted=True."""
        records = _make_records(sr_level=100.0, bullish=True)
        alerts = m.check_forecasts(records, {'AAPL': 101.2}, {}, {})  # >1% above 100
        assert len(alerts) == 1
        assert alerts[0]['direction'] == 'UP'
        assert alerts[0]['as_predicted'] is True

    def test_as_predicted_true_when_bearish_breaks_down(self):
        """Forecast: bullish=NO, price breaks DOWN → as_predicted=True."""
        records = _make_records(sr_level=100.0, bullish=False)
        alerts = m.check_forecasts(records, {'AAPL': 98.9}, {}, {})  # >1% below 100
        assert len(alerts) == 1
        assert alerts[0]['direction'] == 'DOWN'
        assert alerts[0]['as_predicted'] is True

    def test_as_predicted_false_when_bullish_breaks_down(self):
        """Forecast: bullish=YES, but price breaks DOWN → surprise, as_predicted=False."""
        records = _make_records(sr_level=100.0, bullish=True)
        alerts = m.check_forecasts(records, {'AAPL': 98.9}, {}, {})  # >1% below 100
        assert len(alerts) == 1
        assert alerts[0]['direction'] == 'DOWN'
        assert alerts[0]['as_predicted'] is False

    def test_as_predicted_false_when_bearish_breaks_up(self):
        """Forecast: bullish=NO, but price breaks UP → surprise, as_predicted=False."""
        records = _make_records(sr_level=100.0, bullish=False)
        alerts = m.check_forecasts(records, {'AAPL': 101.2}, {}, {})  # >1% above 100
        assert len(alerts) == 1
        assert alerts[0]['direction'] == 'UP'
        assert alerts[0]['as_predicted'] is False

    def test_as_predicted_stored_in_state(self):
        """as_predicted value is persisted in state to survive process restarts."""
        records = _make_records(sr_level=100.0, bullish=True)
        state = {}
        m.check_forecasts(records, {'AAPL': 101.2}, {}, state)  # >1% above 100
        assert state['AAPL_break']['as_predicted'] is True
        assert state['AAPL_break']['direction'] == 'UP'

    def test_no_duplicate_after_break_recorded(self):
        """Once a BREAKING alert fires, the same ticker must NOT fire again."""
        records = _make_records(sr_level=100.0, bullish=True)
        state = {}
        alerts1 = m.check_forecasts(records, {'AAPL': 101.2}, {}, state)  # >1% above 100
        assert len(alerts1) == 1
        # Second check — price still above, state already has the key
        alerts2 = m.check_forecasts(records, {'AAPL': 101.00}, {}, state)
        assert alerts2 == [], "Alert fired twice — deduplication broken"

    def test_surprise_break_also_not_duplicated(self):
        """Surprise break (as_predicted=False) is also deduplicated."""
        records = _make_records(sr_level=100.0, bullish=True)  # expected UP
        state = {}
        # First check: surprise DOWN break fires
        alerts1 = m.check_forecasts(records, {'AAPL': 98.9}, {}, state)  # >1% below
        assert len(alerts1) == 1
        assert alerts1[0]['as_predicted'] is False
        # Second check: same key, should NOT fire again
        alerts2 = m.check_forecasts(records, {'AAPL': 98.5}, {}, state)
        assert alerts2 == []

    # ── Direction-flip re-notification ───────────────────────────────────────

    def test_direction_flip_up_to_down_fires_new_alert(self):
        """After a BREAKING UP alert, a price reversal below SR must fire BREAKING DOWN."""
        records = _make_records(sr_level=100.0, bullish=True)
        state = {}
        # First: price breaks UP
        alerts1 = m.check_forecasts(records, {'AAPL': 101.2}, {}, state)  # >1% above 100
        assert len(alerts1) == 1
        assert alerts1[0]['direction'] == 'UP'
        assert state['AAPL_break']['direction'] == 'UP'
        # Now price reverses and breaks DOWN (>1% below)
        alerts2 = m.check_forecasts(records, {'AAPL': 98.9}, {}, state)
        assert len(alerts2) == 1, "Direction flip UP→DOWN should fire a new alert"
        assert alerts2[0]['direction'] == 'DOWN'
        assert state['AAPL_break']['direction'] == 'DOWN'

    def test_direction_flip_down_to_up_fires_new_alert(self):
        """After a BREAKING DOWN alert, a price reversal above SR must fire BREAKING UP."""
        records = _make_records(sr_level=100.0, bullish=False)
        state = {}
        # First: price breaks DOWN (as_predicted=True, bearish forecast matched)
        alerts1 = m.check_forecasts(records, {'AAPL': 98.9}, {}, state)  # >1% below 100
        assert len(alerts1) == 1
        assert alerts1[0]['direction'] == 'DOWN'
        assert alerts1[0]['as_predicted'] is True
        # Now price reverses and breaks UP (surprise — goes against bearish forecast)
        alerts2 = m.check_forecasts(records, {'AAPL': 101.2}, {}, state)  # >1% above 100
        assert len(alerts2) == 1, "Direction flip DOWN→UP should fire a new alert"
        assert alerts2[0]['direction'] == 'UP'
        assert alerts2[0]['as_predicted'] is False  # now a surprise
        assert state['AAPL_break']['direction'] == 'UP'

    def test_same_direction_after_flip_does_not_re_fire(self):
        """After UP→DOWN flip, another DOWN check must NOT fire again."""
        records = _make_records(sr_level=100.0, bullish=True)
        state = {}
        m.check_forecasts(records, {'AAPL': 101.2}, {}, state)  # >1% above 100  # UP
        m.check_forecasts(records, {'AAPL': 98.9}, {}, state)   # >1% below DOWN (flip)
        alerts3 = m.check_forecasts(records, {'AAPL': 98.5}, {}, state)  # still DOWN
        assert alerts3 == [], "Second DOWN check after flip must be deduplicated"

    def test_as_predicted_updates_correctly_on_flip(self):
        """as_predicted flips from True to False when a bullish ticker reverses DOWN."""
        records = _make_records(sr_level=100.0, bullish=True)  # bullish forecast
        state = {}
        # Initial break UP → as_predicted=True (matched forecast)
        m.check_forecasts(records, {'AAPL': 101.2}, {}, state)  # >1% above 100
        assert state['AAPL_break']['as_predicted'] is True
        # Reversal DOWN → as_predicted=False (surprise — goes against bullish forecast)
        m.check_forecasts(records, {'AAPL': 98.9}, {}, state)  # >1% below 100
        assert state['AAPL_break']['as_predicted'] is False

    def test_price_in_neutral_zone_does_not_trigger_flip(self):
        """Price returning to the neutral zone (between thresholds) must NOT fire."""
        records = _make_records(sr_level=100.0, bullish=True)
        state = {}
        m.check_forecasts(records, {'AAPL': 101.2}, {}, state)  # >1% above 100  # UP break
        # Price pulls back into neutral zone — no alert
        alerts = m.check_forecasts(records, {'AAPL': 100.05}, {}, state)
        assert alerts == [], "Price in neutral zone must not fire any alert"
        # State direction must remain UP (not reset by neutral price)
        assert state['AAPL_break']['direction'] == 'UP'


# ─────────────────────────────────────────────────────────────────────────────
# send_discord_alert — Discord notification
# ─────────────────────────────────────────────────────────────────────────────

class TestSendDiscordAlert:
    """Verifies Discord notification is built and POSTed correctly for all alert types."""

    ALERTS_BREAKING_AND_BOUNCING = [
        {
            'type': 'BREAKING',
            'ticker': 'AAPL',
            'price': 152.0,
            'level': 150.0,
            'direction': 'UP',
            'as_predicted': True,
            'remarks': '',
        },
        {
            'type': 'BREAKING',
            'ticker': 'MSFT',
            'price': 390.0,
            'level': 392.5,
            'direction': 'DOWN',
            'remarks': 'support test',
        },
        {
            'type': 'BOUNCING',
            'ticker': 'TSLA',
            'price': 416.10,
            'level': 416.0,
            'remarks': '416 IS THE ENTRY POINT',
        },
        {
            'type': 'DIRECTION_CHANGE',
            'ticker': 'QQQ',
            'price': 591.0,
            'level': 589.0,
            'direction': 'BEARISH→BULLISH',
            'remarks': '',
        },
    ]

    def _mock_response(self, status=204):
        resp = MagicMock()
        resp.status_code = status
        resp.text = ''
        return resp

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_all_alert_types_sent_in_single_embed(self, mock_post):
        mock_post.return_value = self._mock_response(204)
        result = m.send_discord_alert(self.ALERTS_BREAKING_AND_BOUNCING, source='IB Live')
        assert result is True
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs['json']
        embed = payload['embeds'][0]
        field_names = [f['name'] for f in embed['fields']]
        # All 4 alerts must appear
        assert any('BREAKING' in n and 'AAPL' in n for n in field_names)
        assert any('BREAKING' in n and 'MSFT' in n for n in field_names)
        assert any('BOUNCING' in n and 'TSLA' in n for n in field_names)
        assert any('DIRECTION CHANGE' in n and 'QQQ' in n for n in field_names)

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_breaking_up_uses_rocket_emoji(self, mock_post):
        mock_post.return_value = self._mock_response(204)
        alerts = [{'type': 'BREAKING', 'ticker': 'AAPL', 'price': 152.0,
                   'level': 150.0, 'direction': 'UP', 'remarks': ''}]
        m.send_discord_alert(alerts)
        field = mock_post.call_args.kwargs['json']['embeds'][0]['fields'][0]
        assert '🚀' in field['name']

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_breaking_down_uses_chart_down_emoji(self, mock_post):
        mock_post.return_value = self._mock_response(204)
        alerts = [{'type': 'BREAKING', 'ticker': 'MSFT', 'price': 390.0,
                   'level': 392.5, 'direction': 'DOWN', 'as_predicted': True, 'remarks': ''}]
        m.send_discord_alert(alerts)
        field = mock_post.call_args.kwargs['json']['embeds'][0]['fields'][0]
        assert '📉' in field['name']

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_as_predicted_true_shown_in_discord_field(self, mock_post):
        """as_predicted=True → '✅ As Predicted' appears in the Discord field value."""
        mock_post.return_value = self._mock_response(204)
        alerts = [{'type': 'BREAKING', 'ticker': 'AAPL', 'price': 152.0,
                   'level': 150.0, 'direction': 'UP', 'as_predicted': True, 'remarks': ''}]
        m.send_discord_alert(alerts)
        value = mock_post.call_args.kwargs['json']['embeds'][0]['fields'][0]['value']
        assert '✅ As Predicted' in value

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_as_predicted_false_shown_in_discord_field(self, mock_post):
        """as_predicted=False → '❌ Surprise Move' appears in the Discord field value."""
        mock_post.return_value = self._mock_response(204)
        alerts = [{'type': 'BREAKING', 'ticker': 'AAPL', 'price': 99.80,
                   'level': 100.0, 'direction': 'DOWN', 'as_predicted': False, 'remarks': ''}]
        m.send_discord_alert(alerts)
        value = mock_post.call_args.kwargs['json']['embeds'][0]['fields'][0]['value']
        assert '❌ Surprise Move' in value

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_bouncing_notification_sent(self, mock_post):
        """BOUNCING notification must reach Discord."""
        mock_post.return_value = self._mock_response(204)
        alerts = [{'type': 'BOUNCING', 'ticker': 'TSLA', 'price': 416.1,
                   'level': 416.0, 'remarks': '416 IS THE ENTRY POINT'}]
        result = m.send_discord_alert(alerts, source='yfinance')
        assert result is True
        mock_post.assert_called_once()
        field = mock_post.call_args.kwargs['json']['embeds'][0]['fields'][0]
        assert '🔄' in field['name']
        assert 'TSLA' in field['name']
        assert '416 IS THE ENTRY POINT' in field['value']

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_direction_change_notification_sent(self, mock_post):
        """DIRECTION_CHANGE notification must reach Discord."""
        mock_post.return_value = self._mock_response(204)
        alerts = [{'type': 'DIRECTION_CHANGE', 'ticker': 'SPY', 'price': 661.0,
                   'level': 659.0, 'direction': 'BEARISH→BULLISH', 'remarks': ''}]
        result = m.send_discord_alert(alerts)
        assert result is True
        field = mock_post.call_args.kwargs['json']['embeds'][0]['fields'][0]
        assert '⚠' in field['name']
        assert 'SPY' in field['name']
        assert 'BEARISH→BULLISH' in field['value']

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_source_appears_in_footer(self, mock_post):
        mock_post.return_value = self._mock_response(204)
        alerts = [{'type': 'BOUNCING', 'ticker': 'X', 'price': 10.0,
                   'level': 10.0, 'remarks': ''}]
        m.send_discord_alert(alerts, source='IB Live')
        footer = mock_post.call_args.kwargs['json']['embeds'][0]['footer']['text']
        assert 'IB Live' in footer

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_remarks_appended_to_field_value(self, mock_post):
        mock_post.return_value = self._mock_response(204)
        alerts = [{'type': 'BREAKING', 'ticker': 'X', 'price': 10.0,
                   'level': 10.0, 'direction': 'UP', 'remarks': 'key level'}]
        m.send_discord_alert(alerts)
        value = mock_post.call_args.kwargs['json']['embeds'][0]['fields'][0]['value']
        assert 'key level' in value

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_discord_api_failure_returns_false(self, mock_post):
        mock_post.return_value = self._mock_response(400)
        alerts = [{'type': 'BREAKING', 'ticker': 'X', 'price': 10.0,
                   'level': 9.9, 'direction': 'UP', 'remarks': ''}]
        result = m.send_discord_alert(alerts)
        assert result is False

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post', side_effect=Exception("timeout"))
    def test_network_exception_returns_false(self, mock_post):
        alerts = [{'type': 'BOUNCING', 'ticker': 'X', 'price': 10.0,
                   'level': 10.0, 'remarks': ''}]
        result = m.send_discord_alert(alerts)
        assert result is False

    def test_empty_alert_list_returns_true_without_posting(self):
        with patch('live20_monitor.requests.post') as mock_post:
            result = m.send_discord_alert([])
            assert result is True
            mock_post.assert_not_called()

    def test_dry_run_does_not_post(self):
        with patch('live20_monitor.requests.post') as mock_post:
            alerts = [{'type': 'BREAKING', 'ticker': 'X', 'price': 10.0,
                       'level': 9.9, 'direction': 'UP', 'remarks': ''}]
            result = m.send_discord_alert(alerts, dry_run=True)
            assert result is True
            mock_post.assert_not_called()

    def test_missing_webhook_url_returns_true(self):
        """No webhook configured is a no-op (not an error)."""
        with patch('live20_monitor.DISCORD_WEBHOOK_URL', ''):
            alerts = [{'type': 'BOUNCING', 'ticker': 'X', 'price': 10.0,
                       'level': 10.0, 'remarks': ''}]
            result = m.send_discord_alert(alerts)
            assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# Integration: check_forecasts → send_discord_alert (both notifications)
# ─────────────────────────────────────────────────────────────────────────────

class TestEndToEndNotifications:
    """
    Verifies that a BREAKING and a BOUNCING alert are both dispatched to Discord
    in the same call when triggered by check_forecasts.
    """

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_breaking_and_bouncing_both_sent(self, mock_post):
        resp = MagicMock()
        resp.status_code = 204
        mock_post.return_value = resp

        records = [
            # BREAKING UP: bullish, price above SR
            {'ticker': 'AAPL', 'sr_level': 150.0, 'bullish': True,
             'wait_direction': False, 'bounce_value': None, 'remarks': ''},
            # BOUNCING: bearish with bounce target, price near target
            {'ticker': 'TSLA', 'sr_level': 385.0, 'bullish': False,
             'wait_direction': False, 'bounce_value': 416.0, 'remarks': '416 entry'},
        ]
        prices = {
            'AAPL': 151.60,   # +1.07% above 150 → BREAKING UP
            'TSLA': 416.10,   # 0.024% from 416 bounce target → BOUNCING
        }
        state = {}
        alerts = m.check_forecasts(records, prices, {}, state)

        assert any(a['type'] == 'BREAKING' and a['ticker'] == 'AAPL' for a in alerts)
        assert any(a['type'] == 'BOUNCING' and a['ticker'] == 'TSLA' for a in alerts)

        m.send_discord_alert(alerts, source='IB Live')

        mock_post.assert_called_once()
        fields = mock_post.call_args.kwargs['json']['embeds'][0]['fields']
        field_names = [f['name'] for f in fields]
        assert any('AAPL' in n and 'BREAKING' in n for n in field_names), "BREAKING not in Discord payload"
        assert any('TSLA' in n and 'BOUNCING' in n for n in field_names), "BOUNCING not in Discord payload"

    @patch('live20_monitor.DISCORD_WEBHOOK_URL', 'https://discord.test/webhook')
    @patch('live20_monitor.requests.post')
    def test_breaking_down_and_direction_change_both_sent(self, mock_post):
        resp = MagicMock()
        resp.status_code = 204
        mock_post.return_value = resp

        records = [
            # BREAKING DOWN
            {'ticker': 'JPM', 'sr_level': 292.0, 'bullish': False,
             'wait_direction': False, 'bounce_value': None, 'remarks': 'make or break'},
            # DIRECTION CHANGE (bearish → bullish touch)
            {'ticker': 'QQQ', 'sr_level': 589.0, 'bullish': False,
             'wait_direction': True, 'bounce_value': None, 'remarks': ''},
        ]
        prices = {
            'JPM': 289.08,   # -1.0% below 292 → BREAKING DOWN
            'QQQ': 594.89,   # +1.0% above 589 → DIRECTION_CHANGE (bearish→bullish)
        }
        state = {}
        alerts = m.check_forecasts(records, prices, {}, state)

        assert any(a['type'] == 'BREAKING' and a['direction'] == 'DOWN' for a in alerts)
        assert any(a['type'] == 'DIRECTION_CHANGE' and a['direction'] == 'BEARISH→BULLISH' for a in alerts)

        m.send_discord_alert(alerts, source='yfinance')
        fields = mock_post.call_args.kwargs['json']['embeds'][0]['fields']
        field_names = [f['name'] for f in fields]
        assert any('JPM' in n for n in field_names)
        assert any('QQQ' in n for n in field_names)


# ─────────────────────────────────────────────────────────────────────────────
# is_market_hours
# ─────────────────────────────────────────────────────────────────────────────

class TestMarketHours:
    def _et(self, **kwargs) -> datetime:
        return datetime(2026, 3, 11, **kwargs, tzinfo=NY_TZ)  # Tuesday

    def test_open_at_930(self):
        assert m.is_market_hours(self._et(hour=9, minute=30)) is True

    def test_open_during_session(self):
        assert m.is_market_hours(self._et(hour=13, minute=0)) is True

    def test_closed_before_930(self):
        assert m.is_market_hours(self._et(hour=9, minute=29)) is False

    def test_closed_after_1600(self):
        assert m.is_market_hours(self._et(hour=16, minute=1)) is False

    def test_closed_on_saturday(self):
        sat = datetime(2026, 3, 14, 12, 0, tzinfo=NY_TZ)  # Saturday
        assert m.is_market_hours(sat) is False

    def test_closed_on_sunday(self):
        sun = datetime(2026, 3, 15, 12, 0, tzinfo=NY_TZ)  # Sunday
        assert m.is_market_hours(sun) is False

    def test_exactly_at_close(self):
        assert m.is_market_hours(self._et(hour=16, minute=0)) is True  # 16:00 included


# ─────────────────────────────────────────────────────────────────────────────
# fetch_current_prices — source routing
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchCurrentPrices:

    def test_uses_ib_when_connected(self):
        mock_ib = MagicMock()
        with patch('live20_monitor.fetch_prices_ib', new_callable=AsyncMock,
                   return_value=({'AAPL': 152.0}, {'AAPL': 1.5})):
            prices, vol_ratios, source = asyncio.run(m.fetch_current_prices(mock_ib, ['AAPL']))
        assert prices == {'AAPL': 152.0}
        assert vol_ratios == {'AAPL': 1.5}
        assert 'IB' in source

    def test_falls_back_to_yfinance_when_ib_is_none(self):
        with patch('live20_monitor.fetch_prices_yfinance', return_value={'AAPL': 150.0}):
            prices, vol_ratios, source = asyncio.run(m.fetch_current_prices(None, ['AAPL']))
        assert prices == {'AAPL': 150.0}
        assert vol_ratios == {}  # yfinance does not provide vol_ratios
        assert source == 'yfinance'

    def test_ib_partial_fallback_to_yfinance(self):
        """IB provides price for AAPL, yfinance covers TSLA."""
        mock_ib = MagicMock()
        with patch('live20_monitor.fetch_prices_ib', new_callable=AsyncMock,
                   return_value=({'AAPL': 152.0}, {'AAPL': 2.3})):
            with patch('live20_monitor.fetch_prices_yfinance', return_value={'TSLA': 390.0}):
                prices, vol_ratios, source = asyncio.run(
                    m.fetch_current_prices(mock_ib, ['AAPL', 'TSLA'])
                )
        assert prices['AAPL'] == 152.0
        assert prices['TSLA'] == 390.0
        assert vol_ratios == {'AAPL': 2.3}  # only IB tickers have vol
        assert 'IB' in source and 'yfinance' in source

    def test_all_sources_fail_returns_empty(self):
        with patch('live20_monitor.fetch_prices_yfinance', return_value={}):
            prices, vol_ratios, source = asyncio.run(m.fetch_current_prices(None, ['AAPL']))
        assert prices == {}
        assert vol_ratios == {}


# ─────────────────────────────────────────────────────────────────────────────
# State persistence
# ─────────────────────────────────────────────────────────────────────────────

class TestStatePersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        with patch('live20_monitor.STATE_DIR', tmp_path):
            state = {'AAPL_break': {'time': '10:00:00', 'price': 152.0}}
            m.save_state(state, '20260311')
            loaded = m.load_state('20260311')
        assert loaded == state

    def test_load_missing_state_returns_empty_dict(self, tmp_path):
        with patch('live20_monitor.STATE_DIR', tmp_path):
            result = m.load_state('99991231')
        assert result == {}

    def test_load_corrupted_state_returns_empty_dict(self, tmp_path):
        (tmp_path / '.live20_state_20260311.json').write_text('not json!!!')
        with patch('live20_monitor.STATE_DIR', tmp_path):
            result = m.load_state('20260311')
        assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# Symbol mapping (crypto)
# ─────────────────────────────────────────────────────────────────────────────

class TestSymbolMapping:
    def test_yf_symbol_map_empty(self):
        """Symbol mapping is empty now that we use IBIT/ETHA ETFs instead of crypto."""
        assert m._YF_SYMBOL_MAP == {}

    def test_ibit_etha_are_regular_etfs(self):
        """IBIT and ETHA are regular ETFs, not in special crypto set."""
        assert 'IBIT' not in m._IB_CRYPTO_SYMBOLS
        assert 'ETHA' not in m._IB_CRYPTO_SYMBOLS

    def test_regular_stock_not_in_map(self):
        assert 'AAPL' not in m._YF_SYMBOL_MAP

    def test_crypto_symbols_set_empty(self):
        """IB crypto symbols set is now empty (using IBIT/ETHA ETFs instead)."""
        assert m._IB_CRYPTO_SYMBOLS == set()


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION TESTS — send real Discord notifications
#
# These tests hit the real DISCORD_WEBHOOK_URL_LIVE20 and fetch live prices
# via yfinance. They are skipped automatically when the webhook URL is absent.
#
# Run manually:
#   pytest tests/test_live20_monitor.py -v -m integration
# ─────────────────────────────────────────────────────────────────────────────

import os as _os
_WEBHOOK = _os.getenv('DISCORD_WEBHOOK_URL_LIVE20', '')
_skip_no_webhook = pytest.mark.skipif(
    not _WEBHOOK,
    reason='DISCORD_WEBHOOK_URL_LIVE20 not set'
)


@pytest.mark.integration
class TestIntegrationDiscord:
    """
    Sends REAL Discord notifications. Prices are forced to guaranteed trigger
    values so every alert type fires regardless of live market price.
    """

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _force_prices(records: list[dict]) -> dict[str, float]:
        """
        Return a prices dict that will definitely trigger every alert in records:
          - BREAKING UP  (bullish=True):   price = sr_level * 1.05
          - BREAKING DOWN (bullish=False): price = sr_level * 0.95
          - BOUNCING:                      price = bounce_value  (exact hit)
        """
        prices = {}
        for rec in records:
            ticker = rec['ticker']
            sr = rec['sr_level']
            bounce = rec['bounce_value']
            if bounce is not None:
                prices[ticker] = bounce          # exact bounce hit
            elif sr is not None:
                if rec['bullish']:
                    prices[ticker] = sr * 1.05   # 5% above → BREAKING UP
                else:
                    prices[ticker] = sr * 0.95   # 5% below → BREAKING DOWN
        return prices

    # ── BREAKING UP notification ──────────────────────────────────────────────

    @_skip_no_webhook
    def test_integration_breaking_up_notification(self):
        """
        BREAKING UP: bullish ticker (SPY) crosses above S/R level.
        Sends a real Discord embed and asserts HTTP 204.
        """
        records = [{
            'ticker': 'SPY',
            'sr_level': 100.0,        # will be exceeded
            'bullish': True,
            'wait_direction': False,
            'bounce_value': None,
            'remarks': '[Integration test] BREAKING UP',
        }]
        prices = self._force_prices(records)
        state = {}
        alerts = m.check_forecasts(records, prices, {}, state)

        assert any(a['type'] == 'BREAKING' and a['direction'] == 'UP' for a in alerts), \
            "Expected a BREAKING UP alert"

        result = m.send_discord_alert(alerts, source='integration-test')
        assert result is True, "Discord POST failed"

    # ── BREAKING DOWN notification ────────────────────────────────────────────

    @_skip_no_webhook
    def test_integration_breaking_down_notification(self):
        """
        BREAKING DOWN: bearish ticker (QQQ) breaks below S/R level.
        """
        records = [{
            'ticker': 'QQQ',
            'sr_level': 99999.0,      # unreachably high → price below → BREAKING DOWN
            'bullish': False,
            'wait_direction': False,
            'bounce_value': None,
            'remarks': '[Integration test] BREAKING DOWN',
        }]
        prices = {'QQQ': 99999.0 * 0.95}
        state = {}
        alerts = m.check_forecasts(records, prices, {}, state)

        assert any(a['type'] == 'BREAKING' and a['direction'] == 'DOWN' for a in alerts)
        result = m.send_discord_alert(alerts, source='integration-test')
        assert result is True

    # ── BOUNCING notification ─────────────────────────────────────────────────

    @_skip_no_webhook
    def test_integration_bouncing_notification(self):
        """
        BOUNCING: ticker (TSLA) hits its bounce target exactly.
        """
        records = [{
            'ticker': 'TSLA',
            'sr_level': None,
            'bullish': False,
            'wait_direction': False,
            'bounce_value': 416.0,
            'remarks': '[Integration test] BOUNCING — 416 is entry point',
        }]
        prices = {'TSLA': 416.0}      # exact hit → within 0.5% → BOUNCING
        state = {}
        alerts = m.check_forecasts(records, prices, {}, state)

        assert any(a['type'] == 'BOUNCING' for a in alerts)
        result = m.send_discord_alert(alerts, source='integration-test')
        assert result is True

    # ── DIRECTION CHANGE notification ─────────────────────────────────────────

    @_skip_no_webhook
    def test_integration_direction_change_notification(self):
        """
        DIRECTION CHANGE: bearish ticker (MSTR) rises to S/R level → BEARISH→BULLISH.
        """
        records = [{
            'ticker': 'MSTR',
            'sr_level': 135.70,
            'bullish': False,
            'wait_direction': True,
            'bounce_value': None,
            'remarks': '[Integration test] DIRECTION CHANGE',
        }]
        prices = {'MSTR': 135.70 * 1.005}   # +0.5% → within 1.5% proximity
        state = {}
        alerts = m.check_forecasts(records, prices, {}, state)

        assert any(a['type'] == 'DIRECTION_CHANGE' for a in alerts)
        result = m.send_discord_alert(alerts, source='integration-test')
        assert result is True

    # ── All four types in one embed ───────────────────────────────────────────

    @_skip_no_webhook
    def test_integration_all_alert_types_in_one_message(self):
        """
        Fires BREAKING UP, BREAKING DOWN, BOUNCING, and DIRECTION CHANGE
        in a single Discord embed. This is the primary end-to-end smoke test.
        """
        records = [
            {'ticker': 'SPY',  'sr_level': 100.0,    'bullish': True,
             'wait_direction': False, 'bounce_value': None,
             'remarks': '[Test] Breaking UP'},
            {'ticker': 'QQQ',  'sr_level': 99999.0,  'bullish': False,
             'wait_direction': False, 'bounce_value': None,
             'remarks': '[Test] Breaking DOWN'},
            {'ticker': 'TSLA', 'sr_level': None,     'bullish': False,
             'wait_direction': False, 'bounce_value': 416.0,
             'remarks': '[Test] Bouncing — 416 entry'},
            {'ticker': 'MSTR', 'sr_level': 135.70,   'bullish': False,
             'wait_direction': True,  'bounce_value': None,
             'remarks': '[Test] Direction change'},
        ]
        prices = {
            'SPY':  105.0,               # BREAKING UP
            'QQQ':  99999.0 * 0.95,      # BREAKING DOWN
            'TSLA': 416.0,               # BOUNCING (exact)
            'MSTR': 135.70 * 1.005,      # DIRECTION CHANGE (+0.5%)
        }
        state = {}
        alerts = m.check_forecasts(records, prices, {}, state)

        types_found = {a['type'] for a in alerts}
        assert 'BREAKING' in types_found,        "Expected BREAKING alert"
        assert 'BOUNCING' in types_found,        "Expected BOUNCING alert"
        assert 'DIRECTION_CHANGE' in types_found, "Expected DIRECTION_CHANGE alert"

        result = m.send_discord_alert(alerts, source='integration-test — all types')
        assert result is True, "Discord POST failed"
        print(f"\n  ✓ Sent {len(alerts)} real Discord alerts: {[a['type']+':'+a['ticker'] for a in alerts]}")

    # ── Direction-flip re-notification ────────────────────────────────────────

    @_skip_no_webhook
    def test_integration_direction_flip_up_to_down_re_notification(self):
        """
        Direction-flip re-notification: BREAKING UP fires, then price reverses
        and BREAKING DOWN fires with opposite direction and potentially changed
        as_predicted status. Both notifications sent to Discord.
        """
        records = [{
            'ticker': 'AAPL',
            'sr_level': 150.0,
            'bullish': True,        # forecast is bullish
            'wait_direction': False,
            'bounce_value': None,
            'remarks': '[Integration test] Direction flip UP→DOWN',
        }]
        state = {}

        # First: price breaks UP (as_predicted=True, matches bullish forecast)
        prices_up = {'AAPL': 150.0 * 1.05}  # +5%
        alerts1 = m.check_forecasts(records, prices_up, {}, state)
        assert any(a['type'] == 'BREAKING' and a['direction'] == 'UP'
                   and a['as_predicted'] is True for a in alerts1), \
            "Expected BREAKING UP with as_predicted=True"
        result1 = m.send_discord_alert(alerts1, source='integration-test: flip UP')
        assert result1 is True, "First Discord POST (UP) failed"
        print(f"\n  ✓ Sent BREAKING UP (as_predicted=True)")

        # Now: price reverses and breaks DOWN (as_predicted=False, surprise)
        prices_down = {'AAPL': 150.0 * 0.95}  # -5%
        alerts2 = m.check_forecasts(records, prices_down, {}, state)
        assert any(a['type'] == 'BREAKING' and a['direction'] == 'DOWN'
                   and a['as_predicted'] is False for a in alerts2), \
            "Expected BREAKING DOWN with as_predicted=False (flip + surprise)"
        result2 = m.send_discord_alert(alerts2, source='integration-test: flip DOWN')
        assert result2 is True, "Second Discord POST (DOWN) failed"
        print(f"  ✓ Sent BREAKING DOWN (as_predicted=False) — direction flip detected")


# ─────────────────────────────────────────────────────────────────────────────
# Volume annotation on BREAKING alerts
# ─────────────────────────────────────────────────────────────────────────────

class TestVolumeAnnotation:
    """Volume ratio annotation on BREAKING alerts (soft, not gated)."""

    def test_breaking_high_vol(self):
        records = _make_records(sr_level=100.0, bullish=True)
        prices = {'AAPL': 101.2}  # >1% above 100 to trigger with 1.0% tolerance
        vol_ratios = {'AAPL': 2.5}
        alerts = m.check_forecasts(records, prices, vol_ratios, {})
        assert len(alerts) == 1
        assert alerts[0]['vol_label'] == 'HIGH VOL'
        assert alerts[0]['vol_ratio'] == 2.5

    def test_breaking_normal_vol(self):
        records = _make_records(sr_level=100.0, bullish=True)
        prices = {'AAPL': 101.2}  # >1% above 100 to trigger with 1.0% tolerance
        vol_ratios = {'AAPL': 1.2}
        alerts = m.check_forecasts(records, prices, vol_ratios, {})
        assert len(alerts) == 1
        assert alerts[0]['vol_label'] == 'NORMAL'

    def test_breaking_low_vol(self):
        records = _make_records(sr_level=100.0, bullish=True)
        prices = {'AAPL': 101.2}  # >1% above 100 to trigger with 1.0% tolerance
        vol_ratios = {'AAPL': 0.3}
        alerts = m.check_forecasts(records, prices, vol_ratios, {})
        assert len(alerts) == 1
        assert alerts[0]['vol_label'] == 'LOW VOL'

    def test_breaking_no_vol_data(self):
        records = _make_records(sr_level=100.0, bullish=True)
        prices = {'AAPL': 101.2}  # >1% above 100 to trigger with 1.0% tolerance
        alerts = m.check_forecasts(records, prices, {}, {})
        assert len(alerts) == 1
        assert alerts[0]['vol_label'] is None
        assert alerts[0]['vol_ratio'] is None

    def test_breaking_fires_regardless_of_low_vol(self):
        """Volume is annotation only — alert fires even on LOW VOL."""
        records = _make_records(sr_level=100.0, bullish=True)
        prices = {'AAPL': 101.2}  # >1% above 100 to trigger with 1.0% tolerance
        vol_ratios = {'AAPL': 0.1}
        alerts = m.check_forecasts(records, prices, vol_ratios, {})
        assert len(alerts) == 1
        assert alerts[0]['type'] == 'BREAKING'
        assert alerts[0]['vol_label'] == 'LOW VOL'
