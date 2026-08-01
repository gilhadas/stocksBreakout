# Open hypotheses

Status values: `active` | `blocked` | `closed-null` | `closed-promising`
The **lead** owns this file. Workers read it to pick their next task and may append
evidence lines, but only the lead changes a status or reorders priorities.

Last lead review: 2026-07-28 (no new committed results since 2026-07-27 — this review
audited a stalled, uncommitted worker-picking attempt at H6 found in `research/tmp/` and
rescoped the task; see `decisions.md` 2026-07-28 for the full diagnosis). Prior review:
2026-07-27 (ticks reviewed: H4 per-Type profile table + H5 hold-split-within-TC, both
worker-stops; H2 reopening-trigger recheck + admission-time RSI prescreen,
worker-picking).

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

**Reconfirmed 2026-07-28 (lead, direct panel query, not a worker tick):** panel is now
10003 rows (signal_date up to 2026-07-28 — today's data has landed), but the frozen set
(`bars_available>=30` among `is_episode_start`) is **unchanged**: 1675 starts, 41 dates,
still capped 2026-04-01→2026-06-08. (The prior tick's "43 dates/06-09" figure doesn't
reproduce against a direct `bars_available` query today — likely counted something
slightly different, e.g. all rows vs episode-starts; immaterial either way since the
episode-start count, the number that actually gates a walk-forward, is identical.) No
frozen date has moved past 06-08 since HZ4's gap was first logged. ETA unchanged.

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

## H6 — Does an RSI-conditioned TREND_CONFIRM admission rule survive real multi-regime history?  ·  owner: picking → stops  ·  status: **closed-null (2026-07-30)**

**CLOSED 2026-07-30 — REJECTED.** Gate run (`optimizer_watch.txt`, years 2022/2024,
`--population live`): REALISTIC no-swap 2yr avg Sharpe delta **−0.08** (needs ≥+0.10),
2022 wash (0.00, TC blocked in bear so trade-for-trade identical), **2024 −0.15**
(the only year that actually exercises TREND_CONFIRM — outside the ±0.05 kill-condition
band on the negative side). >15d WR essentially flat (87.1%→86.9%). Full result in
`decisions.md` 2026-07-30 entry. No broader-universe confirmation run — narrow result
already misses the ship bar decisively. RSI as a panel diagnostic (H2's prescreen) is
not invalidated by this; specifically rejected is RSI raw value as a pooled-cap
tiebreak rank-score. H2's blocked multivariate retry remains the only path to a more
definitive answer on RSI as a feature, gated on panel data past ~mid-to-late August
2026 (past the current budget `end_date`).

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

**⚠ RESCOPED 2026-07-28 (lead) — the first attempt stalled, and the task as originally
written is why.** A worker-picking invocation started this (evidence: uncommitted,
gitignored scratch in `research/tmp/` — `h6_build_rsi_rank_scores.py`,
`h6_extract.log`, `h6_baseline.log`, plus one small, safe, additive code change to
`backtest_regime_compare.py`'s `run_scan()` stamping `sig['rsi']` onto every signal,
which the lead reviewed, tested — 20/20 `test_backtest_pooled_cap`/`test_backtest_atr_trail`
still green — and committed [`98276b3`] since it's a harmless prerequisite any future
attempt needs). **Neither log finished**: both cut off silently mid-2024 (no error, no
CSV ever written) — consistent with the runner's own "consecutive failures: 1" and its
existing 30m→8h backoff. Root cause of the stall, from reading the logs and the two
scripts involved:
1. **The task ran two full 5-year scans back to back in one invocation.** `h6_extract.log`
   was building the rank-scores CSV (looping `YEARS=[2022..2026]` on `optimizer_watch.txt`,
   50 symbols); `h6_baseline.log` was a *second*, separate full 5-year scan re-deriving a
   no-op baseline. That second scan was unnecessary duplicate work: **`confirm_backtest.py`
   already runs a paired candidate-vs-baseline comparison internally** (that is its entire
   purpose — see its module docstring) — the baseline half belongs to stops's downstream
   gate step, not to picking's CSV-building step. Cutting it removes roughly half the
   compute for zero information loss.
2. **The CSV-builder never checkpoints.** `h6_build_rsi_rank_scores.py` accumulates `rows`
   across all 5 years in memory and only calls `.to_csv()` once, after the final year. The
   log shows 2022 and 2023 completed *in full* (31 and 156 TC PREMIUM+/GOLD signals
   respectively, printed to stdout) before the process died mid-2024 — that work was
   100% real and 100% lost, because nothing was persisted until the very end. Any
   worker rebuilding this must write (or append) the CSV after each year's loop
   iteration, not just once at the end.
3. **Universe/year mismatch with the downstream gate.** `confirm_backtest.py` defaults to
   `--watchlist input/spx_plus.txt` (548 symbols) and `--years 2022,2024` (2 years) — the
   stalled attempt targeted `input/optimizer_watch.txt` (50 symbols) and all 5 years. If
   stops later runs the gate at its *defaults*, a CSV scored on a 50-symbol universe will
   have almost no `(date,symbol)` coverage against a 548-symbol gate run — unscored
   signals silently keep the default order (by design, degrades gracefully — but here
   that means the candidate barely gets tested at all, and a resulting null would be a
   **coverage failure masquerading as a null finding**, not evidence RSI doesn't work.

**Corrected next task (assign to picking, next tick):**
- Build the `date,symbol,score` CSV scoring TREND_CONFIRM PREMIUM+/GOLD signals by RSI
  (worker's judgment on exact functional form, per the original instruction — direction/
  shape only was established by the prescreen). **Checkpoint after every year** (append to
  the CSV or write `research/tmp/h6_rsi_rank_scores_<year>.csv` per year and concatenate at
  the end) so a repeat stall doesn't lose completed years again.
- **Limit the first pass to years 2022 and 2024** — exactly `confirm_backtest.py`'s own
  default gate years — instead of all 5. This alone should roughly halve the remaining
  compute versus the stalled attempt (which had already burned through 2022+2023 before
  dying in 2024). Extending to 2023/2025/2026 is only worth doing *after* a 2022/2024
  result exists and is promising.
- Keep the universe as `optimizer_watch.txt` (matches the work already validated as
  running successfully in the stalled log) — **do not** switch to `spx_plus.txt` in this
  step. Coverage must match between the CSV and whatever gate run consumes it (see below).
- **Do not run a separate baseline comparison.** Hand the CSV straight to stops's
  `confirm_backtest.py` step, which already produces the paired baseline.

**Follow-on (assign to stops once picking has a checkpointed candidate):** run
`research/confirm_backtest.py --rank-scores <file> --population live --watchlist
input/optimizer_watch.txt` — **the explicit `--watchlist` override is required**, matching
the CSV's own universe; running the gate at its 548-symbol default against a 50-symbol-built
CSV would produce a coverage failure, not a real test (point 3 above). Quote the gate's
printed realized signal-type mix per the standing caveat — 2022 will show few/no
TREND_CONFIRM trades since TC is blocked in RED_MARKET/BEARISH, so 2022 is a downside check
only, not a test of this specific rule; 2024 is where this candidate must actually prove
itself. If this narrower run is promising, broader-universe/more-year confirmation (matching
`confirm_backtest.py`'s actual defaults) is the natural next step before any ship decision —
per §13.5's own lesson, a 50-symbol curated-universe result should not be promoted without a
broader check.

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
- **HZ5 — new, found 2026-07-30, CONTAINED (checked against H4/H5/H6, no impact).**
  `build_panel.py` emits one row per (signal_date, mode, symbol) by design, and
  `is_episode_start`/`episode_id` are grouped by `['symbol','mode']` — so the same
  market event gets counted as two separate "episode starts" when a symbol fires in
  both `swing` and `longterm` mode on the same day (confirmed: only `mode`/`source_file`
  differ between such row pairs, `entry_used`/`mae_pct`/`ret_30d` are identical). This
  inflates TREND_CONFIRM's reported frozen n by **1.69×** (1459 raw vs 865 unique
  (symbol,signal_date) pairs); BOUNCE (1.02×) and CONTINUATION (1.05×) are barely
  affected. Re-ran H5's ≤15d/>15d WR split on deduped data: gap is 46.7pp (vs reported
  43.8pp), z=−13.85 (vs −16.85) — **larger gap, still overwhelming significance. H4/H5/
  H6's conclusions are unaffected**; their stated n/z should be read as mildly-inflated
  upper bounds, not wrong verdicts. Recommend the standard panel-loading recipe in the
  guardrails/prompts add `drop_duplicates(subset=['symbol','signal_date'])` so future
  work doesn't silently inherit the inflated count. **Live-side question CHECKED
  2026-08-01 (human): `auto_portfolio.py:323-325` already runs
  `v9h.drop_duplicates(subset=['Symbol'], keep='first')` on the pooled, priority-sorted
  frame *before* any position is opened — same-day swing+longterm duplicates collapse
  to the higher-priority row. Live never opens two positions for one symbol/day.
  HZ5 is confirmed panel-measurement-only, zero live-trading impact.** Full writeup:
  `decisions.md` 2026-07-30 (worker-stops).
