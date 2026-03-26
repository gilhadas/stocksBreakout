#!/usr/bin/env python3
"""
Breakout Scanner - Main Entry Point
Supports long-term, swing, day trading, and scalping modes
"""

import asyncio
import argparse
import json
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()  # Load .env before any AWS/broker calls

_NY_TZ = ZoneInfo('America/New_York')

# Python 3.14 compatibility: Create event loop before importing ib_insync
# This prevents "RuntimeError: There is no current event loop" from eventkit
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB

from config import IB_PAPER_PORT, IB_LIVE_PORT, IB_HOST, IB_CLIENT_ID, MODES, REGIME_CONFIG, OUTPUT_DIR
from utils import (
    get_watchlist_from_file,
    get_positions_from_file,
    classify_market_regime,
    setup_logging,
    SCAN_LEVEL,
    _QUIET_LIBS,
)
from scan_event_log import attach_scan_csv_handler
from orchestrator import ScannerOrchestrator
from notifier import Notifier
from mock_trader import MockIBConnection, MockTrader, SimulationMode

logger = logging.getLogger(__name__)


async def connect_to_ib(live: bool = False, mock: bool = False, mock_mode: str = 'realistic') -> IB:
    """Connect to Interactive Brokers or Mock connection.

    Connection cascade: LIVE port → PAPER port → None (yfinance fallback)
    """
    if mock:
        logger.info(f"Using MOCK trading mode ({mock_mode})")
        # Try real IB PAPER port for market data even in mock mode (orders never placed).
        # Only fall back to MockIBConnection (yfinance data) if PAPER port is unavailable.
        real_ib = IB()
        try:
            await real_ib.connectAsync(IB_HOST, IB_PAPER_PORT, clientId=IB_CLIENT_ID)
            real_ib.reqMarketDataType(3)  # delayed data — no live subscription needed
            logger.info(f"Mock mode: real IB PAPER data (port {IB_PAPER_PORT}), no orders will be placed")
            return real_ib
        except Exception as e:
            logger.warning(f"Mock mode: IB PAPER (port {IB_PAPER_PORT}) unavailable ({e}) — falling back to yfinance")
        ib = MockIBConnection(mode=mock_mode)
        await ib.connectAsync(IB_HOST, IB_PAPER_PORT, clientId=IB_CLIENT_ID)
        return ib

    # Build port order: if --live, try LIVE first then PAPER; otherwise PAPER first then LIVE
    if live:
        ports = [(IB_LIVE_PORT, 'LIVE', 1), (IB_PAPER_PORT, 'PAPER', 3)]
    else:
        ports = [(IB_PAPER_PORT, 'PAPER', 3), (IB_LIVE_PORT, 'LIVE', 1)]

    for port, label, mkt_data_type in ports:
        ib = IB()
        try:
            await ib.connectAsync(IB_HOST, port, clientId=IB_CLIENT_ID)
            ib.reqMarketDataType(mkt_data_type)
            logger.info(f"Connected to IB {label} (port {port})")
            return ib
        except Exception as e:
            logger.warning(f"IB {label} (port {port}) failed: {e}")

    logger.warning("All IB ports unavailable — falling back to yfinance (15-min delayed data)")
    return None


