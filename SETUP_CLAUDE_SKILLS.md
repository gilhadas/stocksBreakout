# Setting Up Claude Skills in Claude Code

Step-by-step guide to register and use Claude Skills with your stocksBreakout project.

## 📋 Prerequisites

- Claude Code CLI installed (`pip install anthropic`)
- Claude API key configured
- stocksBreakout repository cloned and ready
- Python 3.10+ with project dependencies installed

## 🔧 Installation Steps

### Step 1: Create Skills Directory
```bash
cd /Users/gilhadas/Documents/GitHub/stocksBreakout
mkdir -p skills
```

### Step 2: Copy Skill Files
Skill files are already created:
- `skills/__init__.py` — Skills registry
- `skills/scan.py` — /scan skill
- `skills/backtest.py` — /backtest skill
- `skills/monitor.py` — /monitor skill
- `skills/analyze_market.py` — /analyze-market skill

### Step 3: Configure Claude Settings

Create `.claude/skills.json` in your project root:
```json
{
  "skills": [
    {
      "name": "scan",
      "description": "Run trading scans with intelligent defaults",
      "command": "python skills/scan.py",
      "aliases": ["scan"],
      "tags": ["trading", "signals"]
    },
    {
      "name": "backtest",
      "description": "Run strategy backtests with comparison support",
      "command": "python skills/backtest.py",
      "aliases": ["backtest", "test"],
      "tags": ["backtesting", "analysis"]
    },
    {
      "name": "monitor",
      "description": "Monitor open positions for price drops",
      "command": "python skills/monitor.py",
      "aliases": ["monitor", "watch"],
      "tags": ["monitoring", "positions"]
    },
    {
      "name": "analyze-market",
      "description": "Analyze market regime and impact on strategy",
      "command": "python skills/analyze_market.py",
      "aliases": ["market", "regime"],
      "tags": ["market-analysis", "regime"]
    }
  ],
  "environment": {
    "PYTHONPATH": ".",
    "PROJECT_ROOT": "/Users/gilhadas/Documents/GitHub/stocksBreakout"
  }
}
```

## 🎯 Using Skills in Claude Code

### Method 1: Direct Command
```bash
# In Claude Code conversation
/scan --mode swing --watchlist S&P_500.txt
```

### Method 2: With Arguments
```bash
/scan --mode daytrade --premium --notify
/backtest --period 2024 --mode swing
/monitor --interval 5
/analyze-market --period 1week --forecast
```

### Method 3: Via Claude API
```python
from anthropic import Anthropic

client = Anthropic()

# Register skill
response = client.messages.create(
    model="claude-opus-4-6",
    tools=[{
        "type": "function",
        "function": {
            "name": "scan",
            "description": "Run trading scan",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["swing", "daytrade", "longterm"]},
                    "watchlist": {"type": "string"},
                    "premium_only": {"type": "boolean"}
                }
            }
        }
    }],
    messages=[{
        "role": "user",
        "content": "Run a swing trade scan on the S&P 500"
    }]
)
```

## 📝 Creating Custom Skills

Want to add your own skill? Here's the template:

```python
# skills/my_skill.py
import asyncio
import argparse
import logging

logger = logging.getLogger(__name__)

async def run_my_skill(param1: str, param2: bool = False) -> dict:
    """
    Skill description

    Args:
        param1: First parameter
        param2: Second parameter

    Returns:
        dict with results
    """
    try:
        # Your logic here
        result = {"success": True, "data": "value"}
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='My Skill')
    parser.add_argument('--param1', required=True)
    parser.add_argument('--param2', action='store_true')
    args = parser.parse_args()

    result = asyncio.run(run_my_skill(args.param1, args.param2))
    import json
    print(json.dumps(result, indent=2))
```

Then register in `skills/__init__.py`:
```python
SKILLS_REGISTRY['my-skill'] = {
    'module': 'my_skill',
    'function': 'run_my_skill',
    'description': 'My custom skill'
}
```

## 🔌 Hooking Into Claude Code

### 1. Add Skill to Your Keybindings
Edit `~/.claude/keybindings.json`:
```json
{
  "keybindings": [
    {
      "key": "cmd+shift+s",
      "command": "/scan --mode swing"
    },
    {
      "key": "cmd+shift+m",
      "command": "/monitor"
    }
  ]
}
```

### 2. Auto-Run Skills on File Changes
Add to `.claude/hooks.json`:
```json
{
  "hooks": {
    "post-edit:breakout_scanner.py": "/backtest --period 2024 --mode swing",
    "post-edit:config.py": "/optimize --mode swing --param rsi_overbought"
  }
}
```

### 3. Create Skill Aliases
```bash
# In your shell profile
alias cscan="/scan --mode swing"
alias cmonitor="/monitor"
alias cbacktest="/backtest --period 2024 --mode swing"
```

## 📊 Testing Skills

