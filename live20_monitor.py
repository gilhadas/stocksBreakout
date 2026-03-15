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
# 1.0% = industry standard for "confirmed breakout" (filters noise, catches early moves)
BREAK_TOLERANCE_PCT = 1.0
# Bounce proximity: price within this % of bounce value triggers alert
BOUNCE_TOLERANCE_PCT = 0.5

# Volume annotation thresholds (pace-adjusted IB cumulative volume)
VOL_SURGE_RATIO = 2.0     # >= 2x pace-adjusted avg = HIGH VOL
VOL_NORMAL_RATIO = 0.8    # >= 0.8x = NORMAL, below = LOW VOL
VOL_MIN_MINUTES = 15      # skip vol annotation in first 15 min (extrapolation unreliable)
TRADING_MINUTES = 390     # 6.5 hours (9:30-4:00)
VOL_ROLLING_BARS = 20     # rolling window for 1-min bar vol ratio

# Discord webhook
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL_LIVE20', '')

# yfinance symbol mapping (non-standard tickers)
# Note: IBIT and ETHA are regular ETFs, no special mapping needed
_YF_SYMBOL_MAP = {}

# IB contract overrides for crypto / non-stock symbols
# Note: Switched from BTC-USD/ETH-USD to IBIT/ETHA ETFs
_IB_CRYPTO_SYMBOLS = set()


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

        current_price = _to_float_or_none(row.get('current price', None))
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
            'current_price': current_price,
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

async def fetch_prices_ib(ib, tickers: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    """Fetch current prices and pace-adjusted volume ratios from IB.
    Returns (prices, vol_ratios) dicts keyed by ticker.
    """
    from ib_insync import Stock, Crypto
    prices = {}
    vol_ratios = {}
    now_et = datetime.now(NY_TZ)
    market_open = now_et.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN,
                                 second=0, microsecond=0)
    mins_elapsed = max((now_et - market_open).total_seconds() / 60, 1)

    for ticker in tickers:
        try:
            if ticker in _IB_CRYPTO_SYMBOLS:
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
            # Pace-adjusted volume ratio from IB cumulative volume
            cum_vol = getattr(md, 'volume', None)
            avg_vol = getattr(md, 'avVolume', None)
            if cum_vol and avg_vol and avg_vol > 0 and mins_elapsed >= VOL_MIN_MINUTES:
                day_fraction = mins_elapsed / TRADING_MINUTES
                projected_vol = cum_vol / day_fraction
                vol_ratios[ticker] = round(projected_vol / avg_vol, 2)
        except Exception as e:
            logger.debug(f"IB price failed for {ticker}: {e}")
    return prices, vol_ratios


def _parse_yfinance_ticker_df(ticker_df) -> tuple[float | None, float | None]:
    """Extract (last_close, vol_ratio) from a single-ticker yfinance 1-min DataFrame.
    vol_ratio is bar-relative: latest 1-min bar vs rolling 20-bar mean.
    Note: yfinance free data has ~15-min delay. vol_ratio is marked delayed but still valid
    as a relative measure.
    """
    if hasattr(ticker_df.columns, 'levels'):
        ticker_df = ticker_df.copy()
        ticker_df.columns = ticker_df.columns.get_level_values(-1)

    last_close = None
    vol_ratio = None

    close_col = [c for c in ticker_df.columns if 'close' in str(c).lower()]
    if close_col:
        series = ticker_df[close_col[0]].dropna()
        if len(series) > 0:
            last_close = float(series.iloc[-1])

    vol_col = [c for c in ticker_df.columns if 'volume' in str(c).lower()]
    if vol_col:
        vol_series = ticker_df[vol_col[0]].dropna()
        if len(vol_series) >= VOL_ROLLING_BARS:
            rolling_avg = vol_series.rolling(VOL_ROLLING_BARS).mean().iloc[-1]
            latest_vol = float(vol_series.iloc[-1])
            if rolling_avg > 0:
                vol_ratio = round(latest_vol / rolling_avg, 2)

    return last_close, vol_ratio


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
            ticker_df = data if len(yf_tickers) == 1 else data[yf_sym]
            if ticker_df.empty:
                continue
            last_close, _ = _parse_yfinance_ticker_df(ticker_df)
            if last_close is not None:
                prices[orig] = last_close
        except Exception:
            continue
    return prices


