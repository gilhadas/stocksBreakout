#!/usr/bin/env python3
"""
test_daytrade_cron.py
=====================
Retroactive replay of the 5 daytrade cron phases for a target date.

Each phase uses yfinance 15-min data truncated to only bars available at
the phase's scheduled ET time — so the scanner sees exactly the same data
it would have seen had it run live.

Phases mirrored from cron_jobs.txt:
  09:35 ET  Phase 1 — Wide scan (ALL.txt → export PREMIUM)
  10:00 ET  Phase 2 — PREMIUM re-evaluate
  14:00 ET  Phase 2 — PREMIUM re-evaluate
  15:30 ET  Phase 3 — Exit check (positions_daytrade_mock.csv)
  20:20 ET  Phase 4 — Evening PREMIUM re-evaluate

Usage:
  python test_daytrade_cron.py                        # today (Feb 18, 2026)
  python test_daytrade_cron.py --date 2026-02-18
  python test_daytrade_cron.py --date 2026-02-18 --quick   # Screener.txt instead of ALL.txt
  python test_daytrade_cron.py --date 2026-02-18 --focus ONDS,RCAT,ALAB
"""

import argparse
import asyncio
import logging
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Python 3.14: event loop must exist before ib_insync is imported (pulled in by scanner)
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from utils import get_watchlist_from_file
from yfinance_adapter import YFinanceAdapter
from scanner import BreakoutDetector
from exit_evaluator import ExitEvaluator
from config import MODES
from utils import classify_market_regime
from sentiment import get_sector_for_ticker

NY_TZ = ZoneInfo('America/New_York')
logging.basicConfig(
    level=logging.WARNING,          # suppress scanner debug noise
    format='%(levelname)s | %(message)s',
)
logger = logging.getLogger(__name__)


# ── Phase definitions ────────────────────────────────────────────────────────

PHASES = [
    {
        'label':   '09:35 ET  Phase 1 — Wide scan (export momentum-watch)',
        'cutoff':  '09:35',
        'wl_key':  'main',
        'is_exit': False,
    },
    {
        'label':   '10:00 ET  Phase 2 — Momentum-watch re-evaluate (PREMIUM + HIGH-mom + near-miss)',
        'cutoff':  '10:00',
        'wl_key':  'momentum_watch',
        'is_exit': False,
    },
    {
        'label':   '14:00 ET  Phase 2 — Momentum-watch re-evaluate',
        'cutoff':  '14:00',
        'wl_key':  'momentum_watch',
        'is_exit': False,
    },
    {
        'label':   '15:30 ET  Phase 3 — Exit check',
        'cutoff':  '15:30',
        'wl_key':  'exit',
        'is_exit': True,
    },
    {
        'label':   '20:20 ET  Phase 4 — Evening momentum-watch re-evaluate',
        'cutoff':  '20:20',
        'wl_key':  'momentum_watch',
        'is_exit': False,
    },
]

MODE = 'daytrade'
TF   = '15 mins'


# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Retroactive daytrade cron replay')
    p.add_argument('--date',   default='2026-02-18',
                   help='Target date YYYY-MM-DD (default: 2026-02-18)')
    p.add_argument('--quick',  action='store_true',
                   help='Use Screener.txt instead of ALL.txt for Phase 1 (faster)')
    p.add_argument('--focus',  default='ONDS',
                   help='Comma-separated symbols to highlight in output (default: ONDS)')
    p.add_argument('--no-cache-clear', action='store_true',
                   help='Skip clearing today\'s intraday cache before running')
    return p.parse_args()


def cutoff_naive_et(cutoff_str: str, target_date: str) -> datetime:
    """
    Return a naive ET datetime for the cutoff time on target_date.
    Naive because yfinance_adapter strips tz from the index.
    """
    d = datetime.strptime(target_date, '%Y-%m-%d')
    h, m = map(int, cutoff_str.split(':'))
    return d.replace(hour=h, minute=m, second=0, microsecond=0)