async def run_scan_mode(orchestrator: ScannerOrchestrator, args, notifier: Notifier):
    """Execute scan mode"""
    # Load watchlist
    watchlist = get_watchlist_from_file(args.file)
    if not watchlist:
        logger.error("No symbols loaded from watchlist")
        return []
    
    logger.info(f"Loaded {len(watchlist)} symbols from {args.file}")
    
    # Scan watchlist
    results = await orchestrator.scan_watchlist(
        watchlist=watchlist,
        mode=args.mode,
        timeframe=args.timeframe,
        vol_thresh=args.vol,
        atr_mult=args.atr,
        lookback=args.lookback,
        detect_bounces=getattr(args, 'bounce', False)
    )
    
    # Add sector name to every signal
    if results:
        from sentiment import get_sector_for_ticker
        for sig in results:
            ticker = sig.get('Symbol') or sig.get('symbol', '')
            if ticker:
                sig['Sector'] = get_sector_for_ticker(ticker)

    # Enrich signals with sentiment if requested
    if results and getattr(args, 'sentiment', False):
        from sentiment import check_sentiment
        logger.info("Enriching signals with sentiment data...")
        for sig in results:
            ticker = sig.get('Symbol') or sig.get('symbol', '')
            if ticker:
                sent = check_sentiment(ticker)
                sig['Sentiment'] = sent['sentiment']
                sig['Buzz'] = sent['buzz_score']

    # FinBERT sentiment for HIGH / PREMIUM / GOLD signals only
    # Runs unconditionally when quality signals are present — no extra flag required.
    # Adds FinBERT, FinBERT_Score, FinBERT_Net, FinBERT_Headline keys to each signal dict.
    _quality_sigs = [s for s in results if s.get('Quality') in ('HIGH', 'PREMIUM', 'GOLD')]
    if _quality_sigs:
        try:
            from finbert_sentiment import batch_sentiment as _fb_batch
            _fb_syms = [s.get('Symbol') or s.get('symbol', '') for s in _quality_sigs]
            _fb_syms = [s for s in _fb_syms if s]
            if _fb_syms:
                logger.info(f"FinBERT sentiment: analysing {len(_fb_syms)} HIGH+ signal(s)…")
                _fb_results = _fb_batch(_fb_syms, max_headlines=8, max_age_hours=24)
                for sig in results:
                    sym = sig.get('Symbol') or sig.get('symbol', '')
                    if sym in _fb_results:
                        fb = _fb_results[sym]
                        sig['FinBERT'] = fb['label']
                        sig['FinBERT_Score'] = fb['score']
                        sig['FinBERT_Net'] = fb['net_score']
                        sig['FinBERT_Headline'] = fb['top_headline'][:100]
                        sig['FinBERT_Total'] = sum(fb['breakdown'].values())  # total headlines analyzed
                        logger.info(
                            f"  {fb['emoji']} {sym:<8} {fb['label']:<8} "
                            f"score={fb['score']:.2f} net={fb['net_score']:+.2f}  "
                            f"({fb['breakdown']['bullish']}↑/{fb['breakdown']['bearish']}↓"
                            f"/{fb['breakdown']['neutral']}~)  "
                            f'"{fb["top_headline"][:65]}"'
                        )
        except ImportError:
            logger.debug("FinBERT not available (pip install transformers torch)")
        except Exception as _e:
            logger.warning(f"FinBERT enrichment failed: {_e}")

    # ── FinBERT quality promotion ─────────────────────────────────────────────
    # Bullish FinBERT at sufficient confidence promotes HIGH→PREMIUM or PREMIUM→GOLD.
    # Promotion-only: bearish FinBERT does NOT downgrade signals.
    try:
        from config import FINBERT_PROMOTION as _fb_promo_cfg
        if _fb_promo_cfg.get('enabled', True):
            _h2p = _fb_promo_cfg['high_to_premium']
            _p2g = _fb_promo_cfg['premium_to_gold']
            for sig in results:
                fb_label = sig.get('FinBERT')
                fb_score = sig.get('FinBERT_Score', 0.0)
                fb_net   = sig.get('FinBERT_Net', 0.0)
                quality  = sig.get('Quality', '')
                sym      = sig.get('Symbol', '')

                if fb_label != 'bullish':
                    continue

                # Use actual headline count stored during enrichment, fall back to heuristic
                _total_headlines = sig.get('FinBERT_Total', 0)
                if _total_headlines == 0:
                    _total_headlines = 1
                    if abs(fb_net) < 1.0 and fb_net != 0.0:
                        _total_headlines = max(2, int(round(1 / max(abs(fb_net), 0.01))))

                _h2p_min_hl = _h2p.get('min_headlines', 1)
                _p2g_min_hl = _p2g.get('min_headlines', 1)

                if quality == 'HIGH' and fb_score >= _h2p['min_score'] and fb_net >= _h2p['min_net'] and _total_headlines >= _h2p_min_hl:
                    sig['Quality'] = 'PREMIUM'
                    sig['FinBERT_Promoted'] = 'HIGH→PREMIUM'
                    logger.info(
                        f"  ⬆ {sym} promoted HIGH→PREMIUM "
                        f"(FinBERT bullish {fb_score:.2f}, net={fb_net:+.2f}, ~{_total_headlines} headlines)"
                    )
                elif quality == 'PREMIUM' and fb_score >= _p2g['min_score'] and fb_net >= _p2g['min_net'] and _total_headlines >= _p2g_min_hl:
                    sig['Quality'] = 'GOLD'
                    sig['FinBERT_Promoted'] = 'PREMIUM→GOLD'
                    logger.info(
                        f"  ⬆ {sym} promoted PREMIUM→GOLD "
                        f"(FinBERT bullish {fb_score:.2f}, net={fb_net:+.2f}, ~{_total_headlines} headlines)"
                    )
    except Exception as _fp_e:
        logger.debug(f"FinBERT promotion skipped: {_fp_e}")

    # ── Earnings date enrichment ──────────────────────────────────────────────
    # Adds Earnings_Date, Earnings_Timing (BMO/AMC), Earnings_Warning,
    # Earnings_This_Week (True/False), and Reporting_Watchlist (True/False) to each signal.
    if results:
        try:
            import yfinance as _yf
            from datetime import datetime as _dt, timezone as _tz
            _today = _dt.now(_NY_TZ).date()
            _EARNINGS_WARN_DAYS = 7

            # Load reporting watchlist for cross-reference tagging
            _reporting_syms: set[str] = set()
            _rw_path = os.path.join(os.path.dirname(__file__), 'input', 'reporting_watchlist.txt')
            try:
                with open(_rw_path, encoding='utf-8') as _rw_f:
                    for _line in _rw_f:
                        for _tok in _line.split(','):
                            _s = _tok.strip().split(':')[-1].strip().upper()
                            if _s:
                                _reporting_syms.add(_s)
            except Exception:
                pass

            for sig in results:
                sym = sig.get('Symbol') or sig.get('symbol', '')
                sig['Earnings_Date'] = ''
                sig['Earnings_Timing'] = ''
                sig['Earnings_Warning'] = ''
                sig['Earnings_This_Week'] = False
                sig['Reporting_Watchlist'] = sym.upper() in _reporting_syms if sym else False
                if not sym:
                    continue
                try:
                    _cal = _yf.Ticker(sym).calendar
                    if _cal is None:
                        continue
                    _ed_raw = None
                    if isinstance(_cal, dict):
                        _ed_val = _cal.get('Earnings Date')
                        if _ed_val:
                            _ed_raw = _ed_val[0] if isinstance(_ed_val, list) else _ed_val
                    elif hasattr(_cal, 'iloc'):
                        try:
                            _ed_raw = _cal.iloc[0, 0]
                        except Exception:
                            pass
                    if _ed_raw is None:
                        continue

                    # Parse to datetime
                    if isinstance(_ed_raw, str):
                        _ed_raw = pd.Timestamp(_ed_raw)
                    if hasattr(_ed_raw, 'date'):
                        _ed_date = _ed_raw.date() if callable(_ed_raw.date) else _ed_raw.date
                    else:
                        _ed_date = pd.Timestamp(str(_ed_raw)).date()

                    sig['Earnings_Date'] = str(_ed_date)

                    # BMO / AMC from hour (hour < 12 → BMO, else AMC)
                    if hasattr(_ed_raw, 'hour') and _ed_raw.hour > 0:
                        sig['Earnings_Timing'] = 'BMO' if _ed_raw.hour < 12 else 'AMC'

                    _days_until = (_ed_date - _today).days
                    if 0 <= _days_until <= _EARNINGS_WARN_DAYS:
                        sig['Earnings_This_Week'] = True
                        _timing_str = f" ({sig['Earnings_Timing']})" if sig['Earnings_Timing'] else ""
                        if sig.get('Quality') in ('PREMIUM', 'GOLD'):
                            sig['Earnings_Warning'] = (
                                f"EARNINGS in {_days_until}d{_timing_str}"
                            )
                            logger.warning(
                                f"  ⚠ {sym} — earnings in {_days_until} day(s){_timing_str}, "
                                f"quality={sig['Quality']}"
                            )
                        else:
                            sig['Earnings_Warning'] = f"Earnings in {_days_until}d{_timing_str}"
                except Exception as _e:
                    logger.debug(f"Earnings fetch failed for {sym}: {_e}")
        except Exception as _earn_e:
            logger.debug(f"Earnings enrichment skipped: {_earn_e}")

    # Check market regime — warn if choppy or red
    market_warning = None
    try:
        timeframe = args.timeframe or MODES[args.mode]['default_timeframe']
        lookback = args.lookback or MODES[args.mode]['lookback']
        spy_perf, spy_vol = await orchestrator.market_data.get_spy_performance(timeframe, lookback)
        regime = classify_market_regime(spy_perf, spy_vol)
        spy_pct = spy_perf * 100

        if regime == 'CHOPPY':
            market_warning = f"CHOPPY MARKET — SPY {spy_pct:+.2f}%, vol {spy_vol:.2f}%. Avoid new entries, breakouts likely to fail."
        elif spy_perf < -0.015:  # SPY down > 1.5% = true red day (0% WR for longs per analysis)
            market_warning = f"RED MARKET — SPY {spy_pct:+.2f}%. Blocking long entries: 0% WR, -4.55% avg P&L on red days."
        elif spy_perf < -0.005:  # SPY down 0.5-1.5% = bearish but tradeable (37% WR with BOUNCE GOLD)
            market_warning = f"BEARISH MARKET — SPY {spy_pct:+.2f}%. Prefer oversold bounces; gate SMA20_CROSS."

        if market_warning:
            logger.warning(f"\n{'!'*70}")
            logger.warning(f" ⚠️  {market_warning}")
            logger.warning(f"{'!'*70}\n")
    except Exception as e:
        logger.debug(f"Market regime check failed: {e}")

    # ── Dedup & cap ────────────────────────────────────────────────────────
    # Remove symbols already signaled today (prevents repeat notifications).
    # Then cap at MAX_SIGNALS_PER_SCAN to avoid firehose output.
    MAX_SIGNALS_PER_SCAN = 20
    if results:
        _today_str = datetime.now(_NY_TZ).strftime('%Y%m%d')
        _seen_today = set()
        _sig_dir = Path('scanner_output/signals')
        if _sig_dir.exists():
            for _f in _sig_dir.glob(f'signals_{args.mode}_{_today_str}_*.csv'):
                try:
                    _prev = pd.read_csv(_f, usecols=['Symbol'])
                    _seen_today.update(_prev['Symbol'].dropna().tolist())
                except Exception:
                    pass
        _before = len(results)
        results = [s for s in results if s.get('Symbol', '') not in _seen_today]
        if _before > len(results):
            logger.info(f"Dedup: removed {_before - len(results)} symbols already signaled today ({len(results)} remaining)")

        # Sort by quality tier (GOLD > PREMIUM > HIGH > STANDARD) then volume, keep top N
        _quality_order = {'GOLD': 0, 'PREMIUM': 1, 'HIGH': 2, 'STANDARD': 3}
        results.sort(key=lambda s: (_quality_order.get(s.get('Quality', ''), 9), -s.get('Vol', 0)))
        if len(results) > MAX_SIGNALS_PER_SCAN:
            logger.info(f"Signal cap: keeping top {MAX_SIGNALS_PER_SCAN} of {len(results)} (sorted by quality + volume)")
            results = results[:MAX_SIGNALS_PER_SCAN]

    # Display and save results
    if results:
        logger.info(f"\n{'='*70}")
        logger.info(f" {args.mode.upper()} SIGNALS FOUND: {len(results)}")
        logger.info(f"{'='*70}\n")

        df = pd.DataFrame(results).sort_values(by='Vol', ascending=False)
        print(df.to_string(index=False))

        output_file = orchestrator.save_results(results, args.mode, 'signals')

        # Auto-append to positions file if requested.
        # In CHOPPY/RED markets: block SMA20_CROSS (mass-fires, low alpha).
        # Analysis (Mar 4-16): RED_MARKET BOUNCE = 0% WR, -4.55% P&L → block all in RED.
        # BOUNCE quality gate: GOLD only (PREMIUM bounce avg -1.97% P&L; GOLD avg +1.72%).
        if getattr(args, 'auto_positions', None):
            from utils import append_signals_to_positions
            _is_true_red = market_warning and 'RED MARKET' in market_warning
            _is_bearish  = market_warning and 'BEARISH MARKET' in market_warning
            _is_choppy   = market_warning and 'CHOPPY MARKET' in market_warning

            if _is_true_red:
                # RED_MARKET (SPY < -1.5%): 0% WR historically — block all longs.
                # Only CONTINUATION/Momentum GOLD survive (own catalyst, not market-correlated).
                safe_signals = [
                    s for s in results
                    if s.get('Type') in ('CONTINUATION', 'Momentum') and s.get('Quality') == 'GOLD'
                ]
                blocked = len(results) - len(safe_signals)
                logger.warning(f"⚠️  RED MARKET — blocked {blocked} signals; only CONTINUATION/Momentum GOLD allowed.")
            elif _is_choppy:
                # CHOPPY (SPY flat, low vol): block SMA20_CROSS (mass-fires, 0% alpha).
                # CONTINUATION = 100% WR in CHOPPY; BOUNCE GOLD allowed.
                safe_signals = [
                    s for s in results
                    if s.get('Type') == 'CONTINUATION'
                    or (s.get('Type') in ('BOUNCE', 'Momentum') and s.get('Quality') == 'GOLD')
                ]
                blocked = len(results) - len(safe_signals)
                if blocked:
                    logger.warning(f"⚠️  CHOPPY — blocked {blocked} signals; passing CONTINUATION + BOUNCE/Momentum GOLD.")
            elif _is_bearish:
                # BEARISH (SPY -0.5% to -1.5%): 37% WR — allow BOUNCE GOLD + CONTINUATION.
                # Block SMA20_CROSS (against market direction).
                safe_signals = [
                    s for s in results
                    if s.get('Type') in ('CONTINUATION', 'Momentum')
                    or (s.get('Type') == 'BOUNCE' and s.get('Quality') == 'GOLD')
                ]
                blocked = len(results) - len(safe_signals)
                if blocked:
                    logger.warning(f"⚠️  BEARISH — blocked {blocked} SMA20_CROSS; allowing CONTINUATION + BOUNCE GOLD ({len(safe_signals)} signals).")
            else:
                # NORMAL/EXPANSION: all types, but BOUNCE requires GOLD quality
                safe_signals = [
                    s for s in results
                    if s.get('Type') != 'BOUNCE' or s.get('Quality') == 'GOLD'
                ]
                blocked = len(results) - len(safe_signals)
                if blocked:
                    logger.info(f"ℹ️  NORMAL — filtered {blocked} BOUNCE non-GOLD; BOUNCE requires GOLD quality.")

            if safe_signals:
                append_signals_to_positions(safe_signals, args.auto_positions, args.mode)

        # Export PREMIUM + GOLD tickers to watchlist file for re-evaluation scans
        if getattr(args, 'export_premium', None):
            premium_symbols = [
                sig.get('Symbol') or sig.get('symbol', '')
                for sig in results
                if sig.get('Quality') in ('PREMIUM', 'GOLD')
            ]
            with open(args.export_premium, 'w') as f:
                f.write('\n'.join(premium_symbols))
            logger.info(f"Exported {len(premium_symbols)} PREMIUM/GOLD tickers to {args.export_premium}")

        # Export momentum-watch list: PREMIUM/GOLD + HIGH-momentum + near-miss high-vol
        # This broader set is used for subsequent Phase 2 re-evaluation scans.
        #
        # Inclusion rules for HIGH-quality signals:
        #   • Momentum type (gap ≥5% or intraday move ≥5% + vol ≥3x)
        #   • vol ≥ 2.0x  — decent volume confirmation (3.0 was too strict at open)
        #   • bullish pattern + near 52w high — coiled setup likely to break regardless of vol
        #   • bullish pattern + at key support/resistance — breakout from level imminent
        if getattr(args, 'export_momentum_watch', None):
            watch_symbols = []
            seen = set()
            # Pre-seed from premarket_watch.txt (written by premarket_monitor.py at 8 AM).
            # These are pre-market gappers that must survive Phase 1's rewrite of this file.
            _pm_file = Path('scanner_output/lists/premarket_watch.txt')
            if _pm_file.exists():
                try:
                    _pm_syms = [s.strip() for s in _pm_file.read_text().splitlines() if s.strip()]
                    for s in _pm_syms:
                        if s not in seen:
                            watch_symbols.append(s); seen.add(s)
                    if _pm_syms:
                        logger.info(f"Pre-seeded {len(_pm_syms)} pre-market gappers from premarket_watch.txt")
                except Exception as _e:
                    logger.warning(f"Could not read premarket_watch.txt: {_e}")
            # Regime-aware watch-list filtering
            _restricted = market_warning and ('CHOPPY MARKET' in market_warning or 'RED MARKET' in market_warning)
            _bearish_day = market_warning and 'BEARISH MARKET' in market_warning
            for sig in results:
                sym    = sig.get('Symbol') or sig.get('symbol', '')
                q      = sig.get('Quality', '')
                vol    = sig.get('Vol', 0)
                typ    = sig.get('Type', '')
                checks = sig.get('Checks', {})
                has_pattern  = checks.get('has_bullish_pattern', False)
                near_high    = checks.get('near_52w_high', False)
                at_level     = checks.get('at_key_support', False) or checks.get('sr_breakout', False)
                if sym and sym not in seen:
                    if _restricted:
                        # CHOPPY/RED: CONTINUATION always in (100% WR!), GOLD always in.
                        # BOUNCE GOLD only (PREMIUM bounce avg -1.97% P&L per analysis).
                        # SMA20_CROSS: skip entirely (mass-fires against trend).
                        if typ == 'CONTINUATION' or q == 'GOLD':
                            watch_symbols.append(sym); seen.add(sym)
                        elif typ in ('BOUNCE', 'Momentum') and q == 'GOLD':
                            watch_symbols.append(sym); seen.add(sym)
                    elif _bearish_day:
                        # BEARISH (SPY -0.5 to -1.5%): allow BOUNCE/CONTINUATION, skip SMA20_CROSS
                        if typ in ('CONTINUATION', 'BOUNCE', 'Momentum') and q in ('GOLD', 'PREMIUM'):
                            watch_symbols.append(sym); seen.add(sym)
                        elif typ in ('CONTINUATION', 'BOUNCE') and q == 'HIGH' and vol >= 2.0:
                            watch_symbols.append(sym); seen.add(sym)
                    else:
                        if q in ('PREMIUM', 'GOLD'):
                            watch_symbols.append(sym); seen.add(sym)
                        elif q == 'HIGH' and (
                            typ == 'Momentum'             # V7 momentum surge
                            or vol >= 2.0                 # strong volume
                            or (has_pattern and near_high) # coiled near 52w high → likely to break
                            or (has_pattern and at_level)  # at support/resistance with pattern
                        ):
                            watch_symbols.append(sym); seen.add(sym)
            # Also include near-miss rejections — skip in restricted/bearish markets
            if not _restricted:
                rejections = orchestrator.get_rejection_reasons()
                for rej in rejections:
                    sym = rej.get('symbol', '')
                    if sym and sym not in seen:
                        is_near_miss = 'Near miss' in rej.get('reasons', '')
                        is_high_vol = rej.get('vol_ratio', 0) >= 2.0
                        has_momentum = rej.get('momentum', 0) >= 70
                        if is_near_miss and (is_high_vol or has_momentum):
                            watch_symbols.append(sym); seen.add(sym)
                        elif is_high_vol and has_momentum:
                            watch_symbols.append(sym); seen.add(sym)
            # Sector basket trigger: when a key ETF moves >= threshold, add the whole sector
            from config import SECTOR_BASKETS
            import yfinance as _yf
            for basket_name, basket in SECTOR_BASKETS.items():
                trigger = basket['trigger_etf']
                threshold = basket['trigger_pct']
                try:
                    hist = _yf.Ticker(trigger).history(period='2d', interval='1d')
                    if len(hist) >= 2:
                        move_pct = (hist['Close'].iloc[-1] - hist['Close'].iloc[-2]) / hist['Close'].iloc[-2] * 100
                        if abs(move_pct) >= threshold:
                            added = []
                            for sym in basket['symbols']:
                                if sym not in seen:
                                    watch_symbols.append(sym); seen.add(sym); added.append(sym)
                            logger.info(
                                f"Sector basket '{basket_name}': {trigger} moved {move_pct:+.1f}% "
                                f"— added {len(added)} tickers: {', '.join(added)}"
                            )
                        else:
                            logger.debug(f"Sector basket '{basket_name}': {trigger} moved {move_pct:+.1f}% — below {threshold}% threshold, skipping")
                except Exception as _e:
                    logger.warning(f"Sector basket '{basket_name}' trigger check failed: {_e}")
            with open(args.export_momentum_watch, 'w') as f:
                f.write('\n'.join(watch_symbols))
            logger.info(
                f"Exported {len(watch_symbols)} momentum-watch tickers to {args.export_momentum_watch} "
                f"(PREMIUM/GOLD + HIGH-momentum + near-miss high-vol)"
            )

        # Send notifications with CSV attachment
        # V9-C filter: PREMIUM/GOLD + Minervini≥7  (classic breakout)
        # BOUNCE filter: GOLD only (analysis: BOUNCE PREMIUM avg -1.97% P&L; GOLD avg +1.72%)
        mode_desc = MODES[args.mode]['description']
        watchlist_name = Path(args.file).stem  # Get filename without extension
        subject_prefix = "⚠️ " if market_warning else "🚨 "
        warning_line = f"\n\n⚠️ {market_warning}" if market_warning else ""
        v9c_signals = [
            s for s in results
            if s.get('Quality') in ('GOLD', 'PREMIUM')
            and (s.get('MinerviniScore') or 0) >= 7
        ]
        bounce_signals = [
            s for s in results
            if s.get('Quality') == 'GOLD'   # GOLD only — PREMIUM bounces have negative expected value
            and s.get('Type') == 'BOUNCE'
            and s not in v9c_signals
        ]
        # CONTINUATION and SMA20_CROSS signals (no Minervini requirement)
        alt_signals = [
            s for s in results
            if s.get('Quality') in ('GOLD', 'PREMIUM')
            and s.get('Type') in ('CONTINUATION', 'SMA20_CROSS')
            and s not in v9c_signals and s not in bounce_signals
        ]
        notify_signals = v9c_signals + bounce_signals + alt_signals
        if notify_signals:
            n_v9c   = len(v9c_signals)
            n_bnc   = len(bounce_signals)
            n_alt   = len(alt_signals)
            tag_parts = []
            if n_v9c:  tag_parts.append(f"{n_v9c} V9-C")
            if n_bnc:  tag_parts.append(f"{n_bnc} BOUNCE")
            if n_alt:  tag_parts.append(f"{n_alt} ALT")
            tag = ' + '.join(tag_parts)
            notifier.send_all(
                subject=f"{subject_prefix}{args.mode.upper()} Signals [{watchlist_name}]"
                        f" ({tag} of {len(results)} total)",
                message=f"{tag} signals (PREMIUM/GOLD) from {len(results)} total"
                        f" — {watchlist_name}{warning_line}",
                signals=notify_signals,
                csv_path=output_file
            )
        else:
            logger.info(f"No PREMIUM/GOLD signals to notify ({len(results)} total signals below threshold)")
        
        # Scalping warnings
        if args.mode == 'scalping':
            logger.warning("\n⚠️  SCALPING REMINDERS:")
            logger.warning("   • Exit at target/stop - no exceptions")
            logger.warning("   • Monitor spread widening")
            logger.warning("   • Close all before market close")
            logger.warning("   • Watch for news events")
    else:
        logger.info("No signals found")
        # Clear premium export file so subsequent scans don't use stale tickers
        if getattr(args, 'export_premium', None):
            with open(args.export_premium, 'w') as f:
                f.write('')
            logger.info(f"No signals — cleared {args.export_premium}")

    # Show and save rejections (near misses)
    rejections = orchestrator.get_rejection_reasons()
    if rejections:
        logger.info(f"\n{'='*90}")
        logger.info(f" NEAR MISSES: {len(rejections)} stocks broke out but were rejected")
        logger.info(f"{'='*90}")
        
        # Show top 10 rejections with score details
        rej_df = pd.DataFrame(rejections[:10])
        if 'momentum' in rej_df.columns:
            display_cols = ['symbol', 'price', 'vol_ratio', 'momentum', 'conviction', 'rsi', 'reasons']
            display_cols = [c for c in display_cols if c in rej_df.columns]
            print(rej_df[display_cols].to_string(index=False))
        else:
            print(rej_df.to_string(index=False))
        
        if len(rejections) > 10:
            logger.info(f"   ... and {len(rejections) - 10} more")
        
        orchestrator.save_results(rejections, args.mode, 'rejections')
        logger.info("   (Saved near-miss signals for analysis)")

        # Update near_miss_watch.txt — coiling stocks within 0.5% of breakout level.
        # Monitored every 15 min by monitor_watch.py --near-miss for intraday trigger alerts.
        _nm_file = Path('scanner_output/lists/near_miss_watch.txt')
        _nm_file.parent.mkdir(parents=True, exist_ok=True)
        _nm_date = datetime.now(_NY_TZ).strftime('%Y-%m-%d')
        # Load existing entries (dedup by symbol — keep most recent)
        _existing_nm = {}
        if _nm_file.exists():
            for _ln in _nm_file.read_text().splitlines():
                _ln = _ln.strip()
                if _ln:
                    _parts = _ln.split(',')
                    if len(_parts) >= 1:
                        _existing_nm[_parts[0]] = _ln
        # Add/refresh near-miss entries from this scan
        _added_nm = 0
        for _rej in rejections:
            if 'Near miss' in _rej.get('reasons', '') and _rej.get('prev_high', 0) > 0:
                _sym = _rej['symbol']
                _existing_nm[_sym] = f"{_sym},{_rej['prev_high']:.2f},{_rej.get('mode', args.mode)},{_nm_date}"
                _added_nm += 1
        if _added_nm:
            _nm_file.write_text('\n'.join(_existing_nm.values()) + '\n')
            logger.info(f"   Updated near_miss_watch.txt: {len(_existing_nm)} coiling stocks (+{_added_nm} new)")
    
    return results


