"""
signal_surge_monitor.py — Real-time surge/drop alert bot

Data source priority:
  1. IBKR (TWS/IB Gateway) — true real-time tick data, polled every 30 s
  2. Alpaca WebSocket     — ~1 s IEX bars, market hours only
  3. Alpaca REST          — premarket polling every 60 s (always used 08:00-09:30)

Session phases:
  • 08:00–09:29 ET  Premarket: Alpaca REST poll every 60 s
  • 09:30–16:15 ET  Market hours: IBKR tickers (primary) or Alpaca WebSocket (fallback)
  • Self-terminates at 16:15 ET.

Alert triggers (configurable at top of file):
  - ±SURGE_FROM_OPEN_PCT % from the session open price
  - ±SURGE_VELOCITY_PCT % over the last VELOCITY_BARS polls (velocity)
  - Volume pace: today's cumulative volume > VOL_SPIKE_MULT × expected pace (IBKR)
    OR bar volume > VOL_SPIKE_MULT × 20-day avg bar volume (Alpaca)

Dedup: one alert per (symbol, trigger-type) per session.

Usage:
  python signal_surge_monitor.py           # log only
  python signal_surge_monitor.py --notify  # Telegram + Discord alerts
  python signal_surge_monitor.py --test    # dry-run

Cron: started once at 08:00 ET by cron_agent; self-terminates at 16:15.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Python 3.14 event-loop setup (must precede ib_insync-adjacent imports)
asyncio.set_event_loop(asyncio.new_event_loop())

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent))

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
)

NY = ZoneInfo('America/New_York')

# ── Thresholds ────────────────────────────────────────────────────────────────
SURGE_FROM_OPEN_PCT = 3.0    # ±% from session open → alert
SURGE_VELOCITY_PCT  = 2.0    # ±% over VELOCITY_BARS polls → alert
VELOCITY_BARS       = 5      # poll history depth for velocity window
VOL_SPIKE_MULT      = 3.0    # cumulative vol > N × expected pace → flag
IB_POLL_S           = 30     # seconds between IBKR ticker reads
IB_CLIENT_ID        = 50     # separate client ID — avoids conflict with scanner (ID 1)

# ── Session window ────────────────────────────────────────────────────────────
PREMARKET_START = (8, 0)
MARKET_OPEN     = (9, 30)
MARKET_CLOSE    = (16, 15)
ALPACA_POLL_S   = 60         # premarket REST poll interval

TRADING_MINUTES = 390.0      # 09:30-16:00 = 6.5 h = 390 min

# ── Signal list files to watch ────────────────────────────────────────────────
SIGNAL_FILES = [
    'scanner_output/lists/premium_swing.txt',
    'scanner_output/lists/premium_daytrade.txt',
    'scanner_output/lists/momentum_watch_daytrade.txt',
    'scanner_output/lists/premarket_watch.txt',
    'scanner_output/lists/early_premarket_watch.txt',
]

# Files whose symbols are scanner-confirmed breakouts (PREMIUM/GOLD quality).
# Symbols in these files get a [CONFIRMED] tag; all others get [WATCH].
CONFIRMED_FILES = [
    'scanner_output/lists/premium_swing.txt',
    'scanner_output/lists/premium_daytrade.txt',
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_et() -> datetime:
    return datetime.now(NY)


def _in_window(start_hm: tuple[int, int], end_hm: tuple[int, int]) -> bool:
    t = now_et()
    s = t.replace(hour=start_hm[0], minute=start_hm[1], second=0, microsecond=0)
    e = t.replace(hour=end_hm[0],   minute=end_hm[1],   second=0, microsecond=0)
    return s <= t < e


def is_premarket()    -> bool: return _in_window(PREMARKET_START, MARKET_OPEN)
def is_market_hours() -> bool: return _in_window(MARKET_OPEN, MARKET_CLOSE)
def past_close()      -> bool:
    t = now_et()
    return t >= t.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)


def _elapsed_trading_minutes() -> float:
    """Minutes elapsed since 09:30 ET today (clamped 1..390)."""
    t = now_et()
    open_time = t.replace(hour=9, minute=30, second=0, microsecond=0)
    return max(1.0, min(TRADING_MINUTES, (t - open_time).total_seconds() / 60))


def load_confirmed_symbols() -> set[str]:
    """Return symbols present in PREMIUM/GOLD scanner output files."""
    confirmed: set[str] = set()
    for path in CONFIRMED_FILES:
        p = Path(path)
        if p.exists():
            for line in p.read_text().splitlines():
                sym = line.strip().upper()
                if sym and not sym.startswith('#'):
                    confirmed.add(sym)
    return confirmed


def load_held_symbols() -> set[str]:
    """Return symbols currently held in auto_portfolio (open positions)."""
    try:
        import auto_portfolio as ap
        data = ap.load()
        return {p['symbol'].upper() for p in data.get('positions', [])}
    except Exception as exc:
        logger.debug(f'auto_portfolio not loadable: {exc}')
        return set()


def load_symbols() -> set[str]:
    symbols: set[str] = set()
    for path in SIGNAL_FILES:
        p = Path(path)
        if p.exists():
            for line in p.read_text().splitlines():
                sym = line.strip().upper()
                if sym and not sym.startswith('#'):
                    symbols.add(sym)
    return symbols


def fetch_20d_avg_volume(symbols: list[str]) -> dict[str, float]:
    """Batch-fetch 20-day avg daily volume via Alpaca REST (run once at startup)."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError:
        logger.warning('alpaca-py not installed — volume spike detection disabled')
        return {}

    key, secret = os.environ.get('ALPACA_API_KEY', ''), os.environ.get('ALPACA_SECRET_KEY', '')
    if not key:
        return {}

    client = StockHistoricalDataClient(key, secret)
    end    = now_et().replace(hour=0, minute=0, second=0, microsecond=0)
    start  = end - timedelta(days=30)
    avg_vol: dict[str, float] = {}

    for i in range(0, len(symbols), 50):
        batch = symbols[i:i + 50]
        try:
            req  = StockBarsRequest(symbol_or_symbols=batch, start=start, end=end,
                                    timeframe=TimeFrame.Day, limit=20)
            bars = client.get_stock_bars(req).df
            if bars.empty:
                continue
            bars    = bars.reset_index()
            sym_col = 'symbol' if 'symbol' in bars.columns else None
            for sym in batch:
                sub = bars[bars[sym_col] == sym] if sym_col else bars
                if not sub.empty and 'volume' in sub.columns:
                    avg_vol[sym] = float(sub['volume'].mean())
        except Exception as exc:
            logger.debug(f'avg_vol batch failed: {exc}')

    logger.info(f'Avg volumes loaded for {len(avg_vol)}/{len(symbols)} symbols')
    return avg_vol


