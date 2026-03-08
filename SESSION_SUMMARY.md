# Session Summary — Mar 7–8, 2026

## Main Objectives

1. **Opening surge detection** — catch the first-minute momentum at 9:31 AM
2. **Diagnose why swing BOUNCE signals never sent Discord notifications**
3. **Comprehensive analysis of 2 weeks of production data** (Feb 24 – Mar 6) → improvement plan
4. **Implement all 6 improvement changes** from the plan
5. **Trade validation backtest** — replay all signals with new stops/targets via yfinance

---

## Key Code Changes

### `premarket_monitor.py`
- Added `_load_tv_watchlist(filepath)` — parses `EXCHANGE:SYMBOL` TradingView format, filters non-US exchanges, converts `.`→`-`
- Added `_SKIP_EXCHANGES` set (BINANCE, BSE, XETR, MIL, etc.)
- `PRIORITY_SYMBOLS` now loaded dynamically from `input/1_26_Setups.txt` with hardcoded `_PRIORITY_FALLBACK`
- Added `scan_opening_surge(symbols, now_et, vol_mult, min_move_pct)` — checks first 1-min bar at 9:30 for continuation
- Added `_run_opening_check(args, now_et)` — reads `premarket_watch.txt`, appends surging symbols to `momentum_watch_daytrade.txt`, sends Discord with ★ badge for PM-high breakouts
- Added `--open-check`, `--open-vol`, `--open-move` CLI flags

### `breakout_scanner.py`
- **BOUNCE notification bug fixed**: was filtering `MinerviniScore >= 7` for ALL signals — BOUNCE signals have no Minervini score (None) → all silently blocked
- Fix: split into `v9c_signals` (breakout + Minervini≥7) and `bounce_signals` (BOUNCE type, PREMIUM/GOLD, no Minervini req) → merged into `notify_signals`
- Near-miss watchlist promotion tightened: requires `near_miss AND (vol≥2× OR momentum≥70)` OR `high_vol AND momentum≥70`