async def run_exit_mode(orchestrator: ScannerOrchestrator, args, notifier: Notifier):
    """Execute exit evaluation mode"""
    # Load positions
    positions = get_positions_from_file(args.exit_file)
    if not positions:
        logger.error("No positions loaded")
        return []

    # Only evaluate exits for PREMIUM and GOLD positions
    _quality_rank = {'GOLD': 4, 'PREMIUM': 3, 'HIGH': 2, 'STANDARD': 1, 'REJECT': 0}
    positions = [p for p in positions if _quality_rank.get(p.get('quality', 'PREMIUM'), 0) >= 3]
    if not positions:
        logger.info("No PREMIUM/GOLD positions to evaluate for exit")
        return []

    logger.info(f"Loaded {len(positions)} PREMIUM/GOLD positions from {args.exit_file}")
    
    # Determine regime
    sample_mode = positions[0]['mode']
    if sample_mode != 'scalping':
        spy_perf, spy_vol = await orchestrator.market_data.get_spy_performance(
            args.timeframe, MODES[sample_mode]['lookback']
        )
        regime = classify_market_regime(spy_perf, spy_vol)
        regime_desc = REGIME_CONFIG[regime]['description']
        logger.info(
            f"Market: SPY {spy_perf:.2%}, Vol {spy_vol:.2f}% | "
            f"Regime: {regime} ({regime_desc})"
        )
    else:
        regime = 'INTRADAY'
    
    # Evaluate exits
    exit_results = await orchestrator.evaluate_exits(positions, regime)
    
    # Display and save results
    if exit_results:

        # Deduplicate: skip exits already notified today
        exit_history_file = Path('scanner_output/.exit_history.json')
        today = datetime.now(_NY_TZ).strftime('%Y-%m-%d')
        notified_today = set()
        try:
            if exit_history_file.exists():
                hist = json.loads(exit_history_file.read_text())
                if hist.get('date') == today:
                    notified_today = set(hist.get('symbols', []))
        except Exception:
            pass

        # Filter out already-notified exits
        new_exits = [
            r for r in exit_results
            if r['Action'] != 'HOLD' and r['Symbol'] not in notified_today
        ]
        hold_exits = [r for r in exit_results if r['Action'] == 'HOLD']

        logger.info(f"\n{'='*70}")
        logger.info(f" EXIT EVALUATION: {len(exit_results)} positions "
                     f"({len(new_exits)} new actionable, {len(notified_today)} already notified)")
        logger.info(f"{'='*70}\n")

        df = pd.DataFrame(exit_results)
        print(df.to_string(index=False))

        exit_csv_path = orchestrator.save_results(exit_results, args.mode, 'exits')

        # Send exit notifications (only new actionable exits)
        if new_exits:
            notifier.send_exit_notification(exit_results, csv_path=exit_csv_path)

            # Record newly notified symbols
            new_symbols = [r['Symbol'] for r in new_exits]
            notified_today.update(new_symbols)
            try:
                exit_history_file.parent.mkdir(parents=True, exist_ok=True)
                exit_history_file.write_text(json.dumps({
                    'date': today,
                    'symbols': list(notified_today),
                }))
            except Exception as e:
                logger.debug(f"Exit history save failed: {e}")
        elif not hold_exits or len(hold_exits) == len(exit_results):
            logger.info("All positions HOLD — no exit notifications needed")
        else:
            logger.info("All actionable exits already notified today")
    else:
        logger.info("No exit decisions generated")

    return exit_results


