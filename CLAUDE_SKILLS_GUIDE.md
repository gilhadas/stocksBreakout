# Claude Skills & Subagents Guide for stocksBreakout

This guide explains how to use Claude Skills and Subagents to enhance your interaction with the stocksBreakout trading scanner.

## 🎯 Available Skills

Skills are single-command shortcuts that invoke specialized Claude agents to perform complex tasks. Use `/skillname` in your conversation.

### 1. `/scan` — Run Trading Scans
**Purpose**: Execute breakout scans with smart defaults for different trading modes
**When to use**: Quick daily scans, market checks, signal generation

```bash
/scan --mode swing --watchlist S&P_500.txt
/scan --mode daytrade --premium
/scan --mode longterm --sentiment
```

**What it does**:
- Runs breakout_scanner.py with intelligent parameter selection
- Fetches current market regime (choppy/red/expansion)
- Enriches signals with sentiment data if available
- Generates readable summary of PREMIUM signals
- Auto-appends positions to portfolio files
- Sends notifications

**Related files**: `breakout_scanner.py`, `orchestrator.py`, `scanner.py`

---

### 2. `/backtest` — Run Strategy Backtests
**Purpose**: Test strategy performance on historical data
**When to use**: Validating signal quality, comparing configurations, stress-testing

```bash
/backtest --period 2024 --mode swing
/backtest --compare v1-vs-v2 --period 2025-01-01:2025-12-31
/backtest --optimize momentum --mode daytrade
```

**What it does**:
- Backtests scanner signals on historical OHLCV data
- Compares actual vs. benchmark (SPY) returns
- Generates metrics: Sharpe ratio, max drawdown, win rate, W/L ratio
- Creates comparison reports (V1 vs V2, configs, modes)
- Identifies which indicator combinations work best
- Saves detailed signal-by-signal performance CSV

**Related files**: `enhanced_backtest.py`, `mock_trader.py`, `backtest_validation.py`

---

### 3. `/monitor` — Portfolio Monitoring
**Purpose**: Real-time price tracking of open positions with drop alerts
**When to use**: During trading hours, active position management

```bash
/monitor
/monitor --positions swing daytrade
/monitor --interval 5min
```

**What it does**:
- Fetches current prices for all open positions
- Calculates P&L% and distance to stop loss
- Classifies position status: OK / FALLING / NEAR_STOP / HIT_STOP
- Sends alerts when positions approach stops
- Maintains alert history to prevent duplicate notifications
- Shows dashboard table with all positions

**Related files**: `market_data.py`, `breakout_scanner.py` (monitor mode)

---

### 4. `/validate-signals` — Signal Quality Analysis
**Purpose**: Track which signals work and which don't over time
**When to use**: Strategy improvement, learning from outcomes

```bash
/validate-signals --min-age 3days
/validate-signals --generate-report
/validate-signals --learn
```

**What it does**:
- Compares old signals against current price
- Calculates actual win rate, R:R ratio achieved, Sharpe
- Tags signals as WIN/LOSS/INCONCLUSIVE
- Identifies patterns in winning vs losing signals
- Generates learning recommendations
- Exports results for further analysis

**Related files**: `validate_signals.py` (if exists), signal CSV files

---

### 5. `/optimize` — Parameter Optimization
**Purpose**: Find best parameters for your trading mode
**When to use**: Strategy tuning, quarterly reviews

```bash
/optimize --mode swing --param rsi_overbought
/optimize --mode daytrade --param atr_mult
/optimize --compare-modes
```

**What it does**:
- Sweeps through parameter ranges using grid search
- Tests each parameter set on historical data
- Ranks by Sharpe ratio, return, max drawdown, win rate
- Identifies optimal values for RSI, MACD, ADX, ATR
- Generates performance curves showing sensitivity
- Recommends best config for your preferred metric

**Related files**: `beat_spy_optimizer.py`, `enhanced_backtest.py`

---

### 6. `/cron-setup` — Automated Scan Scheduling
**Purpose**: Configure and manage automated scan schedules
**When to use**: Initial setup, updating cron jobs, troubleshooting

```bash
/cron-setup --install swing
/cron-setup --list
/cron-setup --test longterm
```

**What it does**:
- Sets up cron jobs for automated scans
- Configures timezones (US Eastern by default)
- Sets up Healthchecks.io monitoring
- Verifies cron syntax and log paths
- Tests individual cron jobs manually
- Lists all active scheduled scans

**Related files**: `cron_jobs.txt`, `setup_cron.sh`

---

### 7. `/analyze-market` — Market Regime Analysis
**Purpose**: Understand current market conditions and their impact on strategy
**When to use**: Before entering new positions, market checks

```bash
/analyze-market --period 1week
/analyze-market --forecast
/analyze-market --compare-regimes
```

**What it does**:
- Fetches SPY performance and volatility
- Classifies market regime: CHOPPY / NORMAL / EXPANSION
- Shows regime impact on historical signal success rates
- Forecasts expected return and Sharpe based on regime
- Identifies best trading modes for current regime
- Suggests position sizing adjustments

**Related files**: `utils.py`, `market_data.py`, `config.py` (REGIME_CONFIG)

---

### 8. `/position-report` — Portfolio Analytics
**Purpose**: Detailed analysis of current open positions
**When to use**: Daily reviews, risk assessment, rebalancing

```bash
/position-report
/position-report --risk
/position-report --compare-modes
```

**What it does**:
- Lists all open positions with entry, stop, target
- Calculates portfolio risk: total exposure, margin used, max loss
- Shows expected value per position
- Identifies correlation between positions
- Recommends position adjustments
- Generates exit checklist

**Related files**: `positions_swing_mock.csv`, `positions_daytrade_mock.csv`

---

## 🤖 Available Subagents

