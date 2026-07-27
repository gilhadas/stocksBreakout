# Claude Code Skills — vendored

The six trading skills Claude Code loads when working on this project. They are
**symlinked**, not copied, into `~/.claude/skills/`:

```
~/.claude/skills/technical-indicators -> <repo>/skills/technical-indicators
~/.claude/skills/chart-patterns       -> <repo>/skills/chart-patterns
~/.claude/skills/portfolio-exits      -> <repo>/skills/portfolio-exits
~/.claude/skills/market-regime        -> <repo>/skills/market-regime
~/.claude/skills/fibonacci-bounce     -> <repo>/skills/fibonacci-bounce
~/.claude/skills/sentiment-analysis   -> <repo>/skills/sentiment-analysis
```

## Why symlinks and not copies

A copy would be a second hand-maintained version of the same content with nothing
binding the two together — which is precisely the `cron_jobs.txt` ↔ `docker/crontab`
arrangement that produced **three** separate production incidents (CLAUDE.md §13, and
the `refresh_prices` omission found 2026-07-27). Editing either side of a symlink edits
the same bytes, so drift is structurally impossible rather than merely discouraged.

`voice-to-plan` is deliberately **not** vendored — it is unrelated to this project and
stays a purely local skill.

## Setting this up on a new machine

```bash
cd ~/.claude/skills
for s in technical-indicators chart-patterns portfolio-exits \
         market-regime fibonacci-bounce sentiment-analysis; do
    ln -s "$HOME/Documents/GitHub/stocksBreakout/skills/$s" "$s"
done
```

Adjust the repo path if yours differs. **If the repo is moved or deleted the symlinks
break and the skills silently stop loading** — `scripts/verify_skills.py` reports that
as a missing-skill failure rather than passing vacuously.

## Why they are in the repo at all

These are prose+code documents that Claude reads and then writes real code from. When
they were only in `~/.claude/skills/` they had no history, no review and no backup — and
an audit on 2026-07-26/27 found real defects in **four of the six**:

| Skill | Defect found |
|---|---|
| `technical-indicators` | ADX missing Wilder's directional-exclusivity rule (gate disagreed on 8% of bars); VWAP documented as a bare cumsum with no session reset; two false TradingView-parity claims |
| `portfolio-exits` | "Trailing stop" anchored to entry, *added* rather than subtracted, never ratcheted, triggered intraday — the exact defect `dc3e252` fixed in production; fabricated `DEFAULT_EXIT_CONFIG` (8 of 10 keys did not exist) |
| `market-regime` | `detect_regime` was a different algorithm from `quantkit.regime.detect_regime` — 30.1% agreement over 1043 SPY days; `suggest_params` keys fabricated |
| `sentiment-analysis` | `get_buzz_ratio` did not exist; invented a `sentiment` field the free Finnhub tier cannot return; `buzz_ratio` thresholds inverted; `score` used as polarity when it is a confidence |

`chart-patterns` and `fibonacci-bounce`'s core tables were accurate (the latter had two
`NameError`s that made `score_bounce` return `None` on every call).

## Guarding them

```bash
./venv/bin/python scripts/verify_skills.py
```

76 checks: formula parity by **executing** the skill's own code against `quantkit` on
real bars, plus API contracts by introspection, plus the composite-score / Minervini /
Volume Profile / TradingView-parity claims. Exit 0 means every checkable claim matches
the code the scanner actually runs.

Every check is mutation-tested — reintroducing the original bug produces a named
failure — so a passing run means something. Run it after editing any skill, and note
that roughly a third of the content is generic teaching material that is *deliberately*
not this repo's implementation; the script's docstring says which.