### Test Individual Skill
```bash
python skills/scan.py --mode swing --watchlist input/premium_swing.txt
```

### Test with JSON Output
```bash
python skills/scan.py --mode swing 2>&1 | jq '.summary'
```

### Test All Skills
```bash
for skill in scan backtest monitor analyze_market; do
  echo "Testing $skill..."
  python skills/${skill}.py --help 2>&1 | head -5
done
```

## 🐛 Troubleshooting

### Skill Not Found
```bash
# Verify skill is in registry
python -c "from skills import SKILLS_REGISTRY; print(SKILLS_REGISTRY.keys())"

# Check syntax
python -m py_compile skills/scan.py
```

### Import Errors
```bash
# Ensure PYTHONPATH is set
export PYTHONPATH=/Users/gilhadas/Documents/GitHub/stocksBreakout:$PYTHONPATH

# Verify dependencies
pip list | grep -E "ib-insync|pandas|numpy|yfinance"
```

### Skills Timeout
```bash
# Increase timeout in skills configuration
# Default: 30 seconds
# For backtests, increase to 300 seconds

# In .claude/skills.json:
"timeout": 300
```

## 🔐 Security Considerations

### 1. API Keys
Keep config.py out of version control:
```bash
echo "config.py" >> .gitignore
```

### 2. Skill Permissions
Restrict skill execution in shared environments:
```json
{
  "skills": [{
    "name": "scan",
    "require_confirmation": false,
    "allowed_users": ["your-email@example.com"]
  }]
}
```

### 3. Audit Trail
Enable skill logging:
```python
# In skills/__init__.py
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('skills_audit.log')
    ]
)
```

## 📚 Integration Examples

### Example 1: Daily Routine
```bash
# Create alias
cat > ~/daily_scan.sh << 'EOF'
#!/bin/bash
cd /Users/gilhadas/Documents/GitHub/stocksBreakout
echo "Running daily scans..."
/scan --mode swing
/scan --mode daytrade --premium
/monitor
/analyze-market
EOF

chmod +x ~/daily_scan.sh

# Add to crontab
(crontab -l 2>/dev/null; echo "0 9 * * 1-5 ~/daily_scan.sh") | crontab -
```

### Example 2: Weekly Analysis
```bash
# In Claude Code
/backtest --period last-week --mode swing
claude ask "Analyze signal quality from the backtest results"
```

### Example 3: Monthly Optimization
```bash
# In Claude Code conversation
/analyze-market --forecast
claude ask "Optimize parameters for the forecasted regime"
/backtest --period 2024 --compare v1-vs-v2
```

## 🚀 Advanced: Custom Skill Chain

Create a meta-skill that runs multiple skills:
```python
# skills/daily_report.py
async def run_daily_report_skill() -> dict:
    """Run daily analysis: scan, monitor, market"""
    from skills import run_skill

    results = {
        'scan': await run_skill('scan', mode='swing'),
        'monitor': await run_skill('monitor'),
        'market': await run_skill('analyze-market', period='1week')
    }

    return {
        'success': all(r['success'] for r in results.values()),
        'results': results,
        'summary': f"Scanned {results['scan'].get('signals_count', 0)} signals"
    }
```

Register in `skills/__init__.py`:
```python
SKILLS_REGISTRY['daily-report'] = {
    'module': 'daily_report',
    'function': 'run_daily_report_skill'
}
```

Usage:
```bash
/daily-report
```

## 📞 Getting Help

### List Available Skills
```bash
python -c "from skills import list_skills; print(list_skills())"
```

### Skill Documentation
```bash
python skills/scan.py --help
```

### Debug Skill Execution
```bash
# Add verbose output
export LOGLEVEL=DEBUG
python skills/scan.py --mode swing
```

## ✅ Verification Checklist

- [ ] Skills directory created
- [ ] Skill files copied
- [ ] Python imports work: `python -c "from skills import SKILLS_REGISTRY"`
- [ ] Test skill runs: `python skills/scan.py --mode swing`
- [ ] JSON output is valid: `python skills/scan.py --mode swing 2>&1 | jq`
- [ ] Claude Code recognizes skills: `/scan --help`
- [ ] Aliases work: `cscan` or `/scan`
- [ ] Cron jobs updated if needed
- [ ] config.py is in .gitignore
- [ ] API keys are set in environment

## 🎉 Ready to Go!

Once all items are checked, you can:

1. **Use Skills Immediately**
   ```bash
   /scan --mode swing
   /monitor
   /backtest --period 2024
   ```

2. **Combine with Subagents**
   ```bash
   /scan --mode swing
   claude ask "Analyze the signal quality"
   ```

3. **Schedule with Cron**
   ```bash
   /cron-setup --install swing daytrade
   ```

---

**Next**: Read [CLAUDE_INTEGRATION_GUIDE.md](CLAUDE_INTEGRATION_GUIDE.md) for daily usage
