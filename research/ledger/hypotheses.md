# Open hypotheses

Status values: `active` | `blocked` | `closed-null` | `closed-promising`
The **lead** owns this file. Workers read it to pick their next task and may append
evidence lines, but only the lead changes a status or reorders priorities.

Last lead review: 2026-07-26 (ticks reviewed: H1 stop-sweep + H3 behaviour-bins,
2026-07-25; H2 walk-forward + sensitivity sweep, 2026-07-25). See `decisions.md` same
date for the full audit and rationale.

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

**No task assigned to picking this cycle.** Next task, when reopened: retry the
walk-forward Ridge with ≥70 frozen training days, AND (new, added today) cross the model
against H5's hold-duration split once available — a ranking feature that predicts
≤15d-vs->15d hold outcome would be more actionable than one that predicts raw ret_20d.

---

## H3 — Chart-pattern labels add nothing over simple behaviour features  ·  owner: stops  ·  status: closed-null

**Closed 2026-07-26.** Behaviour-bin pre-test (2026-07-25) inside TREND_CONFIRM (n=1459):
RSI/Gap%/Vol/SMA_Dist% tertiles showed winner-MAE differences well below noise (p50 range
≤1pp, p90 range ≤3.2pp vs the −10.71pp baseline) and every bin's stop-sweep optimum sat
inside the no-stop CI (max delta +0.22pp). Kill condition explicitly met. The 28-detector
pattern library is not worth computing for either the stop question or (per the same
logic) likely the ranking question. No further tasks.

---

## H4 — Live's TREND_CONFIRM-dominated stream behaves differently from the backtest's BOUNCE stream  ·  owner: lead  ·  status: active  ·  **TOP PRIORITY — next worker-stops tick**

**Correction (2026-07-26):** this entry still carried the pre-correction figures (~92%
TREND_CONFIRM / ~5% BOUNCE). CLAUDE.md §14.1 fixed this everywhere else on 2026-07-25 but
missed this file. Measured, episode-deduped (n=1675): **87% TREND_CONFIRM (1459), 8%
BOUNCE (134), 4% CONTINUATION (62).** Every champion baseline in CLAUDE.md §7–§13 was
measured with `--no-tc`, i.e. ~99.7% BOUNCE — a population live barely produces.

**Why it matters:** H1's and H3's cohort tables already show *some* of this (winner-MAE by
Type, cohort sample sizes) as a side effect of the stop/pattern work, but no one has
produced the dedicated per-Type profile this hypothesis calls for. It has been on the
board since bootstrap (2026-07-24) and has never been executed as its own deliverable.
It needs **no new data** — the existing 1675 frozen (20d/30d-complete) episodes are enough
— so, unlike H2, there is nothing blocking it today.

**Next task (assign to worker-stops, next tick):** build the per-`Type` profile table:
forward-return (ret_5/10/20/30d mean+median), hit-rate (`hit_stop`/`hit_target` frequency),
and hold-duration (`bars_to_stop`/`bars_to_target`, and the ≤15d-vs->15d split called out
in CLAUDE.md §8/§13 as the project's one universe-independent drag). Report by Type, and
by Type×Quality where n≥30. This is diagnostic — no lever, no sweep-discipline gate needed,
just report the numbers honestly with n on every cell.

**Follow-on (queued as H5 below):** once this table exists, dig into the ≤15d/>15d split
specifically within TREND_CONFIRM, since that's ~87% of what live trades and the drag is
large everywhere else it's been measured (CLAUDE.md §13.5).

---

## H5 — Is the universe-independent ≤15d-hold drag present, and how large, within TREND_CONFIRM specifically?  ·  owner: stops  ·  status: active  ·  queued after H4

Every backtest universe tested in CLAUDE.md (§8, §13) shows the same shape: ≤15d holds at
4–29% WR, >15d holds at 72–93% WR. That was always measured on the `--no-tc` (~99.7%
BOUNCE) population. H4's diagnostic will show whether the split exists, and how large it
is, in the population live actually trades (87% TREND_CONFIRM).

**Why it matters:** per the lead's "steering toward profit" priorities — this is the
"large + affects what live trades" combination that ranking tweaks (H2) do not have. If
TREND_CONFIRM shows the same short-hold drag, it's the highest-value remaining target on
this panel; if it's much smaller within TC, that changes where effort goes next.

**Next task:** depends on H4's output. If the split is large (comparable to the backtest's
4–29%/72–93% shape) within TC, look for an *admission-time* signature (features available
before the trade, not post-hoc) that predicts which bucket a TC signal falls into — this
is the natural point where H2 (picking) reopens with a sharper, more actionable target than
raw ret_20d.

**Kill condition:** if the ≤15d/>15d split is materially smaller within TC than in the
`--no-tc` backtests (say, WR gap <20pp instead of 40–70pp), close as `closed-null` — the
drag is a BOUNCE/mean-reversion artifact, not something live's dominant signal type
suffers from.

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
