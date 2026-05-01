# EPIC-ALGO-001 — algo_trader.py: AlgoTrader order lifecycle
# Sprint: 1 (Guard Money) | Tier: 3 (IB paper — excluded from CI)

## Why this story matters
algo_trader.py talks directly to Interactive Brokers. An orderId collision or a dangling
`active_orders` entry could result in duplicate fills or missed cancellations. These
scenarios require a live IB paper account to test properly — hence Tier 3.

## Module
`algo_trader.py`

## Acceptance Criteria

| # | Scenario | Expected |
|---|----------|----------|
| AC1 | Place bracket order for a symbol | `active_orders[symbol]` populated with orderId |
| AC2 | Cancel open order | `active_orders[symbol]` removed or status updated to CANCELLED |
| AC3 | Attempt to place duplicate order for same symbol while one is active | Second placement rejected or old order cancelled first — no collision |
| AC4 | IB disconnects mid-order | AlgoTrader logs error, does not crash, `active_orders` reflects stale state |

## Definition of Validated
All ACs pass against IB paper account (port 7497). Run locally only.

## Test file
`tests/test_algo_trader_paper.py` (mark all with `@pytest.mark.tier3`)

## Notes
- Excluded from CI (`pytest -m "not tier3"`)
- Run locally: `make test-integration`
- Requires IB Gateway running on localhost:7497 with paper account
- Scope is intentionally narrow — focus on `active_orders` dict lifecycle, not P&L
