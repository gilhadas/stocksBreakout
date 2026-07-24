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

- **HZ1 — bogus signal prices. CONTAINED, root cause still unknown.** ~32% of rows record a
  `Price` that never traded (PLTR 197.20 on a 131.23–134.68 day; **100% of 2026-07-21**; 80% of
  May–June; swing 43% vs longterm 11%). Ruled out: CSV misparse (rows are internally consistent),
  split adjustment (yfinance reports zero splits on affected names), intra-file row shuffle
  (signal-price multiset ≠ actual-price multiset), and the current yfinance path (returns correct
  prices today).
  **Blast radius checked and it is NOT a live-money bug:** `auto_portfolio` fetches its own entry
  and stop — the 9 positions opened 2026-07-21 all have correct entries (RKLB 69.12 = actual
  close) and sane stops at −3% to −5%, none above entry. The §7 A/B harness likewise uses
  `avail['close'].iloc[0]` plus a stop guard. **The panel now mirrors both** (entry = bar close,
  stop guard applied), so no rows are discarded and `price_in_bar_range` is a diagnostic only.
  **ROOT CAUSE FOUND (2026-07-24).** It is documented in the live code itself —
  auto_portfolio.py:407-427 skips signals whose CSV price diverges >50% from the real entry, with
  the comment *"BOUNCE detected on a historical weekly bar at \$66 when the real daily price is
  \$149"* / *"signal is from a completely different price era (stale historical bar)"*. So the
  scanner sometimes computes a signal on a STALE or WRONG-TIMEFRAME bar, and Price/Stop/Target all
  inherit that bar. This is why it clusters in specific scan runs, and why live is insulated (the
  guard skips these before opening a position). **Still worth fixing at the scanner source** so it
  doesn't emit them at all — corrupts UI display and any analysis trusting those columns. Not
  urgent, not a trading risk.
- **HZ2 — schema drift.** Signal CSVs vary 31→57 columns across Apr–Jul. Check per-column
  coverage before relying on any feature.
- **HZ3 — repeated signals.** Same symbol re-flagged daily; `episode_id` exists for dedup. An
  un-deduped `n` is inflated ~10×.