async def run_combined_mode(orchestrator: ScannerOrchestrator, args, notifier: Notifier):
    """Run both scan and exit in same execution"""
    logger.info("="*70)
    logger.info(" COMBINED MODE: BREAKOUT SCAN + EXIT EVALUATION")
    logger.info("="*70)
    
    # Run breakout scan
    logger.info("\n[1/2] Running breakout scan...")
    scan_results = await run_scan_mode(orchestrator, args, notifier)
    
    # Run exit evaluation
    logger.info("\n[2/2] Running exit evaluation...")
    exit_results = await run_exit_mode(orchestrator, args, notifier)
    
    # Summary
    logger.info(f"\n{'='*70}")
    logger.info(" COMBINED SCAN SUMMARY")
    logger.info(f"{'='*70}")
    logger.info(f"New signals found: {len(scan_results)}")
    logger.info(f"Positions evaluated: {len(exit_results)}")
    
    actionable_exits = [r for r in exit_results if r['Action'] != 'HOLD']
    if actionable_exits:
        logger.info(f"⚠️  {len(actionable_exits)} positions need attention!")


async def run_monitor_mode(orchestrator: ScannerOrchestrator, args, notifier,
                          from_portfolio: bool = False,
                          from_auto_portfolio: bool = False):
    """Monitor open positions: fetch current prices, alert on drops"""
    import pandas as pd

    # Load positions from portfolio.json, auto_portfolio.json, or CSV files
    all_positions = []
    if from_auto_portfolio:
        import auto_portfolio as _ap
        from config import MODES as _MODES
        _ap_data = _ap.load()
        for p in _ap_data.get('positions', []):
            mode = p.get('mode', 'swing')
            all_positions.append({
                'symbol':    p['symbol'],
                'mode':      mode,
                'entry':     p['entry_price'],
                'stop':      p['stop'],
                'target':    p['target'],
                'timeframe': _MODES.get(mode, _MODES['swing'])['default_timeframe'],
            })
        logger.info(f"Loaded {len(all_positions)} positions from auto_portfolio.json")
    if from_portfolio:
        from portfolio import Portfolio
        _portfolio = Portfolio()
        manual_positions = _portfolio.get_positions_as_exit_format()
        all_positions.extend(manual_positions)
        logger.info(f"Loaded {len(manual_positions)} positions from portfolio.json")
    else:
        if not args.monitor:
            logger.warning("run_monitor_mode called without from_auto_portfolio/from_portfolio and no --monitor path")
            return
        for fpath in args.monitor.split(','):
            fpath = fpath.strip()
            if not fpath:
                continue
            positions = get_positions_from_file(fpath)
            if positions:
                logger.info(f"Loaded {len(positions)} positions from {fpath}")
                all_positions.extend(positions)
            else:
                logger.info(f"No positions in {fpath}")

    if not all_positions:
        logger.info("No positions to monitor")
        return

    # Deduplicate by symbol (keep first occurrence)
    seen = set()
    unique_positions = []
    for pos in all_positions:
        if pos['symbol'] not in seen:
            seen.add(pos['symbol'])
            unique_positions.append(pos)
    all_positions = unique_positions

    logger.info(f"\n{'='*70}")
    logger.info(f" PORTFOLIO MONITOR: {len(all_positions)} positions")
    logger.info(f"{'='*70}\n")

    # Fetch current prices
    price_map = {}
    for pos in all_positions:
        symbol = pos['symbol']
        price = await orchestrator.market_data.get_current_price(symbol)
        if price is None:
            logger.warning(f"Could not fetch price for {symbol}")
        else:
            price_map[symbol] = price

    # Trail stops upward (V9: activate trailing when TP reached)
    # Mode-aware trailing: longterm 5%, swing 3%, daytrade/scalping 1%
    _TRAIL_PCT_BY_MODE = {
        'longterm': 0.05, 'swing': 0.03,
        'daytrade': 0.01, 'scalping': 0.01,
    }
    if from_auto_portfolio:
        # Update stops in auto_portfolio.json directly
        import auto_portfolio as _ap
        _ap_data_live = _ap.load()
        _pos_map = {p['symbol']: p for p in _ap_data_live.get('positions', [])}
        for pos in all_positions:
            symbol = pos['symbol']
            current_price = price_map.get(symbol)
            if current_price is None:
                continue
            mode = pos.get('mode', 'swing').lower()
            trail_pct = _TRAIL_PCT_BY_MODE.get(mode, 0.03)
            new_trailing_stop = round(current_price * (1.0 - trail_pct), 2)
            # Never let trailing stop exceed entry price (lock in risk, not loss)
            entry = pos.get('entry', pos.get('entry_price', 0))
            if entry > 0 and new_trailing_stop > entry:
                new_trailing_stop = min(new_trailing_stop, round(entry * 1.005, 2))
            if new_trailing_stop > pos['stop']:
                logger.info(f"  ↑ {symbol} stop: ${pos['stop']:.2f} → ${new_trailing_stop:.2f} (price ${current_price:.2f}, {mode} trail {trail_pct:.0%})")
                if symbol in _pos_map:
                    _pos_map[symbol]['stop'] = new_trailing_stop
                pos['stop'] = new_trailing_stop
        # Persist updated stops
        _ap._save(_ap_data_live)
    elif from_portfolio:
        # Update stops in portfolio.json directly
        for pos in all_positions:
            symbol = pos['symbol']
            current_price = price_map.get(symbol)
            if current_price is None:
                continue

            # V9: Check if target reached — activate trailing stop
            target = pos.get('target', 0)
            if target > 0 and current_price >= target and not pos.get('tp_reached', False):
                _portfolio.mark_tp_reached(symbol)
                pos['tp_reached'] = True
                # Compute ATR-based trailing stop (2.0 ATR)
                try:
                    from yfinance_adapter import YFinanceAdapter
                    yf = YFinanceAdapter()
                    hist = yf.get_historical_data(symbol, '1 day', lookback_bars=20)
                    if hist is not None and len(hist) >= 14:
                        h_l = hist['high'] - hist['low']
                        h_pc = abs(hist['high'] - hist['close'].shift(1))
                        l_pc = abs(hist['low'] - hist['close'].shift(1))
                        tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
                        atr = tr.tail(14).mean()
                        new_stop = round(current_price - (atr * 2.0), 2)
                        if new_stop > pos['stop']:
                            logger.info(f"  🎯 {symbol} TP reached @ ${current_price:.2f} — trail stop: ${pos['stop']:.2f} → ${new_stop:.2f}")
                            _portfolio.update_stop(symbol, new_stop)
                            pos['stop'] = new_stop
                        continue
                except Exception as e:
                    logger.debug(f"ATR calc failed for {symbol}: {e}")

            # Mode-aware trailing stop
            mode = pos.get('mode', 'swing').lower()
            trail_pct = _TRAIL_PCT_BY_MODE.get(mode, 0.03)
            new_trailing_stop = round(current_price * (1.0 - trail_pct), 2)
            if new_trailing_stop > pos['stop']:
                logger.info(f"  ↑ {symbol} stop: ${pos['stop']:.2f} → ${new_trailing_stop:.2f} (price ${current_price:.2f}, {mode} trail {trail_pct:.0%})")
                _portfolio.update_stop(symbol, new_trailing_stop)
                pos['stop'] = new_trailing_stop
    else:
        if args.monitor:
            from utils import update_position_stops
            for fpath in args.monitor.split(','):
                fpath = fpath.strip()
                if not fpath:
                    continue
                updated = update_position_stops(fpath, price_map)
                for u in updated:
                    logger.info(f"  ↑ {u['symbol']} stop: ${u['old_stop']:.2f} → ${u['new_stop']:.2f} (price ${u['price']:.2f})")

        # Reload positions after stop updates
        all_positions = []
        if args.monitor:
            for fpath in args.monitor.split(','):
                fpath = fpath.strip()
                if fpath:
                    all_positions.extend(get_positions_from_file(fpath))
        # Deduplicate again
        seen = set()
        unique_positions = []
        for pos in all_positions:
            if pos['symbol'] not in seen:
                seen.add(pos['symbol'])
                unique_positions.append(pos)
        all_positions = unique_positions

    # Calculate status with updated stops
    dashboard = []
    alerts = []

    for pos in all_positions:
        symbol = pos['symbol']
        price = price_map.get(symbol)
        if price is None:
            continue

        entry = pos['entry']
        stop = pos['stop']
        target = pos['target']

        pnl_pct = ((price - entry) / entry) * 100
        dist_to_stop_pct = ((price - stop) / price) * 100 if price > 0 else 0
        progress_pct = ((price - entry) / (target - entry)) * 100 if target != entry else 0

        # Classify status (NEAR_STOP at 0.5% since stops trail at 1%)
        if price <= stop:
            status = 'HIT_STOP'
        elif dist_to_stop_pct < 0.5:
            status = 'NEAR_STOP'
        elif pnl_pct < -2:
            status = 'FALLING'
        else:
            status = 'OK'

        row = {
            'Symbol': symbol,
            'mode': pos['mode'],
            'current': price,
            'entry': entry,
            'stop': stop,
            'target': target,
            'pnl_pct': pnl_pct,
            'dist_stop': dist_to_stop_pct,
            'progress': progress_pct,
            'status': status,
        }
        dashboard.append(row)

        # Only alert on actionable statuses (HIT_STOP, NEAR_STOP)
        # FALLING is logged in dashboard but doesn't trigger notification
        if status in ('HIT_STOP', 'NEAR_STOP'):
            alerts.append(row)

    # Display dashboard
    if dashboard:
        df = pd.DataFrame(dashboard)
        display_cols = ['Symbol', 'mode', 'current', 'entry', 'stop', 'target',
                        'pnl_pct', 'dist_stop', 'status']
        df_display = df[display_cols].copy()
        df_display['pnl_pct'] = df_display['pnl_pct'].apply(lambda x: f"{x:+.2f}%")
        df_display['dist_stop'] = df_display['dist_stop'].apply(lambda x: f"{x:.1f}%")
        df_display['current'] = df_display['current'].apply(lambda x: f"${x:.2f}")
        df_display['entry'] = df_display['entry'].apply(lambda x: f"${x:.2f}")
        df_display['stop'] = df_display['stop'].apply(lambda x: f"${x:.2f}")
        df_display['target'] = df_display['target'].apply(lambda x: f"${x:.2f}")
        print(df_display.to_string(index=False))

    # Send alerts if positions need attention
    # Track which alerts we've already sent to avoid spam (daily reset)
    from pathlib import Path
    from zoneinfo import ZoneInfo
    _today_str = datetime.now(ZoneInfo('America/New_York')).strftime('%Y%m%d')
    alert_history_file = Path(f'scanner_output/state/.monitor_alerts_{_today_str}.txt')
    alert_history_file.parent.mkdir(parents=True, exist_ok=True)

    # Cleanup old alert history files (keep today only)
    for old_f in Path('scanner_output/state').glob('.monitor_alerts*.txt'):
        if old_f.name != alert_history_file.name:
            old_f.unlink(missing_ok=True)

    # Load history of previously alerted symbols
    alerted_symbols = set()
    if alert_history_file.exists():
        with open(alert_history_file, 'r') as f:
            alerted_symbols = set(line.strip() for line in f if line.strip())

    # Filter to only NEW alerts (not previously alerted)
    new_alerts = [a for a in alerts if a['Symbol'] not in alerted_symbols]

    if new_alerts:
        logger.warning(f"\n⚠️  {len(new_alerts)} NEW alert(s), {len(alerts) - len(new_alerts)} already notified")
        if from_auto_portfolio:
            portfolio_label = "Auto Portfolio"
        elif from_portfolio:
            portfolio_label = "Manual Portfolio"
        else:
            portfolio_label = "Portfolio"
        # Disabled: only BREAKOUT (buy) and EXIT (sell) notifications are active
        # notifier.send_monitor_alert(new_alerts, dashboard, portfolio_label=portfolio_label)

        # Record newly alerted symbols
        with open(alert_history_file, 'a') as f:
            for a in new_alerts:
                f.write(f"{a['Symbol']}\n")
    elif alerts:
        logger.info(f"\n{len(alerts)} position(s) still dropping (already notified)")
    else:
        logger.info("\nAll positions OK")


