# Worker A — stop loss conditioned on stock behaviour

Read `research/prompts/_shared_guardrails.md` first. Every rule there binds you.

## The question

Live applies a **uniform ATR×2.0 trailing stop to every position**, regardless of what the stock
is doing (`auto_portfolio._raise_atr_trail` line 1242; `config.ATR_TRAIL_MULT = 2.0`,
`ATR_TRAIL_FLOOR_BARS = 14`). That multiplier was chosen by a global sweep — it has **never been
tested per pattern or per behaviour regime.**

Your job: find out whether the right stop distance depends on the stock's behaviour, and if so,
what rule captures it.

## Why the panel can answer this without simulating

For every signal, the panel records how the stock actually moved: `mae_pct` (how far it went
against you), `mfe_pct` (how far it ran), `bars_to_mae`, `ret_1/3/5/10/20/30d`, `hit_stop`,
`hit_target`. So:

- **The MAE distribution of eventual winners is the stop-loss answer, directly.** If trades that
  ended up +20% routinely dipped −9% first, then a 6% stop was destroying winners — that is an
  observation, not a model.
- **The tradeoff curve is arithmetic.** For a candidate stop distance `d`, a trade is stopped iff
  `mae_pct <= -d` before `bars_to_mae`; its outcome becomes `-d` instead of `ret_Nd`. Sweep `d`
  and read off return/WR/expectancy per cohort. No config sweep needed.

## Sequence

1. **Coverage first.** Before any hypothesis, report per-cohort counts after
   (a) episode dedup, (b) `bars_available >= 30`. Do **not** filter on `price_in_bar_range` —
   per the guardrails it is a diagnostic of archive quality, not a validity gate, and the panel
   already mirrors live entry semantics via `entry_used`.
   Cohorts to try: signal `Type`, `Quality`, `Sector`, and behaviour bins (ATR percentile, `RSI`,
   `Gap%`, `Vol`). **Say plainly which cohorts can never be answered at this sample size.**
   Note: live is ~87% TREND_CONFIRM, so that is where the statistical power is — **not BOUNCE**
   (~8%), despite BOUNCE dominating the historical backtest work in §7–§13.

2. **Q1 — winner MAE.** Per cohort with n≥30: the MAE distribution of winners (median, p75, p90).
   A stop tighter than p90-of-winner-MAE is provably cutting winners; quantify how many.

3. **Q2 — tradeoff curve.** Sweep stop distance per cohort; find each cohort's expectancy-maximising
   distance. Report it as ATR-multiples too, so it maps onto `ATR_TRAIL_MULT`.

4. **Q3 — behaviour vs pattern label.** Does a behaviour bin (volatility/RSI/gap/volume) predict
   the right stop better than a named chart pattern? Behaviour generalises better and is cheaper
   to compute. If chart patterns add nothing beyond ATR percentile, **say so** — that is a useful
   negative and saves the pattern-labelling cost.

5. **Only if a cohort shows a materially different optimum than ATR×2.0**, propose a rule and run
   `research/confirm_backtest.py` against 2022. The panel has no bear market; a stop rule that is
   never bear-tested must not reach live.

## Traps specific to this question

- **Survivorship in `hit_stop`.** The live stop already exited some positions, so their observed
  forward path after the exit still exists in the panel (the panel tracks the *stock*, not the
  position). That is a feature — it lets you ask "would a wider stop have recovered?" — but never
  mix pre- and post-exit paths in one aggregate without saying so.
- **`bars_to_mae` ordering matters.** A trade whose MAE came on day 25 was not stopped out on
  day 2. Always check the *first* breach, not the extremum, when simulating a stop.
- **Tighter stops always raise win-rate and usually lower expectancy.** Judge on expectancy and
  the `>15d` bucket, never on WR alone — §13's halt criterion exists because of exactly this.
- Kaminski & Lo (CLAUDE.md §12 research review): stops help under momentum, hurt under mean
  reversion. TREND_CONFIRM entries are momentum; BOUNCE entries' first days are mean-reverting.
  Expect the answer to differ by signal type — that is the hypothesis, so test it, don't assume it.

## Output

Append to `research/ledger/results.jsonl`; write conclusions and any proposal to
`research/ledger/decisions.md`. Live config is propose-only.
