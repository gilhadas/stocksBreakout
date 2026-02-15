# Claude Integration for stocksBreakout Trading System

Welcome! This directory now includes a complete Claude Code integration with Skills and Subagents to enhance your trading workflow.

## 🚀 Quick Start (Two Methods Available)

### Method 1: Python Direct (Fastest - 0 min setup)
```bash
# No setup needed - works immediately!
python skills/scan.py --mode swing
python skills/backtest.py --period 2024 --mode swing
python skills/monitor.py
python skills/analyze_market.py
```
→ See **CLAUDE_SKILLS_BOTH_WAYS.md** for full details

### Method 2: Claude Code Integration (Cleaner syntax)
```bash
# After setup, use cleaner syntax
/scan --mode swing
/backtest --period 2024 --mode swing
/monitor
/analyze-market
```
→ See **CLAUDE_SKILLS_BOTH_WAYS.md** for setup & details

### Choose Your Path:

**Path A: I Just Want Results (5 min)**
```bash
1. Read: CLAUDE_SKILLS_BOTH_WAYS.md (Method 1 section)
2. Run: python skills/scan.py --mode swing
3. Done! Results in scanner_output/signals/
```

**Path B: I Want the Full System (30 min)**
```bash
1. Read: CLAUDE_SKILLS_BOTH_WAYS.md (both methods)
2. Try: Both Python direct and Claude Code methods
3. Run: /scan, /monitor, /backtest
4. Use: claude ask "Analyze signal quality"
```

**Path C: I'm a Power User (1 hour)**
```bash
1. Read: All guides in order
2. Set up: /cron-setup --install swing daytrade
3. Automate: Both Python scripts and Claude Code
4. Optimize: Create custom skills
```

## 📚 Documentation (Read in This Order)

### 1️⃣ **CLAUDE_INTEGRATION_GUIDE.md** ← START HERE
- Quick command reference
- 5-minute examples
- Common use cases
- Troubleshooting

### 2️⃣ **CLAUDE_SKILLS_GUIDE.md**
- Detailed skill documentation
- All 8 available skills
- Parameters and options
- Workflow examples

### 3️⃣ **SUBAGENTS_GUIDE.md**
- 6 specialized agents
- Complex analysis tasks
- Use cases and examples
- Advanced workflows

### 4️⃣ **SETUP_CLAUDE_SKILLS.md**
- Installation steps
- Configuration
- Creating custom skills
- Troubleshooting

### 5️⃣ **CLAUDE_SKILLS_SUMMARY.md**
- Overview of entire system
- Success criteria
- Next actions
- Key improvements

## 🎯 What You Can Do Now

### Daily (5 minutes)
```bash
/scan --mode swing
/monitor
/analyze-market
```

### Weekly (30 minutes)
```bash
/backtest --period last-week
claude ask "Which patterns worked best?"
/position-report --risk
```

### Monthly (2 hours)
```bash
/backtest --period last-month --compare v1-vs-v2
claude ask "Optimize RSI threshold for daytrade"
claude ask "Calculate portfolio risk and position sizing"
```

## 📦 What's Included

### Skills (8 Total)
| Skill | What It Does | Time |
|-------|-------------|------|
| `/scan` | Run breakout scans | 2-5 min |
| `/backtest` | Test on historical data | 5-30 min |
| `/monitor` | Track open positions | 1 min |
| `/analyze-market` | Market regime analysis | 2 min |
| `/validate-signals` | Check signal quality | 5-10 min |
| `/optimize` | Tune parameters | 10-30 min |
| `/position-report` | Portfolio review | 2-5 min |
| `/cron-setup` | Automate scans | 5-10 min |

### Subagents (6 Total)
1. **signal-analyzer** — Deep signal analysis
2. **strategy-optimizer** — Parameter optimization
3. **market-regime-analyst** — Regime performance
4. **portfolio-risk-manager** — Risk calculations
5. **news-research-agent** — Market research
6. **level2-analyzer** — Order book analysis