def fetch_vol_ratios_yfinance(tickers: list[str]) -> dict[str, float]:
    """Fetch bar-relative 1-min volume ratios from yfinance (delayed ~15 min).
    Compares latest 1-min bar volume against rolling 20-bar mean — inherently
    pace-adjusted (apples-to-apples bar comparison).
    Used as fallback when IB is not connected.
    """
    import yfinance as yf
    vol_ratios = {}
    yf_tickers = [_YF_SYMBOL_MAP.get(t, t) for t in tickers]
    yf_to_orig = {_YF_SYMBOL_MAP.get(t, t): t for t in tickers}

    try:
        data = yf.download(yf_tickers, period='1d', interval='1m', progress=False,
                           group_by='ticker', threads=True)
    except Exception as e:
        logger.warning(f"yfinance vol fetch failed: {e}")
        return vol_ratios

    for yf_sym in yf_tickers:
        orig = yf_to_orig[yf_sym]
        try:
            ticker_df = data if len(yf_tickers) == 1 else data[yf_sym]
            if ticker_df.empty:
                continue
            _, vr = _parse_yfinance_ticker_df(ticker_df)
            if vr is not None:
                vol_ratios[orig] = vr
        except Exception:
            continue
    logger.info(f"  yfinance vol ratios for {len(vol_ratios)}/{len(tickers)} tickers (delayed ~15m)")
    return vol_ratios


async def fetch_current_prices(ib, tickers: list[str]) -> tuple[dict[str, float], dict[str, float], str]:
    """
    Fetch prices: IB first, yfinance fallback for any missing tickers.
    Returns (prices_dict, vol_ratios_dict, source_label).
    vol_ratios only populated from IB (real-time); yfinance has 15-min delay.
    """
    prices = {}
    vol_ratios = {}
    source = 'yfinance'

    # Try IB first
    if ib is not None:
        try:
            prices, vol_ratios = await fetch_prices_ib(ib, tickers)
            if prices:
                source = 'IB Live'
                logger.info(f"  IB returned prices for {len(prices)}/{len(tickers)} tickers"
                            f" (vol ratios: {len(vol_ratios)})")
        except Exception as e:
            logger.warning(f"  IB price fetch error: {e}")

    # Fallback to yfinance for missing tickers (no vol_ratios from yfinance)
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

    return prices, vol_ratios, source


# ── Alert logic ──────────────────────────────────────────────────────────────

