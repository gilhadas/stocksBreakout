# stocksBreakout Skills Guide

This guide describes the custom Claude Code skills available in this project. Skills are shortcuts for running common trading and analysis tasks.

## Quick Start

Skills are invoked via the `/skillname` syntax in Claude Code. For example:

```bash
/scan --mode swing --notify
/backtest --period 2024 --compare configs
/monitor --once
```

---

## Skills Overview

| Skill | Purpose | Use Case |
|-------|---------|----------|
| **scan** | Run breakout signal scans | Generate trading signals in real-time |
| **backtest** | Compare strategy performance across periods/configs | Validate strategy changes, optimize parameters |
| **monitor** | Track open positions for exits | Watch trades during market hours |
| **analyze-market** | Analyze market regime and strategy fit | Decide when to trade aggressively vs cautiously |

---

## Skill: `scan`

**Aliases:** `scan`, `breakout`

Run breakout detection scans across your watchlist with intelligent defaults.

### Parameters

| Parameter | Type | Default | Options | Description |
|-----------|------|---------|---------|-------------|
| `mode` | string | `swing` | `swing`, `daytrade`, `longterm`, `scalping` | Trading timeframe and analysis depth |
| `<watchlist>` | string | `input/S&P_500.txt` | file path | Which symbols to scan (first positional arg) |
| `premium` | boolean | `false` | — | Export only PREMIUM/GOLD signals (filters noise) |
| `sentiment` | boolean | `false` | — | Enrich with sentiment data (FinBERT headlines) |
| `notify` | boolean | `false` | — | Send Discord/Email/Telegram notifications |

### Examples

```bash
# Scan swing mode on default watchlist
/scan

# Scan with notifications enabled
/scan --notify

# Scan daytrade mode, premium signals only, with sentiment
/scan --mode daytrade --premium --sentiment

# Scan custom watchlist
/scan input/optimizer_watch.txt --mode swing

# Scan longterm, notify on PREMIUM only
/scan input/ALL.txt --mode longterm --premium --notify
```

### Output

- **CSV file:** `scanner_output/signals/signals_{mode}_{timestamp}.csv`
- **Columns:** Symbol, Price, Stop, Target, Quality, Patterns, FinBERT, Earnings_Date, etc.
- **Discord:** Shows top signals if `--notify` (PREMIUM+ filter + Minervini≥7)
- **Console:** Real-time progress, rejections, market regime alert

### Tips

- **Use `--sentiment`** for high-conviction signals (adds ~20% processing time but provides NLP insight)
- **Use `--premium`** to reduce noise when monitoring live (only highest-conviction signals)
- **Run `--notify` only once per scan** — multiple runs will spam Discord
- **Earnings column** is now added automatically — check `Earnings_Warning` for imminent earnings

---

## Skill: `backtest`

**Aliases:** `backtest`, `test`

Run historical backtests to compare strategy configurations, modes, or versions.

### Parameters

| Parameter | Type | Default | Options | Description |
|-----------|------|---------|---------|-------------|
| `period` | string | `2024` | `2024`, `2025`, `last-month`, `YYYY-MM-DD:YYYY-MM-DD` | Historical period to test |
| `mode` | string | `swing` | `swing`, `daytrade`, `longterm` | Trading mode to test |
| `compare` | string | — | `v1-vs-v2`, `configs`, `modes` | What to compare (version, config, or trading mode) |
| `optimize` | string | — | `rsi_overbought`, `atr_mult`, `consolidation` | Parameter to auto-optimize |

### Examples

```bash
# Quick backtest: 2024 on swing mode
/backtest

# Compare V1 vs V2 in 2024
/backtest --period 2024 --compare v1-vs-v2

# Compare all configs (swing vs daytrade vs longterm)
/backtest --period 2024 --compare configs

# Optimize ATR multiplier for 2024
/backtest --period 2024 --optimize atr_mult

# Full year 2025 comparison
/backtest --period 2025 --compare v1-vs-v2

# Custom date range
/backtest --period 2025-01-01:2025-06-30 --compare configs
```

### Output

- **JSON file:** `scanner_output/backtests/multi_config_vs_spy_{start}_{end}.json`
- **Metrics:** Return%, Sharpe, MaxDD, Win Rate, Best/Worst trade
- **Console:** Summary table with ranking of strategies
- **Comparison:** Plots or tables showing which config/mode won

### Tips

- **Use `--compare configs`** to decide between PREMIUM filter vs HIGH quality signals
- **Use `--optimize`** to find the sweet spot for ATR distance or consolidation tightness
- **V9-C is the recommended config** for live trading (beat SPY 2024-2025)
- **2024 was a strong bull market** — results may not reflect choppy market performance

---

## Skill: `monitor`

**Aliases:** `monitor`, `watch`, `positions`

Real-time monitoring of open positions. Track for stop hits, trend breaks, or fading moves.

### Parameters

| Parameter | Type | Default | Options | Description |
|-----------|------|---------|---------|-------------|
| `positions` | string | `portfolio` | `portfolio`, `CSV file path(s)` | Where to read positions (default: auto_portfolio.json, or custom CSV) |
| `interval` | integer | `15` | 1–60 | Check frequency in minutes (ignored if `once=true`) |
| `once` | boolean | `true` | — | Run once instead of continuous loop |

### Examples

```bash
# Check positions once (default)
/monitor

# Monitor every 5 minutes continuously
/monitor --interval 5 --once false

# Monitor specific position file
/monitor --positions scanner_output/lists/positions_swing_mock.csv

# Monitor multiple position files
/monitor --positions "scanner_output/lists/positions_swing_mock.csv,scanner_output/lists/positions_daytrade_mock.csv"

# Quick check before market close
/monitor --once
```