Subagents are more powerful agents that handle multi-step, autonomous research and analysis tasks. Invoke via the `/task` command or directly in the Agent SDK.

### 1. **research-specialist** — Deep Research Agent
**Use for**: Understanding market trends, news research, fundamental analysis

```
Agent: research-specialist
Task: Research why tech stocks have been declining this week and how it impacts momentum breakouts
```

**Capabilities**:
- Web search for financial news and analysis
- Reads multiple financial sources
- Cross-references technical data with news
- Synthesizes findings into actionable insights

---

### 2. **backtest-analyzer** — Advanced Backtesting Agent
**Use for**: Complex backtest analysis, parameter sweeps, optimization

```
Agent: backtest-analyzer
Task: Compare V1 vs V2 scoring on 2024 data, identify where V2 outperforms
```

**Capabilities**:
- Runs backtests with multiple configurations
- Generates comparison reports
- Creates performance visualizations
- Identifies regime-specific performance patterns

---

### 3. **signal-quality-auditor** — Signal Validation Agent
**Use for**: Comprehensive signal quality analysis and improvement

```
Agent: signal-quality-auditor
Task: Analyze all PREMIUM signals from Feb 2026, identify false positives
```

**Capabilities**:
- Validates signals against price action
- Calculates actual win rates
- Identifies correlation with market regime
- Recommends filter improvements

---

### 4. **market-microstructure** — Level 2 & Order Flow Agent
**Use for**: Order book analysis, spread analysis, liquidity checks

```
Agent: market-microstructure
Task: Check bid-ask spreads for current PREMIUM signals, alert if illiquid
```

**Capabilities**:
- Fetches Level 2 market data
- Analyzes order flow patterns
- Checks liquidity conditions
- Identifies potential slippage issues

---

### 5. **risk-management** — Portfolio Risk Agent
**Use for**: Risk assessment, position sizing, correlation analysis

```
Agent: risk-management
Task: Calculate current portfolio risk, suggest optimal position sizing for new signal
```

**Capabilities**:
- Calculates portfolio Greeks (delta, gamma, vega)
- Simulates correlation between positions
- Computes max loss scenarios
- Recommends Kelly Criterion position sizing

---

## 🔗 Workflow Examples

### Daily Trading Morning Checklist
```
1. /analyze-market --period 1week
   → Understand market regime for the day
2. /position-report --risk
   → Review overnight gap risk, current exposure
3. /scan --mode swing --watchlist S&P_500.txt
   → Generate fresh swing signals
4. /scan --mode daytrade --premium
   → Focus on yesterday's PREMIUM tickers
```

### Weekly Strategy Review
```
1. /position-report --compare-modes
   → Analyze which mode is most profitable
2. /validate-signals --min-age 7days
   → Check signal quality from last week
3. /backtest --period last-month --compare v1-vs-v2
   → Validate that V2 scoring is working
4. /optimize --mode swing --param rsi_overbought
   → Fine-tune parameters based on recent performance
```

### Monthly Deep Dive
```
1. /backtest --period last-quarter --compare-modes
   → Which mode won the most?
2. /analyze-market --compare-regimes
   → Performance breakdown by regime
3. /optimize --all-params --mode swing
   → Full parameter sweep
4. /analyze-market --forecast
   → Predictions for next month
```

### Setting Up Automation
```
1. /cron-setup --list
   → See what's currently scheduled
2. /cron-setup --test swing
   → Manually run a swing trade cron job
3. /cron-setup --install daytrade
   → Add daytrade scans to cron
4. /monitor --interval 15min
   → Check on open positions frequently
```

---

## 💡 Tips & Best Practices

### Combining Skills
- Run `/analyze-market` **before** `/scan` to understand market conditions
- Run `/backtest` **after** modifying scanner.py parameters
- Run `/validate-signals` **before** `/optimize` to establish baseline

### Using the CLI
```bash
# Skills can also be invoked from CLI
python breakout_scanner.py --skill scan --mode swing

# Combine with --notify to send results
python breakout_scanner.py --skill backtest --period 2024 --notify

# Use --cron to suppress interactive prompts
python breakout_scanner.py --skill cron-setup --install all --cron
```

### Interpreting Results
- **Sharpe Ratio > 1.0** = excellent risk-adjusted returns
- **Win Rate > 55%** = statistically significant edge
- **Max Drawdown < -20%** = manageable risk
- **W/L Ratio > 1.5** = strong signal quality

### When to Adjust Parameters
- If win rate < 50% → market regime has changed, re-optimize
- If regime is CHOPPY → disable new entries, focus on exits
- If max drawdown > 25% → reduce position size by 25%
- If Sharpe drops below 0.8 → backtest and compare configurations

---

## 📚 Related Documentation
- [README.md](README.md) — Project overview and quick start
- [cron_jobs.txt](cron_jobs.txt) — Automated scan schedules
- [config.py](config.py) — All tunable parameters
- [WEBHOOK_GUIDE.md](WEBHOOK_GUIDE.md) — Auto-trading webhooks
- [ALGO_TRADING_GUIDE.md](ALGO_TRADING_GUIDE.md) — Algorithmic execution

---

## 🔐 Security Notes
- Config file contains real API credentials → never commit to git
- Notifications go to Discord/Email/Telegram → verify webhook URLs
- Live trading requires valid IB account → use paper first!
- Monitor position limits → avoid over-leverage

---

## 🚀 Getting Started
1. Copy `config-example.py` to `config.py` and fill in your details
2. Run `/scan --mode swing --watchlist S&P_500.txt` for first test
3. Review results, check Sharpe/Win Rate/Max DD
4. Run `/backtest --period 2024 --mode swing` to validate
5. Set up cron jobs with `/cron-setup --install swing daytrade`
6. Use `/monitor` during trading hours for position checks
