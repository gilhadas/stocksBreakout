# Shared guardrails — apply to EVERY research agent in this system

You are one agent in a small research team working on the stocksBreakout trading system.
Read `CLAUDE.md` §7–§13 for the project's decision history before forming any hypothesis.

## The prime directive: measurement, not simulation

The research substrate is `research/panel/panel.parquet` — a growing panel of every signal the
LIVE scanner emitted (Apr 2026 onward), joined to **what each stock actually did afterwards**,
measured off real daily bars.

- A statement like "a stop at 8% would have exited this trade" is **arithmetic on an observed
  path** — legitimate, and the whole point of the panel.
- A statement like "config X would have returned Y%" is a **simulation** — only legitimate from
  a backtest, and must be labelled as such.
- **Never present a simulated number as an observed one.** If you cannot tell which you have,
  stop and say so.

## Known data hazards — you MUST handle these

1. **The archive's `Price` column is unreliable — but the panel does NOT use it.** ~32% of rows
   record a price that never traded (PLTR at 197.20 on a day it ranged 131.23–134.68; 100% of
   2026-07-21). Root cause unresolved (HZ1).
   → **`entry_used` is the signal-day bar CLOSE**, mirroring what live actually does
   (`auto_portfolio` fetches its own price; the §7 A/B harness uses `avail['close'].iloc[0]`),
   and `Stop` is replaced by `entry*0.95` whenever the CSV's stop is at/above entry or >30% away
   — the same guard production applies. So every row is measurable and you should **not** filter
   on `price_in_bar_range`; it is a *diagnostic* of archive quality, not a validity gate.
   → **Do not use the raw `Price`, `Stop`, `Target` columns for anything quantitative.** Use
   `entry_used` and the measured forward columns. If you need a stop level, derive it.

2. **`mae_pct` can be positive.** A stock that gaps up and never retraces has no adverse
   excursion. `mae_pct_floored` is the conventional clamped-at-0 version. Pick deliberately;
   the naive assumption `mae <= 0` is wrong and will silently corrupt percentile work.

3. **Day-0 is excluded from forward metrics by design.** Scans fire ~09:35 ET but bars are
   daily, so the signal-day bar contains pre-signal movement. `mae_pct`/`mfe_pct`/`ret_*` start
   at day+1. `day0_low_pct`/`day0_high_pct` are separate — opt in consciously, and say so.

4. **Repeated signals are not independent observations.** The same symbol is re-flagged on
   consecutive days. Use `is_episode_start` / `episode_id` to dedupe, or your `n` is inflated
   and every significance claim is wrong.

5. **Schema drift.** Signal CSVs range from 31 to 57 columns across Apr–Jul as config changed.
   Missing features are NaN, not zero. Check coverage before relying on a column.

## Known structural fact — live and backtest trade different populations

Live runs `TREND_CONFIRM['enabled'] = True` Path A (config.py:226-228). **Every** documented
champion baseline in CLAUDE.md was measured with `--no-tc`, which disables it. Result:

| | Live archive (episode-deduped, n=1675) | Champion backtest |
|---|---|---|
| TREND_CONFIRM | **87%** (1459) | 0% (disabled) |
| BOUNCE | **8%** (134) | ~99.7% |
| CONTINUATION | 4% (62) | ~0% |

So conclusions in §7–§13 drawn from backtest trades describe a signal population live barely
produces. Treat them as **hypotheses to re-test on the panel**, not settled facts. In particular
§7's "WinProb calibration is inert, 99.7% one bucket" is a backtest artifact.

## Statistical discipline

- **Report `n` on every claim. Never conclude from n < 30** (after episode dedup).
- **Walk-forward any fitted model** — train on earlier dates, hold out the most recent. An
  in-sample improvement is not a result.
- **Measurement noise is real:** two identical backtest runs gave 4yr avg Sharpe 1.955 vs 2.20
  because yfinance fetch failures vary the universe. All 11 levers tested in §13 were ±0.01–0.09
  — *below that noise*. If a backtest effect is smaller than ~0.25 Sharpe, you cannot claim it
  from a single unpaired run.
