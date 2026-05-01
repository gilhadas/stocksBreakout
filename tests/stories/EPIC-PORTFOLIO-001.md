# EPIC-PORTFOLIO-001 — auto_portfolio.py Pooled Cap Validation

```
Story ID:     EPIC-PORTFOLIO-001
Module:       auto_portfolio.py
Title:        Validate pooled cross-day cap selects top 10 signals ranked by
              Quality → WinProb → R:R → Dist≤25% → Vol with stop-loss guard

AS A:         portfolio manager
I WANT:       the pooled cap logic to select exactly 10 signals from a larger
              fixture, using the documented tiebreak hierarchy, and to reject
              any signal whose stop distance exceeds 30%
SO THAT:      the +86pt improvement from the pooled cap refactor is protected
              against regression, and the 30% stop guard is enforced at intake

GIVEN:        a fixture of 15 signals (list of dicts or DataFrame rows) with fields:
                symbol, quality (GOLD/PREMIUM/HIGH), win_prob (float),
                rr (float), dist_pct (float), vol (float), stop_distance_pct (float)
              fixture includes:
                - 2 signals with stop_distance_pct > 30% (must be rejected pre-rank)
                - 1 signal with dist_pct = 26% (above 25% cap — deprioritized)
                - remaining signals with varied quality/win_prob/rr/vol combos
              MAX_ADDS_PER_SCAN is patched to 10
WHEN:         the pooled cap selection function is called with the fixture
THEN:         exactly 10 signals are returned (not 11, not 9)
AND:          the 2 signals with stop_distance_pct > 30% are absent from output
AND:          all returned signals have stop_distance_pct <= 30%
AND:          among returned signals, GOLD precedes PREMIUM, PREMIUM precedes HIGH
              within equal win_prob/rr buckets
AND:          the signal with dist_pct = 26% is ranked below all dist_pct <= 25%
              signals with equal or better quality/win_prob/rr
AND:          if two signals are identical on Quality+WinProb+R:R+Dist, higher vol ranks first

ACCEPTANCE CRITERIA:
  AC1: len(selected) == 10
  AC2: no selected signal has stop_distance_pct > 30%
  AC3: quality ordering respected — GOLD > PREMIUM > HIGH within same bucket
  AC4: dist_pct=26% signal not selected if any unselected signal has dist_pct <= 25%
       and equal or better ranking keys
  AC5: vol tiebreak correctly applied as final sort key
  AC6: function is deterministic — identical fixture produces identical output
       on repeated calls (no random shuffle in selection path)

DEFINITION OF DONE:
  □ Test written and passing
  □ Edge cases: exactly 10 valid signals (no truncation needed); all rejected by
    stop guard (returns empty list, not crash); all dist_pct > 25%
  □ No live IB/S3 dependency (fully mocked)
  □ Added to CI pipeline
  □ Product Owner signed off
```

## Architect's Notes

**PREREQUISITE CHECK:** Verify that `MAX_ADDS_PER_SCAN` is read inside the function
body at call time — NOT captured at module load time. If captured at import, patching
with `unittest.mock.patch` won't work.

**Sort key bug risk:** Quality tier ordering must map to an ordinal before sorting:
  GOLD=3, PREMIUM=2, HIGH=1, STANDARD=0
If the sort uses the raw string, alphabetical order gives STANDARD > PREMIUM > HIGH > GOLD
(exactly backwards). AC3 will catch this.

## Backtest Context

Per-file cap problem: On 2026-04-06, the per-file first-3-wins cap exhausted the
daily budget before INTC/AMD/NVDA could be selected. Pooled cross-day cap fixed this,
adding +86 pts over 5 years (+195% vs +109% compound return).

## Sprint Assignment

**Sprint 1** — Guard the Money (portfolio management)
