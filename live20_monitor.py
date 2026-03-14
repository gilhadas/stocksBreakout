#!/usr/bin/env python3
"""
live20_monitor.py
=================
Monitors a CSV forecast file (input/live_20_*.csv) every 15 minutes during
trading hours and sends Discord alerts when price action confirms the forecast.

**Data source**: IB (Interactive Brokers) live prices when TWS/IBG is running,
automatic fallback to yfinance (15-min delayed) when IB is unavailable.

Alerts:
  🚀 BREAKING  — Bullish=YES ticker breaks above its support/resist level
  📉 BREAKING  — Bullish=NO  ticker breaks below its support/resist level
  🔄 BOUNCING  — Ticker hits its "bounce Value" target
  ⚠️  DIRECTION CHANGE — Ticker flagged "WAIT FOR CHANGE DIRECTION" touches
                         reversal zone (bullish→bearish transition)

State is persisted per-day to avoid duplicate alerts.

Usage:
  python live20_monitor.py                              # daemon (default)
  python live20_monitor.py --once                       # single check then exit
  python live20_monitor.py --dry-run                    # print only, no Discord
  python live20_monitor.py --file input/live_20_14_3_26.csv
  python live20_monitor.py --force                      # run outside market hours
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Python 3.14 event loop setup
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pandas as pd
import requests

warnings.filterwarnings('ignore', category=FutureWarning)

sys.path.insert(0, str(Path(__file__).parent))
from config import IB_HOST, IB_PAPER_PORT, IB_LIVE_PORT, IB_CLIENT_ID

from dotenv import load_dotenv
load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('live20')

# ── Constants ────────────────────────────────────────────────────────────────
NY_TZ = ZoneInfo('America/New_York')
OUT_DIR = Path('scanner_output')
STATE_DIR = OUT_DIR / 'live20'
INTERVAL_SECONDS = 15 * 60  # 15 minutes

# Market hours (ET)
MARKET_OPEN_HOUR, MARKET_OPEN_MIN = 9, 30
MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN = 16, 0

# Breakout tolerance: price must exceed level by this % to trigger
BREAK_TOLERANCE_PCT = 0.15
# Bounce proximity: price within this % of bounce value triggers alert
BOUNCE_TOLERANCE_PCT = 0.5

# Discord webhook
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL_LIVE20', '')

# yfinance symbol mapping (non-standard tickers)
_YF_SYMBOL_MAP = {
    'BTCUSD': 'BTC-USD',
    'ETHUSD': 'ETH-USD',
}

# IB contract overrides for crypto / non-stock symbols
_IB_CRYPTO_SYMBOLS = {'BTCUSD', 'ETHUSD'}


# ── CSV Loader ───────────────────────────────────────────────────────────────

def find_csv(explicit_path: str | None = None) -> Path:
    """Find the most recent live_20 CSV if no explicit path given."""
    if explicit_path:
        return Path(explicit_path)
    candidates = sorted(Path('input').glob('live_20*.csv'), key=os.path.getmtime, reverse=True)
    if not candidates:
        logger.error("No live_20*.csv found in input/")
        sys.exit(1)
    return candidates[0]


def _to_float_or_none(value) -> float | None:
    """Convert a cell value to float, returning None for NaN / non-numeric."""
    if pd.isna(value):
        return None
    try:
        result = float(value)
        return None if pd.isna(result) else result
    except (ValueError, TypeError):
        return None


def load_forecast(csv_path: Path) -> list[dict]:
    """Parse the forecast CSV into a list of ticker dicts."""
    df = pd.read_csv(csv_path, skipinitialspace=True)
    df.columns = df.columns.str.strip()
    records = []
    for _, row in df.iterrows():
        raw_ticker = row.get('Ticker', '')
        if pd.isna(raw_ticker):
            continue
        ticker = str(raw_ticker).strip().upper()
        if not ticker:
            continue

        sr_val = _to_float_or_none(row.get('support/ressist', None))

        bullish_raw = str(row.get('Bullish', '')).strip().upper()
        bullish = bullish_raw == 'YES'

        wait_dir_raw = str(row.get('WAIT FOR CHANGE DIRECTION', '')).strip().upper()
        wait_direction = wait_dir_raw == 'YES'

        bounce_val = _to_float_or_none(row.get('bounce Value', None))

        remarks_raw = row.get('Remarks', '')
        remarks = '' if pd.isna(remarks_raw) else str(remarks_raw).strip()

        records.append({
            'ticker': ticker,
            'sr_level': sr_val,
            'bullish': bullish,
            'wait_direction': wait_direction,
            'bounce_value': bounce_val,
            'remarks': remarks,
        })
    return records


# ── State persistence (one file per day) ─────────────────────────────────────

def _state_path(date_str: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f'.live20_state_{date_str}.json'


def load_state(date_str: str) -> dict:
    p = _state_path(date_str)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict, date_str: str) -> None:
    _state_path(date_str).write_text(json.dumps(state, indent=2))


# ── IB Connection ────────────────────────────────────────────────────────────

async def connect_ib() -> 'IB | None':
    """Try to connect to IB (LIVE port first, then PAPER). Return None on failure."""
    try:
        from ib_insync import IB
    except ImportError:
        logger.debug("ib_insync not installed — skipping IB")
        return None

    for port, label, mkt_type in [
        (IB_LIVE_PORT, 'LIVE', 1),
        (IB_PAPER_PORT, 'PAPER', 3),
    ]:
        ib = IB()
        try:
            await ib.connectAsync(IB_HOST, port, clientId=IB_CLIENT_ID + 10)
            ib.reqMarketDataType(mkt_type)
            logger.info(f"Connected to IB {label} (port {port})")
            return ib
        except Exception as e:
            logger.debug(f"IB {label} (port {port}) unavailable: {e}")
    return None


def disconnect_ib(ib) -> None:
    if ib is not None:
        try:
            ib.disconnect()
        except Exception:
            pass


# ── Price fetching ───────────────────────────────────────────────────────────

async def fetch_prices_ib(ib, tickers: list[str]) -> dict[str, float]:
    """Fetch current prices from IB for all tickers. Returns {ticker: price}."""
    from ib_insync import Stock, Crypto
    prices = {}
    for ticker in tickers:
        try:
            if ticker in _IB_CRYPTO_SYMBOLS:
                # Crypto pairs on IB use Crypto contract
                base = ticker.replace('USD', '')
                contract = Crypto(base, 'PAXOS', 'USD')
            else:
                contract = Stock(ticker, 'SMART', 'USD')
            await ib.qualifyContractsAsync(contract)
            md = ib.reqMktData(contract, '', False, False)
            await asyncio.sleep(0.5)
            price = md.last or md.close
            ib.cancelMktData(contract)
            if price and price > 0:
                prices[ticker] = float(price)
        except Exception as e:
            logger.debug(f"IB price failed for {ticker}: {e}")
    return prices


def fetch_prices_yfinance(tickers: list[str]) -> dict[str, float]:
    """Fallback: fetch prices from yfinance batch download."""
    import yfinance as yf
    prices = {}
    yf_tickers = [_YF_SYMBOL_MAP.get(t, t) for t in tickers]
    yf_to_orig = {_YF_SYMBOL_MAP.get(t, t): t for t in tickers}

    try:
        data = yf.download(yf_tickers, period='1d', interval='1m', progress=False,
                           group_by='ticker', threads=True)
    except Exception as e:
        logger.warning(f"yfinance batch download failed: {e}")
        return prices

    for yf_sym in yf_tickers:
        orig = yf_to_orig[yf_sym]
        try:
            if len(yf_tickers) == 1:
                ticker_df = data
            else:
                ticker_df = data[yf_sym]
            if ticker_df.empty:
                continue
            if hasattr(ticker_df.columns, 'levels'):
                ticker_df.columns = ticker_df.columns.get_level_values(-1)
            close_col = [c for c in ticker_df.columns if 'close' in str(c).lower()]
            if close_col:
                last_close = ticker_df[close_col[0]].dropna().iloc[-1]
                prices[orig] = float(last_close)
        except Exception:
            continue
    return prices


async def fetch_current_prices(ib, tickers: list[str]) -> tuple[dict[str, float], str]:
    """
    Fetch prices: IB first, yfinance fallback for any missing tickers.
    Returns (prices_dict, source_label).
    """
    prices = {}
    source = 'yfinance'

    # Try IB first
    if ib is not None:
        try:
            prices = await fetch_prices_ib(ib, tickers)
            if prices:
                source = 'IB Live'
                logger.info(f"  IB returned prices for {len(prices)}/{len(tickers)} tickers")
        except Exception as e:
            logger.warning(f"  IB price fetch error: {e}")

    # Fallback to yfinance for missing tickers
    missing = [t for t in tickers if t not in prices]
    if missing:
        yf_prices = fetch_prices_yfinance(missing)
        prices.update(yf_prices)
        if source == 'IB Live' and yf_prices:
            source = 'IB Live + yfinance fallback'
        elif not prices:
            source = 'yfinance'
        elif source != 'IB Live':
            source = 'yfinance'

    return prices, source


# ── Alert logic ──────────────────────────────────────────────────────────────

def check_forecasts(records: list[dict], prices: dict[str, float],
                    state: dict) -> list[dict]:
    """
    Compare current prices against forecast levels.
    Returns list of alert dicts.
    """
    alerts = []

    for rec in records:
        ticker = rec['ticker']
        price = prices.get(ticker)
        if price is None:
            continue

        sr = rec['sr_level']
        bullish = rec['bullish']

        # ── BREAKING alert ──────────────────────────────────────────────
        # Fires for ANY breakout (UP or DOWN) so surprise moves are also flagged.
        # as_predicted=True  when break direction matches the Bullish forecast.
        # as_predicted=False when the break goes against the forecast (surprise).
        # Re-fires when direction flips (UP→DOWN or DOWN→UP) so reversals are caught.
        if sr is not None:
            break_key = f"{ticker}_break"
            threshold_up   = sr * (1 + BREAK_TOLERANCE_PCT / 100)
            threshold_down = sr * (1 - BREAK_TOLERANCE_PCT / 100)

            if price >= threshold_up:
                direction    = 'UP'
                as_predicted = bullish          # forecast was bullish → expected UP
            elif price <= threshold_down:
                direction    = 'DOWN'
                as_predicted = not bullish      # forecast was bearish → expected DOWN
            else:
                direction    = None
                as_predicted = None

            if direction is not None:
                prev_direction = state.get(break_key, {}).get('direction')
                # Fire on first break OR when direction flips (UP→DOWN or DOWN→UP)
                if prev_direction != direction:
                    alerts.append({
                        'type': 'BREAKING',
                        'ticker': ticker,
                        'price': price,
                        'level': sr,
                        'direction': direction,
                        'as_predicted': as_predicted,
                        'remarks': rec['remarks'],
                    })
                    state[break_key] = {
                        'time': _now_str(),
                        'price': price,
                        'direction': direction,
                        'as_predicted': as_predicted,
                    }

        # ── BOUNCING alert ──────────────────────────────────────────────
        bounce = rec['bounce_value']
        if bounce is not None:
            bounce_key = f"{ticker}_bounce"
            if bounce_key not in state:
                pct_away = abs(price - bounce) / bounce * 100
                if pct_away <= BOUNCE_TOLERANCE_PCT:
                    alerts.append({
                        'type': 'BOUNCING',
                        'ticker': ticker,
                        'price': price,
                        'level': bounce,
                        'remarks': rec['remarks'],
                    })
                    state[bounce_key] = {'time': _now_str(), 'price': price}

        # ── DIRECTION CHANGE alert ──────────────────────────────────────
        if rec['wait_direction'] and sr is not None:
            dir_key = f"{ticker}_direction"
            if dir_key not in state:
                pct_from_sr = (price - sr) / sr * 100
                if abs(pct_from_sr) <= 1.5:
                    if bullish and pct_from_sr < 0:
                        direction = 'BULLISH→BEARISH'
                    elif not bullish and pct_from_sr > 0:
                        direction = 'BEARISH→BULLISH'
                    else:
                        direction = None

                    if direction:
                        alerts.append({
                            'type': 'DIRECTION_CHANGE',
                            'ticker': ticker,
                            'price': price,
                            'level': sr,
                            'direction': direction,
                            'remarks': rec['remarks'],
                        })
                        state[dir_key] = {'time': _now_str(), 'price': price}

    return alerts


def _now_str() -> str:
    return datetime.now(NY_TZ).strftime('%H:%M:%S')


# ── Discord notification ─────────────────────────────────────────────────────

def send_discord_alert(alerts: list[dict], source: str = '',
                       dry_run: bool = False) -> bool:
    """Send alerts to Discord via webhook embed."""
    if not alerts:
        return True

    now_et = datetime.now(NY_TZ)
    title = f"📊 Live20 Monitor — {now_et.strftime('%H:%M ET')} ({now_et.strftime('%Y-%m-%d')})"

    fields = []
    for a in alerts:
        if a['type'] == 'BREAKING':
            emoji = '🚀' if a.get('direction') == 'UP' else '📉'
            name = f"{emoji} BREAKING: {a['ticker']}"
            as_predicted = a.get('as_predicted')
            predicted_tag = (
                '✅ As Predicted' if as_predicted is True
                else '❌ Surprise Move' if as_predicted is False
                else ''
            )
            value = (
                f"Price: **${a['price']:.2f}** | Level: ${a['level']:.2f}\n"
                f"Direction: {a.get('direction', 'N/A')}  |  {predicted_tag}"
            )
        elif a['type'] == 'BOUNCING':
            name = f"🔄 BOUNCING: {a['ticker']}"
            value = (
                f"Price: **${a['price']:.2f}** | Bounce target: ${a['level']:.2f}"
            )
        elif a['type'] == 'DIRECTION_CHANGE':
            name = f"⚠️ DIRECTION CHANGE: {a['ticker']}"
            value = (
                f"Price: **${a['price']:.2f}** | Level: ${a['level']:.2f}\n"
                f"Reversal: {a.get('direction', 'N/A')}"
            )
        else:
            name = f"{a['type']}: {a['ticker']}"
            value = f"Price: ${a['price']:.2f}"

        if a.get('remarks'):
            value += f"\n_{a['remarks']}_"
        fields.append({'name': name, 'value': value, 'inline': False})

    footer_text = f"Live20 Forecast Monitor | Data: {source}" if source else 'Live20 Forecast Monitor'
    embed = {
        'title': title,
        'color': 0x00FF88,
        'fields': fields,
        'footer': {'text': footer_text},
    }
    data = {'embeds': [embed]}

    if dry_run:
        logger.info(f"[DRY-RUN] Would send Discord alert with {len(alerts)} alerts:")
        for a in alerts:
            logger.info(f"  {a['type']}: {a['ticker']} @ ${a['price']:.2f} (level=${a['level']:.2f})")
        return True

    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL_LIVE20 not set, skipping notification")
        return True  # not a failure — webhook simply not configured

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=data, timeout=10)
        if resp.status_code == 204:
            logger.info(f"Discord alert sent ({len(alerts)} alerts)")
            return True
        else:
            logger.error(f"Discord returned {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"Discord send failed: {e}")
        return False


# ── Market hours check ───────────────────────────────────────────────────────

def is_market_hours(now_et: datetime | None = None) -> bool:
    if now_et is None:
        now_et = datetime.now(NY_TZ)
    if now_et.weekday() >= 5:
        return False
    market_open = now_et.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0)
    market_close = now_et.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0)
    return market_open <= now_et <= market_close


# ── Main async loop ──────────────────────────────────────────────────────────

async def run_check(csv_path: Path, ib, dry_run: bool = False,
                    force: bool = False) -> int:
    """Single check iteration. Returns number of alerts fired."""
    now_et = datetime.now(NY_TZ)
    date_str = now_et.strftime('%Y%m%d')

    if not force and not is_market_hours(now_et):
        logger.info(f"Market closed ({now_et.strftime('%H:%M ET %A')}). Use --force to override.")
        return 0

    logger.info(f"{'='*60}")
    logger.info(f"Live20 check @ {now_et.strftime('%H:%M:%S ET')} — {csv_path.name}")
    logger.info(f"{'='*60}")

    records = load_forecast(csv_path)
    if not records:
        logger.warning("No records found in CSV")
        return 0
    logger.info(f"Loaded {len(records)} tickers from forecast")

    tickers = [r['ticker'] for r in records]
    logger.info(f"Fetching prices for {len(tickers)} tickers...")
    prices, source = await fetch_current_prices(ib, tickers)
    logger.info(f"Got prices for {len(prices)}/{len(tickers)} tickers [{source}]")

    state = load_state(date_str)
    alerts = check_forecasts(records, prices, state)

    # Print status table
    print(f"\n  {'Ticker':<8} {'Price':>10} {'S/R Level':>10} {'Bull':>5} {'Status':<15}")
    print(f"  {'─'*8} {'─'*10} {'─'*10} {'─'*5} {'─'*15}")
    for rec in records:
        ticker = rec['ticker']
        price = prices.get(ticker)
        sr = rec['sr_level']
        bull = 'YES' if rec['bullish'] else 'NO'
        if price is None:
            status = 'NO DATA'
        elif sr is None:
            status = '—'
        else:
            diff_pct = (price - sr) / sr * 100
            if rec['bullish']:
                status = f"{'✅ ABOVE' if price >= sr else '⏳ BELOW'} ({diff_pct:+.1f}%)"
            else:
                status = f"{'✅ BELOW' if price <= sr else '⏳ ABOVE'} ({diff_pct:+.1f}%)"
        price_str = f"${price:.2f}" if price else 'N/A'
        sr_str = f"${sr:.2f}" if sr else '—'
        print(f"  {ticker:<8} {price_str:>10} {sr_str:>10} {bull:>5} {status:<15}")

    if alerts:
        logger.info(f"🔔 {len(alerts)} alert(s) triggered!")
        send_discord_alert(alerts, source=source, dry_run=dry_run)
    else:
        logger.info(f"No new alerts (already notified: {len(state)} events)")

    save_state(state, date_str)
    return len(alerts)


async def daemon_loop(csv_path: Path, dry_run: bool = False, force: bool = False):
    """Main daemon: connect IB once, loop every 15 min, reconnect on failure."""
    logger.info(f"Live20 Monitor — daemon mode (every {INTERVAL_SECONDS // 60} min)")
    logger.info(f"Forecast file: {csv_path}")

    ib = await connect_ib()
    if ib is None:
        logger.warning("IB unavailable — using yfinance as data source")
    else:
        logger.info("IB connected — using live prices")

    try:
        while True:
            now_et = datetime.now(NY_TZ)

            if is_market_hours(now_et) or force:
                # Check IB connection health, reconnect if needed
                if ib is not None:
                    try:
                        if not ib.isConnected():
                            logger.warning("IB disconnected — reconnecting...")
                            disconnect_ib(ib)
                            ib = await connect_ib()
                    except Exception:
                        logger.warning("IB connection check failed — reconnecting...")
                        disconnect_ib(ib)
                        ib = await connect_ib()

                await run_check(csv_path, ib, dry_run=dry_run, force=force)
            else:
                logger.info(f"[{now_et.strftime('%H:%M ET')}] Market closed, sleeping...")

            await asyncio.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        disconnect_ib(ib)
        logger.info("IB disconnected. Goodbye.")


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Live20 forecast monitor')
    parser.add_argument('--file', help='Path to forecast CSV')
    parser.add_argument('--dry-run', action='store_true', help='No Discord alerts')
    parser.add_argument('--once', action='store_true',
                        help='Run single check then exit (instead of daemon)')
    parser.add_argument('--force', action='store_true',
                        help='Run even outside market hours')
    args = parser.parse_args()

    csv_path = find_csv(args.file)

    if args.once:
        # Single check mode
        async def _once():
            ib = await connect_ib()
            try:
                await run_check(csv_path, ib, dry_run=args.dry_run, force=args.force)
            finally:
                disconnect_ib(ib)
        asyncio.run(_once())
    else:
        # Daemon mode (default)
        asyncio.run(daemon_loop(csv_path, dry_run=args.dry_run, force=args.force))


if __name__ == '__main__':
    main()
