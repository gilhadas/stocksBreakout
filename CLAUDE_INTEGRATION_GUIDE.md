# Claude Integration Guide for stocksBreakout

Quick reference for using Claude Code, Skills, and Subagents with your trading system.

## 🚀 Quick Start (5 minutes)

### 1. First Scan
```bash
/scan --mode swing --watchlist S&P_500.txt
```

This will:
- Run a swing trade scan
- Check market regime
- Show PREMIUM signals
- Auto-append to positions file

### 2. Check Positions
```bash
/monitor
```

This will:
- Fetch current prices for all open positions
- Alert if any are near stop loss
- Show portfolio dashboard

### 3. Analyze Market
```bash
/analyze-market --period 1week
```

This will:
- Calculate SPY performance and volatility
- Classify market regime
- Show impact on each trading mode
- Recommend position sizing

## 📋 Command Reference

### Daily Commands
```bash
# Morning: Check market and scan
/analyze-market --period 1week
/scan --mode swing
/scan --mode daytrade --premium

# During day: Monitor positions
/monitor

# End of day: Review performance
/position-report
```

### Weekly Commands
```bash
# Validate signals from last week
/validate-signals --min-age 3days

# Backtest latest changes
/backtest --period last-week --mode swing

# Deep analysis (uses subagent)
claude ask "Analyze signal quality from last week"
```

### Monthly Commands
```bash
# Full backtest with comparisons
/backtest --period last-month --compare v1-vs-v2

# Parameter optimization (uses subagent)
claude ask "Find optimal RSI threshold for daytrade"

# Risk management review (uses subagent)
claude ask "Calculate portfolio risk and optimal position sizing"
```

## 🎯 Common Use Cases

### "I want to verify the scanner is working correctly"
```bash
1. /scan --mode swing --watchlist input/premium_swing.txt
2. Check output in scanner_output/signals/
3. /backtest --period last-month --mode swing
4. Compare return metrics vs SPY
```

### "I think the market is about to turn choppy"
```bash
1. /analyze-market --forecast --compare-regimes
2. Review position-sizing recommendations
3. Reduce position size or stop new entries
4. Focus on exits instead of entries
```

### "Some signals are failing - let me debug"
```bash
1. /validate-signals --min-age 7days
2. Claude ask: "Which patterns have the lowest win rate?"
3. Check which sectors/patterns are failing
4. /optimize --mode swing --param rsi_overbought
5. Backtest new parameters
```

### "I want to automate my scans"
```bash
1. /cron-setup --install swing daytrade
2. /cron-setup --list  (verify setup)
3. /cron-setup --test swing  (run one manually)
4. Set up Healthchecks.io for monitoring
```

### "Which trading mode is most profitable?"
```bash
1. /backtest --period 2024 --compare-modes
2. Claude ask: "Analyze performance by mode"
3. Review Sharpe ratio, max drawdown, win rate
4. Allocate capital to best-performing mode
```

## 💾 Key Files & Directories

```
stocksBreakout/
├── breakout_scanner.py          ← Main entry point
├── config.py                    ← Your parameters (keep secret!)
├── scanner.py                   ← Breakout detection
├── indicators.py                ← Technical indicators
├── orchestrator.py              ← Coordinates scanning
├── market_data.py               ← Fetches price data
├── notifier.py                  ← Sends alerts
├── skills/                      ← Claude Skills
│   ├── scan.py                  ← /scan skill
│   ├── backtest.py              ← /backtest skill
│   ├── monitor.py               ← /monitor skill
│   └── analyze_market.py         ← /analyze-market skill
├── input/                       ← Your watchlists & positions
│   ├── S&P_500.txt              ← Full S&P 500 watchlist
│   ├── premium_swing.txt        ← Yesterday's PREMIUM signals
│   ├── positions_swing_mock.csv  ← Open swing positions
│   └── positions_daytrade_mock.csv ← Open daytrade positions
├── scanner_output/              ← Results & logs
│   ├── signals/                 ← Generated signals (CSV)
│   ├── exits/                   ← Exit evaluations
│   ├── logs/                    ← Execution logs
│   └── .monitor_alerts.txt      ← Recent position alerts
└── cron_jobs.txt                ← Automated schedules
```

## 🔧 Configuration

### Essential Settings (config.py)
```python
# Trading mode defaults
MODES = {
    'swing': {
        'lookback': 15,
        'default_timeframe': '1 day',
        'rsi_overbought': 70,
        'min_r_r': 2.0,
    },
    'daytrade': {
        'lookback': 15,
        'default_timeframe': '15 mins',
        'rsi_overbought': 65,
        'min_r_r': 2.0,
    }
}

# Market regime impact
REGIME_CONFIG = {
    'CHOPPY': {
        'vol_mult': 1.3,      # More stops hit
        'atr_mult': 1.3,
    },
    'EXPANSION': {
        'vol_mult': 0.9,      # Better trends
        'atr_mult': 0.9,
    }
}
```

### Notifications (config.py)
```python
DISCORD_WEBHOOK = "https://..."  # Discord alerts
EMAIL_RECIPIENTS = ["your@email.com"]
TELEGRAM_BOT_TOKEN = "..."
```

