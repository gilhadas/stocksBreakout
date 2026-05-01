# EPIC-CLI-001 — breakout_scanner.py: asyncio event loop + CLI smoke test
# Sprint: 2 (Signal) | Tier: 3 (subprocess, excluded from CI)

## Why this story matters
Python 3.14 changed how the default event loop is set up. breakout_scanner.py must
call `asyncio.set_event_loop(asyncio.new_event_loop())` BEFORE importing ib_insync —
the ordering is load-bearing (see CLAUDE.md §5, Critical Patterns). A future refactor
that reorders imports would cause a silent runtime crash.

## Module
`breakout_scanner.py`

## Acceptance Criteria

| # | Test type | Expected |
|---|-----------|----------|
| AC1 | AST/grep check | `asyncio.set_event_loop(asyncio.new_event_loop())` appears before any `import ib_insync` in the source file |
| AC2 | subprocess smoke test: `python breakout_scanner.py --help` | exits 0, no Python tracebacks |
| AC3 | subprocess smoke test: `python breakout_scanner.py --mock --mode swing --dry-run` (if --mock flag exists) | exits without IB connection error |

## Definition of Validated
AC1 passes as a Tier 2 static analysis test (included in CI).
AC2-AC3 are Tier 3 subprocess tests (excluded from CI, run locally).

## Test file
`tests/test_cli_smoke.py`

## Notes
- AC1 can be a static AST check: parse breakout_scanner.py, walk the tree, assert
  the set_event_loop call node appears before any ib_insync import node
- AC2/AC3 marked `@pytest.mark.tier3` — require IB Gateway or --mock flag
- If --mock flag does not exist, AC3 is xfail
