# stocksBreakout — Breakout Scanner for Interactive Brokers

Professional-grade algorithmic breakout scanner with Minervini Stage 2 scoring, VCP detection,
backtesting, and automated cron + Discord notifications.

> **Current version: V10** (VCP + Minervini Stage 2 scoring)
> Last updated: Feb 2026

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Quick Start](#quick-start)
3. [Trading Modes](#trading-modes)
4. [Detection Pipeline](#detection-pipeline)
5. [Signal Scoring (V10)](#signal-scoring-v10)
6. [CLI Reference](#cli-reference)
7. [Output Columns](#output-columns)
8. [Cron Schedule](#cron-schedule)
9. [Backtest Results](#backtest-results)
10. [Streamlit Dashboard](#streamlit-dashboard)
11. [Notifications](#notifications)
12. [IB Connection](#ib-connection)
13. [Troubleshooting](#troubleshooting)

---

## Project Structure

```
breakout_scanner.py    # CLI entry point — IB connection, async loop
orchestrator.py        # Scan coordination, market data, exit routing
scanner.py             # Core breakout detection & scoring (V10)
config.py              # Single source of truth for ALL parameters
market_data.py         # IB data fetching, caching, rate limiting
indicators.py          # ATR, VWAP, BB, RSI, MACD, ADX
exit_evaluator.py      # Position exit signal generation
pattern_recognition.py # 24 patterns: 12 chart + 11 candle + VCP
notifier.py            # Discord / Email / Telegram notifications
portfolio.py           # Position tracking, P&L, snapshots
monitor_watch.py       # 15-min momentum-watch monitor script
enhanced_backtest.py   # Multi-config A/B backtest (V1–V10 vs SPY)
upload_to_s3.py        # Sync scanner_output to S3 for Streamlit Cloud
app.py                 # Streamlit dashboard entry point
pages/
  signals_page.py      # Signal viewer (V9-C default filter)
  portfolio_page.py    # Portfolio P&L dashboard
input/
  ALL.txt              # Full watchlist (~1300 symbols)
  positions_swing_mock.csv   # Mock swing positions
  positions_daytrade_mock.csv
scanner_output/
  signals/             # Breakout signal CSVs
  exits/               # Exit evaluation CSVs
  rejections/          # Near-miss signals for review
  portfolio/           # Portfolio snapshots
  backtests/           # Backtest JSON results
  logs/                # Cron and scan logs
  cache/               # yfinance parquet disk cache
cron_jobs.txt          # Full cron schedule (install with: crontab cron_jobs.txt)
```

---

## Quick Start

```bash
# Activate virtual environment
source venv/bin/activate

# Mock swing scan (no IB needed, uses yfinance)
python breakout_scanner.py input/ALL.txt --mode swing --mock

# Live swing scan with notifications
python breakout_scanner.py input/ALL.txt --mode swing --live --notify

# Launch web dashboard
streamlit run app.py

# Run backtest (50 symbols, 2025)
python enhanced_backtest.py --watchlist input/ALL.txt --start 2025-01-01 --end 2025-12-31 --limit 50

# Install cron schedule (IMPORTANT: % must be escaped as \% in crontab — already done in cron_jobs.txt)
crontab cron_jobs.txt
```

---

## Trading Modes

| Mode | Timeframe | Lookback | Vol Thresh | Hold Period | Best For |
|------|-----------|----------|------------|-------------|----------|
| `longterm` | 1 week | 20 bars | 1.2x | Weeks–months | Position trades |
| `swing` | 1 day | 15 bars | 1.3x | Days–weeks | Swing trades |
| `daytrade` | 15 mins | 15 bars | 1.5x | Hours | Intraday |
| `scalping` | 1 min | 5 bars | 2.0x | Minutes | Scalps |

### Mode-specific behavior

- **Swing / Longterm**: SMA 150/200 trend filter, consolidation before breakout
- **Daytrade**: 9 EMA + VWAP confirmation, tighter stops
- **Scalping**: Requires live IB connection; max spread 0.1%; live data only

---

## Detection Pipeline

Every signal passes through these steps in order:

1. **Fetch OHLCV** — IB or yfinance (mock/backtest)
2. **Calculate indicators** — ATR, VWAP, BB, RSI, MACD, ADX, ROC
3. **Liquidity gate** — Min $5M daily volume (bypassed for momentum surge)
4. **Consolidation check** — BB squeeze + volume < 80% avg (bypassed for VCP or momentum surge)
5. **Breakout candle** — Volume spike, body structure, ATR threshold
6. **Trend alignment** — SMA/EMA/VWAP per mode
7. **24 pattern detectors** — 12 chart + 11 candle + VCP
8. **V10 scoring** — Weighted sum → GOLD / PREMIUM / HIGH / STANDARD
9. **Risk:Reward** — Reject if R:R < min_rr (graded: A=0.7, B=1.0, C=0.5, D=0.0)
10. **Overextension filter** — Reject if > 25% above SMA (V4)
11. **PREMIUM stop-distance gate** — Downgrade PREMIUM → HIGH if stop < 1% away (daytrade)
12. **Output** — Signal dict → CSV + notification (V9-C filter)

### Momentum Surge (V7)

Fires when: `(gap ≥ 5% OR intraday_move ≥ 5% OR daily_move ≥ 5%) AND Vol_Ratio ≥ 3.0`

- Bypasses consolidation check AND liquidity gate
- Signal gets `Type = 'Momentum'`
- 12 pts in scoring

### Market Regime

SPY performance auto-adjusts thresholds:

| Regime | Criteria | Adjustment |
|--------|----------|------------|
| CHOPPY | SPY < 1% move | 30% stricter thresholds |
| EXPANSION | SPY > 5% move | 10% looser thresholds |
| NORMAL | Everything else | Standard |

---

## Signal Scoring (V10)

Max possible score: ~177 pts (denominator only includes VCP if detected).

| Weight | Check | Notes |
|--------|-------|-------|
| 16 pts | `vol_confirm` | Volume ratio ≥ threshold |
| 16 pts | `trend_ok` | Price above SMA/EMA/VWAP |
| 15 pts | `minervini_template` | 8 Stage 2 conditions (0–15 pts proportional) |
| 14 pts | `vcp_quality` | VCP quality 0.0–1.0 (only added when detected) |
| 13 pts | `composite_momentum` | RSI + MACD + ADX + ROC blended |
| 12 pts | `momentum_surge` | Gap/intraday/daily ≥ 5% + vol ≥ 3× |
| 10 pts | `rr_ok` | Graded: A=0.7, B=1.0, C=0.5, D=0.0 |
| 10 pts | `dist_ok` | Distance from breakout in ATR units |
| 10 pts | `bullish_pattern` | 23 pattern detectors |
| 8 pts | `conviction` | Candle structure quality |
| 8 pts | `near_52w_high` | Within 5% of 52-week high |
| 6 pts | `sector_momentum` | Sector ETF in uptrend |
| 5 pts | `rsi_bull_div` | RSI bullish divergence |
| 2 pts | `pattern_vol_confirmed` | Volume confirmed during pattern |

### Quality tiers

| Tier | Score | Description |
|------|-------|-------------|
| GOLD | ≥ 90% | Elite — passes 5 extra hard gates |
| PREMIUM | ≥ 80% | High conviction — Minervini + volume + trend |
| HIGH | ≥ 65% | Good — moderate confluence |
| STANDARD | ≥ 60% | Marginal — basic criteria met |

### Minervini Stage 2 Template (V8, 8 conditions)

1. Price > SMA 150 and SMA 200
2. SMA 150 > SMA 200
3. SMA 200 rising (30+ bars)
4. SMA 50 > SMA 150 and SMA 200
5. Price > SMA 50
6. Price ≥ 30% above 52-week low
7. Price within 25% of 52-week high
8. Relative strength ≥ 70 (vs SPY)

Score: 0–8 met = 0–15 pts proportional.

### VCP Detection (V10)

Minervini Volatility Contraction Pattern — progressively shallower pullbacks + volume dry-up.

- **Algorithm**: find base high → walk alternating swing-low/high → validate contractions
- Each pullback must be < 95% of prior (5% shallower minimum)
- Higher lows required (1% tolerance)
- Volume dry-up: final avg_vol < first × 0.75
- Price within 8% below pivot (`pivot_proximity_pct = 8.0`)
- Final tight range ≤ 5% (`final_tight_range_pct = 5.0`)
- **Quality score 0.0–1.0**: contractions (0.25) + decay (0.25) + vol dry-up (0.20) + tight area (0.15) + proximity (0.15)
- VCP stop: low of final contraction − 1% buffer (tighter than ATR stop)
- Satisfies consolidation gate when `vcp_quality > 0.3`
- Mode overrides: daytrade (pullback 2–15%, tight ≤ 1.5%), scalping (1–8%, tight ≤ 0.8%)

### Version History Summary

```
V1  — Binary momentum (RSI, MACD, ADX, VWAP)
V2  — Composite momentum + conviction scoring
V3  — 23 pattern detectors + BB filter
V4  — Overextension filter (biggest risk-reduction change)
V5  — 52w high + RSI divergence + sector momentum
V5X — V5 + overextension filter
V6  — Pattern volume confirmation scoring
V6X — V6 + overextension filter  ← recommended for risk-adjusted returns (pre-V9)
V7  — Momentum surge detection (gap/run plays)
V8  — Minervini Stage 2 template (proportional 0–15 pts)
V8X — V8 + overextension filter
V9  — TP→Trail: when target hit, activate 2.0 ATR trailing stop
V10 — VCP detection (14 pts proportional)
```

### Live Scanner vs Backtest Configs

**The version labels (V1–V10) are backtest experiment names only** — they live in
`enhanced_backtest.py` and are never selected at scan time.

When a live scan runs today, it always uses the full V10 engine for detection. The
"V9-C" label you see in notifications and Streamlit is a **filter applied at output
time**, not a detection mode:

| Layer | What runs | Where |
|-------|-----------|--------|
| Detection | V10 full engine (all scoring weights active) | `scanner.py` |
| Signal saved | ALL passing signals (STANDARD 60+ through GOLD 90+) | CSV in `scanner_output/signals/` |
| Discord notify | V9-C filter: GOLD/PREMIUM **AND** MinerviniScore ≥ 7 | `breakout_scanner.py:216` |
| Streamlit default | Same V9-C filter (switchable via dropdown) | `pages/signals_page.py` |

**Why V9-C as the output filter?** It is the only backtest config that beat SPY over
2024–2025 (+89.52% vs +87.67%) with the lowest drawdown (−5.23%). Restricting
notifications and the default Streamlit view to this tier reduces noise without losing
the scanner's ability to detect and save all signal quality levels.

---

## CLI Reference

```bash
python breakout_scanner.py <watchlist_file> [options]
```

### Key flags

| Flag | Description |
|------|-------------|
| `--mode` | `swing` \| `longterm` \| `daytrade` \| `scalping` |
| `--live` | Connect to live IB account (default: paper) |
| `--mock` | yfinance data, no IB needed |
| `--cron` | Silent mode (errors only to stdout) |
| `--notify` | Send notifications (V9-C filter: PREMIUM + Minervini≥7) |
| `--bounce` | Also detect bounce/recovery signals |
| `--exit-file <csv>` | Evaluate exit conditions for open positions |
| `--both` | Scan + exit evaluation in one call |
| `--export-premium <txt>` | Write PREMIUM/GOLD tickers to file for Phase 2 re-scan |
| `--export-momentum-watch <txt>` | Write momentum-watch tickers for monitor_watch.py |
| `--auto-positions <csv>` | Auto-enter signals into mock positions file |
| `--monitor <csv1,csv2>` | Portfolio monitor mode (no scan, just price check) |
| `--sector-buzz` | Run sector momentum analysis before scan |
| `--sentiment` | Run sentiment analysis |
| `--vol` | Override volume threshold (e.g. `--vol 1.5`) |
| `--atr` | Override ATR multiplier |
| `--lookback` | Override lookback bars |

### Cron-critical: `%` must be escaped in crontab

In crontab, `%` is a newline character. Always use `\%s` not `%s`:

```bash
# CORRECT — in crontab
START=$(date +\%s) && python breakout_scanner.py ...

# WRONG — will silently break the command chain
START=$(date +%s) && python breakout_scanner.py ...
```

---

## Output Columns

| Column | Description | Notes |
|--------|-------------|-------|
| Symbol | Stock ticker | |
| Price | Entry price | |
| Vol | Volume ratio (vs 20-day avg) | > 1.5x = strong |
| Dist | Distance above breakout (ATR units) | 0.5–1.5 = ideal |
| Stop | Stop-loss price | |
| Target | Take-profit price | |
| R:R | Risk:Reward ratio | > 2.0 = good |
| Gap% | Gap from previous close | |
| Mode | Trading mode | |
| Quality | GOLD / PREMIUM / HIGH / STANDARD | |
| RR_Grade | A / B / C / D | A = R:R ≥ 3.0, B = 2.0–2.99 |
| WinProb | Estimated win probability (0–1) | |
| Patterns | Detected chart patterns | |
| MinerviniScore | Stage 2 conditions met (0–8) | |
| VCP | VCP detected (True/False) | |
| VCP_Quality | VCP quality score (0.0–1.0) | |
| VCP_Pivot | VCP pivot/resistance level | |
| VCP_Contractions | Number of pullbacks detected | |
| Type | 'Momentum' for gap/run signals | |
| RSI | Current RSI | |
| Sector | Sector classification | |

---

## Cron Schedule

Full schedule in `cron_jobs.txt`. Install with:

```bash
crontab cron_jobs.txt   # or: crontab -e and paste manually
```

All times are US Eastern (TZ=America/New_York set in cron_jobs.txt).

| Time (ET) | Days | Job |
|-----------|------|-----|
| Mon 9:00 AM | Mon | Longterm Phase 1: full scan → premium export |
| Mon 9:15 AM | Mon | Longterm exit evaluation |
| 9:35 AM | Mon–Fri | Swing Phase 1: full scan → premium export |
| 9:35 AM | Mon–Fri | Daytrade Phase 1: full scan → momentum-watch export |
| 9:45 AM | Mon–Fri | First momentum-watch monitor check |
| 10:00 AM | Mon–Fri | Daytrade Phase 2: re-scan momentum-watch |
| Every 15 min | Mon–Fri | Portfolio monitor + momentum-watch monitor |
| 2:00 PM | Mon–Fri | Daytrade Phase 2: re-scan momentum-watch |
| 3:30 PM | Mon–Fri | Daytrade exit check |
| 3:45 PM | Mon–Fri | Swing exit evaluation |
| 4:00 PM | Mon–Fri | Final portfolio check + S3 upload |
| 4:30 PM | Mon–Fri | Swing Phase 2: re-scan premium |
| 7:30 PM | Mon–Fri | Swing evening: re-scan premium |
| 8:20 PM | Mon–Fri | Daytrade Phase 4: evening re-scan momentum-watch |
| 8:30 PM | Mon–Fri | Validate signals (3+ days old) |
| Sun 9:00 PM | Sun | Learning recommendations from validation |
| Sun 11:00 PM | Sun | Maintenance: clean old logs / CSVs |

### S3 upload (`upload_to_s3.py`)

Syncs `scanner_output/signals/` and `scanner_output/portfolio/` to S3 so Streamlit Cloud can read results.

```bash
# Upload only files newer than START (avoids re-uploading old files)
python upload_to_s3.py --since-epoch $START --dirs scanner_output/signals scanner_output/portfolio
```

Requires `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in `.env` or environment.

### momentum-watch monitor (`monitor_watch.py`)

Standalone 15-min script — tracks HOLDING/RECOVERING/FADING/FAILED status per symbol.

- Fib retracement column: nearest level (0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%)
- State file: `scanner_output/.watch_monitor_YYYYMMDD.json`
- Alerts only on status **change** (no spam)
- Discord alert on FADING/FAILED; info alert on recovery

---

## Backtest Results

Backtest script: `enhanced_backtest.py` — compares V1 through V10 vs SPY and Minervini buy-and-hold.

### Full run (Jan 2024 – Dec 2025, ALL.txt)

| Config | Return | Sharpe | MaxDD | Win Rate | vs SPY |
|--------|--------|--------|-------|----------|--------|
| **SPY Buy & Hold** | +87.67% | 1.45 | -18.76% | — | baseline |
| Minervini Screen (25 stocks) | +76.32% | — | -17.95% | — | -11.35% |
| **V9-C** V8+TP→Trail PREMIUM+ | **+89.52%** | 1.50 | -5.23% | 60.4% | **+1.85%** ← only config to beat SPY |
| V8-B Minervini PREMIUM+ | +62.16% | 1.84 | -7.55% | 62.5% | -25.51% |
| V1-A HIGH+ baseline | +48.54% | 2.59 | -9.29% | 53.7% | -39.13% |
| V8-A Minervini HIGH+ | +46.79% | 1.99 | -11.00% | 51.9% | -40.88% |
| V10-A VCP HIGH+ | +25.28% | 1.48 | **-6.26%** | **60.4%** | -62.40% |
| V10MX-A VCP+Miner+overext HIGH+ | +20.70% | **1.66** | **-4.42%** | **63.2%** | -66.97% |

### V9-C: the recommended live config

**V9-C = Minervini≥7 + PREMIUM quality + TP→Trail stop**

- `tp_as_trail=True` in `config.py PORTFOLIO`: when target hit, activate 2.0 ATR trailing stop (don't close)
- Only config to beat SPY over 2024–2025 (+89.52% vs +87.67%)
- Dramatically lower drawdown: -5.23% vs SPY -18.76%

### VCP verdict (V10)

V10 dramatically improves **win rate (+6-10pp)** and **halves drawdown** vs baseline.
But too selective for standalone use (~48 trades/2yr on full universe) — best as overlay quality signal.
The 14-pt scoring boost naturally promotes true VCP setups to PREMIUM without being a hard filter.

### Running the backtest

```bash
# Fast: 50 symbols, 1 year
python enhanced_backtest.py --watchlist input/ALL.txt --start 2025-01-01 --end 2025-12-31 --limit 50

# Full: all symbols, 2-year reference
python enhanced_backtest.py --watchlist input/ALL.txt --start 2024-01-01 --end 2025-12-31

# Unbuffered output (see progress)
python -u enhanced_backtest.py ...
```

Results saved to `scanner_output/backtests/multi_config_vs_spy_YYYY-MM-DD_YYYY-MM-DD.json`.

### Intraday optimization findings (daytrade mode)

Best daytrade config (from 12-config sweep, Feb 2026):

| Parameter | Optimal | Rationale |
|-----------|---------|-----------|
| lookback | **15** bars | Captures fresh momentum (vs 20 = stale) |
| vol_thresh | **1.3x** | Quality/opportunity balance |
| atr_mult | **0.25** | Fewer false breakouts |

Expected: +4.10% monthly, 57% win rate, Sharpe 0.79, -2.38% max DD.

---

## Streamlit Dashboard

```bash
streamlit run app.py
```

Pages:
- **Signals**: shows breakout signals; defaults to **V9-C Only** (PREMIUM + Minervini≥7 filter)
  - Switch quality filter to "High and Above" to see all signals
  - Columns include VCP_Quality, VCP_Pivot, VCP_Contractions
- **Portfolio**: P&L tracking, open/closed positions, Minervini score

Data is read from `scanner_output/` (local) or S3 (cloud deployment).

---

## Notifications

Notifications are filtered to **V9-C only** (PREMIUM/GOLD + MinerviniScore ≥ 7).

If no V9-C signals exist: silent (no empty Discord spam). All signals still saved to CSV.

Subject line shows: `SWING Breakout Signals [ALL] (2 V9-C of 18 total)`

### Configure in `config.py`

```python
NOTIFICATIONS = {
    'email':    {'enabled': True,  'sender_email': '...', 'sender_password': '...'},
    'discord':  {'enabled': True,  'webhook_url': 'YOUR_WEBHOOK_URL'},
    'telegram': {'enabled': False, 'bot_token': '', 'chat_id': ''},
}
```

Set secrets via `.env` file: `GMAIL_APP_PASSWORD`, `DISCORD_WEBHOOK_URL`, `TAVILY_API_KEY`.

**Never commit `config.py`** — it contains real webhook URLs.

Enable with `--notify` flag.

---

## IB Connection

| Environment | Port |
|-------------|------|
| Paper TWS | 7497 |
| Live TWS | 7496 |
| Paper Gateway | 4002 |
| Live Gateway | 4001 |

### Setup

1. Open TWS or IB Gateway
2. Configure → Settings → API → Settings
3. Enable "ActiveX and Socket Clients"
4. Add `127.0.0.1` to Trusted IP Addresses
5. Use `--live` for live account, default = paper

### Async pattern (Python 3.14 requirement)

Event loop MUST be created BEFORE importing `ib_insync`:

```python
# breakout_scanner.py lines 13-18
import asyncio
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
# THEN import ib_insync
```

Rate limit: 5 concurrent requests (`MAX_CONCURRENT_REQUESTS=5` semaphore).

---

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| No signals in CSV when using `START=$(date +%s)` in cron | `%` is a crontab metacharacter → newline | Use `\%s` — already fixed in `cron_jobs.txt` |
| "No consolidation" in rejections | Stock not in quiet period before breakout | VCP or momentum surge bypasses this |
| Trend broken exit fires before stop | Normal — two separate exit mechanisms | Exit at trend line (SMA/EMA) is intentional |
| IB connection failed | TWS/Gateway not running or API disabled | Use `--mock` for offline testing |
| "No data" for symbol | Delisted or wrong format | `BRK B` not `BRK.B`; check watchlist |
| VCP not detecting on synthetic test | Synthetic data must fit within bar window (60 bars for swing) | Real market data works fine |
| Spread too wide (scalping) | Illiquid stock | Adjust `max_spread_pct` in config |

### Debug rejections

```bash
# Check why symbols were rejected
ls scanner_output/rejections/
cat scanner_output/rejections/rejections_swing_YYYYMMDD_HHMMSS.csv
```

### Clear data cache

```python
from yfinance_adapter import YFinanceAdapter
YFinanceAdapter().clear_disk_cache()
```

---

## Environment Setup

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install ib_insync pandas numpy yfinance streamlit boto3 python-dotenv

# Set secrets in .env
cat > .env << 'EOF'
GMAIL_APP_PASSWORD=your_app_password
DISCORD_WEBHOOK_URL=https://discordapp.com/api/webhooks/...
TAVILY_API_KEY=tvly-...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
EOF
```

---

## Disclaimer

This tool is for educational purposes. Trading involves substantial risk of loss. Always test on paper trading before using live funds. Past performance does not guarantee future results. Not financial advice.