## 📊 Understanding Results

### Signal CSV Columns
```
Symbol          - Stock ticker
Price           - Entry price at breakout
Vol             - Volume ratio (current/avg)
Quality         - PREMIUM / HIGH / STANDARD / REJECT
RR_Grade        - Risk:Reward grade (A/B/C)
WinProb         - Win probability estimate (0.5-0.75)
Patterns        - Technical pattern (Ascending Triangle, Bull Flag, etc.)
Sentiment       - Market sentiment (bullish/neutral/bearish)
```

### Backtest Metrics
```
Return          - Total return % (vs SPY baseline)
Sharpe Ratio    - Risk-adjusted returns (>1.0 is good)
Max Drawdown    - Worst peak-to-trough loss
Win Rate        - % of profitable trades
W/L Ratio       - Average win / average loss (>1.5 is good)
Trades          - Total signals tested
```

### Market Regime
```
CHOPPY     - Low momentum, high noise (worse for breakouts)
NORMAL     - Standard conditions (baseline)
EXPANSION  - Strong trending, good for breakouts
```

## 🔐 Security Best Practices

1. **Never commit config.py** (contains API keys)
   ```bash
   git add . -u  # Don't add new files with secrets
   ```

2. **Use environment variables for secrets**
   ```bash
   export IB_CLIENT_ID=123
   export DISCORD_WEBHOOK="https://..."
   ```

3. **Rotate credentials regularly**
   - Update API keys monthly
   - Check Discord webhook is still valid
   - Verify email list is current

4. **Monitor account activity**
   - Use paper trading for testing
   - Review all executed trades
   - Set position limits in config

## 🐛 Troubleshooting

### "No signals found"
```bash
# Check watchlist
ls -la input/S&P_500.txt

# Run verbose scan
/scan --mode swing --watchlist S&P_500.txt 2>&1 | head -50

# Check market regime (may be CHOPPY)
/analyze-market
```

### "Scanner is slow"
```bash
# Reduce watchlist size
wc -l input/S&P_500.txt  # How many symbols?

# Limit to sector
/scan --mode swing --watchlist input/tech_stocks.txt

# Check data source
# If using yfinance: expect 15-min delay
# If using IB: must be connected
```

### "Backtest metrics look wrong"
```bash
# Verify signal CSV format
head -5 scanner_output/signals/signals_swing_*.csv

# Check dates
ls -la scanner_output/signals/ | tail

# Rerun with verbose logging
python breakout_scanner.py --mode swing --verbose
```

### "Positions not auto-appending"
```bash
# Check --auto-positions argument
cat cron_jobs.txt | grep auto-positions

# Verify positions file exists and is writable
ls -la input/positions_swing_mock.csv

# Check quality filter (default: PREMIUM)
# Only PREMIUM signals auto-append by default
```

## 🎓 Learning Path

**Week 1: Understand the System**
1. Read README.md
2. Run `/scan --mode swing` and review output
3. Run `/backtest --period 2024 --mode swing`
4. Compare your results vs SPY
5. Read config.py parameters

**Week 2: Build Intuition**
1. Use `/analyze-market` daily
2. Monitor how regime affects signals
3. Review winning vs losing signals
4. Note which patterns work best
5. Check position entry/exit quality

**Week 3: Optimize**
1. Run `/optimize --mode swing --param rsi_overbought`
2. Compare parameter sensitivity
3. Backtest changes before deploying
4. Document what works in your market
5. Set up cron jobs for automation

**Week 4: Validate**
1. Run live scans in paper trading
2. Compare actual fills vs signal prices
3. Validate signal quality with real execution
4. Adjust parameters if needed
5. Deploy to live if comfortable

## 📚 Next Steps

1. **Read the Guides**
   - [CLAUDE_SKILLS_GUIDE.md](CLAUDE_SKILLS_GUIDE.md) — All available skills
   - [SUBAGENTS_GUIDE.md](SUBAGENTS_GUIDE.md) — Advanced analysis agents
   - [README.md](README.md) — Project documentation

2. **Set Up Automation**
   - Run `/cron-setup --install swing daytrade`
   - Verify with `/cron-setup --list`
   - Monitor with Healthchecks.io

3. **Validate Your Setup**
   - Run backtests on historical data
   - Compare results vs SPY
   - Ensure Sharpe > 0.8, Win Rate > 55%

4. **Go Live (carefully)**
   - Start paper trading
   - Monitor positions with `/monitor`
   - Review entries/exits daily
   - Scale up gradually

## 🆘 Getting Help

### In Claude Code
```bash
# Ask about a specific skill
claude ask "How does /scan work?"

# Get optimization recommendations
claude ask "What parameters should I test for daytrade?"

# Research a market question
claude ask "Why are tech stocks down?"
```

### Debugging Commands
```bash
# See all available skills
python -c "from skills import SKILLS_REGISTRY; print(list(SKILLS_REGISTRY.keys()))"

# Run a skill with verbose output
python skills/scan.py --mode swing --watchlist input/S&P_500.txt

# Check cron job logs
tail -f scanner_output/logs/cron_swing.log
```

---

**Last Updated**: February 2026
**Status**: Production Ready ✅