def apply_cutoff(df: pd.DataFrame, cutoff_dt: datetime, tf: str) -> pd.DataFrame:
    """
    For intraday timeframes, return only bars with index <= cutoff_dt.
    For daily/weekly we return the full frame (needed for trend context).
    """
    if 'min' not in tf.lower() and 'hour' not in tf.lower():
        return df  # daily/weekly — no truncation

    idx = df.index
    # Handle tz-aware index (shouldn't happen after adapter strips tz, but be safe)
    if hasattr(idx, 'tz') and idx.tz is not None:
        idx_naive = idx.tz_convert(NY_TZ).tz_localize(None)
    else:
        idx_naive = idx

    mask = idx_naive <= cutoff_dt
    filtered = df[mask]
    return filtered if not filtered.empty else df


def fetch_intraday(adapter: YFinanceAdapter, symbol: str,
                   target_date: str) -> pd.DataFrame | None:
    """Fetch 5-day 15-min history covering target_date (uses disk cache)."""
    start = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    end   = (datetime.strptime(target_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    return adapter.get_historical_data(symbol, TF, start_date=start, end_date=end)


def get_spy_perf(adapter: YFinanceAdapter, cutoff_dt: datetime,
                 target_date: str) -> tuple[float, float]:
    """Compute SPY performance/volatility at the given cutoff."""
    df = fetch_intraday(adapter, 'SPY', target_date)
    if df is None or df.empty:
        return 0.0, 1.0
    df = apply_cutoff(df, cutoff_dt, TF)
    if len(df) < 5:
        return 0.0, 1.0
    # Performance = last bar vs bar 20 bars ago (lookback for daytrade)
    lookback = min(20, len(df) - 1)
    perf = (df['close'].iloc[-1] / df['close'].iloc[-lookback - 1]) - 1
    vol  = df['close'].pct_change().std() * 100
    return float(perf), float(vol)


def load_positions_csv(path: str) -> list[dict]:
    """Load mock positions CSV for exit check."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        df = pd.read_csv(p)
        return df.to_dict('records')
    except Exception:
        return []


def run_scan_phase(phase: dict, symbols: list[str], adapter: YFinanceAdapter,
                   detector: BreakoutDetector, target_date: str,
                   focus_set: set[str]) -> list[dict]:
    """Run a breakout scan phase; returns list of signals."""
    cutoff_dt = cutoff_naive_et(phase['cutoff'], target_date)
    spy_perf, spy_vol = get_spy_perf(adapter, cutoff_dt, target_date)
    regime = classify_market_regime(spy_perf, spy_vol)

    cfg = MODES[MODE]
    min_bars = cfg.get('trend_period', 20)

    signals = []
    n = len(symbols)
    for i, sym in enumerate(symbols, 1):
        bar = f'  [{i:3}/{n}] {sym:12}'
        print(bar, end='\r', flush=True)

        df = fetch_intraday(adapter, sym, target_date)
        if df is None or len(df) < min_bars:
            if sym in focus_set:
                print(f'\n  ⚠️  {sym}: not enough data ({len(df) if df is not None else 0} bars)\n')
            continue

        df_cut = apply_cutoff(df.copy(), cutoff_dt, TF)
        if len(df_cut) < min_bars:
            if sym in focus_set:
                print(f'\n  ⚠️  {sym}: only {len(df_cut)} bars before cutoff {phase["cutoff"]} ET\n')
            continue

        sig = detector.detect(
            df_cut, sym, MODE, TF, spy_perf,
            use_scoring=True, sector_hot=False,
            regime=regime,
        )

        if sig:
            sig['Sector'] = get_sector_for_ticker(sym)
            sig['SPY_Perf'] = f'{spy_perf:+.2%}'
            sig['Regime'] = regime
            signals.append(sig)
        elif sym in focus_set:
            # Pull rejection reasons from detector
            rej = next((r for r in detector.rejection_reasons if r['symbol'] == sym), None)
            if rej:
                print(f'\n  📋 {sym} REJECTED: {rej["reasons"]}\n')
            else:
                print(f'\n  📋 {sym}: no breakout detected (price/vol gate not met)\n')

    print(' ' * 60, end='\r')  # clear progress line
    return signals


def run_exit_phase(phase: dict, symbols: list[str], adapter: YFinanceAdapter,
                   positions: list[dict], target_date: str) -> list[dict]:
    """Run exit evaluator on positions."""
    cutoff_dt = cutoff_naive_et(phase['cutoff'], target_date)
    evaluator = ExitEvaluator()
    cfg = MODES[MODE]
    results = []

    # If no positions loaded, use symbols as placeholder
    pos_by_sym = {p.get('symbol', p.get('Symbol', '')): p for p in positions}
    scan_syms = list(pos_by_sym.keys()) if pos_by_sym else symbols[:20]

    n = len(scan_syms)
    for i, sym in enumerate(scan_syms, 1):
        print(f'  [{i:3}/{n}] {sym:12}', end='\r', flush=True)

        df = fetch_intraday(adapter, sym, target_date)
        if df is None or len(df) < 30:
            continue

        df_cut = apply_cutoff(df.copy(), cutoff_dt, TF)
        if len(df_cut) < 30:
            continue

        pos = pos_by_sym.get(sym, {})
        entry  = float(pos.get('entry_price', pos.get('Price',  df_cut['close'].iloc[0])))
        stop   = float(pos.get('stop_loss',   pos.get('Stop',   entry * 0.97)))
        target = float(pos.get('take_profit', pos.get('Target', entry * 1.05)))
        held   = int(pos.get('days_held', pos.get('DaysHeld', 0)))

        res = evaluator.evaluate(df_cut, sym, MODE, entry, stop, target, TF, days_held=held)
        if res:
            results.append(res)

    print(' ' * 60, end='\r')
    return results


def print_phase_header(phase: dict) -> None:
    print(f'\n{"="*70}')
    print(f'  {phase["label"]}')
    print(f'{"="*70}')


def print_signals(signals: list[dict], focus_set: set[str]) -> None:
    if not signals:
        print('  No signals found.')
        return

    # Sort: PREMIUM first, then by Vol
    qual_order = {'GOLD': 0, 'PREMIUM': 1, 'HIGH': 2, 'STANDARD': 3}
    signals_sorted = sorted(
        signals,
        key=lambda s: (qual_order.get(s.get('Quality', 'STANDARD'), 9),
                       -s.get('Vol', 0))
    )

    cols = ['Symbol', 'Price', 'Vol', 'Gap%', 'Quality', 'Type',
            'RSI', 'R:R', 'Stop', 'Target', 'Sector', 'Regime']

    rows = []
    for s in signals_sorted:
        row = {c: s.get(c, '') for c in cols}
        rows.append(row)

    df = pd.DataFrame(rows)
    print(df.to_string(index=False))

    # Highlight focus symbols
    for s in signals_sorted:
        sym = s.get('Symbol', '')
        if sym in focus_set:
            q   = s.get('Quality', '?')
            typ = s.get('Type', '')
            vol = s.get('Vol', '?')
            gap = s.get('Gap%', 0)
            rsi = s.get('RSI', '?')
            tag = f' [{typ}]' if typ else ''
            print(f'\n  ★ FOCUS: {sym} → {q}{tag} | Vol {vol}x | Gap {gap:+.1f}% | RSI {rsi}')


def print_exit_results(results: list[dict]) -> None:
    if not results:
        print('  No positions / no exit signals.')
        return
    actions = [r for r in results if r.get('Action') != 'HOLD']
    holds   = [r for r in results if r.get('Action') == 'HOLD']
    print(f'  Actionable: {len(actions)} | HOLD: {len(holds)}')
    if actions:
        df = pd.DataFrame(actions)[['Symbol', 'Action', 'Price', 'UnrealizedR', 'Reason']]
        print(df.to_string(index=False))


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    target_date = args.date
    focus_set   = {s.strip().upper() for s in args.focus.split(',')} if args.focus else set()

    print(f'\n🗓  Retroactive daytrade cron replay — {target_date}')
    print(f'   Mode: {MODE} | Timeframe: {TF} | Focus: {", ".join(focus_set) or "none"}')

    # ── Watchlist setup ──────────────────────────────────────────────────────
    main_wl_path = 'input/Screener.txt' if args.quick else 'input/All.txt'
    main_wl      = get_watchlist_from_file(main_wl_path)
    exit_wl      = get_watchlist_from_file('input/1_26_Setups.txt')

    # Ensure focus symbols are in the main watchlist
    for sym in focus_set:
        if sym not in main_wl:
            main_wl.append(sym)

    print(f'\n   Main watchlist ({main_wl_path}): {len(main_wl)} symbols')

    # ── Adapter + detector ───────────────────────────────────────────────────
    adapter  = YFinanceAdapter(use_disk_cache=True)
    detector = BreakoutDetector()

    # Load positions for exit phase
    positions = load_positions_csv('scanner_output/lists/positions_daytrade_mock.csv')

    # ── Timestamp validation ─────────────────────────────────────────────────
    now_et = datetime.now(NY_TZ)
    print(f'\n   Current ET time: {now_et.strftime("%Y-%m-%d %H:%M:%S %Z")}')
    print(f'   (timestamps now use ET thanks to ZoneInfo fix)\n')

    # ── Run phases ───────────────────────────────────────────────────────────
    momentum_watch_symbols: list[str] = []
    all_phase_results: list[dict] = []

    for phase in PHASES:
        wl_key = phase['wl_key']

        if wl_key == 'main':
            symbols = main_wl
        elif wl_key == 'momentum_watch':
            symbols = momentum_watch_symbols if momentum_watch_symbols else main_wl[:10]
        elif wl_key == 'exit':
            symbols = exit_wl
        else:
            symbols = main_wl

        print_phase_header(phase)
        print(f'  Symbols: {len(symbols)} | SPY cutoff: {phase["cutoff"]} ET')

        if phase['is_exit']:
            results = run_exit_phase(phase, symbols, adapter, positions, target_date)
            print_exit_results(results)
        else:
            signals = run_scan_phase(phase, symbols, adapter, detector,
                                     target_date, focus_set)
            print_signals(signals, focus_set)

            # Export momentum-watch symbols for subsequent phases
            # (PREMIUM/GOLD + HIGH-momentum/high-vol + near-miss high-vol)
            if wl_key == 'main':
                watch: list[str] = []
                seen_watch: set[str] = set()
                for s in signals:
                    sym = s.get('Symbol', '')
                    q   = s.get('Quality', '')
                    vol = s.get('Vol', 0)
                    typ = s.get('Type', '')
                    if sym and sym not in seen_watch:
                        if q in ('PREMIUM', 'GOLD'):
                            watch.append(sym); seen_watch.add(sym)
                        elif q == 'HIGH' and (typ == 'Momentum' or vol >= 3.0):
                            watch.append(sym); seen_watch.add(sym)
                # Add near-miss rejections (within 0.5% of breakout) + high-vol rejections
                for rej in detector.rejection_reasons:
                    sym = rej.get('symbol', '')
                    if sym and sym not in seen_watch:
                        is_near_miss = 'Near miss' in rej.get('reasons', '')
                        is_high_vol = rej.get('vol_ratio', 0) >= 3.0
                        if is_near_miss or is_high_vol:
                            watch.append(sym); seen_watch.add(sym)
                momentum_watch_symbols = watch
                print(f'\n  → Exported {len(momentum_watch_symbols)} momentum-watch tickers for next phases')
                if momentum_watch_symbols:
                    print(f'    {", ".join(momentum_watch_symbols[:30])}')

            all_phase_results.extend(signals)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f'\n{"="*70}')
    print(f'  SUMMARY — {target_date}')
    print(f'{"="*70}')
    print(f'  Total signals across all phases: {len(all_phase_results)}')

    if focus_set:
        print(f'\n  Focus symbols ({", ".join(focus_set)}) summary:')
        for sym in focus_set:
            sym_sigs = [s for s in all_phase_results if s.get('Symbol') == sym]
            if sym_sigs:
                for s in sym_sigs:
                    print(f'    ✓ {sym}: {s.get("Quality")} | Vol {s.get("Vol")}x | '
                          f'Gap {s.get("Gap%", 0):+.1f}% | Type={s.get("Type", "-")} | RSI={s.get("RSI", "-")}')
            else:
                print(f'    ✗ {sym}: not detected in any phase')

        # ── Intraday diagnostic for missed focus symbols ─────────────────────
        missed = [sym for sym in focus_set
                  if not any(s.get('Symbol') == sym for s in all_phase_results)]
        if missed:
            print(f'\n  Intraday diagnostic for missed symbols:')
            for sym in missed:
                df_full = fetch_intraday(adapter, sym, target_date)
                if df_full is None or df_full.empty:
                    print(f'\n  {sym}: no data fetched')
                    continue

                # Only show bars from the target date
                date_mask = df_full.index.normalize() == pd.Timestamp(target_date)
                df_day = df_full[date_mask] if date_mask.any() else df_full.tail(26)

                print(f'\n  {sym} — {len(df_day)} bars on {target_date}:')
                print(f'  {"Time":8} {"Open":>8} {"Close":>8} {"High":>8} {"Low":>8} {"Vol":>12}  {"Chg%":>7}')
                prev_close = df_full['close'].iloc[-(len(df_day)+1)] if len(df_full) > len(df_day) else df_day['close'].iloc[0]
                for ts, row in df_day.iterrows():
                    t_str = ts.strftime('%H:%M') if hasattr(ts, 'strftime') else str(ts)
                    chg = (row['close'] - prev_close) / prev_close * 100
                    flag = ' ★' if abs(chg) >= 3 or row.get('volume', 0) > df_full['volume'].mean() * 3 else ''
                    print(f'  {t_str:8} {row["open"]:>8.2f} {row["close"]:>8.2f} '
                          f'{row["high"]:>8.2f} {row["low"]:>8.2f} '
                          f'{int(row.get("volume", 0)):>12,}  {chg:>+7.2f}%{flag}')
                    prev_close = row['close']

                # Show which cron phase would have first caught it
                cfg = MODES[MODE]
                vol_thresh = cfg['vol_thresh']
                avg_vol = df_full['volume'].rolling(20).mean().iloc[-(len(df_day)+1)]

                print(f'\n  {sym} gate check per phase:')
                for p in PHASES:
                    if p['is_exit']:
                        continue
                    cutoff_dt = cutoff_naive_et(p['cutoff'], target_date)
                    df_cut = apply_cutoff(df_full.copy(), cutoff_dt, TF)
                    if df_cut.empty:
                        print(f'    {p["cutoff"]}: no bars yet')
                        continue
                    lat = df_cut.iloc[-1]
                    ph = df_cut['high'].rolling(cfg['lookback']).max().iloc[-2] \
                        if len(df_cut) >= 2 else lat['high']
                    vr = lat['volume'] / avg_vol if avg_vol and avg_vol > 0 else 0
                    pb = lat['close'] > ph
                    vc = vr >= vol_thresh
                    intra = (lat['close'] - lat['open']) / lat['open'] * 100 if lat['open'] > 0 else 0
                    ms = (abs(intra) >= 5.0 or (df_cut['close'].iloc[-1] / df_cut['close'].iloc[-2] - 1)*100 >= 5) \
                        and vr >= 3.0
                    status = '✓ DETECTED' if pb and vc else ('→ MomSurge?' if ms else '✗ missed')
                    print(f'    {p["cutoff"]} ET: close={lat["close"]:.2f} prev_high={ph:.2f} '
                          f'vol_ratio={vr:.1f}x price_break={pb} vol_ok={vc} '
                          f'intra_move={intra:+.1f}% → {status}')

    print()


if __name__ == '__main__':
    main()
