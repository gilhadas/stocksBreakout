# Using Claude Skills: Both Methods

This guide shows you how to use the 8 Claude skills **two different ways**:

1. **Python Direct** (Works immediately, no setup)
2. **Claude Code Integration** (Cleaner UX, requires registration)

Choose the method that works best for your workflow!

## 🚀 Method 1: Python Direct (Immediate Use)

No setup needed — run skills directly from command line.

### Running Skills via Python

#### `/scan` — Run Trading Scans
```bash
# Basic scan
python skills/scan.py --mode swing

# With watchlist
python skills/scan.py --mode swing --watchlist input/S&P_500.txt

# PREMIUM signals only
python skills/scan.py --mode daytrade --premium

# With sentiment
python skills/scan.py --mode swing --sentiment

# All options
python skills/scan.py --mode swing --watchlist input/S&P_500.txt --premium --sentiment --notify
```

**Output**: JSON results with signals count, file location, regime info

---

#### `/backtest` — Backtest Strategies
```bash
# Basic backtest
python skills/backtest.py --period 2024 --mode swing

# Compare V1 vs V2
python skills/backtest.py --period 2024 --compare v1-vs-v2

# Different period
python skills/backtest.py --period 2025-01-01:2025-12-31 --mode daytrade

# Last month
python skills/backtest.py --period last-month --mode swing

# Last quarter
python skills/backtest.py --period last-quarter --mode swing
```

**Output**: JSON with return, Sharpe, max drawdown, win rate metrics

---

#### `/monitor` — Monitor Positions
```bash
# Monitor all positions
python skills/monitor.py

# Specific positions file
python skills/monitor.py --positions input/positions_swing_mock.csv

# Multiple files
python skills/monitor.py --positions input/positions_swing_mock.csv input/positions_daytrade_mock.csv

# Different interval
python skills/monitor.py --interval 5

# Run once vs continuous
python skills/monitor.py --once
```

**Output**: JSON with dashboard, alerts, positions status

---

#### `/analyze-market` — Market Analysis
```bash
# Basic analysis
python skills/analyze_market.py

# Different period
python skills/analyze_market.py --period 1month

# With forecast
python skills/analyze_market.py --forecast

# Compare regimes
python skills/analyze_market.py --compare-regimes

# All options
python skills/analyze_market.py --period 1week --forecast --compare-regimes
```

**Output**: JSON with regime classification, mode impacts, recommendations

---

### Creating a Shell Alias (Optional)

Make Python methods shorter with shell aliases:

```bash
# Add to ~/.bash_profile or ~/.zshrc
alias cscan="python /Users/gilhadas/Documents/GitHub/stocksBreakout/skills/scan.py"
alias cbacktest="python /Users/gilhadas/Documents/GitHub/stocksBreakout/skills/backtest.py"
alias cmonitor="python /Users/gilhadas/Documents/GitHub/stocksBreakout/skills/monitor.py"
alias cmarket="python /Users/gilhadas/Documents/GitHub/stocksBreakout/skills/analyze_market.py"

# Then use:
cscan --mode swing
cbacktest --period 2024
cmonitor
cmarket --forecast
```

---

### Parsing JSON Output

Skills return JSON for easy parsing:

```bash
# Get signals count
python skills/scan.py --mode swing 2>&1 | jq '.signals_count'

# Get regime
python skills/scan.py --mode swing 2>&1 | jq '.regime'

# Get backtest return
python skills/backtest.py --period 2024 2>&1 | jq '.metrics.return'

# Get position alerts
python skills/monitor.py 2>&1 | jq '.alerts'
```

---

### Piping to Files

Save results for later analysis:

```bash
# Save scan results
python skills/scan.py --mode swing > scan_results_$(date +%Y%m%d).json

# Save backtest report
python skills/backtest.py --period 2024 > backtest_$(date +%Y%m%d).json

# Save position monitoring
python skills/monitor.py > monitor_$(date +%Y%m%d).json
```

---

## 🔌 Method 2: Claude Code Integration

Register skills for cleaner `/skillname` syntax in Claude Code.

### Setup (One-Time)

