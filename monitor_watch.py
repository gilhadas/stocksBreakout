#!/usr/bin/env python3
"""
monitor_watch.py
================
Momentum-watch signal validator — runs every 15 minutes during trading hours.

Reads scanner_output/lists/momentum_watch_daytrade.txt (or --file / --mode).
For each symbol, fetches the latest 15-min bars from yfinance and classifies:

  HOLDING    — price still above today's open, momentum intact
  RECOVERING — slightly below open but not a failed move
  FADING     — retreated >3% from intraday high (momentum dying)
  FAILED     — gave back >2% from today's open (move is dead)

Sends Discord alert on first status change to FADING or FAILED.
Alert state stored in scanner_output/.watch_monitor_YYYYMMDD.json
(one per day, reset at midnight ET).

Usage:
  python monitor_watch.py                          # live daytrade watch list
  python monitor_watch.py --mode swing             # swing watch list
  python monitor_watch.py --file scanner_output/lists/my.txt  # custom file
  python monitor_watch.py --dry-run                # print only, no Discord
  python monitor_watch.py --notify                 # force enable Discord
  python monitor_watch.py --held-only              # only monitor open positions
"""

import argparse
import json
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pandas as pd
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)

sys.path.insert(0, str(Path(__file__).parent))
from yfinance_adapter import YFinanceAdapter
from notifier import Notifier

NY_TZ   = ZoneInfo('America/New_York')
OUT_DIR = Path('scanner_output') / 'state'

# ── Default watch-list paths ──────────────────────────────────────────────────
WATCH_FILES = {
    'daytrade': 'scanner_output/lists/momentum_watch_daytrade.txt',
    'swing':    'input/momentum_watch_swing.txt',
}

# ── Thresholds ────────────────────────────────────────────────────────────────
FAIL_THRESHOLD = -2.0   # % below today's open  → FAILED
FADE_THRESHOLD = -3.0   # % below intraday high → FADING
VOL_DRY_RATIO  =  0.5   # vol < 50% of day avg on latest bar → confirms fade


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Momentum-watch signal monitor')
    p.add_argument('--file', help='Watch-list file (overrides --mode)')
    p.add_argument('--mode', default='daytrade', choices=['daytrade', 'swing'],
                   help='Trading mode (chooses default watch-list file)')
    p.add_argument('--dry-run', action='store_true',
                   help='Print results only — no Discord notifications sent')
    p.add_argument('--notify', action='store_true',
                   help='Send Discord notifications (default: only on cron)')
    p.add_argument('--held-only', action='store_true',
                   help='Only monitor symbols with open positions (ignore scanner watch list)')
    return p.parse_args()


def load_watch_list(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    return [
        line.strip().upper()
        for line in p.read_text().splitlines()
        if line.strip() and not line.strip().startswith('#')
    ]


def load_state(date_str: str) -> dict:
    path = OUT_DIR / f'.watch_monitor_{date_str}.json'
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict, date_str: str) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f'.watch_monitor_{date_str}.json'
    path.write_text(json.dumps(state, indent=2))


FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_LABELS = ['0%', '23.6%', '38.2%', '50%', '61.8%', '78.6%', '100%']


def nearest_fib(current: float, day_low: float, day_high: float) -> tuple[str, float]:
    """
    Return (label, price) of the Fibonacci retracement level closest to current price.
    Levels are measured downward from high: fib_price = high - ratio*(high-low).
    """
    swing = day_high - day_low
    if swing <= 0:
        return ('n/a', current)
    fib_prices = [day_high - r * swing for r in FIB_LEVELS]
    closest_i = min(range(len(fib_prices)), key=lambda i: abs(fib_prices[i] - current))
    return FIB_LABELS[closest_i], round(fib_prices[closest_i], 2)


