# Decisions log

Append-only. Newest entries at the bottom. The lead writes here daily; workers write
conclusions and proposals here too. Live config changes are PROPOSALS ONLY — a human ships them.

---

## 2026-07-24 — system bootstrap (human + Claude, not an agent tick)

**Built:** the live signal panel (`research/panel/`), agent role prompts, runner, ledger.

**Two findings surfaced during the build, before any agent ran:**

1. **Live and the validated backtest trade near-disjoint signal populations.** The live archive
   is ~92% TREND_CONFIRM / ~5% BOUNCE; every champion baseline in CLAUDE.md §7-§13 was measured
   with `--no-tc`, which disables TREND_CONFIRM entirely (~99.7% BOUNCE). Live runs
   `TREND_CONFIRM['enabled']=True` Path A (config.py:226-228). Tracked as H4. Consequence:
   §7's "WinProb calibration is inert on a single BOUNCE|PREMIUM bucket" is a backtest artifact,
   and §13's 11 null ranking levers were measured on a population live barely produces.

2. **~24% of measured signal rows carry a price that never traded** (ADBE flagged at 93.87 on a
   day it ranged 227.70-236.30; ADM at 189.99 on a 75-81 day). Rows are internally consistent —
   Stop/Target are derived from the bogus Price — so this is not a CSV parsing artifact.
   Tracked as HZ1. **Root cause unknown and unresolved.** All analysis filters
   `price_in_bar_range == True`.

**Open risk worth a human's attention:** if the live scanner produced those prices, `auto_portfolio`
may have opened real positions at fictional entry prices. That is a live-money question, not a
research one, and it should be chased independently of this agent system.

**Not yet done:** pattern labelling (deferred pending H3), `confirm_backtest.py`, launchd install.
