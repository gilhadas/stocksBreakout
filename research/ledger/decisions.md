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


---

## 2026-07-25 (worker-picking, first tick) — H2: ranking IS ordering, and we now know how

**Panel:** 43 signal_dates (2026-04-01 -> 2026-07-24) after GOLD/PREMIUM filter and
`bars_available>=30`. Median 91 unique symbols/day survive V9-H -> the pooled cap of 10
**binds on 42/43 days**, so the natural experiment is dense.

Reconstructed `admitted` by re-running `auto_portfolio._pooled_cap`'s sort on the panel
(Quality asc -> WinProb desc -> R:R desc -> Dist(<=25) desc -> Vol desc; dedupe by symbol per
day; top-10 = admitted). Known limits: does not model live `open_syms`, splits, or the
price-scale-mismatch skip. So this is the ranking's **intended** order, not the executed set.

### Aggregate: admitted vs skipped (paired by day)

|                                      | ret_10d | ret_20d | ret_30d |
|--------------------------------------|--------:|--------:|--------:|
| All rows, mean delta (pp)            | +1.16   | +2.99   | +3.24   |
| ... bootstrap 95% CI                 | [-0.79, +3.43] | [-0.44, +6.76] | [-0.45, +7.07] |
| ... days admitted wins               | 23/42   | 24/42   | 24/42   |
| **Episode-deduped**, mean delta (pp) | **+3.05** | +4.45 | +2.20 |
| ... 95% CI                           | **[+0.24, +5.86]** | [-0.91, +9.81] | [-3.53, +7.93] |
| ... days admitted wins               | 22/35   | 21/35   | 20/35   |
| Median-based sign-test p-value       | 0.28    | **0.044** | 0.088 |

Direction is clean -- admitted > skipped at every horizon and 55-62% of days -- but only two
cuts pass 95% (episode-deduped 10d mean and 20d median sign-test). CI is wide because n=35
days is small. **Verdict: ranking works, weakly positive on this window.**

### Rank-decile view (episode-deduped) -- the interesting structure

| Rank      |   n | r10 mean | r20 mean | r30 mean | r20 median |
|-----------|----:|---------:|---------:|---------:|-----------:|
| 01-03     |  35 |   +6.63  | **+15.13** | +14.86 | +11.71 |
| 04-10     |  68 |   +5.94  |   +9.44  |   +5.89 |  +4.74 |
| 11-20     |  66 |   +4.92  |   +5.04  |   +4.97 |  +3.47 |
| 21-50     | 201 |   +2.43  |   +7.67  |   +8.87 |  +2.35 |
| 51-100    | 239 |   +3.56  |   +6.94  |   +6.66 |  +3.60 |
| 101+      | 344 |   -0.33  |   +1.67  |   +2.49 |  +0.66 |

Two cliffs: **top-3 dominates 4-10**, and **101+ collapses** vs everything above. Ranks 4-100
are a middle band with no clean gradient. Which raises: what is the ranking actually doing?

### Decomposition -- Quality tier is doing all of the work

The current sort has 4 named inputs (`quality, win_prob, rr, vol`) plus Dist as tiebreak.
On the panel, three of them contribute nothing to the actual ordering:

- **WinProb: 99.8% NaN.** Even section 7 says the calibration JSON is deliberately not
  deployed to `scanner_output/` -- the archive confirms it end-to-end (2026-04..07 non-null
  rate <= 2%). With `fillna(0)`, everyone ties.
- **R:R: median 2.5 everywhere.** No discrimination.
- **Dist: capped at 25.** Everything above 25 ties into a back-bucket; the rest is Vol.

So the effective sort is **Quality tier -> Vol tiebreak**. And Quality tier explains almost
everything:

| Rank bucket | GOLD share |
|-------------|-----------:|
| 01-03       |       **91%** |
| 04-10       |       47%  |
| 11+         |        1%  |

**GOLD vs PREMIUM, episode-deduped, per-day paired (n=32 days):**

|         | ret_10d | ret_20d | ret_30d |
|---------|--------:|--------:|--------:|
| mean delta (pp) | **+5.24** | **+8.33** | **+6.75** |
| 95% CI  | [+2.19, +8.28] | [+3.17, +13.48] | [+0.44, +13.06] |
| days GOLD wins | 24/32 | 22/32 | 21/32 |

