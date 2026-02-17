# Breakout Scanner V5 - Complete Guide

## Quick Start

```bash
# Scan S&P 500 for swing breakouts (no IB needed)
python breakout_scanner.py "input/S&P_500.txt" --mode swing --mock --notify

# Run full backtest comparing all versions
python enhanced_backtest.py

# Launch web dashboard
streamlit run app.py

# Send portfolio daily report
python breakout_scanner.py dummy --portfolio-report
```

---

## Strategy Overview

The scanner detects breakout + pullback patterns, scores them on a 0-100 scale, and assigns quality tiers. Only the highest-scoring signals are acted on.

### Quality Tiers (V6)

| Tier | Score | Description |
|------|-------|-------------|
| GOLD | 90+ | Elite signals - passes 5 extra hard gates (R:R >= 3, above SMA, volume >= 2x, near 52w high, sector hot) |
| PREMIUM | 80+ | Strong signals - high conviction, strong volume + trend |
| HIGH | 65+ | Good signals - moderate conviction |
| STANDARD | 60+ | Marginal signals - weakest accepted |

### What Scores Highest

| Factor | Weight | Why |
|--------|--------|-----|
| Volume confirmation | 16 pts | Volume is king - proves institutional interest |
| Trend alignment | 16 pts | Don't fight the trend |
| Composite momentum | 13 pts | RSI + MACD + ADX + ROC combined |
| Conviction | 10 pts | Close position, volume surge, gaps, green streaks |
| Bullish pattern | 10 pts | Bull flag, ascending triangle, etc. (23 patterns) |
| Near 52w high | 8 pts | Stocks near highs tend to break higher |
| Pattern vol confirmed | 6 pts | Pattern confirmed by volume spike (V6) |
| Sector momentum | 6 pts | Hot sectors carry weaker names |
| RSI divergence | 5 pts | Bullish divergence = hidden strength |

### Backtest Results (Jan 2024 - Dec 2025)

| Config | Return | Sharpe | MaxDD | WinRate | vs SPY |
|--------|--------|--------|-------|---------|--------|
| V1-A HIGH+ baseline | +141.77% | 2.45 | -22.14% | 56.1% | +54.10% |
| V6X-A HIGH+, overextension | +129.13% | 2.62 | -13.56% | 60.3% | +41.45% |
| V6-A HIGH+, 10% cap | +107.59% | 2.15 | -26.85% | 55.5% | +19.91% |
| V6-B PREMIUM+, 10% cap | +60.57% | 1.27 | -19.27% | 55.4% | -27.10% |
| SPY Buy & Hold | +87.67% | 1.45 | -18.76% | N/A | baseline |

**Recommended production config: V6X-A** (HIGH+ with overextension filter, 10% position cap)
- Best risk-adjusted: Sharpe 2.62, MaxDD only -13.56%
- 60.3% win rate with 1.97:1 W/L ratio
- Beats SPY by +41.45% with 28% less drawdown

---

## Trading Modes

| Mode | Timeframe | Bars | Hold Period | Trend | Best For |
|------|-----------|------|-------------|-------|----------|
| `swing` | 1 day | Daily | Days to weeks | SMA-150 | Main strategy |
| `longterm` | 1 week | Weekly | Weeks to months | SMA-150 | Position trading |
| `daytrade` | 15 min | 15-min | Intraday | EMA-9 + VWAP | Active traders |
| `scalping` | 1 min | 1-min | Seconds to minutes | VWAP | Not for cron |

---

## CLI Reference

### Basic Scan

```bash
python breakout_scanner.py <watchlist> --mode <mode> [options]
```

**Required:**
- `<watchlist>` - Path to watchlist file (e.g., `input/S&P_500.txt`)

### Connection Options

| Flag | Description |
|------|-------------|
| (default) | Paper trading via IB Gateway (port 7497) |
| `--live` | Live trading via IB Gateway (port 7496) |
| `--mock` | No IB needed - uses yfinance data |

