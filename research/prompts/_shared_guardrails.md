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

1. **Bad prices in the archive.** Part of the signal archive records prices that never traded
   (ADBE flagged at 93.87 on a day it ranged 227.70–236.30). `Stop`/`Target` are derived from
   `Price`, so those rows' stop/target/return measurements are all meaningless.
   → **Filter `price_in_bar_range == True` for any quantitative claim.** Report how many rows
   you dropped. If a finding only exists in the unfiltered data, it is not a finding.

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

| | Live archive | Champion backtest |
|---|---|---|
| TREND_CONFIRM | ~92% | 0% (disabled) |
| BOUNCE | ~5% | ~99.7% |

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

## Promotion gates — a candidate rule must clear ALL of these

1. Measured on the panel with `price_in_bar_range == True` and episode-deduped `n >= 30`.
2. Confirmed by `research/confirm_backtest.py` against **2022** — the panel window (Apr–Jul 2026)
   contains **no sustained bear**, and stops matter most in one.
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