Significant at 95% at every horizon. Stable in every month (Apr +3.2, May +4.1, Jun +15.5).
**Holds within TREND_CONFIRM alone** (GOLD n=129, mean r20=+9.97 vs PREMIUM n=1330, mean
r20=+5.44 -- delta +4.53pp), so it is not a Type confound. Holds in both swing and longterm.

**Within PREMIUM the sub-ranking is inert:**

| Within-PREMIUM rank | n | r20 mean |
|---------------------|--:|---------:|
| 01-10  | 346 | +4.75 |
| 11-20  | 214 | +5.67 |
| 21-50  | 197 | **+7.46** |
| 51-100 | 100 | +1.61 |
| 101+   |  33 | +2.06 |

Non-monotone. Vol as final tiebreak is not sorting PREMIUM signals by forward return.

### The addressable gap

- GOLD supply is thin and lumpy: median **2.5** GOLD signals/day, **11/43** days have zero,
  weekly supply ranged 39 (early April) -> 2-3 (early June).
- So even when the ranking correctly places all GOLD first, **6-7 of the daily 10 admits fall
  into the PREMIUM sea** where the current tiebreak stack has no measurable signal.

That's where a per-signal continuous model -- the "real ranking upgrade" section 7 called for --
has room to add value. It does not need to reorder GOLD; it needs to order the PREMIUMs.

### Proposal to lead

- **H2 status: keep `active`, do NOT close as `closed-null`.** The kill condition requires
  admitted >= skipped consistently *and* cap rarely binds. Cap binds 42/43 days, and the
  aggregate uplift is real but noisy. The mechanism finding (Quality does the work, PREMIUM
  is unordered) *sharpens* the hypothesis rather than closing it.
- **Next picking task (Task 3 in `hypotheses.md`) is now well-scoped:** fit a continuous
  forward-return model **restricted to the PREMIUM tier**, target `ret_20d`, features drawn
  from the panel (`Vol, Dist_capped, RSI, Gap%, SMA_Dist%, TC_Score, Sector`, and derived
  vol/regime bins), walk-forward across the 43-day window (train <= 2026-05-31, test
  2026-06-01+), and score against the current within-PREMIUM order as baseline. Report
  holdout Kendall's tau + top-decile mean-return uplift. Do **not** fit within GOLD -- GOLD
  already dominates and the sample is too small (n=135 episodes) to justify sub-ordering it.
- **Recommend to human (not an agent action):** the scanner *is* stamping WinProb but the
  calibration JSON is not being loaded in production (99.8% NaN in the archive confirms
  this end-to-end). The section 7 decision to keep it out of `scanner_output/` was because it
  was inert on backtest BOUNCE-only data. On live's TC-dominated stream, it might not be --
  worth reconsidering as a cheap experiment, orthogonal to the model above.
- **Live config: no proposal.** Do not touch `_compute_priority_score`, `MAX_ADDS_PER_SCAN`,
  or the tiebreak. The measured direction of any change would need to come from the
  walk-forward model result, not from this decomposition alone.


---

## 2026-07-25 - worker-stops - H3: behaviour-bins pre-test - NULL (kill condition met)

### What this closes

The final H3 task in `hypotheses.md`:

> Next task: run H1's cohort analysis on cheap behaviour bins (ATR percentile, RSI, Gap%, Vol)
> first. Only if those show structure worth explaining should pattern labels be computed and
> tested for *incremental* power over them.

My prior stop-sweep entry mentioned in passing that tertile bins "pass for free" but never
wrote a standalone H3 result to the ledger. Doing that now.

### Panel state (unchanged for frozen-episode analysis)

New rows landed since the H1 pass (9862 -> 9882), but **frozen-episode count is identical
(1675, date range 2026-04-01 -> 2026-06-08)** -- the fresh rows are still accruing forward
bars. So no new sub-slicing power is available. Re-running H1 tasks would produce identical
numbers, hence skipped per the "do not redo" rule.

### Method

Restricted to `TREND_CONFIRM` (n=1459), which is the only cohort with enough episodes to
survive tertile splits (BOUNCE n=134 -> ~45/bin, sub-slice noise dominates any signal).
Tertile-binned on the four cheapest behaviour features present in the panel with 100%
coverage: `RSI`, `Gap%`, `Vol`, `SMA_Dist%`. (No ATR column in the panel; treated as
out-of-scope for cheap-features.) For each of the 12 bins:

1. Winner-MAE p50/p90 (winners = `ret_30d > 0`, 58.9% base rate).
2. Stop-distance sweep in {5, 7, 10, 15, 20, NO-STOP}%, expectancy at each.
3. Bootstrap 95% CI on the no-stop expectancy; flag whether the best-stop expectancy sits
   inside it.