### Scan Options

| Flag | Description | Default |
|------|-------------|---------|
| `--mode <mode>` | Trading mode | `swing` |
| `--vol <float>` | Volume threshold override | Mode-specific |
| `--atr <float>` | ATR multiplier override | Mode-specific |
| `--lookback <int>` | Lookback period override | Mode-specific |
| `--tf <timeframe>` | Timeframe override | Mode-specific |
| `--bounce` | Also detect bounce/recovery signals | Off |
| `--sector-buzz` | Run sector momentum analysis before scan | Off |
| `--sentiment` | Enrich signals with web sentiment (needs TAVILY_API_KEY) | Off |
| `--level2` | Enable Level 2 market depth analysis (IB only) | Off |

### Output Options

| Flag | Description |
|------|-------------|
| `--notify` | Send results via email, Discord, Telegram |
| `--cron` | Silent mode - errors to console, rest to log file |
| `--auto-positions <file>` | Auto-append PREMIUM signals to positions CSV |
| `--export-premium <file>` | Export PREMIUM/GOLD tickers to file for re-evaluation |

### Exit Evaluation

| Flag | Description |
|------|-------------|
| `--exit-file <path>` | CSV with open positions to check for exit signals |
| `--both` | Run breakout scan AND exit evaluation together |

### Position Monitoring

| Flag | Description |
|------|-------------|
| `--monitor <files>` | Monitor positions for price drops (comma-separated CSVs) |

### Historical Simulation

| Flag | Description |
|------|-------------|
| `--simulate` | Run scan over historical data |
| `--sim-start YYYY-MM-DD` | Simulation start date |
| `--sim-end YYYY-MM-DD` | Simulation end date |
| `--sim-data-source <src>` | `auto` / `ib` / `yfinance` / `mock` |

### Portfolio

| Flag | Description |
|------|-------------|
| `--portfolio-report` | Send daily portfolio email and exit |

---

## Workflows

### Workflow 1: Daily Swing Trading (Recommended)

This is the primary workflow for swing traders. Run these steps in order.

**Step 1 - Morning scan (9:35 AM ET)**
```bash
python breakout_scanner.py "input/S&P_500.txt" \
  --mode swing --bounce --sector-buzz --sentiment \
  --auto-positions input/positions_swing.csv \
  --export-premium input/premium_swing.txt \
  --live --notify
```
- Scans full S&P 500 for breakouts + bounces
- Reports sector momentum
- Adds sentiment analysis
- Saves PREMIUM tickers for re-evaluation
- Auto-appends signals to positions CSV
- Sends email/Discord notification

**Step 2 - Review signals**
- Check email/Discord for scan results
- Or open `streamlit run app.py` to view signals on the web dashboard
- Focus on PREMIUM and GOLD signals only (V5-B config)

**Step 3 - Afternoon exit check (3:45 PM ET)**
```bash
python breakout_scanner.py "input/S&P_500.txt" \
  --mode swing --exit-file input/positions_swing.csv \
  --live --notify
```
- Evaluates open positions for exit signals
- Alerts if price hits stop or target

**Step 4 - After-hours re-evaluation (4:30 PM ET)**
```bash
python breakout_scanner.py input/premium_swing.txt \
  --mode swing --bounce --sentiment \
  --exit-file input/positions_swing.csv --both \
  --auto-positions input/positions_swing.csv \
  --live --notify
```
- Re-evaluates only PREMIUM tickers from morning
- Combined scan + exit evaluation
- Catches breakouts that developed during the day

**Step 5 - Portfolio report (evening)**
```bash
python breakout_scanner.py dummy --portfolio-report
```
- Sends daily portfolio summary with P&L, positions, performance

---

### Workflow 2: Weekly Long-Term Positioning

For position trades held weeks to months.

