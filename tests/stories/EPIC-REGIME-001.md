# EPIC-REGIME-001 — orchestrator.py BBG15 Gate Validation

```
Story ID:     EPIC-REGIME-001
Module:       orchestrator.py
Title:        Validate BBG15 gate skips detect_bounce() at consec >= 15,
              fires at consec = 14

AS A:         risk system
I WANT:       orchestrator to skip bounce detection exactly when the market
              has been below SMA200 for 15 or more consecutive days
SO THAT:      the 2022 bear-market protection (-4.50% improvement) is not
              accidentally disabled by an off-by-one error or a refactor

GIVEN:        get_spy_consec_below_sma200() is monkeypatched to return a
              controlled integer N
              regime is set to RED_MARKET (string constant from config)
              detect_bounce() is replaced with a Mock that records call count
WHEN:         orchestrator._scan_symbol() (or equivalent entry point) is invoked
              with a minimal valid symbol fixture
THEN:         when N = 15, detect_bounce() Mock is called exactly 0 times
AND:          when N = 14, detect_bounce() Mock is called exactly 1 time
AND:          when N = 16, detect_bounce() Mock is called exactly 0 times
AND:          when regime is BULL (not RED_MARKET) and N = 15,
              detect_bounce() Mock is called exactly 1 time
              (gate is AND condition: both regime AND consec required)
AND:          when N = 0 + RED_MARKET, detect_bounce() is called exactly 1 time

ACCEPTANCE CRITERIA:
  AC1: N=15 + RED_MARKET → detect_bounce call count == 0
  AC2: N=14 + RED_MARKET → detect_bounce call count == 1
  AC3: N=15 + BULL        → detect_bounce call count == 1
  AC4: N=0  + RED_MARKET  → detect_bounce call count == 1
  AC5: get_spy_consec_below_sma200() called exactly once per _scan_symbol() invocation

DEFINITION OF DONE:
  □ Test written and passing
  □ Boundary values: N=14 fires, N=15 blocked, N=16 blocked, regime mismatch fires
  □ No live IB/S3 dependency — get_spy_consec_below_sma200() fully mocked
  □ Added to CI pipeline
  □ Product Owner signed off
```

## Architect's Notes

**PREREQUISITE CHECK BEFORE WRITING:** If `_scan_symbol()` is a method on a class
that instantiates an IB connection in `__init__`, you cannot create an instance
in tests without a live broker. You will need either:
  (a) A factory pattern: `OrchestratorFactory.build(ib=mock_ib)`
  (b) Extract the BBG15 gate logic to a standalone injectable function

Confirm the constructor signature before committing to this story.

The boundary test (N=14 fires, N=15 blocks) is the entire reason this story exists.
A `>` vs `>=` typo would invisibly remove the 4.5% bear-market edge.
Two lines of mock setup catches it permanently.

## Backtest Context

BBG15 (BOUNCE_BEAR_GATE=15) justification from 5-year backtest:
- 2022 bear: 57% of days had SPY consec >= 15 → gate active, -12.82% vs -17.32% baseline
- 2023 dips: max ~6 days → gate never triggered, bull run preserved
- April 2025 tariff: 9-14 days → gate never triggered
- BBG10 rejected: kills 2023 at -30.7% vs baseline

## Sprint Assignment

**Sprint 1** — Guard the Money (regime gates)
