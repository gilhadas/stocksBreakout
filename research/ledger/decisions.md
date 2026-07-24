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

---

## 2026-07-24 (later) — HZ1 investigated: contained, NOT a live-money bug

**Correction to this morning's entry.** It stated real positions may have been opened at
fictional prices. That was overstated — the evidence says they were not.

**What was proven:** the scanner does write impossible prices into its signal CSVs. On
2026-07-21, 38/38 rows were wrong, on unambiguous mega-caps: PLTR 197.20 (actual 131.23–134.68),
QCOM 139.41 (170.14–175.49), PANW 163.66 (334.03–352.00), MRNA 164.80 (58.61–60.83), OXY 131.80
(55.36–56.50). No ticker-identity ambiguity is possible for those names.

**Ruled out:** CSV misparse (rows internally consistent — Stop/Target correctly derived from the
bogus Price); split adjustment (yfinance: zero splits on affected names since 2026-01-01);
intra-file row shuffle (signal-price multiset does not match the actual-price multiset for the
same file); the current data path (yfinance today returns correct prices for every affected name).
**Root cause remains unknown.** Note the concentration — 100% of 2026-07-21, ~80% of May–June,
15% of April, and near-zero on every other July date — which points at specific bad scan RUNS
rather than a continuously-broken path.

**Blast radius — checked, and it is contained:**
- `auto_portfolio` fetches its own entry and stop. The 9 positions opened 2026-07-21 (user
  cf699841) all carry correct entries (RKLB 69.12 = the real close) and sane stops at −3% to −5%;
  none sits above entry.
- `daytrade_admission_ab.py` uses `avail['close'].iloc[0]` for entry and guards the stop
  (`stop >= entry or >30% away -> entry*0.95`), so §7's A/B conclusions are not built on bad prices.
- The research panel WAS affected, because it used the CSV price as entry. **Fixed:** the panel
  now uses the signal-day bar close as `entry_used` and applies the same production stop guard —
  mirroring live rather than working around it. This recovered the ~32% of rows previously
  discarded, and `price_in_bar_range` is now a diagnostic, not a filter.

**Still open (low urgency):** find why the scanner emitted those prices. It corrupts its own
output, which matters for the UI and for any future analysis that trusts the `Price`/`Stop`/
`Target` columns. Not a trading risk.
