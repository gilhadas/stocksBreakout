"""
signal_surge_monitor.py — Real-time surge/drop alert bot

Watches all active signal lists for sudden price moves.

  • 08:00–09:29 ET  Premarket: Alpaca REST polling every 60 s
  • 09:30–16:15 ET  Market hours: Alpaca WebSocket 1-min bars (real-time)
  • Self-terminates at 16:15 ET.

Alert triggers (all configurable at top of file):
  - ±SURGE_FROM_OPEN_PCT % from the session open price
  - ±SURGE_VELOCITY_PCT % over the last VELOCITY_BARS × 1-min bars (velocity)
  - Volume spike: bar volume > VOL_SPIKE_MULT × 20-day avg

Dedup: one alert per (symbol, trigger-type) per session.

Usage:
  python signal_surge_monitor.py           # log only
  python signal_surge_monitor.py --notify  # Telegram + Discord alerts
  python signal_surge_monitor.py --test    # dry-run with fake symbols

Cron: started once at 08:00 ET by cron_agent; self-terminates at 16:15.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
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

# ── Thresholds (tune here) ────────────────────────────────────────────────────
SURGE_FROM_OPEN_PCT = 3.0    # ±% from session open → alert
SURGE_VELOCITY_PCT  = 2.0    # ±% over VELOCITY_BARS bars → alert
VELOCITY_BARS       = 5      # number of 1-min bars for velocity window
VOL_SPIKE_MULT      = 3.0    # bar volume > N × 20-day avg → flag in alert

# ── Session window ────────────────────────────────────────────────────────────
PREMARKET_START = (8, 0)
MARKET_OPEN     = (9, 30)
MARKET_CLOSE    = (16, 15)
POLL_INTERVAL_S = 60

# ── Signal list files to watch ────────────────────────────────────────────────
SIGNAL_FILES = [
    'scanner_output/lists/premium_swing.txt',
    'scanner_output/lists/premium_daytrade.txt',
    'scanner_output/lists/momentum_watch_daytrade.txt',
    'scanner_output/lists/premarket_watch.txt',
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
def past_close()      -> bool: return now_et().replace(
    hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0
) <= now_et()


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
            bars = bars.reset_index()
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
        self.notify   = notify
        self.test     = test
        self._notifier = None

        self.symbols:     set[str]          = set()
        self.avg_vol:     dict[str, float]  = {}
        self.open_prices: dict[str, float]  = {}
        self.bar_history: dict[str, list]   = defaultdict(list)
        self._alerted:    set[tuple]        = set()  # (trigger_key, symbol)

    # ── Notification ─────────────────────────────────────────────────────────

    def _get_notifier(self):
        if self._notifier is None:
            from notifier import Notifier
            self._notifier = Notifier()
        return self._notifier

    def _send(self, symbol: str, price: float, reason: str, ts: str):
        if self.test or not self.notify:
            return
        notifier = self._get_notifier()
        msg = f'⚡ {symbol} ${price:.2f} [{ts} ET]\n{reason}'
        try:
            notifier.send_telegram(msg)
        except Exception as exc:
            logger.warning(f'Telegram failed: {exc}')
        try:
            notifier.send_discord(subject=f'Signal Alert: {symbol}',
                                  message=msg, notification_type='alerts')
        except Exception as exc:
            logger.warning(f'Discord failed: {exc}')

    # ── Per-bar evaluation ────────────────────────────────────────────────────

    def on_bar(self, symbol: str, close: float, volume: float, bar_time: datetime | None):
        if symbol not in self.symbols:
            return

        # Set open price on first bar of the session
        if symbol not in self.open_prices:
            self.open_prices[symbol] = close

        open_px = self.open_prices[symbol]

        # Maintain rolling close history
        hist = self.bar_history[symbol]
        hist.append(close)
        if len(hist) > VELOCITY_BARS + 1:
            hist.pop(0)

        ts   = bar_time.strftime('%H:%M') if bar_time else now_et().strftime('%H:%M')
        triggered: list[tuple[str, str]] = []  # (dedup_key, description)

        # 1. From-open surge / drop
        if open_px > 0:
            pct = (close - open_px) / open_px * 100
            if pct >= SURGE_FROM_OPEN_PCT:
                triggered.append(('UP_OPEN',
                    f'🚀 SURGE +{pct:.1f}% from open  ${open_px:.2f} → ${close:.2f}'))
            elif pct <= -SURGE_FROM_OPEN_PCT:
                triggered.append(('DOWN_OPEN',
                    f'📉 DROP {pct:.1f}% from open  ${open_px:.2f} → ${close:.2f}'))

        # 2. Velocity (last N bars)
        if len(hist) >= VELOCITY_BARS:
            ref = hist[-VELOCITY_BARS]
            vel = (close - ref) / ref * 100 if ref > 0 else 0.0
            if vel >= SURGE_VELOCITY_PCT:
                triggered.append(('UP_VEL',
                    f'⚡ VELOCITY +{vel:.1f}% in {VELOCITY_BARS} min  ${ref:.2f} → ${close:.2f}'))
            elif vel <= -SURGE_VELOCITY_PCT:
                triggered.append(('DOWN_VEL',
                    f'⚡ VELOCITY {vel:.1f}% in {VELOCITY_BARS} min  ${ref:.2f} → ${close:.2f}'))

        # 3. Volume spike (included as annotation in the alert, not standalone)
        vol_note = ''
        avg = self.avg_vol.get(symbol, 0)
        if avg > 0 and volume >= avg * VOL_SPIKE_MULT:
            ratio    = volume / avg
            vol_note = f'\n📊 Vol spike {ratio:.1f}× avg ({volume:,.0f} vs {avg:,.0f} avg)'

        # Fire de-duped alerts
        for key, desc in triggered:
            if (key, symbol) in self._alerted:
                continue
            self._alerted.add((key, symbol))
            full = f'{desc}{vol_note}'
            logger.info(f'ALERT  {symbol} @ ${close:.2f} [{ts}]  {desc}')
            self._send(symbol, close, full, ts)

    # ── Symbol management ─────────────────────────────────────────────────────

    def reload_symbols(self) -> set[str]:
        new = load_symbols()
        added = new - self.symbols
        if added:
            logger.info(f'Symbol list updated: {len(new)} total '
                        f'({len(added)} added: {sorted(added)[:8]}{"…" if len(added)>8 else ""})')
        self.symbols = new
        return new

    def reset_session(self):
        self.open_prices.clear()
        self.bar_history.clear()
        self._alerted.clear()
        logger.info('Session reset — open prices and alert history cleared')


# ── Premarket REST polling ────────────────────────────────────────────────────

async def _premarket_poll(monitor: SurgeMonitor):
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestBarRequest
    except ImportError:
        logger.warning('alpaca-py missing — skipping premarket poll')
        return

    key, secret = os.environ.get('ALPACA_API_KEY', ''), os.environ.get('ALPACA_SECRET_KEY', '')
    client = StockHistoricalDataClient(key, secret)
    logger.info('Premarket REST polling started')

    while is_premarket():
        monitor.reload_symbols()
        symbols = list(monitor.symbols)
        if symbols:
            for i in range(0, len(symbols), 50):
                batch = symbols[i:i + 50]
                try:
                    req    = StockLatestBarRequest(symbol_or_symbols=batch)
                    latest = client.get_stock_latest_bar(req)
                    for sym, bar in latest.items():
                        bar_time = bar.timestamp.astimezone(NY) if bar.timestamp else None
                        monitor.on_bar(sym, float(bar.close), float(bar.volume), bar_time)
                except Exception as exc:
                    logger.debug(f'Poll batch error: {exc}')
        await asyncio.sleep(POLL_INTERVAL_S)

    logger.info('Premarket polling done')


# ── Market-hours WebSocket streaming ─────────────────────────────────────────

def _run_websocket(monitor: SurgeMonitor):
    try:
        from alpaca.data.live import StockDataStream
        from alpaca.data.enums import DataFeed
    except ImportError:
        logger.error('alpaca-py missing — cannot start WebSocket')
        return

    key, secret = os.environ.get('ALPACA_API_KEY', ''), os.environ.get('ALPACA_SECRET_KEY', '')
    symbols = list(monitor.symbols)
    if not symbols:
        logger.warning('No symbols to stream — signal lists are empty')
        return

    # macOS Python.org builds don't use system SSL certs — use certifi bundle.
    import ssl, certifi
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    logger.info(f'WebSocket streaming {len(symbols)} symbols (IEX feed)')
    stream = StockDataStream(key, secret, feed=DataFeed.IEX,
                             websocket_params={'ssl': ssl_ctx})

    async def handle_bar(bar):
        if past_close():
            logger.info('Past 16:15 ET — stopping WebSocket')
            stream.stop()
            return
        bar_time = bar.timestamp.astimezone(NY) if hasattr(bar.timestamp, 'astimezone') else None
        monitor.on_bar(bar.symbol, float(bar.close), float(bar.volume), bar_time)

    stream.subscribe_bars(handle_bar, *symbols)

    try:
        stream.run()
    except ValueError as exc:
        if 'connection limit' in str(exc).lower():
            logger.error('Alpaca WebSocket connection limit reached — is another instance running? '
                         'Run: pkill -f signal_surge_monitor.py  then restart.')
        else:
            logger.error(f'WebSocket error: {exc}')
    except Exception as exc:
        logger.error(f'WebSocket error: {exc}')


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Real-time surge/drop alert bot')
    parser.add_argument('--notify', action='store_true', help='Send Telegram + Discord alerts')
    parser.add_argument('--test',   action='store_true', help='Dry run — log only')
    args = parser.parse_args()

    if past_close():
        logger.info('Already past 16:15 ET — nothing to do')
        sys.exit(0)

    monitor = SurgeMonitor(notify=args.notify, test=args.test)
    monitor.reload_symbols()

    if not monitor.symbols:
        logger.warning('No symbols found in signal lists — exiting')
        sys.exit(0)

    # Pre-fetch 20-day avg volumes (used for spike detection, non-fatal if fails)
    monitor.avg_vol = fetch_20d_avg_volume(list(monitor.symbols))

    loop = asyncio.get_event_loop()

    # Phase 1: premarket REST polling (blocking until 09:30)
    if is_premarket():
        loop.run_until_complete(_premarket_poll(monitor))

    # Phase 2: market-hours WebSocket (blocking until 16:15, self-stops)
    if not past_close():
        monitor.reset_session()
        monitor.reload_symbols()  # refresh list in case premarket added symbols
        _run_websocket(monitor)

    logger.info('signal_surge_monitor finished')


if __name__ == '__main__':
    main()
