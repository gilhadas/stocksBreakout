# EPIC-CI-001 — GitHub Actions CI Pipeline (Sprint 0 Blocker)

```
Story ID:     EPIC-CI-001
Module:       infrastructure
Title:        Set up GitHub Actions CI pipeline for Tier 1/2 (offline) tests

AS A:         developer
I WANT:       automated test execution on every push and PR
SO THAT:      regressions are caught before production and stories cannot be
              marked "done" without tests actually passing

GIVEN:        a push to main or a new PR is opened
WHEN:         the GitHub Actions workflow triggers
THEN:         all Tier 1 (unit) and Tier 2 (mock) tests execute in isolation
AND:          the build passes only if 100% of tests pass
AND:          a green/red badge appears on the README
AND:          Tier 3 (IB paper integration) tests are excluded from CI
              and run locally via `make test-integration`

ACCEPTANCE CRITERIA:
  AC1: .github/workflows/ci.yml runs `pytest tests/ -m "not tier3"` on every
       push to main and every PR
  AC2: Environment is clean — no .env loaded, no IB Gateway, no S3 access
  AC3: All 4 test skeletons pass: test_indicators_math, test_market_data_normalize,
       test_scanner_thresholds, test_pattern_recognition_contract
  AC4: Build fails fast if any Tier 1/2 test fails
  AC5: README displays CI badge linked to Actions dashboard
  AC6: Workflow triggers on: push to main, PR creation, manual dispatch

DEFINITION OF DONE:
  □ .github/workflows/ci.yml created ✓
  □ tests/conftest.py stubs ib_insync before any module import ✓
  □ tests/.env.test provides dummy env vars for CI ✓
  □ All 4 test skeletons pass locally with `pytest tests/ -m "not tier3"`
  □ pytest.ini registers the tier3 marker
  □ Makefile `test-integration` target added for Tier 3 local runs
  □ README CI badge added
  □ First green build on main confirmed
```

## Files Created

| File | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | GitHub Actions workflow — runs on push + PR |
| `tests/conftest.py` | Stubs `ib_insync` before any test imports; loads `.env.test` |
| `tests/.env.test` | Dummy env vars (no real secrets — safe to commit) |

## Tier 3 Local Gate

```bash
# Run IB paper integration tests (requires IB Gateway on port 7497)
make test-integration
```

```makefile
# Makefile
test-integration:
    pytest tests/ -m tier3 -v --tb=short
```

## Blocked By

Nothing — this is Sprint 0 and unblocks all other stories.

## Blocks

All Sprint 1-3 stories. No story may be marked "done" until CI badge is green.

## Sprint Assignment

**Sprint 0** — CI Infrastructure (prerequisite for all validation work)
