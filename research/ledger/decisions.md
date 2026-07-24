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

---

## 2026-07-24 (worker-stops, first tick) — H1 first pass: NULL on this panel

**Panel:** 1675 independent episodes (`is_episode_start & bars_available>=30`), 971 symbols,
2026-04-01 → 2026-06-08. Types: TREND_CONFIRM 1459 (87%) / BOUNCE 134 / CONTINUATION 62.
Only TREND_CONFIRM has cohort-level statistical power; BOUNCE is aggregate-only.

### Task 2 — winner MAE by cohort (real finding, informational)

The one clean structural finding: **BOUNCE winners bleed roughly twice as deep as TC winners
before recovering**, and the gap is visible at every percentile.

| Cohort            |  n_win | winner MAE p50 | p25      | p10 (worst 10%) | % winners cut by −10% stop |
|-------------------|-------:|----------------|----------|------------------|-----------------------------|
| TREND_CONFIRM     |    859 | −3.09%         | −6.04%   | −10.71%          |  11.4% |
| TC \| PREMIUM     |    788 | −3.17%         | −6.25%   | −10.80%          |  12.1% |
| TC \| GOLD        |     71 | −2.44%         | −4.87%   | −7.63%           |   4.2% |
| BOUNCE            |     67 | −6.20%         | −10.84%  | −17.14%          |  26.9% |
| CONTINUATION      |     33 | −5.39%         | −9.24%   | −16.77%          |  21.2% |

Consistent with Kaminski & Lo (CLAUDE.md §12): momentum entries (TC) start close to a fresh
high; mean-reversion entries (BOUNCE) buy into an already-adverse move, so first-days MAE is
naturally deeper before the reversal. TC|GOLD winners bleed the least.

Behaviour tertiles WITHIN TREND_CONFIRM (RSI, Gap%, Vol, SMA_Dist%) show winner-MAE p50
differences of ≤1% across tertiles — well below the cohort's own bootstrap noise. **This is
already a partial answer to H3: behaviour features add nothing over the Type label at this
sample size.** Pattern labels would need to show >1% MAE-percentile lift *within TC* to be
worth computing — no evidence for that here.

### Task 3 — stop-distance sweep: NULL in every cohort

Sweep fixed % stops (3, 4, 5, 6, 8, 10, 12, 15, 20, 25) against `mae_pct`/`ret_30d`. Baseline
= no-stop mean 30d return; bootstrap 95% CI on the no-stop mean (500 resamples).

| Cohort           |    n | baseline mean_ret | optimum stop | delta_vs_nostop | inside no-stop CI95? |
|------------------|-----:|-------------------:|--------------:|-----------------:|----------------------|
| TREND_CONFIRM    | 1459 | **+6.40%**         | **NO-STOP**   | monotone         | — (is baseline)      |
| TC \| PREMIUM    | 1330 | +6.19%             | **NO-STOP**   | monotone         | —                    |
| TC \| GOLD       |  129 | +8.55%             | 8%            | +0.69%           | **yes** (NULL)       |
| BOUNCE           |  134 | +1.38%             | 10%           | +0.10%           | **yes** (NULL)       |
| BOUNCE \| HIGH   |   41 | +4.16%             | **NO-STOP**   | monotone         | —                    |
| BOUNCE \| PREMIUM|   78 | +1.71%             | 8%            | +1.76%           | **yes** (NULL, n=78) |
| CONTINUATION     |   62 | +4.38%             | 20%           | +0.84%           | **yes** (NULL)       |

**Verdict per §sweep-discipline: every cohort is a NULL.** Either expectancy rises
monotonically to no-stop (which the guardrails explicitly flag as the "converging on no stop"
false positive), or the apparent optimum sits well inside the no-stop bootstrap CI. There is
no candidate rule to propose, and therefore nothing meaningful to feed to
`confirm_backtest.py`.

### What this NULL does and does not say

**It does say:** on this measurement (Apr–Jun 2026 signals, 30-bar forward window, fixed-%
stops, uniform sizing), no per-cohort stop distance beats no-stop by more than resampling
noise. The Type-conditional MAE profile is real (BOUNCE ≈ 2× TC) but does not translate into a
measurable per-cohort expectancy edge at this n.

**It does not say:**
1. **Remove the live ATR trail.** The panel window has no sustained bear; §7's canonical run
   showed the trail cuts 2022 bear-year losses materially, and the halt metric (`>15d WR`)
   held. This measurement is silent on that regime.
2. **The live trail multiplier is optimal.** Fixed-% ≠ trailing; the panel sweep can only
   justify the *direction* of a trail change, and there is no direction here.
3. **BOUNCE needs a wider stop than TC.** Directionally consistent with the MAE gap, but the
   BOUNCE|PREMIUM optimum delta (+1.76% at 8%) has n=78 and a CI covering ±5% — not evidence.

### Proposal to lead

- **Close H1 (this pass) as `closed-null` on the current panel** with the caveat above.
- **Close H3 as `closed-null`** on the same data: behaviour tertiles inside TC show no
  MAE-percentile structure worth explaining, so pattern labelling has nothing to add yet.
- **Re-open H1 automatically when the panel accumulates its first RED_MARKET stretch of
  ≥15 SPY-below-SMA200 days** (BBG15 semantics) — the regime where stops matter, and the
  regime this window has none of.
- **Live config: no proposal.** Do not touch `ATR_TRAIL_MULT`, `ATR_TRAIL_FLOOR_BARS`, or the
  pooled-cap ranking. This is the correct answer under the current measurement, not a
  fallback.