async def run_simulation_mode(args, ib, data_source='auto'):
    """Run historical simulation on watchlist with real data"""
    from datetime import datetime
    import pandas as pd
    from yfinance_adapter import YFinanceAdapter
    
    logger.info("=" * 70)
    logger.info(" HISTORICAL SIMULATION MODE")
    logger.info("=" * 70)
    
    # Load watchlist
    watchlist = get_watchlist_from_file(args.file)
    if not watchlist:
        logger.error("No symbols loaded from watchlist")
        return
    
    logger.info(f"📊 Loaded {len(watchlist)} symbols from {args.file}")
    logger.info(f"📊 Simulation period: {args.sim_start} to {args.sim_end}")
    logger.info(f"📊 Mode: {args.mode.upper()}")
    
    # Determine data source
    use_yfinance = False
    if data_source == 'yfinance':
        use_yfinance = True
        logger.info(f"📊 Data source: Yahoo Finance (yfinance)")
    elif data_source == 'mock':
        logger.info(f"📊 Data source: Mock (random data)")
    elif data_source == 'auto' or data_source == 'ib':
        # Check if IB is actually connected (not mock)
        if hasattr(ib, 'mode') and ib.mode:
            # It's a mock connection, use yfinance instead
            use_yfinance = True
            logger.info(f"📊 Data source: Yahoo Finance (IB not available)")
        else:
            logger.info(f"📊 Data source: Interactive Brokers")
    
    # Create simulation
    # Import portfolio config
    from config import PORTFOLIO
    
    # Create simulation with portfolio config
    sim = SimulationMode(
        start_date=args.sim_start,
        end_date=args.sim_end,
        initial_capital=PORTFOLIO['initial_capital'],
        max_position_pct=PORTFOLIO['max_position_pct'],
        max_risk_pct=PORTFOLIO['max_risk_pct'],
        use_trailing_stop=PORTFOLIO.get('use_trailing_stop', False),
        trailing_stop_atr_mult=PORTFOLIO.get('trailing_stop_atr_mult', 2.0),
        trailing_stop_activation_pct=PORTFOLIO.get('trailing_stop_activation_pct', 0.0)
    )
    
    # Create orchestrator or yfinance adapter
    if use_yfinance:
        yf_adapter = YFinanceAdapter()
        logger.info("📊 Using Yahoo Finance for historical data")
    else:
        orchestrator = ScannerOrchestrator(ib)
    
    # Get timeframe
    timeframe = args.timeframe
    
    logger.info("📊 Scanning historical data for breakout signals...")
    
    # Generate signals by scanning historical data
    all_signals = []
    
    # Parse dates
    start_date = pd.to_datetime(args.sim_start)
    end_date = pd.to_datetime(args.sim_end)
    
    # For each symbol, scan historical data and find breakouts
    all_historical_data = {}
    
    for idx, symbol in enumerate(watchlist, 1):
        print(f"[{idx}/{len(watchlist)}] Scanning {symbol:6}...", end="\r")
        
        try:
            # Get historical data - need extra history for 200-day SMA and other indicators
            # Fetch from 2 years before simulation start to have enough context
            import datetime
            sim_start_dt = pd.to_datetime(args.sim_start)
            data_start = (sim_start_dt - pd.DateOffset(years=2)).strftime('%Y-%m-%d')
            
            if use_yfinance:
                df = yf_adapter.get_historical_data(
                    symbol, timeframe, 
                    start_date=data_start,  # Start 2 years earlier for context
                    end_date=args.sim_end
                )
            else:
                df = await orchestrator.market_data.get_historical_data(symbol, timeframe)
            
            # Cache full historical data for later use
            if df is not None:
                all_historical_data[symbol] = df
            
            if df is None or len(df) < 50:
                continue
            
            # Filter to simulation period for signal generation
            df_sim = df[(df.index >= start_date) & (df.index <= end_date)]
            
            if len(df_sim) < 10:
                continue
            
            # Scan each bar in the period for breakout signals
            for i in range(len(df_sim)):
                # Get data up to this point
                current_date = df_sim.index[i]
                df_up_to_date = df[df.index <= current_date]
                
                if len(df_up_to_date) < 50:
                    continue
                
                # Detect breakout
                if use_yfinance:
                    # Create temporary detector for yfinance data
                    from scanner import BreakoutDetector
                    detector = BreakoutDetector()
                    signal = detector.detect(
                        df_up_to_date,
                        symbol,
                        args.mode,
                        timeframe,
                        spy_perf=0.0,
                        regime='NORMAL',
                        use_scoring=True,
                        use_legacy_momentum=False,
                        use_v4_overextension=True,
                        vol_thresh=args.vol,
                        atr_mult=args.atr,
                        lookback=args.lookback
                    )
                else:
                    signal = orchestrator.detector.detect(
                        df_up_to_date,
                        symbol,
                        args.mode,
                        timeframe,
                        spy_perf=0.0,
                        regime='NORMAL',
                        use_scoring=True,
                        use_legacy_momentum=False,
                        use_v4_overextension=True,
                        vol_thresh=args.vol,
                        atr_mult=args.atr,
                        lookback=args.lookback
                    )
                
                if signal:
                    # Convert to simulation signal format (quantity will be calculated by risk management)
                    sim_signal = {
                        'date': current_date.strftime('%Y-%m-%d'),
                        'symbol': symbol,
                        'action': 'BUY',
                        'price': float(signal['Price']),
                        'stop_loss': float(signal['Stop']),
                        'take_profit': float(signal['Target'])
                    }
                    all_signals.append(sim_signal)
                    
                    # Get period start price for context
                    period_start_price = df_sim.iloc[0]['close'] if len(df_sim) > 0 else signal['Price']
                    
                    # Calculate gain from signal entry to end of simulation period
                    # This shows how the trade would have performed
                    end_price = df_sim.iloc[-1]['close'] if len(df_sim) > 0 else signal['Price']
                    gain_from_entry = ((end_price / signal['Price']) - 1) * 100
                    
                    logger.info(
                        f"   ✓ Signal: {symbol} @ ${signal['Price']:.2f} on {current_date.date()} "
                        f"(period start: ${period_start_price:.2f}, gain from entry: {gain_from_entry:+.1f}%, quality: {signal['Quality']})"
                    )
                    
                    # Don't break - continue scanning for more signals
                    # (removed the break statement to capture all signals)
        
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"Failed to scan {symbol}: {e}")
            continue
    
    print()  # Clear progress line
    
    logger.info(f"📊 Generated {len(all_signals)} signals from historical data")
    
    if not all_signals:
        logger.warning("No signals found in simulation period. Try:")
        logger.warning("  - Longer date range")
        logger.warning("  - Different mode (swing, daytrade, etc.)")
        logger.warning("  - More symbols in watchlist")
        return
    
    # Collect end prices and historical data for each symbol (for closing positions and trailing stops)
    end_prices = {}
    historical_data = {}
    
    for symbol in set(sig['symbol'] for sig in all_signals):
        try:
            # key in all_historical_data might be different if symbol format changed? 
            # But we used 'symbol' variable in loop, so it should be same.
            df = all_historical_data.get(symbol)
            
            if df is None:
                # Fallback if not cached (shouldn't happen if signal generated)
                if use_yfinance:
                     df = yf_adapter.get_historical_data(symbol, timeframe, start_date=data_start, end_date=args.sim_end)
                else:
                     df = await orchestrator.market_data.get_historical_data(symbol, timeframe)
            
            if df is not None and len(df) > 0:
                # Get the last price in the simulation period
                df_sim = df[(df.index >= start_date) & (df.index <= end_date)]
                if len(df_sim) > 0:
                    end_prices[symbol] = float(df_sim.iloc[-1]['close'])
                    # Store full historical data for trailing stop calculations
                    historical_data[symbol] = df
        except Exception as e:
            logger.debug(f"Could not get end price for {symbol}: {e}")
    
    # Run simulation with end prices and historical data
    report = sim.run_simulation(all_signals, end_prices=end_prices, historical_data=historical_data)
    
    # Save report
    from pathlib import Path
    sim_dir = Path(OUTPUT_DIR, 'simulation_report')
    sim_dir.mkdir(parents=True, exist_ok=True)
    
    report_file = sim_dir / f'simulation_report_{args.sim_start}_{args.sim_end}.json'
    sim.trader.save_report(str(report_file))
    
    logger.info("✓ Simulation complete!")
    
    return report