def check_forecasts(records: list[dict], prices: dict[str, float],
                    vol_ratios: dict[str, float],
                    state: dict) -> list[dict]:
    """
    Compare current prices against forecast levels.
    Returns list of alert dicts. BREAKING alerts are annotated with volume.
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
                    # Volume annotation (soft — does not gate the alert)
                    vr = vol_ratios.get(ticker)
                    if vr is not None:
                        if vr >= VOL_SURGE_RATIO:
                            vol_label = 'HIGH VOL'
                        elif vr >= VOL_NORMAL_RATIO:
                            vol_label = 'NORMAL'
                        else:
                            vol_label = 'LOW VOL'
                    else:
                        vol_label = None

                    alerts.append({
                        'type': 'BREAKING',
                        'ticker': ticker,
                        'price': price,
                        'level': sr,
                        'direction': direction,
                        'as_predicted': as_predicted,
                        'vol_ratio': vr,
                        'vol_label': vol_label,
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
    is_backtest = source.startswith('BACKTEST')
    if is_backtest:
        title = f"🔁 Live20 BACKTEST — {source}"
    else:
        title = f"📊 Live20 Monitor — {now_et.strftime('%H:%M ET')} ({now_et.strftime('%Y-%m-%d')})"

    fields = []
    for a in alerts:
        bar_time = f" @ {a['bar_time']}" if a.get('bar_time') else ''
        if a['type'] == 'BREAKING':
            emoji = '🚀' if a.get('direction') == 'UP' else '📉'
            name = f"{emoji} BREAKING: {a['ticker']}{bar_time}"
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
            if a.get('vol_label'):
                vol_emoji = '🔥' if a['vol_label'] == 'HIGH VOL' else ''
                value += f"\nVolume: {vol_emoji} **{a['vol_label']}** ({a['vol_ratio']:.1f}x avg)"
        elif a['type'] == 'BOUNCING':
            name = f"🔄 BOUNCING: {a['ticker']}{bar_time}"
            value = (
                f"Price: **${a['price']:.2f}** | Bounce target: ${a['level']:.2f}"
            )
        elif a['type'] == 'DIRECTION_CHANGE':
            name = f"⚠️ DIRECTION CHANGE: {a['ticker']}{bar_time}"
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
    color = 0xFFA500 if is_backtest else 0x00FF88

    # Discord limit: 25 fields per embed — split into batches
    MAX_FIELDS = 25
    field_batches = [fields[i:i + MAX_FIELDS] for i in range(0, max(len(fields), 1), MAX_FIELDS)]

    embeds = []
    for i, batch in enumerate(field_batches):
        embed = {
            'title': title if i == 0 else f"{title} (cont.)",
            'color': color,
            'fields': batch,
        }
        if i == len(field_batches) - 1:
            embed['footer'] = {'text': footer_text}
        embeds.append(embed)

    if dry_run:
        logger.info(f"[DRY-RUN] Would send Discord alert with {len(alerts)} alerts "
                    f"({len(embeds)} embed(s)):")
        for a in alerts:
            logger.info(f"  {a['type']}: {a['ticker']} @ ${a['price']:.2f} (level=${a['level']:.2f})")
        return True

    if not DISCORD_WEBHOOK_URL:
        logger.warning("DISCORD_WEBHOOK_URL_LIVE20 not set, skipping notification")
        return True  # not a failure — webhook simply not configured

    # Discord allows up to 10 embeds per message — send in batches of 10
    MAX_EMBEDS = 10
    success = True
    for i in range(0, len(embeds), MAX_EMBEDS):
        payload = {'embeds': embeds[i:i + MAX_EMBEDS]}
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            if resp.status_code == 204:
                logger.info(f"Discord batch {i // MAX_EMBEDS + 1} sent OK")
            else:
                logger.error(f"Discord returned {resp.status_code}: {resp.text[:200]}")
                success = False
        except Exception as e:
            logger.error(f"Discord send failed: {e}")
            success = False

    if success:
        logger.info(f"Discord alert sent ({len(alerts)} alerts)")
    return success


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

    # Start with CSV prices, fill gaps from IB/yfinance
    prices = {}
    csv_price_count = 0
    for rec in records:
        if rec['current_price'] is not None:
            prices[rec['ticker']] = rec['current_price']
            csv_price_count += 1

    logger.info(f"Using {csv_price_count}/{len(records)} prices from CSV")

    # Fetch missing prices from IB/yfinance
    missing_tickers = [r['ticker'] for r in records if r['ticker'] not in prices]
    vol_ratios = {}
    source = 'CSV'
    if missing_tickers:
        logger.info(f"Fetching {len(missing_tickers)} missing prices from market data...")
        live_prices, vol_ratios, live_source = await fetch_current_prices(ib, missing_tickers)
        prices.update(live_prices)
        if csv_price_count > 0:
            source = f"CSV ({csv_price_count}) + {live_source}" if live_prices else 'CSV'
        else:
            source = live_source
    else:
        # All prices from CSV — fetch vol_ratios separately
        all_tickers = [r['ticker'] for r in records]
        if ib is not None:
            _, vol_ratios = await fetch_prices_ib(ib, all_tickers)
            if vol_ratios:
                logger.info(f"  IB vol ratios for {len(vol_ratios)}/{len(all_tickers)} tickers")
        else:
            # IB not connected — fall back to yfinance 1-min bar-relative ratio (delayed ~15m)
            vol_ratios = fetch_vol_ratios_yfinance(all_tickers)
    logger.info(f"Got prices for {len(prices)}/{len(records)} tickers [{source}]")

    state = load_state(date_str)
    alerts = check_forecasts(records, prices, vol_ratios, state)

    # Print status table
    print(f"\n  {'Ticker':<8} {'Price':>10} {'S/R Level':>10} {'Bull':>5} {'Vol':>6} {'Status':<15}")
    print(f"  {'─'*8} {'─'*10} {'─'*10} {'─'*5} {'─'*6} {'─'*15}")
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
        vr = vol_ratios.get(ticker)
        vol_str = f"{vr:.1f}x" if vr is not None else '—'
        print(f"  {ticker:<8} {price_str:>10} {sr_str:>10} {bull:>5} {vol_str:>6} {status:<15}")

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

def _prev_trading_day(from_date=None) -> str:
    """Return the most recent trading day (Mon-Fri) before or on from_date as YYYY-MM-DD."""
    from datetime import date, timedelta
    d = from_date or date.today()
    # Step back until we land on a weekday
    d -= timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d.strftime('%Y-%m-%d')


def run_backtest(csv_path: Path, date_str: str, dry_run: bool = False) -> int:
    """
    Replay previous trading day bar-by-bar using yfinance 1-min data.
    Checks every bar for S/R breaks, bounces, and direction changes.
    Sends Discord notifications with volume annotation.
    Returns total number of alerts triggered.
    """
    import yfinance as yf
    from datetime import date

    logger.info(f"{'='*60}")
    logger.info(f"BACKTEST — {date_str} — {csv_path.name}")
    logger.info(f"{'='*60}")

    records = load_forecast(csv_path)
    if not records:
        logger.warning("No records found in CSV")
        return 0
    logger.info(f"Loaded {len(records)} tickers from forecast")

    tickers = [r['ticker'] for r in records]
    yf_tickers = [_YF_SYMBOL_MAP.get(t, t) for t in tickers]
    yf_to_orig = {_YF_SYMBOL_MAP.get(t, t): t for t in tickers}

    # Download 5 days of 1-min bars (needed to have enough history for vol ratio)
    logger.info(f"Downloading 1-min bars for {len(tickers)} tickers...")
    try:
        data = yf.download(yf_tickers, period='5d', interval='1m', progress=False,
                           group_by='ticker', threads=True)
    except Exception as e:
        logger.error(f"yfinance download failed: {e}")
        return 0

    # Auto-detect available dates and resolve target_date
    # yfinance index is UTC — convert to ET to find actual trading dates
    target_date = date.fromisoformat(date_str)
    try:
        _probe = data if len(yf_tickers) == 1 else data[yf_tickers[0]]
        _idx_et = _probe.index.tz_convert('America/New_York') if _probe.index.tz else \
                  _probe.index.tz_localize('America/New_York')
        available_dates = sorted(set(_idx_et.date))
    except Exception:
        available_dates = []

    if available_dates and target_date not in available_dates:
        latest = available_dates[-1]
        logger.warning(f"No data for {date_str}. Latest available: {latest}. Using {latest}.")
        target_date = latest
        date_str = str(latest)

    # Build per-ticker DataFrames filtered to the backtest date (index in UTC, dates match ET dates)
    ticker_bars: dict[str, pd.DataFrame] = {}

    for yf_sym in yf_tickers:
        orig = yf_to_orig[yf_sym]
        try:
            df = data if len(yf_tickers) == 1 else data[yf_sym]
            if df.empty:
                continue
            if hasattr(df.columns, 'levels'):
                df = df.copy()
                df.columns = df.columns.get_level_values(-1)
            df.columns = [c.lower() for c in df.columns]
            # Convert index to ET so all date/hour comparisons are consistent
            df.index = df.index.tz_convert('America/New_York') if df.index.tz else \
                       df.index.tz_localize('America/New_York')
            df_day = df[df.index.date == target_date].copy()
            if df_day.empty:
                logger.warning(f"  {orig}: no data for {date_str}")
                continue
            ticker_bars[orig] = df_day
        except Exception as e:
            logger.debug(f"  {orig}: {e}")
            continue

    if not ticker_bars:
        logger.error(f"No bar data found for {date_str}.")
        return 0

    logger.info(f"Got bars for {len(ticker_bars)}/{len(tickers)} tickers on {date_str}")

    # Get all unique bar timestamps (market hours only: 9:30-16:00 ET)
    # Index is already in ET after the conversion above
    all_timestamps = set()
    for df in ticker_bars.values():
        for ts in df.index:
            if (ts.hour > 9 or (ts.hour == 9 and ts.minute >= 30)) and ts.hour < 16:
                all_timestamps.add(ts)

    timestamps = sorted(all_timestamps)
    if not timestamps:
        logger.error(f"No market-hours bars found for {date_str}")
        return 0

    logger.info(f"Replaying {len(timestamps)} bars from "
                f"{timestamps[0].strftime('%H:%M')} to {timestamps[-1].strftime('%H:%M')} ET")

    # Pre-seed state from CSV current_price to avoid false alerts for tickers
    # already past S/R on the CSV date (prevent 42 spurious "already past" breaks)
    state = {}
    for rec in records:
        ticker = rec['ticker']
        if rec['current_price'] is not None and rec['sr_level'] is not None:
            price = rec['current_price']
            sr = rec['sr_level']
            bullish = rec['bullish']
            threshold_up = sr * (1 + BREAK_TOLERANCE_PCT / 100)
            threshold_down = sr * (1 - BREAK_TOLERANCE_PCT / 100)

            # Determine which side of SR the CSV price is on
            # (regardless of forecast direction, to catch surprise breaks already in progress)
            direction = None
            if price >= threshold_up:
                direction = 'UP'
            elif price <= threshold_down:
                direction = 'DOWN'

            if direction:
                state[f"{ticker}_break"] = {
                    'direction': direction,
                    'price': price,
                }
    logger.info(f"Pre-seeded state for {len(state)} tickers from CSV baseline prices")

    all_alerts = []

    for ts in timestamps:
        # Build prices and vol_ratios at this exact bar
        prices = {}
        vol_ratios = {}
        for ticker, df in ticker_bars.items():
            # Index is already in ET — filter up to and including current bar
            bars_so_far = df[df.index <= ts]
            if bars_so_far.empty:
                continue
            close_col = [c for c in bars_so_far.columns if 'close' in c]
            vol_col   = [c for c in bars_so_far.columns if 'volume' in c]
            if close_col:
                prices[ticker] = float(bars_so_far[close_col[0]].iloc[-1])
            if vol_col and len(bars_so_far) >= VOL_ROLLING_BARS:
                vol_series = bars_so_far[vol_col[0]].astype(float)
                rolling_avg = vol_series.rolling(VOL_ROLLING_BARS).mean().iloc[-1]
                latest_vol  = vol_series.iloc[-1]
                if rolling_avg > 0:
                    vol_ratios[ticker] = round(latest_vol / rolling_avg, 2)

        bar_alerts = check_forecasts(records, prices, vol_ratios, state)
        if bar_alerts:
            ts_str = ts.strftime('%H:%M')
            for a in bar_alerts:
                a['bar_time'] = ts_str
            all_alerts.extend(bar_alerts)
            logger.info(f"  [{ts_str}] {len(bar_alerts)} alert(s): "
                        + ", ".join(f"{a['ticker']} {a['type']}" for a in bar_alerts))

    logger.info(f"{'='*60}")
    logger.info(f"Backtest complete — {len(all_alerts)} total alerts on {date_str}")

    if all_alerts:
        # Annotate source label with backtest date for Discord
        source = f"BACKTEST {date_str}"
        send_discord_alert(all_alerts, source=source, dry_run=dry_run)
    else:
        logger.info("No alerts triggered — no notification sent")

    return len(all_alerts)


def main():
    parser = argparse.ArgumentParser(description='Live20 forecast monitor')
    parser.add_argument('--file', help='Path to forecast CSV')
    parser.add_argument('--dry-run', action='store_true', help='No Discord alerts')
    parser.add_argument('--once', action='store_true',
                        help='Run single check then exit (instead of daemon)')
    parser.add_argument('--force', action='store_true',
                        help='Run even outside market hours')
    parser.add_argument('--backtest', metavar='DATE',
                        help='Replay a trading day (YYYY-MM-DD). '
                             'Omit for previous trading day.')
    args = parser.parse_args()

    csv_path = find_csv(args.file)

    if args.backtest is not None:
        # Backtest mode
        date_str = args.backtest if args.backtest else _prev_trading_day()
        run_backtest(csv_path, date_str, dry_run=args.dry_run)
    elif args.once:
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