def classify_status(today_bars: pd.DataFrame) -> dict:
    """
    Given today's 15-min bars (already filtered to target date), compute:
      - status: HOLDING / RECOVERING / FADING / FAILED
      - Fibonacci retracement level closest to current price
      - supporting metrics for display / notification
    """
    if today_bars.empty:
        return {'status': 'NO_DATA'}

    first  = today_bars.iloc[0]
    latest = today_bars.iloc[-1]

    day_open  = float(first['open'])
    day_high  = float(today_bars['high'].max())
    day_low   = float(today_bars['low'].min())
    current   = float(latest['close'])

    # Volume: latest bar vs day average
    day_avg_vol  = float(today_bars['volume'].mean()) if 'volume' in today_bars.columns else 0
    latest_vol   = float(latest.get('volume', 0))
    vol_ratio    = (latest_vol / day_avg_vol) if day_avg_vol > 0 else 1.0

    # 3-bar price trend (last 3 closes)
    if len(today_bars) >= 3:
        bars3_trend = (today_bars['close'].iloc[-1] - today_bars['close'].iloc[-3]) \
                      / today_bars['close'].iloc[-3] * 100
    else:
        bars3_trend = 0.0

    from_open_pct = (current - day_open) / day_open * 100
    from_high_pct = (current - day_high) / day_high * 100

    # Fibonacci retracement (from today's swing low → high)
    fib_label, fib_price = nearest_fib(current, day_low, day_high)

    # Volume dry-up reinforces FADING signal
    vol_dry = vol_ratio < VOL_DRY_RATIO

    if from_open_pct <= FAIL_THRESHOLD:
        status = 'FAILED'
    elif from_high_pct <= FADE_THRESHOLD and (bars3_trend < 0 or vol_dry):
        status = 'FADING'
    elif from_open_pct >= 0:
        status = 'HOLDING'
    else:
        status = 'RECOVERING'

    return {
        'status':        status,
        'current':       round(current,       2),
        'day_open':      round(day_open,      2),
        'day_high':      round(day_high,      2),
        'day_low':       round(day_low,       2),
        'from_open_pct': round(from_open_pct, 2),
        'from_high_pct': round(from_high_pct, 2),
        'vol_ratio':     round(vol_ratio,     2),
        'bars3_trend':   round(bars3_trend,   2),
        'n_bars':        len(today_bars),
        'fib_label':     fib_label,
        'fib_price':     fib_price,
    }


STATUS_ICON = {
    'HOLDING':    '✅',
    'RECOVERING': '🟡',
    'FADING':     '⚠️ ',
    'FAILED':     '🔴',
    'NO_DATA':    '❓',
}

NOTIFY_ON_TRANSITION = {
    # (prev_status, new_status): alert level
    # Only notify on DOWNWARD transitions (actionable: consider closing)
    ('HOLDING',    'FADING'):    'warn',
    ('HOLDING',    'FAILED'):    'crit',
    ('RECOVERING', 'FAILED'):    'crit',
    ('FADING',     'FAILED'):    'crit',
    # Recovery transitions removed — not actionable, caused 30%+ of noise
    # ('RECOVERING', 'FADING'):  'warn',   # minor oscillation
    # ('FADING',     'HOLDING'): 'info',   # bounce-back noise
    # ('FAILED',     'HOLDING'): 'info',
    # ('FAILED',  'RECOVERING'): 'info',
}

# Cooldown: suppress re-alerting the same symbol within N minutes
ALERT_COOLDOWN_MINUTES = 60


def should_notify(prev_status: str, new_status: str,
                  sym_state: dict | None = None,
                  now_et: datetime | None = None) -> tuple[bool, str]:
    """Returns (should_notify, alert_level). Applies cooldown check."""
    level = NOTIFY_ON_TRANSITION.get((prev_status, new_status))
    if level is None:
        return (False, '')

    # Cooldown: skip if this symbol was alerted recently
    if sym_state and now_et:
        last_alert = sym_state.get('last_alerted_at')
        if last_alert:
            try:
                last_dt = datetime.strptime(
                    f"{now_et.strftime('%Y-%m-%d')} {last_alert}",
                    '%Y-%m-%d %H:%M'
                ).replace(tzinfo=NY_TZ)
                elapsed = (now_et - last_dt).total_seconds() / 60
                if elapsed < ALERT_COOLDOWN_MINUTES:
                    return (False, '')
            except (ValueError, TypeError):
                pass

    return (True, level)