#### Step 1: Verify Config File Exists
```bash
ls -la /Users/gilhadas/Documents/GitHub/stocksBreakout/.claude/skills.json
```

File already created ✅

#### Step 2: Reload Claude Code
```bash
# Restart Claude Code or reload configuration
# This loads the .claude/skills.json file
```

#### Step 3: Verify Skills Are Registered
```bash
# In Claude Code, ask:
claude ask "list all registered skills"

# Or check:
python -c "from skills import SKILLS_REGISTRY; print(SKILLS_REGISTRY.keys())"
```

---

### Using Claude Code Skills

Once registered, use the `/skillname` syntax:

#### `/scan` — Run Trading Scans
```
/scan
/scan --mode daytrade
/scan --mode swing --watchlist input/S&P_500.txt --premium
/scan --mode longterm --sentiment --notify
```

#### `/backtest` — Backtest Strategies
```
/backtest
/backtest --period 2024 --mode swing
/backtest --compare v1-vs-v2 --period 2024
/backtest --period last-month --optimize rsi_overbought
```

#### `/monitor` — Monitor Positions
```
/monitor
/monitor --interval 5
/monitor --positions input/positions_swing_mock.csv --once
```

#### `/analyze-market` — Market Analysis
```
/analyze-market
/analyze-market --period 1month
/analyze-market --forecast --compare-regimes
```

---

### Claude Code Skill Features

When registered, skills have:

✅ **Autocomplete** — `/scan --mode [TAB]` shows options
✅ **Help text** — `/scan --help` shows parameters
✅ **Validation** — Only valid enums accepted
✅ **Timeout** — Skills auto-timeout after period
✅ **Error handling** — Graceful failure messages
✅ **Logging** — Results logged for audit

---

## 📊 Comparison: Python vs Claude Code

| Feature | Python Direct | Claude Code |
|---------|--------------|-------------|
| Setup Time | 0 min | 5 min |
| Syntax | `python skills/scan.py --mode swing` | `/scan --mode swing` |
| Autocomplete | No | Yes ✅ |
| Help Text | `--help` flag | `/scan --help` ✅ |
| Validation | Manual | Automatic ✅ |
| Timeout | No | Yes ✅ |
| Logging | Manual | Automatic ✅ |
| Integration | Works anywhere | Claude Code only |
| Complexity | Simple | More setup |

---

## 🎯 Which Method to Use?

### Use Python Direct If:
- ✅ You want zero setup time
- ✅ You prefer command-line scripts
- ✅ You run skills in cron/automation
- ✅ You integrate with other tools
- ✅ You use shell aliases

### Use Claude Code If:
- ✅ You want cleaner `/skillname` syntax
- ✅ You want autocomplete & validation
- ✅ You use Claude Code heavily
- ✅ You want better IDE integration
- ✅ You want automatic logging

### Recommendation:
**Use Both!**
- Use **Python Direct** for automation/cron
- Use **Claude Code** for interactive work
- Both reference the same underlying code

---

## 💡 Example Workflows

### Workflow 1: Daily Trading (Python Direct)
```bash
#!/bin/bash
# daily_trading.sh

cd /Users/gilhadas/Documents/GitHub/stocksBreakout

echo "1. Analyzing market..."
python skills/analyze_market.py --period 1week

echo "2. Running scans..."
python skills/scan.py --mode swing --watchlist input/S&P_500.txt
python skills/scan.py --mode daytrade --premium

echo "3. Monitoring positions..."
python skills/monitor.py --once

echo "Done!"
```

Run with: `bash daily_trading.sh`

---

### Workflow 2: Weekly Review (Claude Code)
```
1. /analyze-market --period 1week
   → Understand market conditions

2. /scan --mode swing --watchlist input/premium_swing.txt
   → Generate fresh signals

3. /monitor
   → Check position status

4. /backtest --period last-week --mode swing
   → Validate recent performance

5. claude ask "Analyze signal quality from last week"
   → Get detailed insights
```

---

### Workflow 3: Monthly Optimization (Hybrid)
```bash
# Python for batch processing
python skills/backtest.py --period last-month --compare v1-vs-v2 > monthly_report.json

# Claude Code for interactive analysis
/analyze-market --compare-regimes

# Then ask Claude
claude ask "Based on the backtest, what parameters should I optimize?"
```