### Results

**Winner-MAE across bins is nearly flat** (TC-overall baseline: p50=-3.09, p90=-10.71):

| Feature   | p50 range across LO/MID/HI | p90 range |
|-----------|---------------------------:|----------:|
| RSI       |                     0.66pp |    0.22pp |
| Gap%      |                     0.97pp |    3.17pp |
| Vol       |                     0.27pp |    2.18pp |
| SMA_Dist% |                     0.84pp |    1.30pp |

The largest single spread (Gap% p90: -8.87 -> -12.04) is 3.17pp -- ~30% of the -10.71
baseline. Every other range is <=2.2pp. There is no behaviour bin whose winners bleed
materially differently from the TC average.

**Stop-sweep: every bin's apparent optimum sits inside the no-stop CI95.**
- 7/12 bins: monotone-to-NO-STOP (converging on no stop entirely).
- 5/12 bins: apparent optimum at stop=20%, delta vs no-stop = +0.03 to +0.22pp.
- Max delta observed: +0.22pp (Vol_HI, on n=477, no-stop CI95 [5.46, 11.78]) - well
  inside the CI, i.e. indistinguishable from noise.

### Verdict: NULL. H3 kill condition met.

Per the guardrails sweep-discipline rule: if the apparent optimum is inside the no-stop
CI and/or the sweep is monotone-to-no-stop, the honest finding is **stops add no
measurable expectancy** *on this panel window*, and cheap behaviour bins add no
structure to explain. **Pattern labels therefore cannot be expected to add incremental
power** over features that already fail to reveal any -- H3's precondition ("only if
those show structure worth explaining should pattern labels be computed") is negated.

### Caveats (mandatory to staple)

1. Panel window (Apr-Jun 2026 frozen episodes) contains **no sustained bear** (BBG15's
   ≥15d SPY<SMA200 condition was not triggered). This does NOT refute the live ATR×2.0
   trail; it only says a *fixed-%* stop shows no measurable expectancy edge, per-bin or
   in aggregate, in this window.
2. **30-bar truncation flatters no-stop** systematically -- a no-stop loser can only
   bleed for 30 days here; in reality it bleeds until stopped by the live trail.
3. **Fixed-% != trailing.** Panel measures a static level; live uses an ATR trail that
   ratchets up. Panel sweep is at best a lower bound on the trail's own curve.
4. **Sizing not held constant across stop widths.** Per-trade %-return expectancy is
   NOT comparable across stop widths without holding dollar-risk constant.

None of these caveats change the H3-specific conclusion, because the H3 test is
**relative between bins under identical measurement**, not absolute.

### Diagnostic side-note (forwarded to H2/picking, not a stop finding)

`ret30_mean` varies **11.09 -> 25.36pp across Gap%/SMA_Dist tertiles** inside TC (see
raw table in `research/tmp/h3_analysis.py` output). This is a **ranking signal**, not
an exit signal -- worth flagging for worker-picking's continuous-model task (§decisions
2026-07-25 picking), whose feature list already includes both. No action from the stops
side.

### Proposal to lead

- **Close H3 as `closed-null`.** Kill condition per hypotheses.md met.
- **Close H1 as `closed-null`.** All three H1 tasks (coverage, winner-MAE, stop-sweep)
  are logged, all optima sit inside their no-stop CI95, and the H1 kill condition ("no
  cohort's optimum differs from ATR×2.0 by more than the cohort's own bootstrap CI") is
  met. Note that "ATR×2.0" was never directly comparable here -- the sweep tested
  fixed-%, not ATR-multiples -- so what H1 actually proves on this window is stronger:
  **no fixed stop distance shows a measurable edge over no-stop, on any cohort**. That
  either confirms the live trail is fine or the panel window has no bear stress to
  discriminate on. Either way, no proposal to change live is warranted from panel data
  alone; the mandatory 2022 gate (`research/confirm_backtest.py`) exists precisely for
  this reason and there is currently nothing to feed it.
- **No live config proposal.** Live ATR×2.0 trail stays.
- **No new stops-side work is queued.** Worker-stops is now idle until either:
  (a) the lead opens a new hypothesis owned by `stops`; or
  (b) enough new frozen episodes accumulate to justify a re-run with materially
  different power (rough guide: another ~50% more episodes, i.e. ~2500 total, which at
  the current ~25 episodes/day means roughly late September before the analysis moves).