def build_discord_message(sym: str, info: dict, prev_status: str,
                          now_et: datetime) -> tuple[str, str, int]:
    """Return (subject, body, discord_color)."""
    status = info['status']
    icon   = STATUS_ICON.get(status, '?')

    colors = {'crit': 0xFF0000, 'warn': 0xFF8800, 'info': 0x00CC00}
    _, level = should_notify(prev_status, status)
    color = colors.get(level, 0xAAAAAA)

    fib_label = info.get('fib_label', 'n/a')
    fib_price = info.get('fib_price', 0)
    subject = f"{icon} Watch: {sym} → {status}  [{now_et.strftime('%H:%M ET')}]"
    body = (
        f"**{sym}**  {prev_status} → **{status}**\n"
        f"Price: **${info['current']:.2f}**  |  "
        f"Open: ${info['day_open']:.2f}  |  High: ${info['day_high']:.2f}  |  Low: ${info['day_low']:.2f}\n"
        f"vs Open: **{info['from_open_pct']:+.1f}%**  |  "
        f"vs High: {info['from_high_pct']:+.1f}%\n"
        f"Fib: **{fib_label}** (${fib_price:.2f})  |  "
        f"Vol: {info['vol_ratio']:.1f}x  |  "
        f"3-bar: {info['bars3_trend']:+.1f}%"
    )
    return subject, body, color


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args    = parse_args()
    now_et  = datetime.now(NY_TZ)
    today   = now_et.strftime('%Y-%m-%d')

    # Market-hours guard: skip silently outside 9:30–16:15 ET.
    # Pre-market runs fetch 400+ symbols from yfinance and return "NO TODAY DATA" for all —
    # pure wasted compute. Analysis: 48 pre-market runs, 0 status changes detected.
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=15, second=0, microsecond=0)
    if not (market_open <= now_et <= market_close):
        print(f'\n Monitor skipped — outside market hours ({now_et.strftime("%H:%M ET")})')
        return

    watch_file = args.file or WATCH_FILES.get(args.mode, WATCH_FILES['daytrade'])
    symbols    = load_watch_list(watch_file)

    # Filter to held positions only if requested
    if args.held_only:
        held_syms: set[str] = set()
        # scalp_portfolio
        try:
            import scalp_portfolio as sp
            held_syms |= sp.open_symbols(sp.load())
        except Exception:
            pass
        # auto_portfolio
        try:
            import auto_portfolio as ap
            held_syms |= ap.open_symbols(ap.load())
        except Exception:
            pass
        # manual portfolio.json
        try:
            from portfolio import Portfolio
            _p = Portfolio()
            for pos in _p.get_positions_as_exit_format():
                held_syms.add(pos.get('symbol', '').upper())
        except Exception:
            pass
        symbols = [s for s in symbols if s in held_syms]
        print(f'\n Momentum-Watch Monitor — {now_et.strftime("%Y-%m-%d %H:%M %Z")} [HELD ONLY]')
        print(f' Portfolios: {len(held_syms)} open position(s)  |  {len(symbols)} on watch list')
    else:
        print(f'\n Momentum-Watch Monitor — {now_et.strftime("%Y-%m-%d %H:%M %Z")}')
        print(f' File: {watch_file}  |  {len(symbols)} symbols')

    if not symbols:
        print(' No symbols to monitor. Run Phase 1 scan first to populate the watch list.')
        return

    state   = load_state(today)
    adapter = YFinanceAdapter(use_disk_cache=False)   # always fresh for live monitor

    # Fetch range: yesterday → tomorrow (covers pre-market + today's bars)
    start = (now_et - timedelta(days=2)).strftime('%Y-%m-%d')
    end   = (now_et + timedelta(days=1)).strftime('%Y-%m-%d')

    print(f'\n {"Symbol":8} {"Status":12} {"Price":>8} {"vsOpen":>7} {"vsHigh":>7} {"Vol":>5}  {"3bar":>6}  {"Fib":>6}  {"Bars":>4}')
    print(f' {"-"*80}')

    alerts: list[dict] = []

    for sym in symbols:
        df = adapter.get_historical_data(sym, '15 mins', start_date=start, end_date=end)
        if df is None or df.empty:
            print(f' {sym:8} {"NO DATA":12}')
            continue

        # Filter to today's bars (adapter returns naive timestamps)
        date_mask  = df.index.normalize() == pd.Timestamp(today)
        today_bars = df[date_mask]

        if today_bars.empty:
            print(f' {sym:8} {"NO TODAY DATA":15}')
            continue

        info       = classify_status(today_bars)
        status     = info['status']
        prev_state = state.get(sym, {})
        prev_status = prev_state.get('status', 'HOLDING')

        icon = STATUS_ICON.get(status, '?')
        fib_tag = info.get('fib_label', 'n/a')
        print(
            f' {sym:8} {icon}{status:11} '
            f'{info["current"]:>8.2f} '
            f'{info["from_open_pct"]:>+7.1f}% '
            f'{info["from_high_pct"]:>+7.1f}% '
            f'{info["vol_ratio"]:>5.1f}x '
            f'{info["bars3_trend"]:>+6.1f}% '
            f'{fib_tag:>6}  '
            f'{info["n_bars"]:>4}'
        )

        # Detect transitions that warrant alerts (with cooldown)
        do_alert, level = should_notify(prev_status, status, prev_state, now_et)
        if do_alert:
            alerts.append({
                'symbol':      sym,
                'prev_status': prev_status,
                'level':       level,
                **info,
            })

        # Update state — track consecutive FAILED count for auto-prune
        consecutive_failed = prev_state.get('consecutive_failed', 0)
        if status == 'FAILED':
            consecutive_failed += 1
        else:
            consecutive_failed = 0

        state[sym] = {
            'status':             status,
            'checked_at':         now_et.strftime('%H:%M'),
            'consecutive_failed': consecutive_failed,
            'last_alerted_at': (now_et.strftime('%H:%M')
                                if do_alert
                                else prev_state.get('last_alerted_at')),
            **info,
        }

    # Auto-prune: remove symbols that have been FAILED for 3+ consecutive checks.
    # The watch list grows to 400-470 symbols without pruning, slowing every run.
    # FAILED = stop was hit; no reason to keep monitoring.
    prune_threshold = 3
    to_prune = {sym for sym, s in state.items()
                if s.get('consecutive_failed', 0) >= prune_threshold}
    if to_prune:
        remaining = [s for s in symbols if s not in to_prune]
        if len(remaining) < len(symbols):
            watch_path = Path(watch_file)
            watch_path.write_text('\n'.join(remaining))
            print(f' Auto-pruned {len(to_prune)} persistently FAILED symbols from watch list '
                  f'({len(symbols)} → {len(remaining)}): {", ".join(sorted(to_prune))}')

    save_state(state, today)
    print()

    # ── Status change summary ────────────────────────────────────────────────
    if alerts:
        print(f' ── Status changes ({len(alerts)}) ──────────────────────────────────────')
        for a in alerts:
            icon = STATUS_ICON.get(a['status'], '?')
            print(
                f'  {icon} {a["symbol"]:6} {a["prev_status"]:12} → {a["status"]:12} '
                f'${a["current"]:.2f}  vsOpen {a["from_open_pct"]:+.1f}%'
            )
        print()

    # ── Send Discord notifications (batched into ONE message) ────────────────
    send_notifications = args.notify and not args.dry_run
    if alerts and send_notifications:
        try:
            notifier = Notifier()
            # Group alerts by level for summary
            crit_alerts = [a for a in alerts if a['level'] == 'crit']
            warn_alerts = [a for a in alerts if a['level'] == 'warn']

            time_str = now_et.strftime('%H:%M ET')
            subject = f"📊 Watch Monitor: {len(alerts)} status change(s)  [{time_str}]"
            if crit_alerts:
                subject = f"🔴 Watch Monitor: {len(crit_alerts)} FAILED + {len(warn_alerts)} FADING  [{time_str}]"
            elif warn_alerts:
                subject = f"⚠️  Watch Monitor: {len(warn_alerts)} FADING  [{time_str}]"

            # Build batched body: one line per symbol
            lines = []
            for a in sorted(alerts, key=lambda x: x['level'] == 'warn'):
                icon = STATUS_ICON.get(a['status'], '?')
                lines.append(
                    f"{icon} **{a['symbol']}** {a['prev_status']}→{a['status']}  "
                    f"${a['current']:.2f}  vsOpen {a['from_open_pct']:+.1f}%  "
                    f"Fib {a.get('fib_label', 'n/a')}"
                )
            body = '\n'.join(lines)

            notifier.send_discord(subject, body)
            print(f'  📢 Sent 1 batched notification ({len(alerts)} alerts)')
        except Exception as e:
            print(f'  ⚠️  Notification error: {e}')
    elif alerts and args.dry_run:
        print(' [dry-run] Notifications suppressed.')
    elif alerts:
        print(' (Pass --notify to send Discord alerts)')

    # Always check near-miss watch (lightweight price vs prev_high comparison)
    check_near_miss_triggers(notify=args.notify, dry_run=args.dry_run)

    print()


