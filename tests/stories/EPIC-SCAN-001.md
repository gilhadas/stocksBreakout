# EPIC-SCAN-001 — scanner.py Threshold Boundaries + Volume Gate

```
Story ID:     EPIC-SCAN-001
Module:       scanner.py
Title:        Validate PREMIUM/HIGH/GOLD score threshold boundaries and volume gate

AS A:         portfolio risk manager
I WANT:       BreakoutDetector to classify signals at exact threshold boundaries
SO THAT:      no signal crosses a quality tier without meeting the mathematical criterion

GIVEN:        a mock MarketData fixture injected into BreakoutDetector
WHEN:         _classify_quality(score) is called with boundary values
THEN:         score=99  → GOLD
AND:          score=98  → PREMIUM
AND:          score=69  → PREMIUM  (lower bound)
AND:          score=68  → HIGH
AND:          score=65  → HIGH     (lower bound)
AND:          score=64  → STANDARD
AND:          score=50  → STANDARD (lower bound)
AND:          score=49  → below STANDARD (rejected or labeled WEAK)
WHEN:         volume = 149% of 20-bar MA on breakout candle
THEN:         breakout is NOT classified (volume gate blocks)
WHEN:         volume = 151% of 20-bar MA on breakout candle
THEN:         breakout IS classified (volume gate passes)

ACCEPTANCE CRITERIA:
  AC1: score=99  → GOLD
  AC2: score=98  → PREMIUM
  AC3: score=69  → PREMIUM
  AC4: score=68  → HIGH
  AC5: score=65  → HIGH
  AC6: score=64  → STANDARD
  AC7: volume=149% → breakout=False (volume gate blocks)
  AC8: volume=151% → breakout=True
  AC9: No live IB connection — MarketData fully mocked

DEFINITION OF DONE:
  □ Test written and passing
  □ All 8 boundary values covered
  □ Volume gate at 150% boundary documented (strictly > or >=)
  □ No live IB/S3 dependency (fully mocked)
  □ Added to CI pipeline
  □ Product Owner signed off
```

## Notes

- `_classify_quality()` and `_check_volume_expansion()` must be extractable methods.
  If this logic is inlined inside `detect()`, a prerequisite refactor is needed to
  expose these as independently testable functions.
- Volume gate spec says >150% — document whether the implementation uses strict `>`
  or `>=` at exactly 150%, and align the AC accordingly.

## Sprint Assignment

**Sprint 1** — Guard the Money (scoring engine)

## Test File

`tests/test_scanner_thresholds.py`
