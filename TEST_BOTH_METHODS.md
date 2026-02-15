# Testing Both Methods - Quick Guide

Test your skills with both Python Direct and Claude Code methods.

## ✅ Test Python Direct Method (Works Right Now)

### Test 1: Verify Skills Directory
```bash
ls -la /Users/gilhadas/Documents/GitHub/stocksBreakout/skills/
```
Should show: `__init__.py`, `scan.py`, `backtest.py`, `monitor.py`, `analyze_market.py`

### Test 2: Run /scan
```bash
python /Users/gilhadas/Documents/GitHub/stocksBreakout/skills/scan.py --mode swing --watchlist input/premium_swing.txt
```

Expected output:
```json
{
  "success": true,
  "signals_count": X,
  "regime": "NORMAL|CHOPPY|EXPANSION",
  "summary": "..."
}
```

### Test 3: Run /backtest
```bash
python /Users/gilhadas/Documents/GitHub/stocksBreakout/skills/backtest.py --period 2024 --mode swing
```

Expected output:
```json
{
  "success": true,
  "metrics": {
    "return": "...",
    "sharpe": "...",
    "max_drawdown": "...",
    "win_rate": "..."
  }
}
```

### Test 4: Run /monitor
```bash
python /Users/gilhadas/Documents/GitHub/stocksBreakout/skills/monitor.py --once
```

Expected output:
```json
{
  "success": true,
  "positions_monitored": X,
  "alerts_count": Y,
  "alerts": [...]
}
```

### Test 5: Run /analyze-market
```bash
python /Users/gilhadas/Documents/GitHub/stocksBreakout/skills/analyze_market.py --period 1week
```

Expected output:
```json
{
  "success": true,
  "market_data": {
    "spy_performance": "...",
    "regime": "...",
    "volatility": "..."
  }
}
```

✅ **All 5 tests pass?** Python Direct method is working!

---

## ✅ Test Claude Code Integration (Optional Setup)

### Step 1: Verify Config File
```bash
ls -la /Users/gilhadas/Documents/GitHub/stocksBreakout/.claude/skills.json
```
Should show: File exists and is readable

### Step 2: Check File Contents
```bash
cat /Users/gilhadas/Documents/GitHub/stocksBreakout/.claude/skills.json | head -20
```
Should show: JSON structure with "skills" array

### Step 3: Reload Claude Code
In Claude Code terminal:
```bash
# Reload configuration
claude config reload

# Or restart Claude Code entirely
```

### Step 4: Verify Registration
```bash
# In Claude Code, run:
/scan --help
```

Expected: Help text with parameters

### Step 5: Test Each Skill in Claude Code

#### Test /scan
```
/scan --mode swing
```
Should return: Signals and summary

#### Test /backtest
```
/backtest --period 2024 --mode swing
```
Should return: Metrics and comparison

#### Test /monitor
```
/monitor --once
```
Should return: Position dashboard

#### Test /analyze-market
```
/analyze-market --period 1week
```
Should return: Regime and recommendations

✅ **All tests work?** Claude Code integration is set up!

---

## 🔧 Troubleshooting

### Python Direct Issues

**Error: ModuleNotFoundError**
```bash
# Fix: Set PYTHONPATH
export PYTHONPATH=/Users/gilhadas/Documents/GitHub/stocksBreakout:$PYTHONPATH
python skills/scan.py --mode swing
```

**Error: File not found**
```bash
# Fix: Use absolute paths
cd /Users/gilhadas/Documents/GitHub/stocksBreakout
python skills/scan.py --mode swing --watchlist input/premium_swing.txt
```

**Error: JSON output is invalid**
```bash
# Fix: Check for errors in stderr
python skills/scan.py --mode swing 2>&1 | jq '.'
```

### Claude Code Integration Issues

**Skills not showing up**
```bash
# Reload configuration
claude config reload

# Or check if .claude/skills.json exists
ls -la .claude/skills.json
```

**Parameters not autocompleting**
```bash
# Make sure .claude/skills.json has proper JSON syntax
python -c "import json; json.load(open('.claude/skills.json'))"
```

**Timeout errors**
```bash
# Increase timeout in .claude/skills.json
# Change: "timeout": 60 to "timeout": 300
```

---

## 📊 Comparison Test Results

After testing both methods, create a comparison:

| Method | Setup Time | Works? | Syntax | Preferred For |
|--------|-----------|--------|--------|---------------|
| Python Direct | 0 min | ✅ | `python skills/scan.py` | Automation, cron, scripts |
| Claude Code | 5 min | ✅/⏳ | `/scan` | Interactive work in Claude |

---

## 🎯 Quick Test Command

Run this to test both methods at once:

```bash
#!/bin/bash
# test_both.sh

echo "=== Testing Python Direct ==="
python skills/scan.py --mode swing --watchlist input/premium_swing.txt && echo "✅ PASS" || echo "❌ FAIL"

echo ""
echo "=== Testing Claude Code Config ==="
test -f .claude/skills.json && echo "✅ Config exists" || echo "❌ Config missing"
python -c "import json; json.load(open('.claude/skills.json'))" && echo "✅ Valid JSON" || echo "❌ Invalid JSON"

echo ""
echo "=== Testing Other Skills ==="
python skills/backtest.py --period 2024 --mode swing 2>&1 | jq '.success' && echo "✅ Backtest works" || echo "❌ Backtest fails"
python skills/monitor.py --once 2>&1 | jq '.success' && echo "✅ Monitor works" || echo "❌ Monitor fails"
python skills/analyze_market.py 2>&1 | jq '.success' && echo "✅ Market analysis works" || echo "❌ Market analysis fails"

echo ""
echo "=== Summary ==="
echo "Python Direct: Ready to use immediately"
echo "Claude Code: Configure when ready"
```

Save and run:
```bash
bash test_both.sh
```

---

## ✨ Expected Results

After testing both methods, you should see:

### Python Direct ✅
```
✅ PASS - /scan works
✅ PASS - /backtest works
✅ PASS - /monitor works
✅ PASS - /analyze-market works
```

### Claude Code Integration ✅
```
✅ Config exists
✅ Valid JSON
✅ Skills ready (after reload)
```

---

## 📖 Next Steps After Testing

### If Python Direct Works:
1. Create shell aliases for shorter commands
2. Use in your daily workflow
3. Set up cron jobs
4. Done! 🚀

### If Claude Code Works:
1. Use `/scan`, `/backtest` syntax
2. Enjoy autocomplete & validation
3. Use in Claude Code workflows
4. Done! 🚀

### If Both Work:
1. Use Python Direct for automation
2. Use Claude Code for interactive work
3. Best of both worlds! 🚀

---

## 📞 Support

Having issues?

1. **Check file permissions**
   ```bash
   chmod +x /Users/gilhadas/Documents/GitHub/stocksBreakout/skills/*.py
   ```

2. **Verify Python path**
   ```bash
   which python
   python --version
   ```

3. **Check dependencies**
   ```bash
   pip list | grep -E "pandas|numpy|yfinance"
   ```

4. **Read detailed guides**
   - CLAUDE_SKILLS_BOTH_WAYS.md
   - SETUP_CLAUDE_SKILLS.md
   - CLAUDE_INTEGRATION_GUIDE.md

---

**Status**: Ready to test ✅

Start with Python Direct, then add Claude Code when convenient!