### `scanner.py` — V13 scoring fixes
- **Import fix**: removed stale class-level `SCORING_WEIGHTS` (had `dist_confirm=10` vs optimizer's `24`, `minervini_template=15` vs `0`, etc.) → now imports `SCORING_WEIGHTS, SCORE_THRESHOLDS` from `config.py`
- **Quality thresholds fix**: hardcoded `PREMIUM≥80%` replaced with `SCORE_THRESHOLDS` lookup (`PREMIUM=69%` from optimizer)
- **Stale data guard**: rejects symbols where last bar date > 7 days ago (fixes the DAY symbol phantom)
- **Momentum override**: stocks with `Momentum_Score≥90 AND Vol_Ratio≥2.5× AND RSI<75` bypass consolidation requirement — unlocks META, STX, RDDT
- **V13 target upgrade**: after S/R levels computed, if `sr_data['nearest_resistance']` is > 2% above price and above current TP → use S/R as target
- **Fibonacci target in `_calculate_rr()`**: `tp = consol_high + 1.618 × (consol_high - consol_low)` from 20-bar range, used if > ATR target

### `config.py`
- Added `MOMENTUM_OVERRIDE = {'min_momentum': 90, 'min_vol_ratio': 2.5, 'max_rsi': 75}`

### `exit_evaluator.py` — BOUNCE exit strategy
- Added `signal_type: str = ''` parameter to `evaluate()`
- For BOUNCE signals: **skip** trend-broken exit (checks #2) and SMA150 check (#3) — they're below trend by design
- BOUNCE-specific exits added:
  - **Recovery**: `price >= trend_line` → `TRAIL` (priority 80) — mean reversion complete
  - **Failed bounce**: `price < swing_low_10 * 0.98` → `EXIT_FULL` (priority 95)
  - **Time decay**: `days_held >= 10 AND below trend AND R < 0.5` → `EXIT_FULL` (priority 75)
- **UnrealizedR overflow fixed**: `risk = max(entry - stop, entry * 0.01)` (was dividing by near-zero)

### `orchestrator.py`
- Passes `signal_type=pos.get('signal_type', '')` to `exit_evaluator.evaluate()`

### `mock_trader.py`
- Added `signal_type: str = ''` field to `MockTrade` dataclass
- `enter_trade()` now accepts and stores `signal_type`
- Simulation passes `signal_type=signal.get('signal_type', signal.get('type', ''))` when opening trades

### `cron_jobs.txt`
- Added `31 9 * * 1-5` entry for `premarket_monitor.py --open-check --notify` (opening surge at 9:31 AM)

### `validate_trades.py` *(new)*
- Standalone backtest replaying all signal CSVs from the last N days
- Fetches yfinance history per symbol → computes structural stop (swing low) + Fibonacci target
- **Flow A**: fixed SL + updated Fibonacci TP
- **Flow B**: 2×ATR trailing stop, no TP
- Outputs `scanner_output/backtests/validated_trades_YYYYMMDD_HHMMSS.csv` with one BUY + one SELL row per signal per flow

---

## Bugs Identified & Fixed

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| BOUNCE notifications silently blocked | `MinerviniScore >= 7` filter applied to all; BOUNCE has `None` | Split into v9c + bounce lists |
| Zero PREMIUM daytrade signals | `scanner.py` had own stale `SCORING_WEIGHTS` (PREMIUM threshold 80% vs config's 69%) | Delete class var, import from config |
| `DAY` phantom symbol (same price daily) | Delisted/stale ticker appearing in every scan | Added `last_bar_date > 7 days` guard |
| UnrealizedR = 7,354,993 | Stop == entry → division by near-zero | `risk = max(entry-stop, entry*0.01)` |
| META/RDDT/STX always rejected | Consolidation filter too strict; these break from momentum | Momentum override (score≥90, vol≥2.5×) |
| BOUNCE exits immediately on "trend broken" | Price is below SMA by design for mean-reversion plays | Skip trend-broken check for `signal_type='BOUNCE'` |

---

## Backtest Results (2-Week Validation, Feb 24 – Mar 6)

| Flow | Trades | WR | Avg P&L | Notes |
|------|--------|----|---------|----|
| A — Fixed SL + Fib TP | 781 | 33% | -1.08% | Market selloff period |
| **B — Trail 2×ATR** | 781 | 33% | **-0.48%** | Better by 0.60% avg |
| PREMIUM only (Flow A) | 65 | **43%** | **-0.40%** | Best quality tier |

Key insight: **PREMIUM filter is the #1 alpha driver** (43% WR vs 32-33% for HIGH/STANDARD). Poor overall performance reflects bearish market context (SPY -7.51%).

---

## Pending / Next Steps

1. **Run backtest in a bull period** — re-run `python validate_trades.py` after market stabilizes to get a fair assessment. Results are distorted by the tariff selloff.

2. **PREMIUM daytrade signals** — with the fixed weights (PREMIUM threshold now 69%), monitor tomorrow's scans to confirm PREMIUM signals now appear in daytrade mode.

3. **Trailing stop vs fixed TP experiment** — Flow B consistently outperforms. Consider making `--trail-only` the default for V9-C configuration.

4. **FinBERT backtest** — run `finbert_backtest.py` with `--finnhub-key` to compare configs A/B (no FinBERT) vs C/D (FinBERT promotion) on 2024 data.

5. **Opening surge in production** — the 9:31 AM cron is configured; validate next week that surging symbols correctly pre-seed Phase 1 scan.

6. **validate_trades.py improvements**:
   - Add PREMIUM-only filter mode (`--quality PREMIUM`)
   - Add portfolio-level equity curve (not just per-trade P&L)
   - Support re-running on older data by date range (`--from 2026-01-01 --to 2026-03-07`)

7. **V13 backtest in enhanced_backtest.py** — add V13 as a named configuration (Fibonacci target + S/R upgrade) to the A/B comparison suite.
