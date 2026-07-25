# Worker B — which signal gets picked into auto_portfolio

Read `research/prompts/_shared_guardrails.md` first. Every rule there binds you.

## The question

More signals fire each day than there is cash for. `auto_portfolio` ranks them and admits ~10
(`_pooled_cap`, and `_compute_priority_score` at line 870, which takes only four inputs:
`quality, win_prob, rr, vol`). Your job: is that ranking picking the right ones, and can a better
one be built?

## Start here — the system already runs a free natural experiment

Every trading day the system admits some signals and skips the rest for cash. **Both groups'
forward paths are in the panel.** So the first question needs no model at all:

> On the same day, did admitted signals actually outperform skipped ones?

If skipped signals match or beat admitted ones, the ranking is adding nothing — measurable
directly. Stratify by day (never pool across days: a good day lifts both groups and will fake a
result). Report per-day paired comparisons and the aggregate with `n` days.

**Read `admitted` carefully** — reconstruct it from `auto_portfolio` history / `skipped_cash`. If
that join is unreliable for a stretch of dates, say so and restrict the window rather than
guessing.

## Then, and only then, a model

If the ranking is measurably mis-ordering, fit a **continuous** forward-return model on panel
features to replace the 4-input bucket formula — this is the upgrade CLAUDE.md §7 itself called
for ("real ranking upgrade requires per-signal features + a continuous model").

- Target: `ret_10d` or `ret_20d` (state which and why); consider expectancy-after-stop instead of
  raw return, since the position has a stop.
- Features: everything in the panel — `Vol, Dist, SMA_Dist%, R:R, Gap%, RSI, TC_Score, TC_Path,
  Quality, Type, Sector, FinBERT_*`, plus derived volatility/regime bins.
- **Walk-forward mandatory**: train on earlier dates, hold out the most recent. Report holdout
  error, not fit quality.
- Keep it simple and inspectable. A linear/GBM model you can explain beats a black box that
  cannot be reasoned about at 09:35 on a live trading morning.

## Explicitly out of scope

**Do not test more bucket/tiebreak/cap variants.** Eleven have been tested and all were null
(§13): live-tiebreak, sleeve-slots, panic-throttle (+bear-only), normal-bounce-cap, residual-dist,
SMA200 gates (both), WinProb-cal, Tension, Supertrend, Breakeven. Adding a twelfth is not research.
The unexplored direction is per-signal features and a continuous model — stay there.

## Traps specific to this question

- **§7's "WinProb is inert, 99.7% one bucket" is a backtest artifact.** Live is ~87%
  TREND_CONFIRM with a genuinely mixed quality distribution. Re-derive the bucket structure from
  the panel before assuming anything about it.
  **Already measured (tick 1, `results.jsonl`):** `WinProb` is 99.8% NaN in the archive — the
  calibration JSON is not deployed to `scanner_output/`, so the column is empty *end-to-end in
  live*, not merely uninformative. Do not re-derive this; build on it.
- **Selection effect:** admitted signals were chosen *because* they ranked highly, so comparing
  their outcomes to skipped ones measures the ranking's value — but a null result could also mean
  cash was the binding constraint, not rank quality. Check how often the cap actually bound.
- **Same-day correlation:** admitted names on one day are often the same theme (§10's Feb-2026
  cluster, §11's 2022 EXPANSION bucket). Treat a day as roughly one observation for significance,
  not ten.
- **Episode dedup** — a symbol re-flagged five days running is one opportunity, not five.

## Output

Append to `research/ledger/results.jsonl`; conclusions and proposals to
`research/ledger/decisions.md`. Live config is propose-only.
