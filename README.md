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
12. [Momentum-Watch Monitor](#momentum-watch-monitor-monitor_watchpy)
13. [scanner_output/lists/ — Live Working Files](#scanner_outputlists--live-working-files)
14. [Backtest Results](#backtest-results)
15. [Streamlit Dashboard](#streamlit-dashboard)
16. [Notifications](#notifications)
17. [IB Connection](#ib-connection)
18. [Troubleshooting](#troubleshooting)

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

## Disclaimer

This tool is for educational purposes. Trading involves substantial risk of loss. Always test on paper trading before using live funds. Past performance does not guarantee future results. Not financial advice.
