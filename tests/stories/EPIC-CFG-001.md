# EPIC-CFG-001 — config.py Schema Validation

```
Story ID:     EPIC-CFG-001
Module:       config.py
Title:        Validate config.py schema, types, and threshold ordering

AS A:         CI pipeline
I WANT:       automated verification that config.py exports all required keys
              with correct types and values before any module imports it
SO THAT:      a misconfigured deploy (e.g. BOUNCE_BEAR_GATE='15' as string,
              or GOLD threshold below PREMIUM) cannot reach production silently

GIVEN:        config.py is importable in the test environment
              (no IB connection, no S3, no .env secrets required for schema check)
WHEN:         the test module imports config at the top of the test file
THEN:         BOUNCE_BEAR_GATE is an int with value exactly 15
AND:          V9H_REGIME_GATE is a dict with key 'enabled' equal to False
AND:          TREND_CONFIRM is a dict with keys 'enabled' (bool True) and
              'enabled_paths' (list containing exactly 'A')
AND:          scoring thresholds satisfy GOLD > PREMIUM > HIGH > STANDARD
              (99 > 69 > 65 > 50) with no two thresholds equal
AND:          a second import of config within the same process returns
              the identical object (singleton, no re-evaluation)

ACCEPTANCE CRITERIA:
  AC1: assert isinstance(config.BOUNCE_BEAR_GATE, int) and
       config.BOUNCE_BEAR_GATE == 15
  AC2: assert config.V9H_REGIME_GATE['enabled'] is False
  AC3: assert config.TREND_CONFIRM['enabled'] is True
       assert config.TREND_CONFIRM['enabled_paths'] == ['A']
  AC4: assert config.GOLD > config.PREMIUM > config.HIGH > config.STANDARD
  AC5: mutating config.BOUNCE_BEAR_GATE in one test does NOT affect the value
       seen by a second import in the same process (document the risk if Python
       module caching makes mutation visible — flag as architecture defect,
       not test defect)

DEFINITION OF DONE:
  □ Test written and passing
  □ Edge cases covered: string-typed threshold caught by isinstance check;
    GOLD == PREMIUM equality caught by strict > not >=
  □ No live IB/S3 dependency (fully mocked)
  □ Added to CI pipeline
  □ Product Owner signed off
```

## Architect's Notes

- Python modules are mutable singletons. A test mutating `config.BOUNCE_BEAR_GATE = 99`
  contaminates all subsequent imports in the process. AC5 surfaces this risk —
  the real fix is a frozen dataclass or `__setattr__` guard raising `TypeError`.
  Flag as follow-on story if the team cares about test isolation.
- String-typed threshold (e.g. `BOUNCE_BEAR_GATE = "15"`) won't be caught by a
  value-only check — the `isinstance(x, int)` in AC1 is critical.
- GOLD == PREMIUM equality is caught only by strict `>`, not `>=`. Use the chain
  `GOLD > PREMIUM > HIGH > STANDARD` in a single assertion.

## Sprint Assignment

**Sprint 1** — Guard the Money (foundation layer)