---

## 🔧 Advanced: Custom Commands

### Create a Meta-Script (Python Direct)
```bash
#!/bin/bash
# daily.sh - Run all daily tasks

python skills/analyze_market.py --period 1week && \
python skills/scan.py --mode swing && \
python skills/scan.py --mode daytrade --premium && \
python skills/monitor.py --once

echo "✅ Daily routine complete"
```

### Create a Cron Job (Python Direct)
```bash
# In crontab
0 9 * * 1-5 cd /Users/gilhadas/Documents/GitHub/stocksBreakout && bash daily.sh >> cron.log 2>&1
```

### Create a Claude Code Hook
In `.claude/hooks.json`:
```json
{
  "hooks": {
    "on-morning": "/analyze-market --period 1week && /scan --mode swing",
    "on-market-close": "/monitor && /position-report --risk"
  }
}
```

---

## 📝 Skill-by-Skill Reference

### `/scan` Skill

**Python Direct:**
```bash
python skills/scan.py --mode swing --watchlist input/S&P_500.txt
```

**Claude Code:**
```
/scan --mode swing --watchlist input/S&P_500.txt
```

**Parameters:**
- `--mode` (swing, daytrade, longterm, scalping)
- `--watchlist` (file path)
- `--premium` (boolean)
- `--sentiment` (boolean)
- `--notify` (boolean)

**Time:** 2-5 minutes

---

### `/backtest` Skill

**Python Direct:**
```bash
python skills/backtest.py --period 2024 --mode swing --compare v1-vs-v2
```

**Claude Code:**
```
/backtest --period 2024 --mode swing --compare v1-vs-v2
```

**Parameters:**
- `--period` (2024, last-month, last-quarter, YYYY-MM-DD:YYYY-MM-DD)
- `--mode` (swing, daytrade, longterm)
- `--compare` (v1-vs-v2, configs, modes)
- `--optimize` (rsi_overbought, atr_mult, consolidation)

**Time:** 5-30 minutes

---

### `/monitor` Skill

**Python Direct:**
```bash
python skills/monitor.py --interval 5 --once
```

**Claude Code:**
```
/monitor --interval 5 --once
```

**Parameters:**
- `--positions` (CSV files, comma-separated)
- `--interval` (minutes between checks)
- `--once` (boolean, run once instead of loop)

**Time:** 1 minute

---

### `/analyze-market` Skill

**Python Direct:**
```bash
python skills/analyze_market.py --period 1week --forecast --compare-regimes
```

**Claude Code:**
```
/analyze-market --period 1week --forecast --compare-regimes
```

**Parameters:**
- `--period` (1week, 1month, 1quarter)
- `--forecast` (boolean)
- `--compare-regimes` (boolean)

**Time:** 2 minutes

---

## ✅ Verification Checklist

### For Python Direct (Works Now)
- [ ] `python skills/scan.py --mode swing` returns JSON
- [ ] `python skills/backtest.py --period 2024` completes
- [ ] `python skills/monitor.py` shows positions
- [ ] `python skills/analyze_market.py` shows regime

**All working?** → Use Python Direct immediately!

### For Claude Code Integration
- [ ] `.claude/skills.json` exists
- [ ] Claude Code is reloaded
- [ ] `/scan --help` shows parameters
- [ ] `/scan --mode [TAB]` autocompletes

**All working?** → Use Claude Code syntax!

---

## 🚀 Next Steps

1. **Try Python Direct Now**
   ```bash
   python skills/scan.py --mode swing
   ```

2. **Register for Claude Code** (Optional)
   - Reload Claude Code
   - Try `/scan --mode swing`

3. **Create Aliases** (Optional)
   ```bash
   alias cscan="python skills/scan.py"
   ```

4. **Build Your Workflow**
   - Daily: `/scan` + `/monitor`
   - Weekly: `/backtest` + `claude ask`
   - Monthly: Full analysis with subagents

---

**Status**: Both methods ready to use ✅

**Recommendation**: Start with Python Direct (no setup), then add Claude Code integration when convenient!