### Output

- **Console:** Current price, vs open %, entry date, Fib retracement level
- **State file:** `scanner_output/.watch_monitor_YYYYMMDD.json` (tracks status changes)
- **Discord:** Alerts only when status changes (HOLDING → FADING → FAILED)
- **Columns:**
  - `Status`: HOLDING (≥open), RECOVERING, FADING (3% below), FAILED (2% below)
  - `Fib Level`: nearest retracement (0% = high, 100% = low)
  - `Vol Ratio`: intraday volume vs 20-day avg
  - `3-bar Trend`: direction over last 3 bars

### Tips

- **Run in cron every 15 min** during market hours for automation
- **Fib retracement** shows how much of the intraday move is being given back (61.8% = danger zone)
- **State file prevents spam** — alerts only on transitions, not every check
- **No CSV writes** — monitoring is read-only (safe to run in parallel)

---

## Skill: `analyze-market`

**Aliases:** `analyze-market`, `market`, `regime`

Analyze current market regime and how it impacts strategy performance.

### Parameters

| Parameter | Type | Default | Options | Description |
|-----------|------|---------|---------|-------------|
| `period` | string | `1week` | `1week`, `1month`, `1quarter` | Time window for regime analysis |
| `forecast` | boolean | `false` | — | Include machine-learning regime forecast |
| `compare-regimes` | boolean | `false` | — | Show how strategy performs in each regime |

### Examples

```bash
# Quick 1-week market analysis
/analyze-market

# Detailed monthly analysis with regime forecast
/analyze-market --period 1month --forecast

# Compare signal performance across market regimes
/analyze-market --period 1quarter --compare-regimes

# Full quarter analysis with forecast
/analyze-market --period 1quarter --forecast --compare-regimes
```

### Output

- **SPY performance:** % gain, volatility (ATR %), trend (up/down/choppy)
- **Regime classification:** EXPANSION (>5% SPY move), NORMAL, or CHOPPY (<1% SPY move)
- **Strategy impact:** How thresholds adjust per regime
- **Forecast (optional):** Predicted regime for next 1–5 days
- **Regime performance (optional):** Win rate + Avg gain per regime

### Regime Impact on Strategy

| Regime | SPY Move | Strategy Adjustment | Best Config |
|--------|----------|------------------|------------|
| **EXPANSION** | >5% | Thresholds loosen 10% (easier to trigger) | V8-C (most signals) |
| **NORMAL** | 1–5% | Standard thresholds | V9-C (balanced) |
| **CHOPPY** | <1% | Thresholds tighten 30% (fewer false breakouts) | V10MX-A (VCP focus) |

### Tips

- **Use before market open** to decide how aggressively to trade
- **CHOPPY regime** is when VCP and consolidation quality matter most
- **EXPANSION regime** favors momentum + looser filters (gap plays work well)
- **Forecast is ML-based** — good for context, not a crystal ball

---

## Running Skills from Command Line

Skills are Python scripts in the `skills/` directory. You can also run them directly:

```bash
# Same as /scan
python skills/scan.py --mode swing --notify

# Same as /backtest
python skills/backtest.py --period 2024 --compare configs

# Same as /monitor
python skills/monitor.py --once

# Same as /analyze-market
python skills/analyze_market.py --period 1month --forecast
```

---

## Skill Configuration

Skills are defined in `.claude/skills.json`:

```json
{
  "skills": [
    {
      "name": "scan",
      "description": "Run trading scans with intelligent defaults",
      "command": "python skills/scan.py",
      "aliases": ["scan", "breakout"],
      "tags": ["trading", "signals", "breakout"],
      "timeout": 60,
      "parameters": [...]
    }
  ]
}
```

### To add a new skill:

1. Create a Python script in `skills/` directory
2. Add entry to `.claude/skills.json` with name, command, parameters
3. Use `/skillname` in Claude Code to invoke

---

## Common Workflows

### Morning: Pre-market Setup

```bash
/analyze-market --period 1week --forecast
/scan input/optimizer_watch.txt --mode swing --premium
/monitor --once
```

### Intraday: Monitor + Adapt

```bash
/monitor --interval 15 --once false  # runs every 15 min
/scan input/ALL.txt --mode daytrade --notify
```

### Post-Market: Backtest Learning

```bash
/backtest --period 2024 --compare configs
/analyze-market --period 1quarter --compare-regimes
```

### Weekly: Strategic Review

```bash
/backtest --period 2025-01-01:2025-12-31 --optimize atr_mult
/analyze-market --period 1month --forecast
```

---

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| "No signals found" | Watchlist empty or all symbols rejected | Check watchlist file; try `--mode swing` (less strict) |
| Backtest too slow | Large watchlist + complex comparison | Reduce symbol count; use `--period 2024` instead of full range |
| Monitor not alerting | Discord not configured or `once=true` | Check `config.py` Discord webhook; set `--once false` for continuous |
| Skills not found | `.claude/skills.json` not loaded | Restart Claude Code; verify `.claude/` directory exists |

---

## References

- Full scanner docs: [README.md](README.md)
- V9-C recommended config (beats SPY): [Signal Scoring](README.md#signal-scoring-v12--optuna-optimized-weights)
- Earnings warning feature: [Earnings Date Warning](README.md#earnings-date-warning)
- Pre-market monitor: [Pre-Market Monitor](README.md#pre-market-monitor-premarket_monitorpy)