**Step 1 - Monday morning scan (9:00 AM ET)**
```bash
python breakout_scanner.py "input/S&P_500.txt" \
  --mode longterm --bounce --sector-buzz --sentiment \
  --export-premium input/premium_longterm.txt \
  --live --notify
```

**Step 2 - Monday exit evaluation (9:15 AM ET)**
```bash
python breakout_scanner.py "input/S&P_500.txt" \
  --mode longterm --exit-file input/positions_longterm.csv \
  --live --notify
```

---

### Workflow 3: Day Trading

For intraday breakouts on 15-min charts.

**Step 1 - Morning scan (9:35 AM ET)**
```bash
python breakout_scanner.py "input/S&P_500.txt" \
  --mode daytrade --bounce --sector-buzz --sentiment \
  --export-premium input/premium_daytrade.txt \
  --live --notify
```

**Step 2 - Mid-morning re-eval (10:00 AM ET)**
```bash
python breakout_scanner.py input/premium_daytrade.txt \
  --mode daytrade --bounce --sentiment \
  --live --notify
```

**Step 3 - Afternoon re-eval (2:00 PM ET)**
```bash
python breakout_scanner.py input/.txt \
  --mode daytrade --bounce --sentiment \
  --live --notify
```

**Step 4 - Exit check before close (3:30 PM ET)**
```bash
python breakout_scanner.py "input/S&P_500.txt" \
  --mode daytrade --exit-file input/positions_daytrade.csv \
  --live --notify
```

---

### Workflow 4: Position Monitoring (Every 15 min)

Tracks open positions for price drops toward stop loss.

```bash
python breakout_scanner.py "input/S&P_500.txt" \
  --monitor input/positions_swing.csv,input/positions_daytrade.csv \
  --live --notify
```
- Trails stops upward as price increases
- Alerts on large drops toward stop
- Run every 15 minutes during market hours (see cron_jobs.txt)

---

### Workflow 5: Signal Validation & Learning

Validates past signals to improve scoring weights over time.

**Step 1 - Validate recent signals (daily, 8:30 PM ET)**
```bash
python validate_signals.py validate-all --min-age-days 3
```
- Checks signals from 3+ days ago against actual price action
- Records MFE (max favorable excursion) and MAE (max adverse excursion)
- Stores outcomes in `scanner_output/outcomes/`

**Step 2 - Generate learning recommendations (weekly, Sunday)**
```bash
python validate_signals.py learn --min-signals 20
```
- Analyzes all accumulated outcomes
- Recommends weight adjustments
- Saves to `scanner_output/score_adjustments.json`
- Scanner reads this file on next run (read-only learning loop)

---

### Workflow 6: Backtesting

#### Quick backtest (all versions compared)
```bash
python enhanced_backtest.py
```
- Fetches 2 years of data for ~100 stocks (cached on disk after first run)
- Single-pass scan: V1, V2/V5, V5X all at once
- Tests 20+ configurations
- Compares against SPY buy-and-hold
- Outputs to `scanner_output/backtests/`

#### Regime validation (bear/bull/mixed)
```bash
python backtest_validation.py
```
- Tests strategy across 2022 bear, 2023-24 bull, and mixed periods
- Validates resilience across market regimes

#### Parameter optimization
```bash
python optimize_strategy.py
```
- Grid search over vol_thresh, atr_mult, lookback
- Finds optimal parameters for each mode

---

### Workflow 7: Web Dashboard

```bash
streamlit run app.py
```

Opens at http://localhost:8501 with these pages:

| Page | What It Does |
|------|-------------|
| **Scan** | Interactive scan - pick watchlist, mode, params. View results as cards. Click "Chart" for TradingView |
| **Portfolio** | Virtual portfolio - add/close positions, view P&L, daily/WTD/YTD tracking |
| **Chart** | TradingView chart viewer with entry/exit levels |
| **Backtest** | Run and view historical simulations |
| **Watchlists** | Create/edit/delete watchlist files |

