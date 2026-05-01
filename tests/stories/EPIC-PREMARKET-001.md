# EPIC-PREMARKET-001 — early_premarket_scan.py: output schema + API fallback chain
# Sprint: 2 (Signal) | Tier: 2 (shallow mocks, no network)

## Why this story matters
early_premarket_scan.py writes early_premarket_watch.txt which is consumed by
premarket_monitor.py and signal_surge_monitor.py at 8 AM. If the file is malformed
(wrong encoding, symbols with spaces, empty lines) the downstream readers silently
skip symbols. The 3-tier API fallback chain (Alpha Vantage → Alpaca → Finnhub) must
degrade gracefully — a broken AV key must not crash the 4 AM job.

## Module
`early_premarket_scan.py`

## Acceptance Criteria

| # | Given | When | Then |
|---|-------|------|------|
| AC1 | write_watch_file(['AAPL', 'TSLA', 'NVDA'], dry_run=False) | function called | file written; one symbol per line; no blank lines; no leading/trailing whitespace |
| AC2 | write_watch_file([], dry_run=False) | function called | file written with 0 symbols (empty or commented header only); no crash |
| AC3 | write_watch_file(['AAPL'], dry_run=True) | function called | file NOT written (dry_run guard) |
| AC4 | filter_movers(movers_list, threshold=3.0) | mover A: gap=2%, no catalyst; mover B: gap=2%, articles=1; mover C: gap=7% (≥STRONG_GAP=6%) | returns B (catalyst) and C (strong gap) but NOT A |
| AC5 | discover_news_symbols() called with mocked AV returning HTTP 403 | function called | returns [] gracefully, no exception propagates |
| AC6 | build_scan_universe(['AAPL']) | function called | result is a list of strings, 'AAPL' is included, no duplicates |

## Definition of Validated
All 6 ACs pass as Tier 2 pytest tests using pytest-mock to stub yfinance and HTTP
calls — no real network access.

## Test file
`tests/test_early_premarket_scan.py`

## Notes
- `write_watch_file(symbols, dry_run)` — writes EARLY_WATCH_FILE (use tmp_path fixture
  to redirect the write path)
- `filter_movers(movers, threshold)` — filters by abs(gap_pct) >= threshold
- `build_scan_universe(er_candidates)` — merges ER candidates, priority symbols, news;
  deduplicates and caps at MAX_SCAN_SYMBOLS=300
- `discover_news_symbols()` imports news_scanner internally — mock that import
- AC5: patch `news_scanner.discover_news_movers` to raise requests.HTTPError(403)
