# Claude Skills & Subagents - Complete Index

## 📖 START HERE

**New to this system?** Read these in order:

1. **[CLAUDE_README.md](CLAUDE_README.md)** (10 min)
   - Overview of what you can do
   - Quick start options
   - File structure

2. **[CLAUDE_INTEGRATION_GUIDE.md](CLAUDE_INTEGRATION_GUIDE.md)** (15 min)
   - Command reference
   - Daily/weekly/monthly workflows
   - Common use cases

3. **[CLAUDE_SKILLS_GUIDE.md](CLAUDE_SKILLS_GUIDE.md)** (20 min)
   - Detailed documentation for all 8 skills
   - Parameters and options
   - Real-world examples

## 🎯 Quick Links by Use Case

### "I want to run scans"
- Read: [CLAUDE_INTEGRATION_GUIDE.md](CLAUDE_INTEGRATION_GUIDE.md) - "Quick Start"
- Command: `/scan --mode swing`
- File: `skills/scan.py`

### "I want to backtest my strategy"
- Read: [CLAUDE_SKILLS_GUIDE.md](CLAUDE_SKILLS_GUIDE.md) - "/backtest" section
- Command: `/backtest --period 2024 --mode swing`
- File: `skills/backtest.py`

### "I want to monitor positions"
- Read: [CLAUDE_SKILLS_GUIDE.md](CLAUDE_SKILLS_GUIDE.md) - "/monitor" section
- Command: `/monitor`
- File: `skills/monitor.py`

### "I want market analysis"
- Read: [CLAUDE_SKILLS_GUIDE.md](CLAUDE_SKILLS_GUIDE.md) - "/analyze-market" section
- Command: `/analyze-market --period 1week`
- File: `skills/analyze_market.py`

### "I want deep analysis"
- Read: [SUBAGENTS_GUIDE.md](SUBAGENTS_GUIDE.md)
- Command: `claude ask "Analyze signal quality"`
- Info: See subagent descriptions

### "I want to automate"
- Read: [SETUP_CLAUDE_SKILLS.md](SETUP_CLAUDE_SKILLS.md)
- Command: `/cron-setup --install swing daytrade`
- File: `cron_jobs.txt`

## 📚 All Documentation Files

### Main Guides (Read These)
- **[CLAUDE_README.md](CLAUDE_README.md)** - Start here, overview & quick start
- **[CLAUDE_INTEGRATION_GUIDE.md](CLAUDE_INTEGRATION_GUIDE.md)** - Daily reference
- **[CLAUDE_SKILLS_GUIDE.md](CLAUDE_SKILLS_GUIDE.md)** - All 8 skills explained
- **[SUBAGENTS_GUIDE.md](SUBAGENTS_GUIDE.md)** - 6 advanced agents
- **[SETUP_CLAUDE_SKILLS.md](SETUP_CLAUDE_SKILLS.md)** - Installation & config

### Support Documents
- **[CLAUDE_SKILLS_SUMMARY.md](CLAUDE_SKILLS_SUMMARY.md)** - Feature overview
- **[CLAUDE_CHECKLIST.md](CLAUDE_CHECKLIST.md)** - Verification checklist
- **[CLAUDE_BUILD_SUMMARY.txt](CLAUDE_BUILD_SUMMARY.txt)** - Build report
- **[INDEX.md](INDEX.md)** - This file

## 💻 Code Files

### Skills Implementation
- `skills/__init__.py` - Skills registry & launcher
- `skills/scan.py` - /scan skill
- `skills/backtest.py` - /backtest skill
- `skills/monitor.py` - /monitor skill
- `skills/analyze_market.py` - /analyze-market skill

## 🚀 Getting Started Paths

### Path A: I just want to trade (30 min)
1. Read CLAUDE_README.md
2. Run `/scan --mode swing`
3. Review results in scanner_output/signals/
4. Done!

### Path B: I want to validate my strategy (1 hour)
1. Read CLAUDE_INTEGRATION_GUIDE.md
2. Run `/backtest --period 2024 --mode swing`
3. Run `/analyze-market --compare-regimes`
4. Run `/position-report --risk`
5. Done!

### Path C: I want full automation (2 hours)
1. Read CLAUDE_SKILLS_GUIDE.md
2. Read SUBAGENTS_GUIDE.md
3. Read SETUP_CLAUDE_SKILLS.md
4. Run `/cron-setup --install swing daytrade`
5. Try `claude ask "Analyze signal quality"`
6. Done!

### Path D: I want to master it all (4 hours)
1. Read all guides
2. Try all skills
3. Use all subagents
4. Create custom skills
5. Optimize parameters
6. Done!

## 🎓 Learning Order