async def main():
    """Main execution flow"""
    parser = argparse.ArgumentParser(
        description='Breakout Scanner for Interactive Brokers',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Long-term position trading (weekly bars)
  python main.py watchlist.txt --mode longterm
  
  # Swing trading scan
  python main.py watchlist.txt --mode swing
  
  # Day trade scan with custom volume threshold
  python main.py watchlist.txt --mode daytrade --vol 1.5
  
  # Scalping (1min bars)
  python main.py watchlist.txt --mode scalping
  
  # Exit evaluation only
  python main.py watchlist.txt --mode swing --exit-file positions.csv
  
  # Combined: scan + exit evaluation
  python main.py watchlist.txt --mode swing --exit-file positions.csv --both
  
  # Cron mode (silent, notifications only)
  python main.py watchlist.txt --mode swing --cron
  
  # Historical simulation (scans watchlist for signals)
  python main.py watchlist.txt --mode swing --simulate --sim-start 2025-01-01 --sim-end 2025-12-31
  
  # Live trading (CAREFUL!)
  python main.py watchlist.txt --mode swing --live
        """
    )
    
    parser.add_argument('file', nargs='?', default='input/1_26_Setups.txt',
                        help='Path to watchlist file (default: input/1_26_Setups.txt)')
    parser.add_argument(
        '--mode',
        choices=['longterm', 'swing', 'daytrade', 'scalping'],
        default='swing',
        help='Trading mode'
    )
    parser.add_argument('--vol', type=float, help='Volume threshold override')
    parser.add_argument('--atr', type=float, help='ATR multiplier override')
    parser.add_argument('--lookback', type=int, help='Lookback period override')
    parser.add_argument('--tf', type=str, help='Timeframe override')
    parser.add_argument(
        '--live',
        action='store_true',
        help='Use live account (default: paper)'
    )
    parser.add_argument(
        '--exit-file',
        type=str,
        help='CSV with positions for exit evaluation'
    )
    parser.add_argument(
        '--both',
        action='store_true',
        help='Run both breakout scan and exit evaluation'
    )
    parser.add_argument(
        '--cron',
        action='store_true',
        help='Cron mode: minimal output, send notifications only'
    )
    parser.add_argument(
        '--notify',
        action='store_true',
        help='Enable notifications (requires config.py setup)'
    )
    parser.add_argument(
        '--level2',
        action='store_true',
        help='Enable Level 2 (Market Depth) analysis'
    )
    parser.add_argument(
        '--mock',
        action='store_true',
        help='Use mock trading (no real IB connection needed)'
    )
    parser.add_argument(
        '--mock-mode',
        choices=['realistic', 'optimistic', 'pessimistic'],
        default='realistic',
        help='Mock trading simulation mode'
    )
    parser.add_argument(
        '--simulate',
        action='store_true',
        help='Run historical simulation'
    )
    parser.add_argument(
        '--sim-start',
        type=str,
        help='Simulation start date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--sim-end',
        type=str,
        help='Simulation end date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--sim-data-source',
        choices=['auto', 'ib', 'yfinance', 'mock'],
        default='auto',
        help='Data source for simulation: auto (try IB, fallback to yfinance), ib (IB only), yfinance (Yahoo Finance), mock (random data)'
    )
    parser.add_argument(
        '--sim-mock',
        action='store_true',
        help='Use mock data for simulation (shorthand for --sim-data-source mock)'
    )
    parser.add_argument(
        '--bounce',
        action='store_true',
        help='Also detect bounce/recovery signals (oversold stocks showing strong recovery)'
    )
    parser.add_argument(
        '--sector-buzz',
        action='store_true',
        help='Run sector buzz analysis before scan (market context report)'
    )
    parser.add_argument(
        '--sentiment',
        action='store_true',
        help='Enrich each signal with web sentiment data (requires TAVILY_API_KEY)'
    )
    parser.add_argument(
        '--auto-positions',
        type=str,
        metavar='FILE',
        help='Auto-append PREMIUM signals to a positions CSV (for mock/test exit evaluation)'
    )
    parser.add_argument(
        '--export-premium',
        type=str,
        metavar='FILE',
        help='Export PREMIUM signal tickers to a watchlist file (for subsequent re-evaluation scans)'
    )
    parser.add_argument(
        '--export-momentum-watch',
        type=str,
        metavar='FILE',
        help='Export PREMIUM/GOLD + HIGH-momentum + near-miss high-vol tickers for Phase 2 re-scans'
    )
    parser.add_argument(
        '--monitor',
        type=str,
        metavar='FILES',
        help='Monitor positions for price drops. Comma-separated CSV files. '
             'Example:  scanner_output/lists/positions_swing_mock.csv,scanner_output/lists/positions_daytrade_mock.csv'
    )
    parser.add_argument(
        '--portfolio-report',
        action='store_true',
        help='Send daily portfolio status email and exit. Use in cron: '
             'python breakout_scanner.py dummy --portfolio-report'
    )
    parser.add_argument(
        '--exit-from-portfolio',
        action='store_true',
        help='Evaluate exits for open positions in portfolio.json (instead of --exit-file CSV)'
    )
    parser.add_argument(
        '--monitor-portfolio',
        action='store_true',
        help='Monitor positions from portfolio.json (manual portfolio)'
    )
    parser.add_argument(
        '--monitor-auto-portfolio',
        action='store_true',
        help='Monitor positions from auto_portfolio.json (auto portfolio)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable DEBUG logging — prints per-ticker skip reasons to the log'
    )
    parser.add_argument(
        '--cache-file',
        type=str,
        metavar='PATH',
        help='Load market data from cache file (used by job_launcher.py for batch execution)'
    )

    args = parser.parse_args()

    # Setup logging
    if args.cron:
        # Cron mode: only errors to console, everything to log file
        from config import OUTPUT_DIR
        
        log_dir = Path(OUTPUT_DIR, 'logs')
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f'scanner_{datetime.now(_NY_TZ):%Y%m%d}.log'
        
        log_level = SCAN_LEVEL if args.debug else logging.INFO
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s | %(levelname)s | %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stderr)  # Only errors to console
            ]
        )
        logging.getLogger().handlers[1].setLevel(logging.ERROR)
        for _lib in _QUIET_LIBS + ['ib_insync']:
            logging.getLogger(_lib).setLevel(logging.WARNING)
    else:
        setup_logging(debug=getattr(args, 'debug', False))

    # Attach structured CSV handler for SCAN-level decisions (learning loop)
    attach_scan_csv_handler(mode=getattr(args, 'mode', ''))

    # Load cached market data if provided (from job_launcher.py batch execution)
    cached_market_data = None
    if args.cache_file:
        import pickle
        try:
            with open(args.cache_file, 'rb') as f:
                cached_market_data = pickle.load(f)
            logger.info(f"✓ Loaded cached market data from {args.cache_file}")
            logger.info(f"  Cached at: {cached_market_data.timestamp}")
            logger.info(f"  Market condition: {cached_market_data.market_condition.upper()}")
            logger.info(f"  Symbols available: {len(cached_market_data.symbols)}")
        except Exception as e:
            logger.warning(f"Failed to load cache from {args.cache_file}: {e}")
            logger.info("Will proceed with fresh data fetch")

    # Get timeframe
    if args.tf:
        args.timeframe = args.tf
    else:
        args.timeframe = MODES[args.mode]['default_timeframe']
    
    # Scalping warnings
    if args.mode == 'scalping' and args.live and not args.mock:
        logger.warning("⚠️  SCALPING ON LIVE ACCOUNT!")
        if not args.cron:
            response = input("Type 'YES' to continue: ")
            if response != 'YES':
                logger.info("Aborted by user")
                return
    
    # Scalping always forces LIVE-first, so only warn if explicitly paper (no --live, no scalping override)
    if args.mode == 'scalping' and not args.live and not args.cron and not args.mock:
        logger.info("ℹ️  Scalping: forcing IB LIVE connection (port %d)", IB_LIVE_PORT)
    
    # Mock mode info
    if args.mock:
        logger.info("🔧 MOCK TRADING MODE")
        logger.info(f"   Simulation: {args.mock_mode}")
        logger.info("   No real orders will be placed")
        logger.info("   Perfect for testing strategies!")
        
        # Scalping warning for mock mode
        if args.mode == 'scalping':
            logger.warning("")
            logger.warning("⚠️  SCALPING + MOCK MODE WARNING:")
            logger.warning("   • yfinance provides limited 1-min data (5 days only)")
            logger.warning("   • No real-time bid/ask spreads available")
            logger.warning("   • VWAP calculations may be inaccurate")
            logger.warning("   • Signals may be unreliable or missing")
            logger.warning("")
            logger.warning("   For accurate scalping, use live IB connection:")
            logger.warning("   python3 breakout_scanner.py input/watchlist.txt --mode scalping --live")
            logger.warning("")
    
    # Simulation mode
    if args.simulate:
        if not args.sim_start or not args.sim_end:
            logger.error("❌ Simulation requires --sim-start and --sim-end dates")
            logger.error("   Example: --simulate --sim-start 2025-01-01 --sim-end 2025-12-31")
            return
        
        logger.info("📊 HISTORICAL SIMULATION MODE")
        logger.info(f"   Period: {args.sim_start} to {args.sim_end}")
        logger.info(f"   Mode: {args.mode.upper()}")
        
        # Determine data source
        data_source = 'mock' if args.sim_mock else args.sim_data_source
        
        # Connect to IB if needed
        if data_source == 'yfinance':
            # yfinance doesn't need IB connection, use mock placeholder
            ib = await connect_to_ib(args.live, mock=True, mock_mode='realistic')
        elif data_source == 'mock':
            # Mock mode
            ib = await connect_to_ib(args.live, mock=True, mock_mode='realistic')
        else:
            # 'ib' or 'auto' - try real IB connection, fallback to mock (which triggers yfinance)
            try:
                ib = await connect_to_ib(args.live, mock=False)
            except Exception as e:
                logger.warning(f"Could not connect to IB: {e}")
                logger.info("Falling back to yfinance data source")
                ib = await connect_to_ib(args.live, mock=True, mock_mode='realistic')
                data_source = 'yfinance'
        
        try:
            # Run simulation with selected data source
            return await run_simulation_mode(args, ib, data_source=data_source)
        finally:
            ib.disconnect()
            logger.info("✓ Disconnected from IB")
        
        return
    
    # Initialize notifier
    notifier = Notifier() if args.notify or args.cron else None
    if not notifier:
        # Create dummy notifier that does nothing
        class DummyNotifier:
            def send_all(self, *args, **kwargs): pass
            def send_exit_notification(self, *args, **kwargs): pass
            def send_monitor_alert(self, *args, **kwargs): pass
        notifier = DummyNotifier()
    
    # Portfolio daily report mode — send email and exit
    if getattr(args, 'portfolio_report', False):
        from portfolio import Portfolio
        p = Portfolio()
        report = p.send_daily_report()
        logger.info("Portfolio report sent. Exiting.")
        return

    # Sector buzz pre-scan report
    if getattr(args, 'sector_buzz', False):
        from sentiment import get_sector_buzz, format_sector_buzz
        logger.info("Running sector buzz analysis...")
        buzz_data = get_sector_buzz()
        print(format_sector_buzz(buzz_data))
        print()

    # Scalping always needs IB LIVE — force live-first connection order
    scalping_live = args.mode == 'scalping' and not args.mock
    ib = await connect_to_ib(args.live or scalping_live, args.mock, args.mock_mode)
    # Use Alpaca/yfinance fallback when there's no real IB connection
    yf_fallback = (ib is None) or isinstance(ib, MockIBConnection)
    if yf_fallback:
        if scalping_live:
            logger.error("❌ Scalping mode requires an IB LIVE connection — aborting")
            logger.error("   Start TWS/IBG and ensure port %d (LIVE) is open", IB_LIVE_PORT)
            return
        logger.info("📊 No real IB connection — using Alpaca/yfinance for market data")
    elif ib and not args.live:
        # No live data subscription — switch IB to 15-min delayed streaming data.
        # reqMarketDataType(3) prevents Error 10089 on reqMktData calls;
        # reqHistoricalDataAsync is unaffected by this setting.
        ib.reqMarketDataType(3)
        logger.info("📊 IB connected — using delayed market data (no live subscription)")

    try:
        # Create orchestrator with yfinance fallback if IB unavailable
        orchestrator = ScannerOrchestrator(ib, yf_fallback=yf_fallback)

        # Inject cached market data if provided (from job_launcher.py batch execution)
        if cached_market_data:
            orchestrator.market_data.set_cached_data(cached_market_data)

        # Load exit positions from portfolio.json + auto_portfolio.json when:
        # 1. --exit-from-portfolio is explicitly set, OR
        # 2. --both is set but no --exit-file provided (auto-fallback)
        if not args.exit_file and (
            getattr(args, 'exit_from_portfolio', False) or
            getattr(args, 'both', False)
        ):
            from portfolio import Portfolio
            import auto_portfolio

            exit_positions = []

            # 1. Load from portfolio.json
            p = Portfolio()
            exit_positions.extend(p.get_positions_as_exit_format())

            # 2. Load from auto_portfolio.json (merge, dedup by symbol)
            ap_data = auto_portfolio.load()
            existing_symbols = {ep['symbol'] for ep in exit_positions}
            for pos in ap_data.get('positions', []):
                if pos['symbol'] not in existing_symbols:
                    mode = pos.get('mode', 'swing')
                    exit_positions.append({
                        'symbol': pos['symbol'],
                        'mode': mode,
                        'entry': pos['entry_price'],
                        'entry_date': pos.get('date_added', ''),
                        'stop': pos['stop'],
                        'target': pos['target'],
                        'timeframe': MODES.get(mode, MODES['swing'])['default_timeframe'],
                        'quality': pos.get('quality', 'PREMIUM'),
                    })

            if not exit_positions:
                logger.info("Portfolio has no open positions — nothing to evaluate")
            else:
                import tempfile, csv
                tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='')
                fieldnames = ['symbol', 'mode', 'entry', 'entry_date', 'stop', 'target', 'timeframe', 'quality']
                writer = csv.DictWriter(tmp, fieldnames=fieldnames, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(exit_positions)
                tmp.close()
                args.exit_file = tmp.name
                logger.info(f"Loaded {len(exit_positions)} positions from portfolio.json + auto_portfolio.json for exit evaluation")

        # Determine execution mode
        if getattr(args, 'monitor_auto_portfolio', False) or getattr(args, 'monitor_portfolio', False):
            # Monitor auto_portfolio.json and/or portfolio.json in one pass
            await run_monitor_mode(
                orchestrator, args, notifier,
                from_auto_portfolio=getattr(args, 'monitor_auto_portfolio', False),
                from_portfolio=getattr(args, 'monitor_portfolio', False),
            )
        elif args.monitor:
            # Portfolio monitoring mode
            await run_monitor_mode(orchestrator, args, notifier)
        elif args.both and args.exit_file:
            # Combined mode
            await run_combined_mode(orchestrator, args, notifier)
        elif args.exit_file:
            # Exit evaluation only
            await run_exit_mode(orchestrator, args, notifier)
        else:
            # Breakout scan only
            await run_scan_mode(orchestrator, args, notifier)

    except Exception as e:
        logger.error(f"Scanner error: {e}", exc_info=True)
        if args.notify or args.cron:
            _mode_tag = getattr(args, 'mode', 'unknown').upper()
            _input_tag = Path(getattr(args, 'file', '') or '').name or 'unknown'
            # Disabled: only BREAKOUT (buy) and EXIT (sell) notifications are active
            # notifier.send_all(
            #     subject=f"❌ Scanner Error [{_mode_tag} | {_input_tag}]",
            #     message=f"Scanner encountered an error: {str(e)}"
            # )

    finally:
        if ib is not None:
            ib.disconnect()
            if isinstance(ib, MockIBConnection):
                try:
                    data_src = orchestrator.market_data.data_source_name
                except NameError:
                    data_src = 'yfinance'
                logger.info(f"🔧 Mock IB disconnected — data source: {data_src}")
            else:
                logger.info("Disconnected from IB")


if __name__ == "__main__":
    asyncio.run(main())