---

## Cron Setup (Full Automation)

Install with `crontab -e` and paste from `cron_jobs.txt`. Key schedule:

| Time (ET) | Job | Mode |
|-----------|-----|------|
| Mon 9:00 AM | S&P 500 wide scan | longterm |
| Mon 9:15 AM | Exit evaluation | longterm |
| 9:35 AM daily | S&P 500 wide scan + export PREMIUM | swing + daytrade |
| 10:00 AM daily | Re-evaluate PREMIUM | daytrade |
| 2:00 PM daily | Re-evaluate PREMIUM | daytrade |
| 3:30 PM daily | Exit check before close | daytrade |
| 3:45 PM daily | Exit check before close | swing |
| 4:30 PM daily | Combined scan + exit | swing |
| 7:30 PM daily | Evening PREMIUM re-eval | swing |
| 8:20 PM daily | Evening PREMIUM re-eval | daytrade |
| 8:30 PM daily | Signal validation | validate |
| 9:45 AM - 4:00 PM /15min | Position monitoring | monitor |
| Sun 9:00 PM | Learning recommendations | learn |
| Sun 11:00 PM | Clean logs + archives | maintenance |

**Requirements:**
- Set `TZ=America/New_York` at top of crontab
- IB Gateway running on port 7496 (live) or 7497 (paper)
- `.env` file with `GMAIL_APP_PASSWORD`, `DISCORD_WEBHOOK_URL`, `TAVILY_API_KEY`
- Optional: healthchecks.io UUIDs for remote monitoring

---

## File Formats

### Watchlist (input)
```
# One symbol per line, or comma-separated
AAPL
MSFT, GOOGL, TSLA
# Lines starting with # are comments
### Section headers are ignored
NVDA
```

### Positions CSV (input for --exit-file and --monitor)
```csv
symbol,mode,entry,stop,target,timeframe
AAPL,swing,185.50,180.00,195.00,1 day
NVDA,daytrade,520.30,515.00,530.00,15 mins
```

### Environment Variables (.env)
```bash
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
export NOTIFY_RECIPIENTS="you@email.com, other@email.com"
export DISCORD_WEBHOOK_URL=https://discordapp.com/api/webhooks/...
export TAVILY_API_KEY=tvly-dev-...
```

---

## Output Structure

```
scanner_output/
  signals/        # Breakout signal CSVs (one per scan)
  exits/          # Exit evaluation CSVs
  rejections/     # Near-miss symbols + rejection reasons
  logs/           # Cron and scan logs
  backtests/      # Backtest result JSONs
  portfolio/      # portfolio.json + daily snapshots
  outcomes/       # Signal validation outcomes
  cache/          # Parquet disk cache for yfinance data
  score_adjustments.json  # Learning loop weight recommendations
```

---

## Portfolio Manager (Python API)

```python
from portfolio import Portfolio

p = Portfolio()                               # Load or create portfolio.json

# Add position from a scan signal
p.add_position(signal_dict, shares=100, sector='Technology')

# Close position
p.close_position('AAPL', exit_price=190.00)

# Update all prices (fetches from yfinance)
p.update_prices()

# Take daily snapshot (for WTD/YTD tracking)
p.daily_snapshot()

# Get summary
summary = p.get_summary()
# Returns: cash, market_value, total_value, total_pnl, total_pnl_pct,
#          daily_pnl, wtd_pnl, ytd_pnl

# Get performance metrics
perf = p.get_performance()
# Returns: sharpe, max_drawdown_pct, win_rate, total_trades, avg_hold_days

# Send daily email report
p.send_daily_report()
```

---

## V6 Features (Current)