- **Prefer a clean negative to a weak positive.** This project has 11 logged nulls; that is
  healthy. Do not manufacture a finding.

### Sweep discipline — read this before reporting ANY "optimal parameter"

A parameter sweep that finds an optimum has found nothing until you compare that optimum to the
**no-op baseline** (the value that disables the mechanism). Three ways a sweep lies, all of which
have already happened here:

1. **The optimum equals doing nothing.** A stop-distance sweep whose expectancy rises monotonically
   as the stop widens is converging on *no stop*. If `expectancy(optimum) − expectancy(no-stop
   baseline)` is within noise (say < a few % of the baseline), the honest finding is **"stops add
   no measurable expectancy on this data"**, NOT "the optimal stop is 21.5%". Always record the
   baseline value next to the optimum and lead with the delta, not the optimum. A sweep with
   `delta ≈ 0` or `delta < 0` is a NULL — report it as one, in those words.
2. **Truncation bias.** The panel caps forward measurement at 30 bars. A no-stop loser can only
   bleed for 30 days here; in reality it bleeds until stopped. So "no stop" looks artificially good
   in every sweep. This means a monotone-to-no-stop curve is *doubly* untrustworthy — say so.
3. **Sizing non-comparability.** A wider stop implies a smaller position for the same dollar risk
   (`MAX_PORTFOLIO_ATR_RISK` in auto_portfolio.py sizes by ATR risk). Per-trade %-return expectancy
   is therefore NOT comparable across stop widths without holding dollar-risk constant. State this
   whenever you compare returns across stop distances; do not treat raw %-expectancy as the answer.

**Fixed-% stop ≠ trailing stop.** The panel measures a static level; live uses an ATR trail that
ratchets up. The panel sweep is at best a lower bound on a trail, never the trail's own curve.
Never propose a specific trail multiplier *from* a fixed-% sweep — the sweep can only justify the
*direction* of a `confirm_backtest.py` run, which measures the actual trail.

## Promotion gates — a candidate rule must clear ALL of these

1. Measured on the panel with episode-deduped `n >= 30`, using `entry_used` (never the raw
   `Price` column), and reported against its no-op baseline per the sweep-discipline rules above.
2. Confirmed by `research/confirm_backtest.py` against **2022** — the panel window (Apr–Jul 2026)
   contains **no sustained bear**, and stops matter most in one.
   ⚠ **Known unresolved flaw in this gate — state it whenever you invoke it.** `confirm_backtest.py`
   hardcodes `--no-tc`, which *disables TREND_CONFIRM* — the 87% of live signals your panel finding
   is most likely drawn from. So the gate currently confirms candidate rules against a ~99.7%-BOUNCE
   population. A pass is therefore **not** evidence the rule works on live's stream, and a fail may
   be for an irrelevant reason. Report the gate result *with this caveat attached*; do not treat it
   as settled. Fixing the gate is a human decision, not yours to make unilaterally.
   ⚠ **The gate only accepts an ATR-trail multiplier (`--mult`).** There is no path to confirm a
   *ranking* candidate (Worker B's continuous model). Until one exists, picking findings are
   **diagnostic-only** — write them up as such rather than proposing them for live.
3. Ship bar: **≥ +0.10 Sharpe on the `--realistic-sizing` arm** (CLAUDE.md §11 standing rule).
   Idealized numbers are reference only.
4. **`>15d` hold win-rate must NOT shrink** (§13 halt criterion). That bucket is the entire edge
   (72–93% WR vs 4–29% for `≤15d`). If it shrinks, halt and report.

## Operating rules

- **Live config is PROPOSE-ONLY.** Never edit `config.py`, `docker/crontab`, `cron_jobs.txt`, or
  anything on EC2. Write proposals to `research/ledger/decisions.md`. A human ships them.
- **Commit only to the `research/auto-agents` branch.** Never `main`.
- Append findings to `research/ledger/results.jsonl`; never rewrite history in it.
- Respect `research/ledger/budget.json`. If over budget, stop and record why.
- If a task is blocked or an assumption fails, **write that to the ledger and stop** — do not
  improvise a different experiment to have something to report.
