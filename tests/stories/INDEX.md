# Validation Story Index — stocksBreakout

Generated: 2026-05-01 | Team: John (PM), Amelia (QA/Dev), Mary (Scrum Master), Winston (Architect)

## Sprint 1 — Guard the Money (Epics 1+2 core)

| Story | Module | Title | Test File | Status |
|-------|--------|-------|-----------|--------|
| [EPIC-CFG-001](EPIC-CFG-001.md) | `config.py` | Schema, types, threshold ordering | *(inline in test_scanner_thresholds.py)* | ✅ DONE |
| [EPIC-IND-001](EPIC-IND-001.md) | `indicators.py` | RSI Wilder EMA + ATR + MACD math | `test_indicators_math.py` | ✅ DONE |
| [EPIC-SCAN-001](EPIC-SCAN-001.md) | `scanner.py` | PREMIUM/HIGH/GOLD boundaries + volume gate | `test_scanner_thresholds.py` | ✅ DONE |
| [EPIC-REGIME-001](EPIC-REGIME-001.md) | `orchestrator.py` | BBG15 gate N=14 fires / N=15 blocked | *(TBD — needs prerequisite refactor)* | 🔲 TODO |
| [EPIC-PORTFOLIO-001](EPIC-PORTFOLIO-001.md) | `auto_portfolio.py` | Pooled cap top-10 + stop-loss guard | *(TBD — check MAX_ADDS_PER_SCAN capture)* | 🔲 TODO |

## Sprint 2 — Protect the Signal

| Story | Module | Title | Test File | Status |
|-------|--------|-------|-----------|--------|
| [EPIC-PAT-001](EPIC-PAT-001.md) | `pattern_recognition.py` | get_pattern_score() 7-tuple contract | `test_pattern_recognition_contract.py` | ✅ DONE |
| [EPIC-DATA-001](EPIC-DATA-001.md) | `market_data.py` | _normalize_timeframe() all variants | `test_market_data_normalize.py` | ✅ DONE |
| [EPIC-EXIT-001](EPIC-EXIT-001.md) | `exit_evaluator.py` | ExitEvaluator.evaluate() exit signals | `test_exit_evaluator.py` | ✅ DONE |
| [EPIC-FIB-001](EPIC-FIB-001.md) | `fib_retracement.py` | score_bounce math + scoring gates | `test_fib_retracement.py` | ✅ DONE |
| [EPIC-PREMARKET-001](EPIC-PREMARKET-001.md) | `early_premarket_scan.py` | Output schema + API fallback chain | `test_early_premarket_scan.py` | ✅ DONE |
| [EPIC-CLI-001](EPIC-CLI-001.md) | `breakout_scanner.py` | asyncio event loop order + CLI smoke | `test_cli_smoke.py` | 🔲 TODO (Tier 3 — local only) |
| [EPIC-ALGO-001](EPIC-ALGO-001.md) | `algo_trader.py` | active_orders dict lifecycle | `test_algo_trader_paper.py` | 🔲 TODO (Tier 3 — IB paper) |

## Sprint 3 — Operational Excellence

| Story | Module | Title | Test File | Status |
|-------|--------|-------|-----------|--------|
| [EPIC-API-001](EPIC-API-001.md) | `api/server.py` | execute-swap returns 401 + /healthz | *(TBD — needs /healthz endpoint)* | 🔲 TODO |

## Prerequisite Tasks (Sprint 0)

Before certain stories can be implemented:

1. **EPIC-REGIME-001 prereq:** Check if `orchestrator._scan_symbol()` instantiates IB in `__init__`. If so, extract BBG15 gate logic to a standalone injectable function.
2. **EPIC-PORTFOLIO-001 prereq:** Verify `MAX_ADDS_PER_SCAN` is read at call time (not captured at import). If import-time, refactor to pass as parameter or read at call time.
3. **EPIC-API-001 prereq:** Add `GET /healthz` endpoint to `api/server.py` (5 lines).
4. **EPIC-SCAN-001 prereq:** Verify `_classify_quality()` and `_check_volume_expansion()` are extractable methods (not inlined in `detect()`).

## Sprint 2b — Backtest Integrity

| Story | Module | Title | Test File | Status |
|-------|--------|-------|-----------|--------|
| [EPIC-ABLATION-001](EPIC-ABLATION-001.md) | `backtest_regime_compare.py` | _pooled_cap() regression + --pooled-cap CLI wire-through | `test_backtest_pooled_cap.py` | ✅ DONE |

## Sprint 3 Backlog (Stories TBD)

| Module | Epic | Sprint |
|--------|------|--------|
| `notifier.py` | EPIC-NOTIFY | 3 |
| `mock_trader.py` | EPIC-BACKTEST | 3 |
| `enhanced_backtest.py` | EPIC-BACKTEST | 3 |
| `premarket_monitor.py` | EPIC-PREMARKET | 3 |
| `cron_agent.py` | EPIC-CRON | 3 |

## Definition of Done (Module Validated)

A module is **validated** when:
1. ≥ 80% line coverage on core logic paths
2. All IB, S3, and external API calls are mocked — tests run offline
3. At minimum BULL, BEAR, and NEUTRAL regime states exercised (where applicable)
4. Edge cases: empty DataFrame, single-row data, NaN-heavy series handled without exception
5. Tests in CI — block merges on failure
6. Product Owner confirmed "happy path" AC matches actual trading intent
