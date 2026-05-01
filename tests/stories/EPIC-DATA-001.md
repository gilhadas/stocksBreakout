# EPIC-DATA-001 — market_data._normalize_timeframe() Validation

```
Story ID:     EPIC-DATA-001
Module:       market_data.py
Title:        Validate _normalize_timeframe() maps all string variants to valid IB codes

AS A:         data pipeline engineer
I WANT:       _normalize_timeframe() to reject or correct every non-IB-valid timeframe string
SO THAT:      silent empty-data returns from IB are eliminated at the source

GIVEN:        a set of canonical valid strings and a set of known-bad alias strings
WHEN:         _normalize_timeframe(s) is called for each string
THEN:         canonical inputs return the expected IB-valid code unchanged
AND:          known aliases ('1 week', '1week', 'DAILY') are mapped to their IB equivalents
              OR raise ValueError — never silently return the invalid input string
AND:          None, empty string, and unknown strings raise ValueError or return a sentinel

ACCEPTANCE CRITERIA:
  AC1: _normalize_timeframe('1W')      == '1W'       (unchanged, IB-valid)
  AC2: _normalize_timeframe('1D')      == '1 day'    (or equivalent IB code)
  AC3: _normalize_timeframe('1H')      == '1 hour'
  AC4: _normalize_timeframe('4H')      == '4 hours'
  AC5: _normalize_timeframe('1 week')  raises ValueError OR returns '1W' — NEVER returns '1 week'
  AC6: _normalize_timeframe(None)      raises TypeError or ValueError
  AC7: _normalize_timeframe('')        raises ValueError
  AC8: _normalize_timeframe('garbage') raises ValueError
  AC9: return value on valid input is always a member of IB's accepted timeframe set

DEFINITION OF DONE:
  □ Test written and passing
  □ Edge cases covered (None, empty string, unknown strings, alias variants)
  □ No live IB/S3 dependency (fully mocked)
  □ Added to CI pipeline
  □ Product Owner signed off
```

## Background

The critical bug: passing '1 week' (with space) to IB silently returns empty bar data.
The scanner then operates on an empty DataFrame — no exception, no log, no signal.
This test enforces that '1 week' never reaches the IB API verbatim.

AC5 allows either raising OR mapping to '1W'. The key invariant is that the raw
alias string NEVER passes through. Document which behavior the implementation uses.

## Sprint Assignment

**Sprint 2** — Protect the Signal (data pipeline)

## Test File

`tests/test_market_data_normalize.py`