| Feature | Description | Flag |
|---------|-------------|------|
| Composite momentum scoring | RSI + MACD + ADX + ROC combined (0-100) | `use_scoring=True` (default) |
| 23 pattern detectors | 12 chart patterns + 11 candlestick patterns | Automatic |
| Pattern volume confirmation | Patterns scored higher when volume confirms | Automatic (6 pts) |
| Overextension filter | Rejects stocks stretched too far from SMA | `use_v4_overextension=True` (default) |
| Learning loop | Reads score_adjustments.json to tune weights | Automatic on startup |
| 52-week high proximity | Bonus score for stocks near 52w high | Automatic |
| RSI divergence | Detects bullish/bearish RSI divergence | Automatic |
| Sector momentum | Pre-computes sector buzz, passes to scorer | `--sector-buzz` |
| Multi-timeframe | Weekly confirmation for swing PREMIUM signals | Automatic (live only) |
| GOLD tier | Elite quality gate above PREMIUM | Automatic |
| Parquet disk cache | Caches yfinance OHLCV data to disk | Automatic |
| Virtual portfolio | JSON-based portfolio with snapshots | `portfolio.py` |
| Daily email reports | Portfolio summary via email/Discord | `--portfolio-report` |

### Pattern Reference

**Chart Patterns (12):**

| Pattern | Type | Direction | Volume Check |
|---------|------|-----------|--------------|
| Bull Flag | Continuation | Bullish | Pole high vol, flag low vol, breakout rising |
| Bear Flag | Continuation | Bearish | Pole high vol, flag low vol, breakdown rising |
| Ascending Triangle | Consolidation | Bullish | Contracting vol, spike on breakout |
| Descending Triangle | Consolidation | Bearish | Contracting vol, spike on breakdown |
| Symmetrical Triangle | Consolidation | Neutral | Contracting vol during formation |
| Cup and Handle | Continuation | Bullish | Low vol at cup bottom, rising on breakout |
| Inverse Head & Shoulders | Reversal | Bullish | Rising vol from left shoulder to breakout |
| Head & Shoulders | Reversal | Bearish | Spike on neckline breakdown |
| Double Bottom | Reversal | Bullish | Lower vol on 2nd bottom, spike on breakout |
| Double Top | Reversal | Bearish | Spike on neckline breakdown |
| Rectangle Breakout | Consolidation | Bullish | Low vol in range, spike on breakout |
| Rectangle Breakdown | Consolidation | Bearish | Low vol in range, spike on breakdown |

**Candlestick Patterns (11):**

| Pattern | Direction | Confirmation |
|---------|-----------|--------------|
| Hammer | Bullish | Above-avg volume on candle |
| Inverted Hammer | Bullish | Above-avg volume on candle |
| Hanging Man | Bearish | Above-avg volume on candle |
| Bullish Engulfing | Bullish | Above-avg volume on candle |
| Bearish Engulfing | Bearish | Above-avg volume on candle |
| Bullish Harami | Bullish | Above-avg volume on candle |
| Bearish Harami | Bearish | Above-avg volume on candle |
| Morning Star | Bullish | Above-avg volume on 3rd candle |
| Evening Star | Bearish | Above-avg volume on 3rd candle |
| Bullish Doji | Bullish | Above-avg volume on doji |
| Bearish Doji | Bearish | Above-avg volume on doji |

---

## Debugging

| Problem | Solution |
|---------|----------|
| No signals found | Check `scanner_output/rejections/` for reasons |
| IB connection fails | Use `--mock` or ensure IB Gateway is running |
| Email not sending | Check `.env` has valid `GMAIL_APP_PASSWORD` (generate new app password at Google) |
| Backtest slow (first run) | Normal - fetching data. Subsequent runs use parquet cache (~50x faster) |
| Want to clear cache | `python -c "from yfinance_adapter import YFinanceAdapter; YFinanceAdapter().clear_disk_cache()"` |
| Wrong timeframe | Check `market_data.py` `_normalize_timeframe()` - IB needs `1W` not `1 week` |
| Stale signals | Check `score_adjustments.json` - learning loop may be downweighting features |