### Day 1: Foundations (1 hour)
- [ ] Read CLAUDE_README.md
- [ ] Read CLAUDE_INTEGRATION_GUIDE.md
- [ ] Run `/scan --mode swing`
- [ ] Review output

### Day 2: Skills (2 hours)
- [ ] Read CLAUDE_SKILLS_GUIDE.md
- [ ] Try each skill at least once
- [ ] Create daily workflow
- [ ] Take notes

### Day 3: Advanced (2 hours)
- [ ] Read SUBAGENTS_GUIDE.md
- [ ] Try 2-3 subagent queries
- [ ] Run optimization
- [ ] Review results

### Day 4: Automation (1 hour)
- [ ] Read SETUP_CLAUDE_SKILLS.md
- [ ] Run /cron-setup
- [ ] Verify cron jobs
- [ ] Done!

## 📋 Skills Quick Reference

| Skill | What | Time | File |
|-------|------|------|------|
| `/scan` | Run scans | 2-5 min | `skills/scan.py` |
| `/backtest` | Test strategy | 5-30 min | `skills/backtest.py` |
| `/monitor` | Monitor positions | 1 min | `skills/monitor.py` |
| `/analyze-market` | Market analysis | 2 min | `skills/analyze_market.py` |
| `/validate-signals` | Signal validation | 5-10 min | (built-in) |
| `/optimize` | Parameter tuning | 10-30 min | (built-in) |
| `/position-report` | Portfolio review | 2-5 min | (built-in) |
| `/cron-setup` | Automation | 5-10 min | (built-in) |

## 🤖 Subagents Quick Reference

| Agent | Use For | Docs |
|-------|---------|------|
| signal-analyzer | Deep signal analysis | SUBAGENTS_GUIDE.md |
| strategy-optimizer | Parameter optimization | SUBAGENTS_GUIDE.md |
| market-regime-analyst | Regime analysis | SUBAGENTS_GUIDE.md |
| portfolio-risk-manager | Risk management | SUBAGENTS_GUIDE.md |
| news-research-agent | Market research | SUBAGENTS_GUIDE.md |
| level2-analyzer | Order book analysis | SUBAGENTS_GUIDE.md |

## 💡 Common Questions & Answers

**Q: Where do I start?**
A: Read CLAUDE_README.md, then run `/scan --mode swing`

**Q: How do I use a skill?**
A: `/skillname --args`. Read CLAUDE_SKILLS_GUIDE.md for details.

**Q: When should I use subagents?**
A: When you need deep analysis. Read SUBAGENTS_GUIDE.md.

**Q: How do I automate scans?**
A: Run `/cron-setup --install swing`. Read SETUP_CLAUDE_SKILLS.md.

**Q: Where are the results?**
A: In `scanner_output/signals/` directory as CSV files.

**Q: How do I get help?**
A: Try `claude ask "your question"` or read the guides.

## 🔗 Important Links

- Main entry: CLAUDE_README.md
- Daily reference: CLAUDE_INTEGRATION_GUIDE.md
- Skill details: CLAUDE_SKILLS_GUIDE.md
- Advanced: SUBAGENTS_GUIDE.md
- Setup: SETUP_CLAUDE_SKILLS.md
- Verification: CLAUDE_CHECKLIST.md

## ✅ Verification Checklist

Before you start, verify:

- [ ] Python can import skills: `python -c "from skills import SKILLS_REGISTRY"`
- [ ] Directories exist: `ls scanner_output/signals/`
- [ ] Watchlist exists: `cat input/S&P_500.txt | head`
- [ ] Positions file exists: `cat input/positions_swing_mock.csv | head`

All checked? You're ready to go!

## 🎯 Success Criteria

You'll know everything is working when:

✅ `/scan --mode swing` returns signals in < 5 minutes
✅ `/monitor` shows position status instantly
✅ `/backtest --period 2024` completes in < 10 minutes
✅ `/analyze-market` classifies regime correctly
✅ `claude ask "..."` returns detailed analysis
✅ Results save to correct directories
✅ All documentation is clear and accurate

All criteria met? You're production-ready!

## 🚀 Next Steps

1. **Right Now**: Open CLAUDE_README.md
2. **In 5 min**: Run `/scan --mode swing`
3. **In 15 min**: Read CLAUDE_INTEGRATION_GUIDE.md
4. **In 30 min**: Try all skills
5. **Today**: Read all guides
6. **This week**: Automate with /cron-setup
7. **This month**: Go live!

---

**Last Updated**: February 14, 2026
**Status**: ✅ Production Ready
**Ready to Use**: YES, Right Now!

Start with CLAUDE_README.md → Good luck! 🚀