# ── Core monitor ──────────────────────────────────────────────────────────────

class SurgeMonitor:
    def __init__(self, notify: bool = False, test: bool = False):
        self.notify    = notify
        self.test      = test
        self._notifier = None

        self.symbols:           set[str]         = set()
        self.confirmed_symbols: set[str]         = set()  # PREMIUM/GOLD scanner signals → SURGE tag
        self.held_symbols:      set[str]         = set()  # auto_portfolio open positions → DROP tag
        self.avg_vol:           dict[str, float] = {}
        self.open_prices:       dict[str, float] = {}
        self.bar_history:       dict[str, list]  = defaultdict(list)
        self._alerted:          set[tuple]       = set()  # (trigger_key, symbol)

    # ── Notification ─────────────────────────────────────────────────────────

    def _get_notifier(self):
        if self._notifier is None:
            from notifier import Notifier
            self._notifier = Notifier()
        return self._notifier

    def _send(self, symbol: str, price: float, reason: str, ts: str, tag: str):
        if self.test or not self.notify:
            return
        notifier = self._get_notifier()
        msg = f'⚡ {symbol} ${price:.2f} [{ts} ET]  {tag}\n{reason}'
        try:
            notifier.send_telegram(msg)
        except Exception as exc:
            logger.warning(f'Telegram failed: {exc}')
        try:
            notifier.send_discord(subject=f'Signal Alert: {symbol}',
                                  message=msg, notification_type='alerts')
        except Exception as exc:
            logger.warning(f'Discord failed: {exc}')

    # ── Price alert logic (shared by all sources) ────────────────────────────

    def on_bar(self, symbol: str, close: float, volume: float, bar_time: datetime | None):
        """
        Evaluate one price sample. volume = bar/period volume for Alpaca;
        pass 0 and call on_ib_cumvol() separately when using IBKR.
        """
        if symbol not in self.symbols:
            return

        if symbol not in self.open_prices:
            self.open_prices[symbol] = close

        open_px = self.open_prices[symbol]
        hist    = self.bar_history[symbol]
        hist.append(close)
        if len(hist) > VELOCITY_BARS + 1:
            hist.pop(0)

        ts = bar_time.strftime('%H:%M') if bar_time else now_et().strftime('%H:%M')
        triggered: list[tuple[str, str]] = []

        # 1. From-open surge / drop
        if open_px > 0:
            pct = (close - open_px) / open_px * 100
            if pct >= SURGE_FROM_OPEN_PCT:
                triggered.append(('UP_OPEN',
                    f'🚀 SURGE +{pct:.1f}% from open  ${open_px:.2f} → ${close:.2f}'))
            elif pct <= -SURGE_FROM_OPEN_PCT:
                triggered.append(('DOWN_OPEN',
                    f'📉 DROP {pct:.1f}% from open  ${open_px:.2f} → ${close:.2f}'))

        # 2. Velocity (last N polls)
        if len(hist) >= VELOCITY_BARS:
            ref = hist[-VELOCITY_BARS]
            vel = (close - ref) / ref * 100 if ref > 0 else 0.0
            if vel >= SURGE_VELOCITY_PCT:
                triggered.append(('UP_VEL',
                    f'⚡ VELOCITY +{vel:.1f}% in {VELOCITY_BARS} polls  ${ref:.2f} → ${close:.2f}'))
            elif vel <= -SURGE_VELOCITY_PCT:
                triggered.append(('DOWN_VEL',
                    f'⚡ VELOCITY {vel:.1f}% in {VELOCITY_BARS} polls  ${ref:.2f} → ${close:.2f}'))

        # 3. Alpaca bar-level volume spike annotation
        vol_note = ''
        avg = self.avg_vol.get(symbol, 0)
        if volume > 0 and avg > 0:
            # avg is daily; Alpaca passes 1-min bar volume — compare at per-minute scale
            avg_per_min = avg / TRADING_MINUTES
            if volume >= avg_per_min * VOL_SPIKE_MULT:
                ratio    = volume / avg_per_min
                vol_note = f'\n📊 Vol {ratio:.1f}× avg/min ({volume:,.0f} vs {avg_per_min:,.0f})'

        # Dedup: one alert per direction per session.
        # Once any UP alert fires, suppress all further UP alerts (OPEN+VEL = same move).
        # Once any DOWN alert fires, suppress all further DOWN alerts.
        # Direction reversals (UP after DOWN or vice versa) are still allowed.
        # Re-check after each fire so same-cycle triggers also dedup correctly.
        is_confirmed = symbol in self.confirmed_symbols
        is_held      = symbol in self.held_symbols
        for key, desc in triggered:
            if (key, symbol) in self._alerted:
                continue
            up_fired   = ('UP_OPEN',   symbol) in self._alerted or ('UP_VEL',   symbol) in self._alerted
            down_fired = ('DOWN_OPEN', symbol) in self._alerted or ('DOWN_VEL', symbol) in self._alerted
            if key in ('UP_OPEN',   'UP_VEL')   and up_fired:
                continue
            if key in ('DOWN_OPEN', 'DOWN_VEL') and down_fired:
                continue
            self._alerted.add((key, symbol))
            is_down = key in ('DOWN_OPEN', 'DOWN_VEL')
            if is_down:
                tag = '⚠️ HELD position' if is_held else '👁 WATCH'
            else:
                tag = '✅ CONFIRMED breakout' if is_confirmed else '👁 WATCH'
            log_icon = '⚠️' if (is_down and is_held) else ('✅' if (not is_down and is_confirmed) else '👁')
            logger.info(f'ALERT {log_icon} {symbol} @ ${close:.2f} [{ts}]  {desc}')
            self._send(symbol, close, f'{desc}{vol_note}', ts, tag)

    def on_ib_cumvol(self, symbol: str, cum_volume: float, bar_time: datetime | None):
        """
        IBKR-specific volume pace check using cumulative day volume.
        Fires a standalone VOL_PACE alert if today's volume is running at
        VOL_SPIKE_MULT × the expected pace for the current time of day.
        """
        if ('VOL_PACE', symbol) in self._alerted:
            return
        avg = self.avg_vol.get(symbol, 0)
        if avg <= 0 or cum_volume <= 0:
            return

        elapsed   = _elapsed_trading_minutes()
        expected  = avg * (elapsed / TRADING_MINUTES)  # how much volume is normal by now
        if expected <= 0:
            return

        pace_mult = cum_volume / expected
        if pace_mult >= VOL_SPIKE_MULT:
            self._alerted.add(('VOL_PACE', symbol))
            ts        = bar_time.strftime('%H:%M') if bar_time else now_et().strftime('%H:%M')
            price     = self.open_prices.get(symbol, 0)
            confirmed = symbol in self.confirmed_symbols
            msg   = (f'📊 VOL PACE {pace_mult:.1f}× expected  '
                     f'({cum_volume:,.0f} today vs {expected:,.0f} expected at {ts})')
            log_icon = '✅' if confirmed else '👁'
            tag = '✅ CONFIRMED breakout' if confirmed else '👁 WATCH'
            logger.info(f'ALERT {log_icon} {symbol}  {msg}')
            self._send(symbol, price, msg, ts, tag)

    # ── Symbol management ─────────────────────────────────────────────────────

    def reload_symbols(self) -> set[str]:
        new       = load_symbols()
        confirmed = load_confirmed_symbols()
        held      = load_held_symbols()
        added     = new - self.symbols
        if added:
            logger.info(f'Symbol list updated: {len(new)} total '
                        f'({len(added)} added: {sorted(added)[:8]}{"…" if len(added) > 8 else ""})')
        self.symbols           = new
        self.confirmed_symbols = confirmed
        self.held_symbols      = held
        return new

    def reset_session(self):
        self.open_prices.clear()
        self.bar_history.clear()
        self._alerted.clear()
        logger.info('Session reset — open prices and alert history cleared')


