# Open hypotheses

Status values: `active` | `blocked` | `closed-null` | `closed-promising`
The **lead** owns this file. Workers read it to pick their next task and may append
evidence lines, but only the lead changes a status or reorders priorities.

Last lead review: 2026-07-27 (ticks reviewed: H4 per-Type profile table + H5 hold-split-
within-TC, both 2026-07-27 worker-stops; H2 reopening-trigger recheck + admission-time
RSI prescreen, 2026-07-27 worker-picking). See `decisions.md` same date for the full
audit and rationale. New hypothesis H6 opened this review.

---

## H1 — Optimal stop distance depends on stock behaviour  ·  owner: stops  ·  status: closed-null

**Closed 2026-07-26.** Stop-distance sweep (2026-07-25) ran every cohort with n≥30
(TREND_CONFIRM, TC|PREMIUM, TC|GOLD, BOUNCE, BOUNCE|PREMIUM, BOUNCE|HIGH, CONTINUATION).
Every cohort's optimum was either monotone-to-no-stop or sat inside the no-stop bootstrap
CI95 (`all_optima_within_nostop_ci95: true`). Kill condition as written ("if no cohort's
optimum differs from ATR×2.0 by more than the cohort's own bootstrap CI") is met.

**What this does and does NOT say:**
- It says a **fixed-% stop** shows no measurable expectancy edge over no-stop on the
  Apr–Jul 2026 panel window, per cohort, right now.
- It does **not** question the live ATR×2.0 **trailing** stop — a trail ratchets up and is
  a different instrument (guardrails: "fixed-% stop ≠ trailing stop... never propose a
  specific trail multiplier from a fixed-% sweep"). The champion trail is validated
  separately in `confirm_backtest.py`/CLAUDE.md §7, not by this hypothesis.
- The panel window has **no sustained bear** — stops matter most there, and 30-bar
  truncation flatters no-stop (guardrails' sweep-discipline point 2). So this is a clean
  null for *this data*, not a general proof stops don't matter.

No further tasks. Historical record only.

---

## H2 — The admission ranking is not ordering signals correctly  ·  owner: picking  ·  status: blocked

**Split verdict, 2026-07-26 — mechanism sub-question ANSWERED, model sub-question BLOCKED
on data volume (not closed-null: the model was never given enough training days to fail on
its merits, so this isn't a permanent negative).**

**Answered, no further work:** the pooled-cap ranking's only real effect is sorting by
Quality tier. GOLD>PREMIUM is robust — +8.33pp at 20d (CI [3.17, 13.48], n=32 paired days),
holds in every month tested, and holds *within TREND_CONFIRM alone* (+4.53pp, n=129 vs
1330). Below Quality, WinProb is 99.8% NaN (calibration JSON not deployed, by design —
CLAUDE.md §7), R:R is a near-constant 2.5, and Dist/Vol tiebreaks do not order forward
returns within PREMIUM (rank buckets 01-10/11-20/21-50 inside PREMIUM: 4.75/5.67/7.46pp,
non-monotone). This confirms current live behaviour is already doing the one thing that
matters (Quality-first sort) — **no proposal needed, nothing to change.**

**Blocked:** walk-forward Ridge on 16 features (36 train days ≤2026-05-31, 5 holdout days
2026-06-01→08) overfit to regime (top coefs: Sec_Technology +6.7, RSI −2.2 — both reversed
sign in June) and failed holdout (ρ −0.18 vs baseline −0.02; top-3 paired delta −9.74pp,
CI95 excludes 0). A 36-variant sensitivity sweep (3 feature sets × 3 targets × 4 ridge
λ) found no configuration that beat the Vol baseline outside noise. This is a real result
but on a training window too short/regime-narrow to trust — the guardrail's own
"walk-forward any fitted model" standard says a model that only just fits 36 days isn't
ready to score.

**Reopening trigger — checked today, not yet met:** the panel's 20d/30d "frozen" episode
set (needed to score a model against a real forward return) has **not grown since this
result was produced** — still 41 dates, 2026-04-01→06-08, 1675 episodes, identical to the
walk-forward's own window. This is not a bug: there is a genuine ~4-week gap in the raw
signal archive between **2026-06-10 and 2026-07-05** (zero files — plausibly the
pre-EC2-cutover period, CLAUDE.md §9's cutover completed 2026-07-07; not yet root-caused,
noted for a human, not urgent), and the July signals since then simply haven't aged the
30 trading days needed to freeze yet. First new frozen dates arrive starting with the
2026-07-06 cohort, roughly **mid-to-late August 2026** — after the current
`budget.json` end_date (2026-07-31). Flagged to the human in `decisions.md`; not
reopening prematurely.

**Reconfirmed 2026-07-27:** frozen episode-start count is still 1675/43 dates
(2026-04-01→06-09); the panel grew to 9961 rows and one new frozen date, but that date
added zero new episode starts (all re-flags). No change to the mid-to-late-August ETA.

**No task assigned to picking this cycle for the multivariate retry.** Next task, when
reopened: retry the walk-forward Ridge with ≥70 frozen training days, targeting H5's now-
confirmed ≤15d-vs->15d hold split rather than raw ret_20d — and lead with **RSI** as the
first feature to test (see evidence below), not a fresh 16-feature sweep.

**RSI named as the candidate feature (prescreen, 2026-07-27):** a diagnostic univariate
screen (not a fitted model) found RSI's tercile/decile spread on the TC gt15d rate is the
largest of 10 admission-time features tested (37.9pp, next-best sector at 32.6pp with no
MC correction), monotonic across deciles (21.3%→66.4%), and orthogonal to Quality/TC_Score/
Vol (near-zero correlation, near-identical mean RSI by quality tier). Caveat: 86% of the
frozen set is April; the April-internal chronological split (first-5-days vs last-12-days)
shows the relationship replicating and strengthening (ρ 0.29→0.45), not reversing, but
May/June are much weaker (ρ −0.004, 0.202) — consistent with either genuine regime-
dependence or simple underpower at n=50–150; the data cannot yet distinguish these.
**This is promising but not walk-forward-ready on the panel alone — see H6, which tests
the same RSI signal on a data source that already has multi-regime coverage.**

---

## H3 — Chart-pattern labels add nothing over simple behaviour features  ·  owner: stops  ·  status: closed-null

**Closed 2026-07-26.** Behaviour-bin pre-test (2026-07-25) inside TREND_CONFIRM (n=1459):
RSI/Gap%/Vol/SMA_Dist% tertiles showed winner-MAE differences well below noise (p50 range
≤1pp, p90 range ≤3.2pp vs the −10.71pp baseline) and every bin's stop-sweep optimum sat
inside the no-stop CI (max delta +0.22pp). Kill condition explicitly met. The 28-detector
pattern library is not worth computing for either the stop question or (per the same
logic) likely the ranking question. No further tasks.

---

## H4 — Live's TREND_CONFIRM-dominated stream behaves differently from the backtest's BOUNCE stream  ·  owner: lead  ·  status: closed-promising

**Closed 2026-07-27 — deliverable produced, folded into H5.** Worker-stops built the
per-Type profile table (n=1459 TREND_CONFIRM, 134 BOUNCE, 62 CONTINUATION, all frozen
episode-starts, entry_used-based). Headline: TC and BOUNCE differ materially —
`ret_30d_mean` 6.4% (TC) vs 1.38% (BOUNCE); `overall_wr_pct` (exit-based: stop=loss,
target=win, else mark-to-market) 42.3% (TC) vs 34.3% (BOUNCE) vs 45.2% (CONTINUATION,
n=62, thin). TC|GOLD (n=129) beats TC|PREMIUM (n=1330) on ret_30d_mean (8.55% vs 6.19%)
and hit_stop_rate (46.5% vs 55.7%) — consistent with H2's already-closed GOLD>PREMIUM
finding. This is descriptive infrastructure, not a lever — its value was enabling H5's
run below, which is where the actionable finding lives. No further tasks under H4 itself.

---

## H5 — Is the universe-independent ≤15d-hold drag present, and how large, within TREND_CONFIRM specifically?  ·  owner: stops  ·  status: closed-promising

**Closed 2026-07-27 — kill condition NOT met; this is the single largest, most
statistically overdetermined finding on the panel to date.** Measured within TC
(n=1459, frozen episode-starts): ≤15d WR **22.6%** (n=804) vs >15d WR **66.4%** (n=655),
gap **43.8pp**, two-prop z=−16.85. Within TC|PREMIUM (n=1330, the highest-powered cell):
gap **45.7pp**, z=−16.81. TC|GOLD (n=129+53) attenuates but does not eliminate it: gap
23.6pp, z=−2.64. BOUNCE shows the same shape (36.8pp gap, n=76+58). This squarely matches
the 40–70pp shape every `--no-tc` backtest universe showed (CLAUDE.md §8/§13) — **the
drag is not a BOUNCE/mean-reversion-only artifact; it is present, large, and highly
significant inside live's dominant TREND_CONFIRM stream (87% of live signals).**

**Why this is the highest-value finding of the cycle:** per "steering toward profit"
priority 1 (does it change what live actually trades?) and priority 2 (is the drag it
targets large?) — this is the first result this cycle that is both. Nothing else on the
board (H1–H3, H2's Quality-sort answer) combines "affects TC" with "large effect."

**Follow-on, spun out as H6 below:** the natural next step — find an admission-time
feature that predicts the ≤15d/>15d bucket and test it as a rule — is picked up there,
using a data source that does NOT require waiting on H2's blocked panel-growth trigger.

---

## H6 — Does an RSI-conditioned TREND_CONFIRM admission rule survive real multi-regime history?  ·  owner: picking → stops  ·  status: active  ·  **TOP PRIORITY — next tick**

**Opened 2026-07-27.** H5 confirmed the ≤15d/>15d drag is large and highly significant
within TREND_CONFIRM (live's dominant type). H2's own prescreen named RSI as the
strongest, cleanest, most orthogonal admission-time predictor of that split — but the
panel can't validate it across regimes yet: 86% of the frozen episode set is a single
month (April), and H2's own reopening trigger (more frozen days, spanning more than one
regime) won't fire until mid-to-late August — **after** the current `budget.json`
`end_date` (2026-07-31, only 4 days from today).

**Why this doesn't have to wait:** TREND_CONFIRM already carries an `RSI` value on every
signal, and `backtest_regime_compare.py` already has a `--rank-scores FILE` mechanism
(CSV `date,symbol,score`, applied within quality tier, forwarded by `confirm_backtest.py
--rank-scores`) built for exactly this kind of continuous-score test — confirmed present
and wired end-to-end by reading both files just now. The **5-year historical backtest is
a completely different data source from the panel** — it already spans 2022 (bear,
though TC is blocked in RED_MARKET/BEARISH — see the standard caveat below),
2023–2025 (bull/mixed), and 2026 (mixed), i.e. genuine multi-regime coverage the panel
does not have yet. This sidesteps H2's data-volume block entirely rather than waiting on
it, and is checkable within the current budget window.

**Next task (assign to picking, next tick):** build a `date,symbol,score` CSV scoring
TREND_CONFIRM signals in the historical backtest universe by RSI (e.g., percentile within
day, or a monotone transform matching the decile shape H2's prescreen measured — worker's
judgment on the exact form, since the panel screen only established direction/shape, not
a specific functional form). Run `backtest_regime_compare.py --realistic-sizing
--rank-scores <file>` across the full 2022–2026 window (`--population live`, i.e. TC Path
A enabled — do NOT use `--no-tc`, that reproduces a population that is not TREND_CONFIRM).
Report per-year Sharpe (realistic-sizing arm per the standing §11 rule) vs the champion
baseline, AND the ≤15d/>15d trade-count and WR split per year (the halt criterion below).

**Follow-on (assign to stops once picking has a candidate):** run the result through
`research/confirm_backtest.py --rank-scores <file> --population live` (the mandatory
promotion gate, default years 2022,2024) and quote its printed realized signal-type mix —
per the standing caveat, 2022 will show few/no TREND_CONFIRM trades since TC is blocked
in RED_MARKET/BEARISH, so 2022 is a downside check only, not a test of this specific rule;
2024 is where this candidate must actually prove itself.

**Ship bar (unchanged from standing rules):** ≥+0.10 Sharpe on the realistic-sizing arm
vs the no-op baseline (no RSI conditioning), AND >15d hold win-rate must not shrink in any
tested year. A result that only wins via a partial-year (2026 YTD) blend does not count —
recompute full-year-only averages by hand before reading a verdict (CLAUDE.md §13.5 /
`feedback_backtest_verdict_pitfalls`).

**Kill condition:** if the realistic-sizing Sharpe delta is within ±0.05 of the no-op
baseline in every full year tested, or if it requires shrinking >15d WR to get there,
close as `closed-null` — RSI's panel-measured correlation would then be an April-specific
artifact that doesn't generalize, exactly the risk H2's own prescreen flagged and did not
rule out.

**Budget note for the lead/human:** if H6 comes back null or ambiguous, the only
remaining path to resolving whether RSI (or any admission-time feature) predicts TC hold
duration is H2's blocked multivariate retry, which needs panel data that will not exist
until after the current budget `end_date`. That would require a human decision to extend
`end_date` past 2026-07-31, not something this run can do on its own.

---

## Data hazards being tracked (not hypotheses — active risks)

- **HZ1 — bogus signal prices. CONTAINED, root cause found.** ~32% of rows record a
  `Price` that never traded (PLTR 197.20 on a 131.23–134.68 day; **100% of 2026-07-21**; 80%
  of May–June; swing 43% vs longterm 11%). Root cause: auto_portfolio.py:407-427's own
  comment — the scanner sometimes computes a signal off a stale/wrong-timeframe historical
  bar, and Price/Stop/Target all inherit it. Live is insulated (that same guard skips these
  before opening a position; §7 A/B harness uses `avail['close'].iloc[0]`). **The panel
  mirrors both** (entry = bar close, stop guard applied) — no rows discarded,
  `price_in_bar_range` is a diagnostic only, never a filter. Still worth fixing at the
  scanner source eventually (not urgent, not a trading risk).
- **HZ2 — schema drift.** Signal CSVs vary 31→57 columns across Apr–Jul. Check per-column
  coverage before relying on any feature.
- **HZ3 — repeated signals.** Same symbol re-flagged daily; `episode_id` exists for dedup. An
  un-deduped `n` is inflated ~10×.
- **HZ4 — new, noted 2026-07-26, not yet investigated.** The raw signal archive has a
  genuine ~4-week gap with zero files, 2026-06-10 → 2026-07-05, immediately preceding the
  2026-07-07 EC2 cutover (CLAUDE.md §9). Does not corrupt any analysis to date (it's simply
  absent data, not bad data) but caps how fast the panel's frozen (20d/30d) training window
  can grow — see H2's reopening trigger above. Not escalated as urgent since it predates
  the current production system and there is no evidence of a live-money impact; flagged
  here so it isn't lost, and worth a one-line human check on whether signals were actually
  being generated (just not archived) during that window.
