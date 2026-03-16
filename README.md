# stocksBreakout — Breakout Scanner for Interactive Brokers

Professional-grade algorithmic breakout scanner with Minervini Stage 2 scoring, VCP detection,
backtesting, and automated cron + Discord notifications.

> **Current version: V12** (28 patterns + S/R trendlines + Optuna weight optimizer)
> Last updated: Mar 2026

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Quick Start](#quick-start)
3. [Trading Modes](#trading-modes)
4. [Detection Pipeline](#detection-pipeline)
5. [Signal Scoring (V12)](#signal-scoring-v12--optuna-optimized-weights)
6. [CLI Reference](#cli-reference)
7. [Output Columns](#output-columns)
8. [Cron Schedule](#cron-schedule)
9. [Pre-Market Monitor](#pre-market-monitor-premarket_monitorpy)
10. [FinBERT Quality Promotion](#finbert-quality-promotion)
11. [FinBERT Backtest](#finbert-backtest-finbert_backtestpy)
12. [Earnings Date Warning](#earnings-date-warning)
13. [Momentum-Watch Monitor](#momentum-watch-monitor-monitor_watchpy)
14. [scanner_output/lists/ — Live Working Files](#scanner_outputlists--live-working-files)
15. [Backtest Results](#backtest-results)
16. [Streamlit Dashboard](#streamlit-dashboard)
17. [Notifications](#notifications)
18. [IB Connection](#ib-connection)
19. [Batch Execution with Shared Data Loading](#batch-execution-with-shared-data-loading-job_launcherpy)
20. [Cron Agent with Scheduler](#cron-agent-with-scheduler-cron_agentpy)
21. [Automated Test Agent](#automated-test-agent-automated_test_agentpy)
22. [Regime Detection](#regime-detection-regime_detectorpy)
23. [Troubleshooting](#troubleshooting)
24. [Environment Setup](#environment-setup)

---

## Project Structure

```
breakout_scanner.py    # CLI entry point — IB connection, async loop
orchestrator.py        # Scan coordination, market data, exit routing
scanner.py             # Core breakout detection & scoring (V12)
config.py              # Single source of truth for ALL parameters
market_data.py         # IB data fetching, caching, rate limiting
indicators.py          # ATR, VWAP, BB, RSI, MACD, ADX
exit_evaluator.py      # Position exit signal generation
pattern_recognition.py # 28 patterns: 16 chart + 11 candle + VCP (V12)
notifier.py            # Discord / Email / Telegram notifications
portfolio.py           # Position tracking, P&L, snapshots
premarket_monitor.py   # Pre-market gap scanner + FinBERT + X trending (8:00, 8:45 AM)
finbert_sentiment.py   # ProsusAI/finbert model wrapper (batch sentiment, per-symbol)
finbert_backtest.py    # FinBERT quality-promotion backtest vs baseline (Finnhub historical news)
monitor_watch.py       # 15-min momentum-watch monitor script
enhanced_backtest.py   # Multi-config A/B backtest (V1–V12 vs SPY)
weight_optimizer.py    # Optuna walk-forward weight optimizer (V12)
upload_to_s3.py        # Sync scanner_output to S3 for Streamlit Cloud
app.py                 # Streamlit dashboard entry point
input/
  ALL.txt              # Full watchlist (~1300 symbols)
pages/
  signals_page.py      # Signal viewer (V9-C default filter)
  portfolio_page.py    # Portfolio P&L dashboard
scanner_output/
  signals/             # Breakout signal CSVs
  exits/               # Exit evaluation CSVs
  rejections/          # Near-miss signals for review
  portfolio/           # Portfolio snapshots
  backtests/           # Backtest JSON results
  logs/                # Cron and scan logs
  cache/               # yfinance parquet disk cache + Finnhub news JSON cache
    finnhub/           # Permanent Finnhub news cache (keyed by symbol+date range)
  lists/               # Auto-generated watch lists and live position files
    positions_swing_mock.csv    # PREMIUM/GOLD swing positions — Phase 2 watchlist + exit evaluator input
    positions_daytrade_mock.csv # PREMIUM/GOLD daytrade positions — exit evaluator input
    premium_longterm.txt        # PREMIUM/GOLD longterm signals (weekly, no positions CSV for longterm)
    momentum_watch_daytrade.txt # Daytrade momentum watch: PREMIUM/GOLD + HIGH-momentum + near-miss
    optimizer_watch.txt         # 78 diverse symbols for weight optimization
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

# Pre-market gap scan + FinBERT (runs at 8:00/8:45 AM)
python premarket_monitor.py

# FinBERT quality-promotion backtest (Finnhub historical news recommended)
python finbert_backtest.py --start 2025-01-01 --end 2025-12-31 --finnhub-key YOUR_KEY

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
7. **28 pattern detectors** — 16 chart + 11 candle + VCP (V12 adds Falling Wedge, Rising Wedge, Rounding Bottom, Inv. Cup & Handle)
8. **V12 scoring** — Weighted sum → GOLD / PREMIUM / HIGH / STANDARD
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

## Signal Scoring (V12 — Optuna-optimized weights)

Weights below are **Optuna walk-forward optimized** (Mar 2026, 300 trials, 78 symbols, 4 folds 2023–2024).
Max possible score: ~230 pts (denominator only includes VCP if detected).

| Weight | Check | Optimizer note |
|--------|-------|----------------|
| 24 pts | `dist_confirm` | Distance from MA — top predictor ↑↑ |
| 24 pts | `at_key_support` | V11: Key support level — top predictor ↑↑ |
| 19 pts | `candle_ok` | Candle body structure ↑ |
| 17 pts | `near_52w_high` | Within 5% of 52-week high ↑↑ |
| 16 pts | `vol_confirm` | Volume ratio ≥ threshold (unchanged) |
| 16 pts | `rs_ok` | Relative strength vs SPY ↑↑ |
| 15 pts | `vcp_quality` | VCP quality 0.0–1.0 (only added when detected) |
| 14 pts | `rsi_divergence` | RSI bullish divergence ↑↑ |
| 13 pts | `has_bullish_pattern` | 28 pattern detectors ↑ |
| 12 pts | `consolidation` | Tightness before breakout ↑ |
| 12 pts | `pattern_vol_confirmed` | V6: Volume confirmed during pattern ↑↑ |
| 11 pts | `sr_breakout` | V11: Breaking above tested resistance (≥2 touches) ↑ |
| 9 pts | `trendline_break` | V11: Breaking above angled resistance trendline ↑ |
| 8 pts | `sector_momentum` | Sector ETF in uptrend |
| 6 pts | `no_vol_divergence` | No distribution during breakout |
| 4 pts | `conviction_strong` | Breakout conviction score ≥ 40 |
| 3 pts | `momentum_strong` | RSI+MACD+ADX+ROC composite ↓ |
| 2 pts | `trend_ok` | Price above SMA/EMA/VWAP ↓↓ |
| 2 pts | `momentum_surge` | Gap/intraday/daily ≥ 5% + vol ≥ 3× ↓ |
| 2 pts | `rr_ok` | R:R grade (A=0.7, B=1.0, C=0.5, D=0.0) ↓↓ |
| 0 pts | `minervini_template` | Eliminated — use as screener not scorer |

> **Key optimizer insights:** `dist_confirm` and `at_key_support` emerged as top predictors.
> `trend_ok` and `rr_ok` weight dropped sharply — the Minervini screener handles trend better.
> `minervini_template` weight set to 0 (redundant with V8 screener filter).

### Quality tiers (Optuna-optimized thresholds)

| Tier | Score | Change | Description |
|------|-------|--------|-------------|
| GOLD | ≥ 99 | was 90 | Elite — tighter, fewer but ultra-high conviction |
| PREMIUM | ≥ 69 | was 80 | High conviction — more signals qualify |
| HIGH | ≥ 65 | unchanged | Good — moderate confluence |
| STANDARD | ≥ 50 | was 60 | Wider funnel |

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
V11 — Support & Resistance levels: sr_breakout (11pts), at_key_support (24pts), trendline_break (9pts)
      trendlines fitted to swing-point clusters; S/R zones from price memory
V12 — 4 new chart patterns: Falling Wedge (bullish), Rising Wedge (bearish),
      Rounding Bottom (bullish), Inverted Cup & Handle (bearish) → 28 total patterns
      has_bullish_pattern now properly included in V1 legacy checks
V12-Opt — Optuna walk-forward weight optimizer (weight_optimizer.py):
      300 trials, 4 folds × 6 months, TPE sampler
      Best Sharpe on held-out fold 4: 0.771 (no catastrophic overfitting)
      Optimizer improved simulation Sharpe 2.38 → 3.09 (+40.5% vs +25.9% return in 2024)
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

### Weight optimizer (`weight_optimizer.py`)

```bash
# Run with 300 Optuna trials on diverse watchlist
python weight_optimizer.py --trials 300 --symbols scanner_output/lists/optimizer_watch.txt

# Print config.py patch after finding best weights
python weight_optimizer.py --trials 300 --symbols scanner_output/lists/optimizer_watch.txt --apply

# Faster smoke test
python weight_optimizer.py --trials 10 --symbols input/MAGS.txt
```

Options: `--trials N`, `--symbols <file>`, `--limit N`, `--quality [HIGH|PREMIUM]`, `--apply`

Output: `scanner_output/optimizer/best_weights_YYYYMMDD_HHMMSS.json` + Optuna study pickle.
`--apply` prints a ready-to-paste config.py block but **does not auto-edit config.py** — review first.

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
| FinBERT | Sentiment label: bullish / bearish / neutral | Added post-scan |
| FinBERT_Score | FinBERT confidence for dominant label (0–1) | |
| FinBERT_Net | (bullish − bearish) / total headlines (−1 to +1) | |
| FinBERT_Headline | Top headline used for sentiment | |
| FinBERT_Promoted | Promotion tier: 'HIGH→PREMIUM' or 'PREMIUM→GOLD' | Only when promoted |
| Earnings_Date | Next quarterly earnings report date (YYYY-MM-DD) | Added post-scan |
| Earnings_Timing | BMO or AMC (Before Market Open / After Market Close) | BMO = 6-12 AM, AMC = 4-6 PM ET |
| Earnings_Warning | Warning text for imminent earnings (within 7 days) | PREMIUM/GOLD only, empty for others |

---

## Cron Schedule

Full schedule in `cron_jobs.txt`. Install with:

```bash
crontab cron_jobs.txt   # or: crontab -e and paste manually
```

All times are US Eastern (TZ=America/New_York set in cron_jobs.txt).

| Time (ET) | Days | Job |
|-----------|------|-----|
| 8:00 AM | Mon–Fri | Pre-market monitor: gap scan + FinBERT + X trending → `premarket_watch.txt` + Discord |
| 8:45 AM | Mon–Fri | Pre-market monitor: second scan (updated pre-market prices) |
| Mon 9:00 AM | Mon | Longterm Phase 1: full scan → premium export |
| Mon 9:15 AM | Mon | Longterm exit evaluation |
| 9:35 AM | Mon–Fri | Swing Phase 1: full scan → auto-positions append |
| 9:35 AM | Mon–Fri | Daytrade Phase 1: full scan → auto-positions + momentum-watch export |
| 9:45 AM | Mon–Fri | First momentum-watch monitor check |
| 10:00 AM | Mon–Fri | Daytrade Phase 2: re-scan momentum-watch |
| Every 15 min | Mon–Fri | Portfolio monitor + momentum-watch monitor |
| 2:00 PM | Mon–Fri | Daytrade Phase 2: re-scan momentum-watch |
| 3:30 PM | Mon–Fri | Daytrade exit check |
| 3:45 PM | Mon–Fri | Swing exit evaluation |
| 4:00 PM | Mon–Fri | Final portfolio check + S3 upload |
| 4:30 PM | Mon–Fri | Swing Phase 2: re-scan all open positions (positions_swing_mock.csv) |
| 7:30 PM | Mon–Fri | Swing evening: re-scan all open positions (positions_swing_mock.csv) |
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

---

## Pre-Market Monitor (`premarket_monitor.py`)

Standalone agent run at 8:00 AM and 8:45 AM ET via cron. Scans for pre-market gaps,
enriches top gappers with FinBERT sentiment, fetches yesterday's top gainers, and
optionally pulls real-time X (Twitter) trending cashtags.

### Data sources

| Source | What it provides |
|--------|-----------------|
| yfinance `prepost=True` | Pre-market candles 4 AM–9:29 AM ET for gap calculation |
| Yahoo Finance Screener API | Yesterday's top 25 gainers (prev-day % gain, volume) |
| `finbert_sentiment.py` | FinBERT sentiment on top 15 gappers |
| X API v2 (optional) | Real-time `$CASHTAG` trending (likes + 2× retweets = engagement) |

### Features

- **Gap detection**: scans `PRIORITY_SYMBOLS` (36 high-beta symbols) + any `SECTOR_BASKET` triggered (e.g. IBIT ≥ 3% → entire crypto basket added)
- **WATCHONLY_ETFs**: QQQ, XLE, XLF, IBB, XME, ARKK, GLD, TLT shown in Discord for market context
- **Yesterday's top gainers**: `fetch_previous_day_gainers(top_n=25, min_pct=3.0)` — shown in Discord "Yesterday's Top Gainers" section; symbols merged into FinBERT batch
- **X trending** (optional): `scan_x_trending_cashtags()` searches `$SYMBOL lang:en -is:retweet`, sorted by engagement; shown in "Trending on X" section
- **Sector basket trigger**: IBIT ≥ 3% → all crypto symbols auto-added to watch list
- **Output**: writes `premarket_watch.txt` → pre-seeds Phase 1 `momentum_watch_daytrade.txt`

### Usage

```bash
# Full pre-market scan with FinBERT sentiment
python premarket_monitor.py

# Skip FinBERT (faster on quiet days)
python premarket_monitor.py --no-sentiment

# Include X trending (requires TWITTER_BEARER_TOKEN env var or --x-token)
python premarket_monitor.py --x-token YOUR_BEARER_TOKEN

# Adjust X trending sensitivity
python premarket_monitor.py --x-token YOUR_BEARER_TOKEN --x-mentions 30

# Dry run (no Discord, no file write)
python premarket_monitor.py --dry-run
```

### Key lesson from Mar 4 retrospective

Gap days (8–16% moves on IBIT/COIN/MSTR) can occur on **low volume ratios (1.3–1.6×)** — the standard vol threshold would miss them. The pre-market monitor catches these before market open by using actual pre-market price data rather than scanner volume logic.

### Gap direction vs sentiment signal (most actionable)

| Scenario | Signal type |
|----------|------------|
| Gap down + FinBERT bullish | Buy-the-dip candidate (IREN Mar 4: -8% gap, bullish 0.91) |
| Gap up + FinBERT bearish | Fade candidate (MDB Mar 5: +7% gap, bearish 0.97 on guidance cut) |
| Gap up + FinBERT bullish | Momentum continuation (standard breakout) |

### Environment variables

```
TWITTER_BEARER_TOKEN=...   # X API Basic tier ($200/mo) — for cashtag trending
FINNHUB_API_KEY=...        # Free at finnhub.io — for historical news in finbert_backtest.py
```

> **X API tiers**: Free (7-day lookback), Basic $200/mo (7-day), Pro $5k/mo (30-day).
> Full archive requires Enterprise ($42k+/mo). X API is suitable for **real-time trending only**,
> not historical backtesting.

---

## FinBERT Quality Promotion

After every scan, `breakout_scanner.py` enriches HIGH+ signals with FinBERT news sentiment.
Bullish FinBERT signals that meet threshold requirements are **promoted one quality tier up**:

| From | To | FinBERT_Score threshold | FinBERT_Net threshold |
|------|----|------------------------|----------------------|
| HIGH | PREMIUM | ≥ 0.70 | ≥ 0.25 |
| PREMIUM | GOLD | ≥ 0.82 | ≥ 0.40 |

**`FinBERT_Net`** = (bullish_count − bearish_count) / total_headlines. A value of 0.25 means
roughly 5 bullish vs 2 bearish in 8 headlines — a clear majority, not just a single article.

### Rules

- **Promotion only** — bearish FinBERT never downgrades a signal
- **HIGH+ only** — STANDARD signals are never promoted
- **PREMIUM→GOLD uses a higher bar** (0.82 / 0.40) because GOLD bypasses scanner hard gates
- `FinBERT_Promoted` key added to signal dict → visible in CSV export for auditability
- Promotion happens **before CSV export and Discord notification**

### Configure in `config.py`

```python
FINBERT_PROMOTION = {
    'enabled': True,
    'high_to_premium': {'min_score': 0.70, 'min_net': 0.25},
    'premium_to_gold':  {'min_score': 0.82, 'min_net': 0.40},
}
```

### Discord badge

Promoted signals show an extra line in the Discord embed:

```
⬆ PROMOTED: HIGH→PREMIUM
```

---

## FinBERT Backtest (`finbert_backtest.py`)

Evaluates whether FinBERT quality promotion improves risk-adjusted returns vs a baseline
scanner, using **true historical news** via Finnhub (no lookahead bias).

```bash
# Baseline only (no sentiment)
python finbert_backtest.py --start 2025-01-01 --end 2025-12-31 --no-sentiment

# With Finnhub historical news (recommended — no lookahead bias)
python finbert_backtest.py --start 2025-01-01 --end 2025-12-31 --finnhub-key YOUR_KEY

# Or set env var (reads FINNHUB_API_KEY automatically)
export FINNHUB_API_KEY=your_key
python finbert_backtest.py --start 2025-01-01 --end 2025-12-31
```

### 4 simulation configs

| Config | Description |
|--------|------------|
| **A** | Baseline HIGH+ (no FinBERT) |
| **B** | Baseline PREMIUM+ (no FinBERT) |
| **C** | FinBERT HIGH+ — promoted signals get PREMIUM position sizing |
| **D** | FinBERT PREMIUM+ — includes FinBERT-promoted symbols |

### News data sources

| Source | Coverage | Notes |
|--------|----------|-------|
| **Finnhub** (recommended) | Date-range filtered historical news | 60 req/min free; cached to `scanner_output/cache/finnhub/` |
| **yfinance** (fallback) | ~10 most recent articles (today only) | No historical coverage — cannot simulate past dates accurately |

> **Why Finnhub?** yfinance only returns the ~10 most recent news articles, all timestamped today.
> Without per-date news, any "historical" backtest using yfinance sentiment has **lookahead bias**
> (you'd be reading today's news for a signal from 2 months ago). Finnhub provides true dated news
> with `from`/`to` query parameters.

> **Finnhub free tier**: sign up at [finnhub.io](https://finnhub.io) — 60 req/min, no cost.

### Promotion table output

```
Symbol     Date       Quality(before) Quality(after) FinBERT_Score  Net    Outcome
AAPL       2025-02-10 HIGH            PREMIUM        0.83           +0.40  +8.2% (TP hit)
NVDA       2025-02-14 PREMIUM         GOLD           0.91           +0.55  +12.1% (TP hit)
```

### Statistical note

A 1-month backtest window on a focused watchlist (e.g. MAGS.txt) produces ~7 HIGH+ signals — not
statistically significant. Use a 6–12 month window with a broader watchlist (e.g. ALL.txt) for
meaningful comparison.

---

## Earnings Date Warning

Every signal is automatically enriched with the next quarterly earnings report date and timing (Before or After market hours). Earnings dates are critical for breakout traders — a stock breaking out right before earnings carries extra **gap risk**.

### Features

**Automatic enrichment** (post-scan, per-signal):
- Fetches `yf.Ticker(sym).calendar` for next earnings date
- Determines timing from the datetime hour (6 AM–12 PM → **BMO**, 12 PM–6 PM → **AMC**)
- Adds 3 columns to every signal:
  - `Earnings_Date` — next earnings announcement date (YYYY-MM-DD)
  - `Earnings_Timing` — "BMO" or "AMC" (empty if unknown)
  - `Earnings_Warning` — warning text for imminent earnings (within 7 days) on PREMIUM/GOLD signals only

### Discord Notification

In Discord embeds, signals show earnings information:

| Quality | Earnings within 7 days | Earnings > 7 days away |
|---------|------------------------|------------------------|
| **PREMIUM / GOLD** | **⚠ EARNINGS in Xd (BMO/AMC)** ← **Bold warning badge** | Earnings: YYYY-MM-DD (BMO/AMC) |
| HIGH / STANDARD | Earnings: YYYY-MM-DD (BMO/AMC) | Earnings: YYYY-MM-DD (BMO/AMC) |

**Why the distinction?** PREMIUM and GOLD signals are your highest-conviction trades. Earnings within 7 days adds significant overnight/opening gap risk. The warning is a **visual alert** (bold red in Discord) to reconsider position sizing or delay entry.

### Example Discord embed

```
Symbol: COIN [Finance] (PREMIUM)
Price: $142.50 | SL: $139.20 | TP: $156.80
R:R: 2.1 | Vol: 2.8x

⚠ EARNINGS in 3d (AMC)   ← Warning badge for PREMIUM with earnings in 7 days
```

### Rules

- **BMO (Before Market Open):** Earnings announced pre-market (6 AM–9:30 AM ET)
  - Stock often gaps open, breakout candle structure invalidated overnight
  - Higher risk: can't place stops or take profits until market opens

- **AMC (After Market Close):** Earnings announced after-hours (4 PM–6 PM ET)
  - More predictable: trade during normal hours, then manage overnight gap risk
  - Slightly lower risk than BMO

- **No earnings in sight:** `Earnings_Date` is empty; no warning issued

- **7-day threshold (configurable):** Hardcoded in `breakout_scanner.py` line ~232
  - Change `_EARNINGS_WARN_DAYS = 7` to adjust sensitivity

### CSV export

All 3 columns appear in the signal CSV — useful for post-analysis and audit trail:

```csv
Symbol,Price,Quality,Earnings_Date,Earnings_Timing,Earnings_Warning
COIN,142.50,PREMIUM,2026-03-22,AMC,"EARNINGS in 3d (AMC)"
NVDA,156.23,HIGH,2026-04-25,AMC,""
TPL,401.62,GOLD,2026-05-10,BMO,"EARNINGS in 9d (BMO)"
```

### Strategy notes

- **Before earnings:** consider **reducing position size** on PREMIUM/GOLD signals
- **After earnings:** look for **post-earnings breakouts** (scanner runs post-market, can catch follow-through)
- **Swing traders:** earnings within 7 days is a signal to **tighten stops** and **take profits early**
- **Daytrade mode:** earnings same day can trigger **wide 4 PM gaps** — monitor positions closely into close

---

## Momentum-Watch Monitor (`monitor_watch.py`)

Standalone script run every 15 minutes during market hours. Tracks whether stocks on the
momentum-watch list are still running or fading — without running a full scan.

> **Does NOT write any CSV files.** It writes only one hidden JSON state file per day
> (`scanner_output/.watch_monitor_YYYYMMDD.json`). It is therefore **not** a cause of
> duplicate signal CSVs appearing in S3.

### How it works

**Input:** `scanner_output/lists/momentum_watch_daytrade.txt` — populated by the Phase 1 daytrade scan
(`--export-momentum-watch`). Contains PREMIUM/GOLD + HIGH-momentum + near-miss symbols.

**Data:** Fetches 15-min bars from yfinance (no IB needed). Always fresh — disk cache disabled.

**Classification per symbol (4 statuses):**

| Status | Trigger | Icon |
|--------|---------|------|
| `HOLDING` | Price ≥ today's open | ✅ |
| `RECOVERING` | Price slightly below open but not failed | 🟡 |
| `FADING` | Price > 3% below intraday high **AND** (3-bar trend falling OR volume dried up) | ⚠️ |
| `FAILED` | Price > 2% below today's open (move is dead) | 🔴 |

**Fibonacci retracement column:** For each symbol, prints the nearest Fib level to the current price,
measured downward from today's intraday high to intraday low:

```
0%  →  At today's high (full move intact)
23.6%, 38.2%, 50%, 61.8%, 78.6%  →  Partial retracement levels
100%  →  Full give-back to today's low (move failed)
```

**State file** (`scanner_output/.watch_monitor_YYYYMMDD.json`):
Stores the last known status for each symbol. One file per trading day (auto-reset at midnight ET).
Used to detect **transitions** — alerts fire only when status *changes*, not on every check.

**Discord notification logic:**

| Transition | Alert level |
|-----------|-------------|
| HOLDING → FADING | `warn` (orange) |
| HOLDING → FAILED | `crit` (red) |
| RECOVERING → FADING | `warn` |
| RECOVERING → FAILED | `crit` |
| FADING → FAILED | `crit` |
| FADING → HOLDING | `info` (green — recovery, don't close early) |
| FAILED → HOLDING / RECOVERING | `info` |

Each Discord alert shows: current price, vs-open%, vs-high%, nearest Fib level, volume ratio, 3-bar trend.

### Usage

```bash
# Live daytrade watch list (default)
python monitor_watch.py --notify

# Swing watch list
python monitor_watch.py --mode swing --notify

# Custom file, no Discord (dry run)
python monitor_watch.py --file input/my_watchlist.txt --dry-run

# Print only (no notifications even with --notify)
python monitor_watch.py                     # omit --notify → no Discord
```

### Cron schedule

```
# First check after Phase 1 scan (9:45 AM)
45 9 * * 1-5   python monitor_watch.py --notify

# Every 15 min 10:00 AM – 3:45 PM
*/15 10-15 * * 1-5   python monitor_watch.py --notify

# Final check at market close
0 16 * * 1-5   python monitor_watch.py --notify
```

### Output files — what it writes (and what it does NOT)

| File | Written? | Notes |
|------|---------|-------|
| `scanner_output/.watch_monitor_YYYYMMDD.json` | ✅ Yes | Hidden state file, reset daily |
| `scanner_output/signals/signals_*.csv` | ❌ No | Only written by `breakout_scanner.py` |
| Any other CSV | ❌ No | This script never writes CSVs |

---

## scanner_output/lists/ — Live Working Files

All files in `scanner_output/lists/` are **auto-generated at runtime** by cron jobs. They are not static configuration — they are the live state of the scanning pipeline and are overwritten or appended to on each run. Do not edit them manually while cron is active.

---

### `positions_swing_mock.csv` as Phase 2 watchlist

> **Why there is no `premium_swing.txt` anymore.**
>
> Previously the Phase 1 scan exported a separate `premium_swing.txt` containing just the Symbol column
> of PREMIUM/GOLD signals, which Phase 2 and Evening scans used as their watchlist. This was pure
> duplication: `positions_swing_mock.csv` already tracks exactly those same signals with full position
> details (entry, stop, target, quality). Maintaining two files in sync added complexity with no benefit.
>
> **Current design:** Phase 1 writes PREMIUM/GOLD signals to `positions_swing_mock.csv` via
> `--auto-positions`. Phase 2 and Evening scans pass `positions_swing_mock.csv` directly as their
> watchlist — `get_watchlist_from_file()` reads the `symbol` column when given a `.csv` file. One file,
> three purposes: Phase 2 watchlist input · exit evaluator input · Streamlit Watch Lists display.
>
> `premium_daytrade.txt` was removed for the same reason. Daytrade's Phase 2 always used
> `momentum_watch_daytrade.txt` (a broader set) anyway, so `premium_daytrade.txt` had no readers
> and was always empty in production.

---

### `premium_longterm.txt`

**Written by:** Phase 1 longterm scan (cron Monday 9:00 AM ET, weekly)
**Read by:** Future longterm re-evaluation runs (not currently scheduled)
**CLI flag:** `--export-premium scanner_output/lists/premium_longterm.txt`

Weekly export of PREMIUM/GOLD tickers from the full `input/ALL.txt` scan in longterm mode (1-week bars, SMA trend). Typically contains fewer signals than swing/daytrade due to the stricter weekly trend requirements. Useful for manually reviewing and seeding long-term positions.

**Reset:** Overwritten every Monday morning. Previous week's list is replaced entirely.

---

### `momentum_watch_daytrade.txt`

**Written by:** Phase 1 daytrade scan (cron 9:35 AM ET, daily)
**Read by:** Phase 2 daytrade scans (10:00 AM, 2:00 PM, 8:20 PM ET), `monitor_watch.py` (every 15 min)
**CLI flag:** `--export-momentum-watch scanner_output/lists/momentum_watch_daytrade.txt`

This is the most actively used list file. It is a **broader** export than `premium_daytrade.txt` and includes:

| Inclusion criteria | Reason |
|--------------------|--------|
| PREMIUM or GOLD quality | Highest conviction signals |
| HIGH quality + momentum surge | Strong intraday move (≥5% gap/move + Vol_Ratio ≥3.0) |
| HIGH quality + Vol_Ratio ≥3.0 | High volume even without a gap |
| Near-miss breakout (within 0.5% of 52w high) + vol_confirm | Almost broke out — worth watching for follow-through |

**How it flows through the day:**

```
9:35 AM  Phase 1: scans ALL.txt → writes momentum_watch_daytrade.txt
9:45 AM  monitor_watch.py first check (HOLDING/FADING/FAILED status)
10:00 AM Phase 2: re-scans momentum_watch_daytrade.txt with fresh 15-min data
Every 15 min  monitor_watch.py tracks status changes, sends Discord alerts
2:00 PM  Phase 2: re-scans momentum_watch_daytrade.txt again
8:20 PM  Phase 4: evening re-scan on momentum_watch_daytrade.txt
```

**Current size:** ~100–500 symbols depending on market activity.

**Reset:** Overwritten by every Phase 1 daytrade scan. The monitor (`monitor_watch.py`) only reads it, never writes it.

---

### `optimizer_watch.txt`

**Written by:** Manually curated (not auto-generated)
**Read by:** `weight_optimizer.py`, `enhanced_backtest.py`
**CLI flag:** `--symbols scanner_output/lists/optimizer_watch.txt`

A hand-picked list of ~80 symbols covering diverse sectors and market caps, designed to give the Optuna walk-forward optimizer a representative cross-section of the market. Unlike `ALL.txt` (1300+ symbols), this smaller set allows the optimizer to run 300 trials in ~15–30 minutes rather than hours.

**Sector breakdown:**

| Sector | Example symbols |
|--------|-----------------|
| Large-Cap Tech | AAPL, MSFT, NVDA, AMD, AVGO |
| Mid-Cap Tech / High-Growth | DKNG, MELI, HOOD, COIN, RKLB |
| Finance | GS, JPM, BAC, V, MA |
| Healthcare | LLY, UNH, ABBV, MRK |
| Energy | XOM, CVX, OXY, DVN |
| Industrials | CAT, DE, GE, HON |
| Consumer | AMZN, COST, WMT, HD |
| ETFs / Benchmarks | SPY, QQQ, IWM, GLD |

SPY is auto-appended by `weight_optimizer.py` for benchmark comparison even if not listed here.

**Usage:**

```bash
# Run optimizer on this watchlist
python weight_optimizer.py --trials 300 --symbols scanner_output/lists/optimizer_watch.txt

# Run backtest comparison using same symbols
python enhanced_backtest.py --watchlist scanner_output/lists/optimizer_watch.txt \
  --start 2024-01-01 --end 2024-12-31 --versions v1,v11,v12 --limit 30
```

**Note:** This file should be edited manually when you want to add/remove symbols. It is the only file in `scanner_output/lists/` that is not auto-generated.

---

### `positions_swing_mock.csv`

**Written by:** `--auto-positions` flag after every swing scan that finds PREMIUM/GOLD signals
**Read by:** Exit evaluator (3:45 PM, 4:30 PM), portfolio monitor (every 15 min)
**CLI flags:** `--auto-positions` (write), `--exit-file` (read for exits), `--monitor` (read for monitoring)

> **Not the Streamlit portfolio.** This is a flat input list for the **cron exit evaluator and portfolio monitor scripts**. The Streamlit "Auto Portfolio (V9-C)" tab reads `scanner_output/portfolio/auto_portfolio.json` instead — a separate system managed by `auto_portfolio.py` that simulates $100K of paper capital, tracks P&L, and handles trailing stops. These two systems are independent and do not share data.

Tracks open **swing positions** for cron exit evaluation — PREMIUM and GOLD quality breakout signals auto-appended after each scan. Only PREMIUM (score ≥ PREMIUM threshold) and GOLD (score ≥ GOLD threshold) signals are ever written here. Lower quality signals (HIGH, STANDARD) are never added.

**CSV format:**

```csv
symbol,mode,entry,entry_date,stop,target,timeframe,quality
TPL,swing,401.62,2026-01-26,397.60,524.17,1 day,PREMIUM
HAS,swing,104.00,2026-01-26,102.96,125.16,1 day,GOLD
```

| Column | Description |
|--------|-------------|
| `symbol` | Ticker |
| `mode` | Always `swing` |
| `entry` | Price at signal time |
| `entry_date` | Date auto-appended (YYYY-MM-DD) |
| `stop` | Initial stop: 1% below entry (hard floor) |
| `target` | Scanner-computed price target |
| `timeframe` | Always `1 day` for swing |
| `quality` | `PREMIUM` or `GOLD` |

**Deduplication:** A symbol already in the file is never re-added, even if it appears in later scans. The scanner checks existing symbols before appending.

**Exit behavior:** The exit evaluator reads this file and evaluates each PREMIUM/GOLD position for stop hits, trend breaks, or target proximity. Positions are NOT automatically removed from this file when an exit signal fires — you must remove them manually or via the portfolio reset UI in the Streamlit dashboard.

---

### `positions_daytrade_mock.csv`

**Written by:** `--auto-positions` flag after every daytrade scan that finds PREMIUM/GOLD signals
**Read by:** Exit evaluator (3:30 PM), portfolio monitor (every 15 min)
**CLI flags:** `--auto-positions` (write), `--exit-file` (read for exits), `--monitor` (read for monitoring)

> **Not the Streamlit portfolio.** Same distinction as `positions_swing_mock.csv` above — this is a flat input list for cron scripts only. The Streamlit Auto Portfolio tab uses `auto_portfolio.json`.

Same structure as `positions_swing_mock.csv` but for **daytrade mode** (15-min bars). Positions here are intraday — they are typically entered and closed on the same or next trading day.

**CSV format:**

```csv
symbol,mode,entry,entry_date,stop,target,timeframe,quality
MPC,daytrade,204.17,2026-03-02,202.13,205.83,15 mins,PREMIUM
TRMB,daytrade,69.03,2026-03-02,68.34,70.22,15 mins,PREMIUM
```

**Key differences from swing positions:**

| | Swing | Daytrade |
|--|-------|---------|
| `mode` | `swing` | `daytrade` |
| `timeframe` | `1 day` | `15 mins` |
| Typical hold | Days to weeks | Hours to 1–2 days |
| Exit scan | 3:45 PM, 4:30 PM | 3:30 PM |
| Stop distance | ~1% below entry | ~1% below entry |

**Important:** Daytrade positions accumulate across sessions since auto-positions only appends, never removes. Clean this file manually at the start of each week or use the Streamlit dashboard reset button to clear closed positions.

---

### Summary table

| File | Auto-generated? | Written by | Read by | Reset cadence |
|------|----------------|------------|---------|---------------|
| `positions_swing_mock.csv` | ✅ Yes (append) | Phase 1 swing scan | Phase 2/Evening swing scans · **Cron** exit evaluator · monitor | Manual / dashboard reset |
| `positions_daytrade_mock.csv` | ✅ Yes (append) | Phase 1 daytrade scan | **Cron** exit evaluator · monitor | Manual / dashboard reset |
| `premium_longterm.txt` | ✅ Yes | Phase 1 longterm scan | Manual review | Weekly (overwrite) |
| `momentum_watch_daytrade.txt` | ✅ Yes | Phase 1 daytrade scan | Phase 2 daytrade scans + monitor_watch | Daily (overwrite) |
| `optimizer_watch.txt` | ❌ Manual | Hand-curated | weight_optimizer, backtest | Never (edit manually) |

**Separate system — `scanner_output/portfolio/auto_portfolio.json`:** The Streamlit Auto Portfolio tab uses this file instead. Managed by `auto_portfolio.py`, it reads raw signal CSVs directly, simulates $100K paper capital with 10% position sizing, tracks full P&L history, and applies 8% trailing stops. It is independent of the positions CSVs above.

---

## Backtest Results

Backtest script: `enhanced_backtest.py` — compares V1 through V12 vs SPY and Minervini buy-and-hold.

### Full run (Jan 2024 – Dec 2025, ALL.txt) — pre-optimizer baseline

| Config | Return | Sharpe | MaxDD | Win Rate | vs SPY |
|--------|--------|--------|-------|----------|--------|
| **SPY Buy & Hold** | +87.67% | 1.45 | -18.76% | — | baseline |
| Minervini Screen (25 stocks) | +76.32% | — | -17.95% | — | -11.35% |
| **V9-C** V8+TP→Trail PREMIUM+ | **+89.52%** | 1.50 | **-5.23%** | 60.4% | **+1.85%** ← only config to beat SPY |
| V8-B Minervini PREMIUM+ | +62.16% | 1.84 | -7.55% | 62.5% | -25.51% |
| V1-A HIGH+ baseline | +48.54% | 2.59 | -9.29% | 53.7% | -39.13% |
| V8-A Minervini HIGH+ | +46.79% | 1.99 | -11.00% | 51.9% | -40.88% |
| V10-A VCP HIGH+ | +25.28% | 1.48 | -6.26% | 60.4% | -62.40% |
| V10MX-A VCP+Miner+overext HIGH+ | +20.70% | 1.66 | -4.42% | 63.2% | -66.97% |

### V12 comparison (Jan 2024, 30 symbols from optimizer_watch.txt)

2024 was a strong bull market (SPY +58.82%) dominated by mega-cap momentum. Results:

| Config | Signals | Return | Sharpe | MaxDD | vs SPY |
|--------|---------|--------|--------|-------|--------|
| SPY Buy & Hold | 1 | +58.82% | 1.88 | -9.97% | — |
| Minervini Screen (20 stocks) | 20 | +67.06% | 2.07 | -17.17% | +8.24% |
| **V8-C** Minervini HIGH+ aggressive | 90 | **+52.09%** | **2.50** | **-9.66%** | -6.73% |
| V1-A HIGH+ baseline | 96 | +42.21% | 2.24 | -9.53% | -16.61% |
| V1-B ALL quality | 179 | +46.04% | 2.47 | -8.61% | -12.78% |

> Note: V6X-A (overextension filter) degraded to +12.71% in 2024 — the overextension filter
> excluded the biggest winners (NVDA, TSLA, COIN) which ran 100-300% continuously.

### V11/V12 feature isolation (Mar 2026, 2024, 30 symbols)

| Config | Signals | Return | Sharpe | MaxDD | Finding |
|--------|---------|--------|--------|-------|---------|
| V1-A HIGH+ (baseline) | 96 | +42.21% | 2.24 | -9.53% | — |
| **V11-Z** WITHOUT S/R feature | 77 | +39.51% | **2.35** | **-7.68%** | Better risk-adjusted |
| V11-A WITH S/R feature | 19 | +19.71% | 1.27 | -8.16% | Underperforms in 2024 |
| V12-A Pattern confirmed | 96 | +42.21% | 2.24 | -9.53% | 100% pattern rate — not discriminating |

**V11 insight:** S/R-confirmed signals underperformed in 2024 (pure momentum market). In choppier markets, S/R confirmation adds value. Signals *without* S/R features had slightly better risk-adjusted returns.

**V12 insight:** `has_bullish_pattern` fires on ~100% of breakout signals (by design — breakouts are patterns). Pattern scoring boosts signal scores (+13 pts) but the boolean alone is not a useful gate.

### Optuna Weight Optimizer (Mar 2026, 78 symbols)

```bash
python weight_optimizer.py --trials 300 --symbols scanner_output/lists/optimizer_watch.txt --apply
```

Walk-forward: 4 folds × 6 months (optimize on folds 1–3, validate on fold 4).

| Fold | Period | Role | Sharpe | Return |
|------|--------|------|--------|--------|
| 1 | 2023-01 → 2023-06 | optimize | 3.201 | +79.15% |
| 2 | 2023-07 → 2023-12 | optimize | 0.445 | +27.52% |
| 3 | 2024-01 → 2024-06 | optimize | 3.309 | +52.84% |
| **4** | **2024-07 → 2024-12** | **held-out** | **0.771** | **+4.69%** |

Optimizer comparison (2024 full year): current weights +25.9% → optimized +40.5%, Sharpe 2.38 → 3.09.
Fold 4 validation positive (no catastrophic overfitting).

### Best combination recommendation

| Use case | Config | Why |
|----------|--------|-----|
| **Live notifications** | V9-C (Minervini PREMIUM+ + TP→Trail) | Only config to beat SPY 2024-2025; -5.23% max DD |
| **High signal volume** | V8-C (Minervini HIGH+ aggressive) | Best Sharpe in pure momentum markets (2024) |
| **Risk-minimized** | V10MX-A (VCP + Minervini + overext) | -4.42% max DD, 63.2% win rate |
| **Research/scan all** | V1-B ALL quality | Maximum signal visibility; best for discovery |

**Bottom line:** V9-C is the live trading standard. Use V8-C for 2024-style bull markets.
The optimizer-tuned weights improve signal scoring but the primary alpha driver is the
**Minervini PREMIUM filter**, not the individual scoring weight values.

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

# V11/V12 isolation only
python enhanced_backtest.py --watchlist scanner_output/lists/optimizer_watch.txt --start 2024-01-01 --end 2024-12-31 --versions v1,v11,v12 --limit 30

# Run weight optimizer (300 trials, ~45 min)
python weight_optimizer.py --trials 300 --symbols scanner_output/lists/optimizer_watch.txt --apply
```

Results saved to `scanner_output/backtests/multi_config_vs_spy_YYYY-MM-DD_YYYY-MM-DD.json`.
Optimizer results: `scanner_output/optimizer/best_weights_YYYYMMDD_HHMMSS.json`.

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
FINNHUB_API_KEY=...            # Free at finnhub.io — historical news for finbert_backtest.py
TWITTER_BEARER_TOKEN=...       # X API Basic ($200/mo) — real-time cashtag trending in premarket_monitor.py
EOF
```

---

## Batch Execution with Shared Data Loading (`job_launcher.py`)

Optimizes execution when multiple trading modes run simultaneously by loading market data once and caching it for reuse. This eliminates redundant data fetching and reduces execution time by **50-70%**.

### When to use

- Multiple modes (swing + daytrade + scalping) scheduled at same time
- Want to eliminate duplicate market data fetching
- Need faster execution for simultaneous scans

### How it works

1. **Detect simultaneous jobs** in cron schedule
2. **Load market data once**: SPY bars + all symbols + indicators (35-40 seconds)
3. **Cache to disk**: `scanner_output/cache/market_data_*.pkl` (pickle serialized)
4. **Launch modes with cache**: Each mode reads cached data (5-10 seconds per mode)
5. **Auto-cleanup**: Keeps 3 most recent caches

### Performance comparison

```
Before (separate fetches):
  09:35 swing:    Fetch (25s) + Scan (5s) = 30s
  09:35 daytrade: Fetch (25s) + Scan (5s) = 30s
  09:35 scalping: Fetch (25s) + Scan (5s) = 30s
  Total: 90 seconds

After (batched):
  Load data once:  25s
  Launch swing:    5s
  Launch daytrade: 5s
  Launch scalping: 5s
  Total: 40 seconds
  SAVINGS: 50 seconds (56% faster!)
```

### Usage

```bash
# Find all simultaneous jobs in cron schedule
python job_launcher.py --find-simultaneous

# Run specific time with batch loading
python job_launcher.py --time 09:35 --modes swing,daytrade,scalping --notify

# Dry-run (preview without executing)
python job_launcher.py --time 09:35 --modes swing,daytrade --dry-run

# With cron minimal output
python job_launcher.py --time 09:35 --modes swing,daytrade --cron --notify
```

### Auto-integration with cron_agent.py

When `cron_agent.py --daemon` detects multiple modes at the same time, it automatically calls `job_launcher.py` instead of running modes sequentially. No configuration changes needed!

```bash
# Automatic batch execution
python cron_agent.py --daemon
```

### Cache structure

Each cache file contains:
- `timestamp`: ISO format when cache was created
- `spy_timeframe`: 'daily'
- `spy_bars`: SPY OHLCV data
- `symbols`: List of all symbols
- `indicators_by_symbol`: Dict of {symbol: {indicator: value}}
- `market_condition`: 'bull'/'bear'/'mixed' (from regime_detector.py)

---

## Cron Agent with Scheduler (`cron_agent.py`)

Unified scheduler that parses `cron_jobs.txt` and executes all breakout scanner jobs programmatically. Replaces manual crontab setup with Python-based scheduling.

### Features

- **Parse cron syntax**: Full support for minute hour day month weekday with:
  - Exact values: `30`, `9`
  - Wildcards: `*`
  - Ranges: `10-15`, `1-5`
  - Step values: `*/15`, `0-14/2` (properly handled after Mar 2026 fix)
  - Lists: `1,2,3,4,5`

- **Category-based execution**: Run all jobs in a category (swing, daytrade, etc.)
- **Time-based execution**: Run jobs scheduled for specific time
- **Intelligent batching**: Detects multiple modes at same time → auto-calls `job_launcher.py`
- **Daemon mode**: Continuous monitoring with automatic job execution
- **Dry-run mode**: Preview jobs without executing
- **Healthchecks.io integration**: Automatic pings for monitoring
- **Time simulation**: Test jobs at market times

### Quick Start

#### 1. View All Jobs

List all 29 cron jobs grouped by category and time:

```bash
python cron_agent.py --list-jobs
```

Output includes:
- Job name and category
- Scheduled time (ET - Eastern Time)
- Execution day/weekday

#### 2. Run Jobs Immediately

Run all jobs in a specific category NOW (ignoring schedule):

```bash
# Run all swing trading jobs
python cron_agent.py --run-now swing

# Run all daytrade jobs
python cron_agent.py --run-now daytrade

# Run pre-market monitoring
python cron_agent.py --run-now premarket
```

Available categories:
- `longterm` — Weekly position trading (Monday only)
- `swing` — Daily swing trade scans + exits
- `daytrade` — Intraday momentum scans
- `premarket` — Pre-market gap detection (8:00, 8:45, 9:31 AM)
- `momentum_watch` — 15-min momentum monitoring
- `portfolio` — 15-min portfolio tracking
- `validate` — Signal validation & learning
- `maint` — Cleanup & archive tasks

#### 3. Run Jobs at Specific Time

Simulate market time and run all jobs scheduled for that time:

```bash
# Run jobs scheduled for 9:35 AM
python cron_agent.py --run-time 09:35

# Run jobs scheduled for 3:45 PM
python cron_agent.py --run-time 15:45
```

#### 4. Dry-Run Mode

Preview what would execute without running:

```bash
python cron_agent.py --run-now swing --dry-run
python cron_agent.py --run-time 09:35 --dry-run
python cron_agent.py --list-jobs --category daytrade
```

#### 5. Run as Daemon

Start continuous monitoring that respects cron schedule:

```bash
# Run as daemon (will execute jobs on schedule)
python cron_agent.py --daemon

# Simulate market time for testing
python cron_agent.py --daemon --sim-time "09:35"

# Dry-run daemon (preview without executing)
python cron_agent.py --daemon --sim-time "14:00" --dry-run
```

### Use Cases

#### Development & Testing

```bash
# Check all jobs parse correctly
python cron_agent.py --list-jobs

# Test a specific category
python cron_agent.py --run-now swing --dry-run

# Simulate market time flow
python cron_agent.py --daemon --sim-time "09:30" --dry-run
```

#### Quick Execution

```bash
# Run swing trades immediately (don't wait for 9:35 AM)
python cron_agent.py --run-now swing

# Run evening rebalance
python cron_agent.py --run-now portfolio
```

#### Background Daemon

```bash
# Start agent as background service (respects cron schedule)
nohup python cron_agent.py --daemon > scanner_output/logs/cron_agent_daemon.log 2>&1 &
```

#### Integration with Docker

```bash
# In docker-compose.yml or crontab:
CMD ["python", "cron_agent.py", "--daemon"]
```

### Job Categories Explained

#### Pre-Market (8:00 - 9:31 AM ET)
- **8:00 AM** - Initial gap scan on ETFs and priority symbols
- **8:45 AM** - Second gap check (fresher data)
- **9:31 AM** - Opening surge detection (first-minute momentum)

#### Long-Term (Weekly, Monday 9:00 AM ET)
- **9:00 AM** - S&P 500 wide scan, export PREMIUM tickers
- **9:15 AM** - Long-term position exit evaluation

#### Swing Trading (Daily, Weekday 9:35 AM - 7:30 PM ET)
- **9:35 AM** - Phase 1: Wide scan → export PREMIUM + watchlist
- **3:45 PM** - Exit check for swing positions
- **4:30 PM** - Phase 2: Re-evaluate all open positions
- **7:30 PM** - Evening re-evaluation

#### Day Trading (Daily, Weekday 9:35 AM - 8:20 PM ET)
- **9:35 AM** - Phase 1: Wide scan → export momentum-watch
- **10:00 AM** - Phase 2: Re-evaluate momentum-watch
- **2:00 PM** - Phase 2 afternoon: Re-evaluate momentum-watch
- **3:30 PM** - Day trade exit check
- **8:20 PM** - Phase 4: Evening re-evaluate

#### Momentum Watch (Every 15 min, 9:45 AM - 4:00 PM ET)
- **9:45 AM** - First check after Phase 1
- **Every 15 min** (10:00 AM - 3:45 PM)
- **4:00 PM** - Final check at market close

#### Portfolio Monitoring (Every 15 min, 9:45 AM - 4:00 PM ET)
- **9:30 AM** - Reset alert history for new trading day
- **9:45 AM** - First check after market open
- **Every 15 min** (10:00 AM - 3:45 PM)
- **4:00 PM** - Final check + S3 upload

#### Signal Validation (Evening, Weekday 8:30 PM ET)
- Validate signals from 3+ days ago

#### Learning Report (Weekly, Sunday 9:00 PM ET)
- Generate learning recommendations

#### Maintenance (Weekly, Sunday 11:00 PM - 11:10 PM ET)
- Clean logs older than 30 days
- Archive results/rejections older than 90 days
- Clean monitor alert history

### Logging

All job execution is logged to:

```
scanner_output/logs/cron_agent.log
```

Each category has its own detailed log:
- `cron_longterm.log`
- `cron_swing.log`
- `cron_daytrade_morning.log`
- `cron_premarket.log`
- `cron_watch_monitor.log`
- `cron_monitor.log`
- `cron_validate.log`
- `cron_upload.log`

### Healthchecks.io Integration

Jobs automatically ping healthchecks.io on success/failure. Configure UUIDs in `cron_jobs.txt`:

```
HC_UUID_LONGTERM
HC_UUID_SWING
HC_UUID_DAYTRADE
HC_UUID_MONITOR
HC_UUID_VALIDATE
```

The agent will:
- **Ping on success** — Job completed successfully
- **Ping /fail** on error — Job failed (timeout, non-zero exit)
- **Log missed pings** — If healthchecks is unreachable

### Advanced Examples

#### Run All Morning Jobs (9:35 AM Block)

```bash
python cron_agent.py --run-time 09:35
```

This runs:
- Swing Phase 1 scan
- Daytrade Phase 1 scan
- Momentum-watch first check
- Portfolio first check

#### Simulation: Full Trading Day

```bash
# Simulate 9:35 AM (market open)
python cron_agent.py --daemon --sim-time "09:35" --dry-run

# Then check what would have run
python cron_agent.py --list-jobs
```

#### Run Just Exit Evaluations

```bash
# No direct category, but you can run by time:
python cron_agent.py --run-time 15:45    # Swing exits
python cron_agent.py --run-time 15:30    # Daytrade exits
```

#### Continuous Background Execution

```bash
# Start daemon, write to log, run in background
nohup python cron_agent.py --daemon \
  > scanner_output/logs/cron_agent_daemon.log 2>&1 &

# Kill daemon
pkill -f "python cron_agent.py --daemon"

# Monitor log
tail -f scanner_output/logs/cron_agent_daemon.log
```

### Migration from Crontab

**Old approach (crontab):**
```bash
# In crontab -e:
35 9 * * 1-5 cd /path/to/stocksBreakout && python3 breakout_scanner.py ...
```

**New approach (cron_agent):**
```bash
# Run immediately:
python cron_agent.py --run-now swing

# Or start daemon:
python cron_agent.py --daemon
```

### Troubleshooting

#### "No jobs found in category"

```bash
# Check available categories:
python cron_agent.py --list-jobs
```

#### Job didn't run

```bash
# Verify parsing:
python cron_agent.py --list-jobs --category swing

# Try dry-run to see command:
python cron_agent.py --run-now swing --dry-run

# Check logs:
tail -f scanner_output/logs/cron_agent.log
```

#### Daemon not executing jobs

```bash
# Test with simulated time:
python cron_agent.py --daemon --sim-time "09:35" --dry-run

# If it shows "Would execute", daemon should work
```

#### Command replacement issues

Commands like `$PYTHON_BIN` are automatically replaced with `python3`. If you see errors like `python3: command not found`, ensure Python is in PATH:

```bash
which python3
python3 --version
```

### Architecture

- **Parsing**: Reads `cron_jobs.txt`, extracts 29 jobs with full cron syntax
- **Execution**: Runs via `subprocess.run()` with 900-second timeout
- **Scheduling**: Checks `datetime.now()` against cron fields every 30 seconds
- **Logging**: Captures stdout/stderr and logs to file
- **Monitoring**: Pings healthchecks.io on completion

### Performance Notes

- **Memory**: ~15 MB (lightweight)
- **CPU**: Minimal (sleeps between checks)
- **Timeout**: 15 minutes per job (configurable)
- **Check interval**: 30 seconds (configurable)

### Mar 2026 Fix: Step-value handling

**Problem**: `*/15` (every 15 minutes) jobs were running **EVERY minute** (60 times/hour) instead of 4 times/hour.

**Root cause**: Step values parsed as wildcard `-1`, matching all times.

**Solution**: Redesigned to store lists of valid times:
- Old: `minute: int = -1` (lost pattern)
- New: `minute: List[int] = [0, 15, 30, 45]` (preserves pattern)

**Result**: ✅ Fixed—portfolio/monitor jobs now run correctly (4 times/hour vs 60).

---

## Automated Test Agent (`automated_test_agent.py`)

Full trading day simulator that runs complete scanner workflow on schedule, capturing live results and sending consolidated email/Telegram reports. Ideal for backtesting production workflows, validating new features, or running end-to-end tests.

### Purpose

- **Simulate a complete trading day** (8:00 AM — 4:15 PM ET) with all scanner modules
- **Run in parallel**: Breakout scanner (cron_agent) + MACD/RSI scanner + comparison reports
- **Capture results**: Each scheduled job output is logged and grepped for signals
- **Generate reports**: Email summary + Telegram notification with daily statistics
- **Dry-run mode**: Preview schedule without executing (testing only)

### What It Executes

The agent runs a structured schedule covering:

**Morning Phase (8:00–9:35 AM)**
- Pre-market gap scans (2 runs)
- Opening surge detection
- Parallel MACD/RSI scans
- Phase 1 breakout scans (daytrade + swing)

**Intraday Phase (9:45 AM–4:00 PM)**
- Hourly momentum watch monitors (every hour)
- 15-minute portfolio tracking
- Daytrade re-evaluation at 2 PM + 3:30 PM
- Parallel MACD/RSI updates

**Close Phase (4:00–4:15 PM)**
- Final portfolio snapshot
- End-of-day summary statistics

### Quick Start

#### 1. Dry-Run Mode (No Execution)

Preview the full schedule without running any commands:

```bash
python automated_test_agent.py --dry-run
```

Output shows:
- All scheduled jobs with timestamps (NY time)
- Command to be executed
- Checks/grepping rules for each job
- Estimated duration for each phase

#### 2. Run Full Day (Real Execution)

Schedule and execute all jobs for today's date (NY time):

```bash
python automated_test_agent.py
```

The agent will:
- Compute today's date in NY timezone
- Schedule all jobs using APScheduler
- Execute each job at its scheduled time
- Capture stdout/stderr from each command
- Grep output for signal counts, alerts, errors
- Sleep until next scheduled time
- Generate final email/Telegram report at 4:15 PM

#### 3. Execution Details

As the day progresses, the agent logs:

```
2026-03-13 08:00:00 | INFO     | Starting job: cron_0800 — [BREAKOUT] Premarket Gap Check #1
2026-03-13 08:00:05 | INFO     | ✓ Gap alerts: 12 new gaps detected (grep: scanner_output/logs/cron_premarket.log)
2026-03-13 08:00:10 | INFO     | Job completed in 5.2 seconds
2026-03-13 08:45:00 | INFO     | Starting job: cron_0845 — [BREAKOUT] Premarket Gap Check #2
...
2026-03-13 16:15:00 | INFO     | ✓ Job completed — generating daily report
2026-03-13 16:15:15 | INFO     | Report sent via email + Telegram
```

### Capture & Report

Each scheduled job includes **checks** (grep queries) that extract key metrics:

| Job | Checks |
|-----|--------|
| Premarket (8:00 AM) | Gap alerts from log, signal count |
| Opening Surge (9:31 AM) | Surge signals, volume expansion |
| Phase 1 Scans (9:35 AM) | CONTINUATION + SMA20_CROSS counts |
| Hourly Monitor (10:00–4:00 PM) | Watch alerts, portfolio alerts, alert count |
| Phase 2 Scan (2:00 PM) | New signals since 9:35 |
| EOD Summary (4:15 PM) | Total signals by type, total alerts sent, missed movers |

### Final Report

At 4:15 PM, the agent generates a summary report sent via email + Telegram:

```
🔔 AUTOMATED TEST AGENT — Trading Day Summary
Date: 2026-03-13 (Friday)

BREAKOUT SCANNER (cron_agent)
  Phase 1 (9:35 AM): 47 breakout signals
    • CONTINUATION: 23
    • SMA20_CROSS: 12
    • VOLUME_SPIKE: 9
    • Other: 3

  Phase 2 (2:00 PM): 18 new signals

  Total Watch Alerts Sent: 156
  Total Portfolio Alerts: 89
  Missed Movers: 5

MACD/RSI SCANNER (parallel)
  Daily Scan: 34 signals
  Intraday Scans: 127 signals
  Portfolio P&L: +2.3%

COMPARISON REPORT
  Agreement (both scanners): 18 signals
  Divergence: 63 signals
```

### Options

```bash
# Dry-run: preview without executing
python automated_test_agent.py --dry-run

# Run real schedule
python automated_test_agent.py

# Custom log output level
python automated_test_agent.py --log-level DEBUG
```

### When to Use

✅ **Running nightly backtests** to validate production workflows
✅ **Testing new scanner features** end-to-end before deploying
✅ **Validating cron schedule** matches market hours
✅ **Capturing metrics** for dashboard/reporting
✅ **Training new systems** with realistic daily workflow

---

## Regime Detection (`regime_detector.py`)

Detects current market regime (bull/bear/mixed) and automatically applies optimized parameters from the mode optimizer.

### Market regimes

| Regime | Characteristics | Optimal Strategy |
|--------|-----------------|------------------|
| **BULL** | SPY uptrend (price > SMA50 > SMA200), low volatility, >60% win rate | Aggressive: tight stops, wide targets |
| **BEAR** | SPY downtrend (price < SMA50 < SMA200), high volatility | Defensive: wider stops, tight targets |
| **MIXED** | Choppy market, neutral trend, high volatility | Balanced: moderate risk/reward |

### Indicators used

- **Trend**: SMA 50 vs SMA 200 (crossing, positioning)
- **Volatility**: ATR % and Bollinger Band width
- **Win rate**: % of days closing > open (trend confirmation)
- **Volume**: Recent vs historical average

### Parameter sets

6 optimized configurations (swing/daytrade × bull/bear/mixed) based on mode_optimizer backtests:

```
Swing Bull    (2023: +18%)   → aggressive (tight stops, wide targets)
Swing Mixed   (2024H1: +29%) → balanced
Swing Bear    (optimized)    → defensive

Daytrade Bull    (2023: +23%) → aggressive (fast entries/exits)
Daytrade Mixed   (2024H1: +24%) → balanced
Daytrade Bear    (optimized)  → defensive
```

### Usage

```bash
# Detect current regime
python regime_detector.py

# Show suggested parameters for swing mode
python regime_detector.py --suggest swing

# Detect regime & apply to config.py
python regime_detector.py --apply daytrade

# Use 200 days history instead of default 60
python regime_detector.py --days 200 --apply swing --notify
```

### Output

```
======================================================================
  MARKET REGIME DETECTION
======================================================================

  Regime: MIXED

  Metrics:
    Price:        $677.18
    SMA 50:       $687.59
    SMA 200:      $670.45
    ATR %:        1.29%
    BB Width:     1.57%
    Win Rate:     52.9%
    Vol Trend:    1.03x

  Suggested Parameters for SWING (regime: mixed):
    Balanced: mixed volatility (Mixed 2024H1: +29%)
    vol_thresh: 0.90
    atr_mult: 0.75
    sl_mult: 3.0
    tp_mult: 10.0
    min_rr: 0.55
    quality_filter: HIGH
```

### Integration with config.py

When you run `--apply MODE`, these parameters are updated:
- `vol_thresh`: Volume threshold multiplier
- `atr_mult`: ATR multiplier for position sizing
- `sl_mult`: Stop loss width multiplier
- `tp_mult`: Take profit width multiplier
- `min_rr`: Minimum risk/reward ratio

Changes are automatically applied to `config.py` with backup saved as `config.py.bak`.

---

## Disclaimer

This tool is for educational purposes. Trading involves substantial risk of loss. Always test on paper trading before using live funds. Past performance does not guarantee future results. Not financial advice.