# ── IBKR source (primary, market hours) ──────────────────────────────────────

async def _try_connect_ib():
    """Try paper then live port. Returns connected IB instance or None."""
    try:
        from ib_insync import IB
        from config import IB_HOST, IB_PAPER_PORT, IB_LIVE_PORT
    except ImportError:
        return None

    for port, label in [(IB_PAPER_PORT, 'PAPER'), (IB_LIVE_PORT, 'LIVE')]:
        ib = IB()
        try:
            await asyncio.wait_for(
                ib.connectAsync(IB_HOST, port, clientId=IB_CLIENT_ID), timeout=5
            )
            ib.reqMarketDataType(3)  # delayed OK — we just need prices, not strict real-time
            logger.info(f'IBKR connected ({label} port {port})')
            return ib
        except Exception as exc:
            logger.debug(f'IBKR {label} port {port} unavailable: {exc}')
    return None


async def _run_ib_stream(monitor: SurgeMonitor, ib):
    """
    Subscribe to reqMktData for all symbols; poll every IB_POLL_S seconds.
    IB tickers give true real-time last price + cumulative day volume.
    Reload symbol list every 10 minutes to pick up new signals.
    """
    from ib_insync import Stock

    tickers: dict[str, object] = {}

    async def _subscribe(symbols: set[str]):
        for sym in symbols - set(tickers):
            try:
                contract = Stock(sym, 'SMART', 'USD')
                await ib.qualifyContractsAsync(contract)
                tickers[sym] = ib.reqMktData(contract, '', False, False)
            except Exception as exc:
                logger.debug(f'IB subscribe failed {sym}: {exc}')

    await _subscribe(monitor.symbols)
    logger.info(f'IBKR: subscribed to {len(tickers)} tickers (polling every {IB_POLL_S}s)')

    reload_counter = 0
    while not past_close():
        await asyncio.sleep(IB_POLL_S)
        reload_counter += 1

        # Reload symbol list every 10 min and subscribe to new symbols
        if reload_counter % (600 // IB_POLL_S) == 0:
            monitor.reload_symbols()
            await _subscribe(monitor.symbols)

        now = now_et()
        for sym, ticker in list(tickers.items()):
            price = float(getattr(ticker, 'last',  None) or
                          getattr(ticker, 'close', None) or 0)
            if price <= 0:
                continue

            cum_vol = float(getattr(ticker, 'volume', 0) or 0)
            monitor.on_bar(sym, price, 0, now)          # price checks (no bar vol)
            monitor.on_ib_cumvol(sym, cum_vol, now)     # volume pace check

    # Clean up subscriptions
    for ticker in tickers.values():
        try:
            ib.cancelMktData(ticker.contract)
        except Exception:
            pass
    ib.disconnect()
    logger.info('IBKR stream ended — disconnected')


# ── Alpaca premarket REST polling ─────────────────────────────────────────────

async def _premarket_poll(monitor: SurgeMonitor):
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestBarRequest
    except ImportError:
        logger.warning('alpaca-py missing — skipping premarket poll')
        return

    key, secret = os.environ.get('ALPACA_API_KEY', ''), os.environ.get('ALPACA_SECRET_KEY', '')
    client = StockHistoricalDataClient(key, secret)
    logger.info('Premarket REST polling started (Alpaca)')

    while is_premarket():
        monitor.reload_symbols()
        for i in range(0, len(monitor.symbols), 50):
            batch = list(monitor.symbols)[i:i + 50]
            try:
                req    = StockLatestBarRequest(symbol_or_symbols=batch)
                latest = client.get_stock_latest_bar(req)
                for sym, bar in latest.items():
                    bar_time = bar.timestamp.astimezone(NY) if bar.timestamp else None
                    monitor.on_bar(sym, float(bar.close), float(bar.volume), bar_time)
            except Exception as exc:
                logger.debug(f'Premarket poll error: {exc}')
        await asyncio.sleep(ALPACA_POLL_S)

    logger.info('Premarket polling done')


# ── Alpaca WebSocket fallback (market hours) ──────────────────────────────────

def _run_alpaca_websocket(monitor: SurgeMonitor):
    try:
        from alpaca.data.live import StockDataStream
        from alpaca.data.enums import DataFeed
    except ImportError:
        logger.error('alpaca-py missing — cannot start WebSocket fallback')
        return

    key, secret = os.environ.get('ALPACA_API_KEY', ''), os.environ.get('ALPACA_SECRET_KEY', '')
    symbols     = list(monitor.symbols)
    if not symbols:
        logger.warning('No symbols to stream')
        return

    import ssl, certifi
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    logger.info(f'Alpaca WebSocket fallback: streaming {len(symbols)} symbols (IEX)')
    stream = StockDataStream(key, secret, feed=DataFeed.IEX,
                             websocket_params={'ssl': ssl_ctx})

    async def handle_bar(bar):
        if past_close():
            stream.stop()
            return
        bar_time = bar.timestamp.astimezone(NY) if hasattr(bar.timestamp, 'astimezone') else None
        monitor.on_bar(bar.symbol, float(bar.close), float(bar.volume), bar_time)

    stream.subscribe_bars(handle_bar, *symbols)

    try:
        stream.run()
    except ValueError as exc:
        if 'connection limit' in str(exc).lower():
            logger.error('Alpaca connection limit — kill other instances: '
                         'pkill -f signal_surge_monitor.py')
        else:
            logger.error(f'Alpaca WebSocket error: {exc}')
    except Exception as exc:
        logger.error(f'Alpaca WebSocket error: {exc}')


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Real-time surge/drop alert bot')
    parser.add_argument('--notify',   action='store_true', help='Send Telegram + Discord alerts')
    parser.add_argument('--test',     action='store_true', help='Dry run — log only')
    parser.add_argument('--no-ibkr',  action='store_true', help='Skip IBKR, use Alpaca only')
    args = parser.parse_args()

    if past_close():
        logger.info('Already past 16:15 ET — nothing to do')
        sys.exit(0)

    monitor = SurgeMonitor(notify=args.notify, test=args.test)
    monitor.reload_symbols()

    if not monitor.symbols:
        logger.warning('No symbols found in signal lists — exiting')
        sys.exit(0)

    monitor.avg_vol = fetch_20d_avg_volume(list(monitor.symbols))

    loop = asyncio.get_event_loop()

    # Phase 1: premarket REST polling (Alpaca — always)
    if is_premarket():
        loop.run_until_complete(_premarket_poll(monitor))

    if past_close():
        logger.info('signal_surge_monitor finished')
        return

    monitor.reset_session()
    monitor.reload_symbols()

    # Phase 2: market hours — try IBKR first, fall back to Alpaca WebSocket
    ib = None
    if not args.no_ibkr:
        ib = loop.run_until_complete(_try_connect_ib())

    if ib is not None:
        logger.info('Data source: IBKR (real-time tickers, 30s poll)')
        loop.run_until_complete(_run_ib_stream(monitor, ib))
    else:
        logger.info('Data source: Alpaca WebSocket (IEX ~1s bars) — IBKR unavailable')
        _run_alpaca_websocket(monitor)

    logger.info('signal_surge_monitor finished')


if __name__ == '__main__':
    main()
