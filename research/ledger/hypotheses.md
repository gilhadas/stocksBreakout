# Open hypotheses

Status values: `active` | `blocked` | `closed-null` | `closed-promising`
The **lead** owns this file. Workers read it to pick their next task and may append
evidence lines, but only the lead changes a status or reorders priorities.

---

## H1 — Optimal stop distance depends on stock behaviour  ·  owner: stops  ·  status: active

Live applies a uniform ATR×2.0 trail to every position (`config.ATR_TRAIL_MULT`,
`auto_portfolio._raise_atr_trail`). Never tested per cohort.

**Why it matters:** stops fire on every position, so a wrong global multiplier taxes the whole
book. This is also the one question the panel answers by direct measurement.

**Next tasks (priority order):**
1. Coverage table — episode-deduped, `price_in_bar_range==True`, `bars_available>=30` counts by
   `Type`, `Quality`, `Sector`, and behaviour bins. Establish what is answerable *before*
   analysing. Expect power to be in TREND_CONFIRM (~92% of live), not BOUNCE.
2. Winner-MAE distribution per cohort (median/p75/p90). Quantify how many winners a 2.0×ATR stop
   is cutting.
3. Stop-distance sweep per cohort → expectancy curve, expressed in ATR multiples.

**Kill condition:** if no cohort's optimum differs from ATR×2.0 by more than the cohort's own
bootstrap CI, close as `closed-null` — uniform 2.0 is already right.

---

## H2 — The admission ranking is not ordering signals correctly  ·  owner: picking  ·  status: active

`_compute_priority_score` uses 4 inputs (`quality, win_prob, rr, vol`) to decide which ~10 of the
day's signals get bought.

**Why it matters:** free natural experiment — the system admits some signals and skips the rest
for cash every single day, and the panel has both groups' forward paths.

**Next tasks:**
1. Reconstruct the `admitted` flag from `auto_portfolio` history / `skipped_cash`; report join
   reliability per date range before using it.
2. Day-stratified paired comparison: admitted vs skipped forward returns. Never pool across days.
3. Only if mis-ordering is measurable: continuous forward-return model, walk-forward.

**Kill condition:** if admitted ≥ skipped consistently and the cap rarely binds, the ranking is
doing its job → `closed-null`, and the picking effort folds into H1.

---

## H3 — Chart-pattern labels add nothing over simple behaviour features  ·  owner: stops  ·  status: active

`quantkit/patterns.py` has 28 detectors that the backtest never calls. Computing them for the
panel is a real cost.

**Why it matters:** decides whether pattern labelling is worth building at all. A clean negative
saves the cost permanently.

**Next task:** run H1's cohort analysis on cheap behaviour bins (ATR percentile, RSI, Gap%, Vol)
first. Only if those show structure worth explaining should pattern labels be computed and tested
for *incremental* power over them.

---

## H4 — Live's TREND_CONFIRM-dominated stream behaves differently from the backtest's BOUNCE stream  ·  owner: lead  ·  status: active

Discovered 2026-07-24 while sizing the panel: live is ~92% TREND_CONFIRM / ~5% BOUNCE; every
champion baseline in CLAUDE.md was measured with `--no-tc`, i.e. ~99.7% BOUNCE.

**Why it matters:** if true, a large part of §7–§13's conclusions describe a population live
barely trades — including §7's "WinProb calibration is inert".

**Next task:** quantify on the panel — per-`Type` forward-return, MAE, hold-duration, and hit-rate
profiles. This is diagnostic, not a lever; it tells the team where the other hypotheses should
aim.

---

## Data hazards being tracked (not hypotheses — active risks)

- **HZ1 — bogus signal prices.** ~24% of measured rows in the first build had a `Price` outside
  the signal-day bar range (ADBE at 93.87 on a 227–236 day). Root cause NOT yet established.
  Every analysis must filter `price_in_bar_range == True`. **Someone should find the root cause**
  — if the live scanner wrote these, `auto_portfolio` may have opened real positions at fictional
  prices. Escalate rather than work around.
- **HZ2 — schema drift.** Signal CSVs vary 31→57 columns across Apr–Jul. Check per-column
  coverage before relying on any feature.
- **HZ3 — repeated signals.** Same symbol re-flagged daily; `episode_id` exists for dedup. An
  un-deduped `n` is inflated ~10×.