def check_near_miss_triggers(notify: bool = False, dry_run: bool = False) -> None:
    """
    Check near_miss_watch.txt for stocks that have crossed their breakout level.
    Sends a Discord alert and removes triggered symbols from the file.
    Called automatically at the end of every monitor_watch run.
    """
    nm_file = Path('scanner_output/lists/near_miss_watch.txt')
    if not nm_file.exists():
        return

    entries = []
    for line in nm_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(',')
        if len(parts) >= 2:
            try:
                entries.append({'symbol': parts[0], 'prev_high': float(parts[1]),
                                 'mode': parts[2] if len(parts) > 2 else '?',
                                 'date': parts[3] if len(parts) > 3 else '?',
                                 'raw': line})
            except ValueError:
                pass

    if not entries:
        return

    adapter = YFinanceAdapter(use_disk_cache=False)
    triggered = []
    remaining = []

    for entry in entries:
        sym = entry['symbol']
        try:
            now_et = datetime.now(NY_TZ)
            start = (now_et - timedelta(days=1)).strftime('%Y-%m-%d')
            end   = (now_et + timedelta(days=1)).strftime('%Y-%m-%d')
            df = adapter.get_historical_data(sym, '15 mins', start_date=start, end_date=end)
            if df is None or df.empty:
                remaining.append(entry['raw'])
                continue
            date_mask  = df.index.normalize() == pd.Timestamp(now_et.strftime('%Y-%m-%d'))
            today_bars = df[date_mask]
            if today_bars.empty:
                remaining.append(entry['raw'])
                continue
            current = float(today_bars['close'].iloc[-1])
            if current > entry['prev_high']:
                triggered.append({**entry, 'current': current})
                print(f' ⚡ NEAR-MISS TRIGGERED: {sym}  ${current:.2f} > ${entry["prev_high"]:.2f}  (coiling since {entry["date"]})')
            else:
                remaining.append(entry['raw'])
        except Exception as e:
            print(f' ⚠️  near-miss check failed for {sym}: {e}')
            remaining.append(entry['raw'])

    if triggered:
        # Rewrite file without triggered symbols
        nm_file.write_text('\n'.join(remaining) + ('\n' if remaining else ''))

        if notify and not dry_run:
            try:
                notifier = Notifier()
                lines = [
                    f"⚡ **{t['symbol']}** triggered: ${t['current']:.2f} > ${t['prev_high']:.2f}  "
                    f"(coiling since {t['date']}, {t['mode']})"
                    for t in triggered
                ]
                notifier.send_discord(
                    f"⚡ Near-Miss Breakout: {len(triggered)} triggered",
                    '\n'.join(lines)
                )
                print(f'  📢 Sent near-miss trigger alert ({len(triggered)} symbols)')
            except Exception as e:
                print(f'  ⚠️  Near-miss notification error: {e}')


if __name__ == '__main__':
    main()