### Documentation (6 Guides)
- CLAUDE_README.md (this file)
- CLAUDE_INTEGRATION_GUIDE.md
- CLAUDE_SKILLS_GUIDE.md
- SUBAGENTS_GUIDE.md
- SETUP_CLAUDE_SKILLS.md
- CLAUDE_SKILLS_SUMMARY.md

### Code (4 Skill Implementations)
- skills/__init__.py — Skills registry
- skills/scan.py — /scan implementation
- skills/backtest.py — /backtest implementation
- skills/monitor.py — /monitor implementation
- skills/analyze_market.py — /analyze-market implementation

## 💡 Example Workflows

### Workflow 1: Validate Your Strategy (15 min)
```
1. /backtest --period 2024 --mode swing
   ↓ Get historical performance
2. /analyze-market --compare-regimes
   ↓ See how you do in different markets
3. /position-report
   ↓ Review current risk levels
✅ Now you know your strategy works!
```

### Workflow 2: Daily Trading (5 min)
```
1. /analyze-market --period 1week
   ↓ Understand market conditions
2. /scan --mode swing
   ↓ Generate fresh signals
3. /monitor
   ↓ Check on existing positions
✅ Ready to trade!
```

### Workflow 3: Improve Your System (1 hour)
```
1. /validate-signals --min-age 7days
   ↓ See which signals worked
2. claude ask "Which patterns have highest win rate?"
   ↓ Identify best signal types
3. /optimize --mode swing --param rsi_overbought
   ↓ Find best parameter values
4. /backtest --period 2024 --mode swing
   ↓ Test improvement on historical data
✅ Strategy is now better!
```

## 🎓 Learning Path

### Day 1: Get Comfortable
- Read CLAUDE_INTEGRATION_GUIDE.md (10 min)
- Run `/scan --mode swing` (5 min)
- Review output in scanner_output/signals/ (5 min)
- Run `/monitor` (1 min)
- **Total: 20 minutes**

### Day 2: Understand Performance
- Run `/backtest --period 2024 --mode swing` (15 min)
- Run `/analyze-market --compare-regimes` (2 min)
- Compare results vs SPY benchmark (5 min)
- Read CLAUDE_SKILLS_GUIDE.md (20 min)
- **Total: 45 minutes**

### Day 3: Optimize
- Read SUBAGENTS_GUIDE.md (20 min)
- Run `claude ask "Analyze signal quality"` (10 min)
- Run `claude ask "Optimize parameters"` (20 min)
- Review recommendations (10 min)
- **Total: 60 minutes**

### Day 4-5: Deploy
- Read SETUP_CLAUDE_SKILLS.md (15 min)
- Run `/cron-setup --install swing daytrade` (10 min)
- Verify cron jobs with `/cron-setup --list` (2 min)
- Test one job with `/cron-setup --test swing` (5 min)
- **Total: 30 minutes**

## 🔍 Finding What You Need

### "I want to..."

**...run a quick scan**
→ `/scan --mode swing` (CLAUDE_INTEGRATION_GUIDE.md)

**...test my strategy**
→ `/backtest --period 2024` (CLAUDE_SKILLS_GUIDE.md)

**...understand market conditions**
→ `/analyze-market --forecast` (CLAUDE_SKILLS_GUIDE.md)

**...find best parameters**
→ `claude ask "Optimize parameters"` (SUBAGENTS_GUIDE.md)

**...calculate portfolio risk**
→ `claude ask "Portfolio risk analysis"` (SUBAGENTS_GUIDE.md)

**...analyze why trades fail**
→ `claude ask "Analyze signal quality"` (SUBAGENTS_GUIDE.md)

**...set up automation**
→ `/cron-setup --install swing` (CLAUDE_SKILLS_GUIDE.md)

**...get help**
→ `claude ask "..."` (Always works!)

## ✅ Verification Steps

Run these to verify everything works:

```bash
# 1. Check Python imports
python -c "from skills import SKILLS_REGISTRY; print(f'Skills: {len(SKILLS_REGISTRY)}')"
# Expected output: Skills: 4

# 2. Test a skill
python skills/scan.py --mode swing --watchlist input/premium_swing.txt
# Should return JSON with signals

# 3. Verify market data connection
python -c "import asyncio; from market_data import MarketDataHandler; print('✓ Market data OK')"

# 4. Check signal files exist
ls -la scanner_output/signals/ | head -3
# Should show recent CSV files

# 5. Verify positions files
cat input/positions_swing_mock.csv | head -3
# Should show position data
```

## 🎯 Success Indicators

After setup, you should see:

✅ `/scan` returns signals in < 5 minutes
✅ `/backtest` shows realistic metrics (Sharpe > 0.8)
✅ `/monitor` alerts on position changes
✅ `/analyze-market` classifies regimes correctly
✅ `claude ask` returns detailed analysis
✅ Cron jobs log results daily

## 🐛 Troubleshooting

### Skills Not Working?
→ See "Troubleshooting" in SETUP_CLAUDE_SKILLS.md

### Getting Import Errors?
→ Run: `export PYTHONPATH=/Users/gilhadas/Documents/GitHub/stocksBreakout:$PYTHONPATH`

### Claude Integration Not Working?
→ Check: Is Claude Code CLI installed? `claude --version`

### Signals Look Wrong?
→ Check config.py parameters match your market

### Backtest Results Differ?
→ Verify date range: `ls -la scanner_output/signals/ | tail`

## 📞 Getting Help

### Quick Questions
```bash
claude ask "How do I use /scan?"
# → Explains the skill

claude ask "What does PREMIUM quality mean?"
# → Defines the term

claude ask "Is my strategy profitable?"
# → Analyzes your signals
```

### Problem Diagnosis
```bash
claude ask "Why are my signals failing?"
# → Analyzes signal CSV, identifies issues

claude ask "Should I be in the market right now?"
# → Analyzes regime and recommends action
```

### Advanced Analysis
```bash
claude ask "Optimize all parameters for swing trading"
# → Runs parameter sweep, returns best values

claude ask "Calculate optimal position sizing"
# → Uses Kelly Criterion based on your stats
```

## 🚀 Next Steps

**Right Now:**
1. Open CLAUDE_INTEGRATION_GUIDE.md
2. Run `/scan --mode swing`
3. Look at results in scanner_output/signals/

**In 30 Minutes:**
1. Read CLAUDE_SKILLS_GUIDE.md
2. Try 2-3 different skills
3. Run `/backtest --period 2024 --mode swing`

**Today:**
1. Read SETUP_CLAUDE_SKILLS.md
2. Run all skills once
3. Understand the output

**This Week:**
1. Read SUBAGENTS_GUIDE.md
2. Try `claude ask` for analysis
3. Set up `/cron-setup` for automation

**This Month:**
1. Use daily workflows
2. Validate strategy with backtests
3. Optimize parameters
4. Go live!

---

## 📋 File Index

```
stocksBreakout/
├── CLAUDE_README.md                     ← You are here
├── CLAUDE_INTEGRATION_GUIDE.md          ← Quick reference
├── CLAUDE_SKILLS_GUIDE.md               ← Full skill docs
├── SUBAGENTS_GUIDE.md                   ← Advanced analysis
├── SETUP_CLAUDE_SKILLS.md               ← Installation
├── CLAUDE_SKILLS_SUMMARY.md             ← Overview
│
├── skills/                              ← Skill implementations
│   ├── __init__.py
│   ├── scan.py
│   ├── backtest.py
│   ├── monitor.py
│   └── analyze_market.py
│
├── breakout_scanner.py                  ← Main entry point
├── config.py                            ← Your settings
├── orchestrator.py
├── scanner.py
├── indicators.py
├── market_data.py
├── notifier.py
├── utils.py
│
├── input/                               ← Your data
│   ├── S&P_500.txt
│   ├── positions_swing_mock.csv
│   └── positions_daytrade_mock.csv
│
└── scanner_output/                      ← Results
    ├── signals/
    ├── exits/
    ├── logs/
    └── monitor_alerts.txt
```

---

**Status**: ✅ Production Ready
**Last Updated**: February 14, 2026
**Support**: Run `claude ask` for help
