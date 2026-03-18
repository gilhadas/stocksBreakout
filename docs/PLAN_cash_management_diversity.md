# Cash Management, Risk Ranking & Portfolio Diversity

## Context
When the portfolio runs out of cash, new promising signals are silently skipped (`skipped_cash` array in auto_portfolio.py). The user has no proactive warning to re-evaluate holdings. Positions that sit flat or negative for days tie up capital that could fund better setups. Additionally, the portfolio can become unbalanced — overweight in one sector, one mode, or all stocks/no ETFs — increasing correlated risk. This plan adds:
1. **Cash monitoring** with alerts when running low
2. **Stale position detection** (flat/negative for N days)
3. **Risk-based ranking** so riskiest positions are first to trim
4. **Diversity scoring** across 4 dimensions: sector, mode, asset type, risk balance

### Critical Constraint: Preserve V9-H Performance
This system is **advisory only** — it notifies and suggests, but does NOT auto-close positions or block entries. V9-H's edge (3yr +108%, bear protection via regime gate) must remain untouched. The health check runs AFTER V9-H decisions, never overrides them. Entry filtering (GOLD-only in bear_macro, PREMIUM+ in BEARISH) stays in orchestrator.py unchanged.

### 70/30 Portfolio Balance Rule
Safer positions (risk score < 40) should represent ~70% of total portfolio value. Riskier positions (risk score >= 40) are capped at ~30% of portfolio value. When this ratio is violated, the diversity alert suggests which risky positions to downsize. This is enforced at the **sizing recommendation** level — on new entries, if adding a position would push risky allocation above 30%, the health check suggests a smaller position size.

## Files to Create
- **`position_health.py`** (~280 lines) — standalone module for portfolio health + diversity analysis

## Files to Modify
- [config.py](config.py) — add `CASH_MANAGEMENT` dict (after `QUALITY_SIZING` at line 80)
- [auto_portfolio.py](auto_portfolio.py) — hook into `scan_and_add()` when signals are skipped due to low cash (line ~204); add sector enrichment on entry
- [scan_feedback_agent.py](scan_feedback_agent.py) — add periodic health check pass in notification loop
- [pages/portfolio_page.py](pages/portfolio_page.py) — add health + diversity dashboard in `_render_auto_portfolio()`

---

## Step 1: Config — `CASH_MANAGEMENT` in config.py

Add after `QUALITY_SIZING` (line 80):

```python
CASH_MANAGEMENT = {
    'low_cash_pct': 0.15,           # Alert when cash < 15% of total value
    'critical_cash_pct': 0.05,      # Urgent alert when cash < 5%
    'stale_days': 5,                # Days flat/negative = "stale"
    'stale_threshold_pct': 0.0,     # Gain % below this = stale
    'notify_cooldown_hours': 12,    # Don't re-alert same condition
    'quality_risk_penalty': {       # Lower quality = higher risk score
        'GOLD': 0.0, 'PREMIUM': 0.3, 'HIGH': 0.6, 'STANDARD': 1.0,
    },
    # ── Diversity Limits ──
    'max_per_sector': 3,            # Max positions in same sector
    'max_per_mode': 5,              # Max positions in same mode (swing/daytrade/longterm)
    'ideal_etf_pct': 0.20,         # Target 20% of positions in ETFs for stability
    'max_single_position_pct': 0.20,# Largest position should not exceed 20% of portfolio value
    # ── 70/30 Risk Balance ──
    'safe_allocation_pct': 0.70,    # 70% of portfolio value in safe positions (risk < 40)
    'risky_threshold': 40,          # Risk score >= this = "risky" position
}
```

## Step 2: `position_health.py` — Core Module

### Risk Score Formula (0–100, higher = riskier = close first)

Each position scored on 5 weighted factors (all normalized 0–1):

| Factor | Weight | Logic |
|--------|--------|-------|
| **Days stale** | 30% | `days_held` with no gain / `(2 * 30)`. 0 if gaining. |
| **Stop proximity** | 25% | `1 - dist_to_stop / (dist_to_stop + dist_to_target)`. Near stop = high. |
| **Unrealized P&L** | 25% | Losers score higher: `clamp(-pnl_pct / 0.10, 0, 1)`. Winners = 0. |
| **Quality penalty** | 15% | GOLD=0, PREMIUM=0.3, HIGH=0.6, STANDARD=1.0 |
| **R:R consumed** | 5% | `1 - (current - stop) / (target - stop)`, clamped. Far from target = high. |

**`risk_score = 100 * sum(weight_i * factor_i)`**

### Diversity Analysis (4 dimensions)

The health check also computes a **diversity report** to ensure balanced risk:

#### 1. Sector Concentration
- Use `sentiment.get_sector_for_ticker()` to resolve each position's sector
- ETF sectors come from `config.SENTIMENT['sector_etfs']` mapping
- Alert if any sector has > `max_per_sector` (3) positions
- When trimming for cash, prefer closing from the most crowded sector

#### 2. Mode Balance (longterm / swing / daytrade)
- Each position already stores `mode` field
- Alert if any mode has > `max_per_mode` (5) positions
- Ideal: spread across modes for different time horizons

#### 3. ETF vs Stock Balance
- Detect ETFs: check symbol against all ETFs in `config.SENTIMENT['sector_etfs']` values + known ETF list (QQQ, SPY, IWM, DIA, IBIT, etc.)
- Target: ~20% ETF allocation (`ideal_etf_pct`) — ETFs are lower risk anchors
- Alert if ETF % drops below 10% or no ETFs held

#### 4. Risk-Balanced Sizing — 70/30 Rule (major positions = less risky)
- **Core rule**: ~70% of portfolio value must be in safe positions (risk score < 40), ~30% max in risky positions (risk score >= 40)
- Compute current split: `safe_value = sum(pos.value for pos if risk < 40)` vs `risky_value`
- Compute `size_risk_mismatch`: for each position, compare its value rank vs its risk rank
  - If a HIGH-risk position is also the LARGEST by value → flag as **oversized risk**
  - `mismatch_score = value_rank - risk_rank` (positive = oversized risky position)
- Alert when risky allocation exceeds 30%: "Risky positions are 42% of portfolio ($42K) — target is 30%. Consider trimming: AAPL (risk 78, $15K), MSFT (risk 65, $12K)"
- On new entries: if adding would push risky % above 30%, suggest reduced position size in the alert
- When trimming: prioritize positions that are both high-risk AND oversized

#### Diversity in the Risk Score

Sector concentration feeds into the risk score as an additional factor:

| Factor | Weight | Logic |
|--------|--------|-------|
| **Days stale** | 25% | `days_held` with no gain / `(2 * 30)`. 0 if gaining. |
| **Stop proximity** | 20% | `1 - dist_to_stop / (dist_to_stop + dist_to_target)`. Near stop = high. |
| **Unrealized P&L** | 20% | Losers score higher: `clamp(-pnl_pct / 0.10, 0, 1)`. Winners = 0. |
| **Quality penalty** | 10% | GOLD=0, PREMIUM=0.3, HIGH=0.6, STANDARD=1.0 |
| **R:R consumed** | 5% | `1 - (current - stop) / (target - stop)`, clamped. |
| **Sector crowding** | 10% | `min(1, sector_count / 5)` — more same-sector positions = higher risk |
| **Mode crowding** | 5% | `min(1, mode_count / max_per_mode)` — all eggs in one mode = risk |
| **Size mismatch** | 5% | Oversized risky positions penalized |

### Key Functions

```python
def evaluate_portfolio_health(data: dict) -> dict:
    """Returns cash_status, stale_positions, risk_ranking (sorted desc), diversity, alerts."""

def compute_risk_score(position: dict, sector_counts: dict, mode_counts: dict, value_rank: int) -> dict:
    """Returns position with risk_score + factor breakdown."""

def analyze_diversity(positions: list) -> dict:
    """Returns sector_breakdown, mode_breakdown, etf_pct, size_risk_mismatches, alerts."""

def format_cash_alert(health: dict) -> str:
    """Telegram/Discord message: cash %, diversity issues, top 3 risky positions to trim."""

def run_health_check(data: dict = None) -> dict:
    """CLI entry: load portfolio → evaluate → notify if needed."""

def _is_etf(symbol: str) -> bool:
    """Check if symbol is an ETF (from config sector_etfs + known list)."""
```

### Alert Types

1. **LOW CASH** (cash < 15% of total value):
   - Lists available cash, open position count, skipped signals today
   - Shows top 3 risk-ranked positions as trim candidates (riskiest first)
   - Includes diversity note if trimming from crowded sector helps both cash AND diversity
   - Discord: orange embed. Telegram: warning message.

2. **STALE POSITIONS** (5+ days flat/negative):
   - Lists stale positions with days held, P&L%, distance to stop
   - Discord: yellow embed.

3. **CRITICAL CASH** (cash < 5%):
   - Same as LOW CASH but red/urgent formatting

4. **DIVERSITY IMBALANCE** (new):
   - Fires when: sector > 3 positions, mode > 5 positions, ETF% < 10%, or largest position is also riskiest
   - Message shows the imbalance and suggests which position to trim (highest risk in crowded group)
   - Discord: blue embed.

### Dedup / Cooldown
- Use existing `notifier.py` cache pattern (`scanner_output/.notification_cache.json`)
- Cache key: `health:{alert_type}:{date}` — max 1 alert per type per cooldown period

## Step 3: Hook into `auto_portfolio.py`

Two changes:

**A. Enrich positions with sector on entry** (line ~223, where position dict is built):
```python
from sentiment import get_sector_for_ticker
pos['sector'] = get_sector_for_ticker(sym)  # persist sector in position dict
```
This avoids repeated yfinance lookups in the health check.

**B. Trigger health check on cash skip** — after `_save(data)`, before return (line ~239):
```python
if skipped_cash:
    from position_health import run_health_check
    run_health_check(data)  # triggers notification if thresholds met
```

This is the most actionable moment — user just missed a trade due to low cash.

## Step 4: Integration in `scan_feedback_agent.py`

Add a health check call in `_run_one_pass()` after processing scan decisions. Run at most once per `notify_cooldown_hours`. This catches stale positions even when no new signals are skipped.

## Step 5: Streamlit Dashboard in `portfolio_page.py`

In `_render_auto_portfolio()`, add a "Portfolio Health" expander:
- **Cash bar**: green/orange/red based on cash %
- **Positions table**: add `Risk Score` column, sorted by risk desc; stale rows highlighted yellow
- **Diversity panel**: 4 mini-charts/metrics:
  - Sector pie chart (flag if any sector > 3 positions)
  - Mode breakdown bar (longterm / swing / daytrade)
  - ETF vs Stock ratio
  - Size vs Risk scatter (flag mismatches — big positions that are also high risk)
- **Suggested Actions**: top 3 trim candidates with reasoning ("stale 8d + crowded Tech sector")

## Step 6: Cron Entry

Add to `cron_jobs.txt`:
```
# Portfolio health check (after main scans)
0 10,15 * * 1-5  python position_health.py
```

---

## Verification Plan
1. **V9-H preserved**: Confirm health check does NOT modify orchestrator.py entry/exit logic. Run `enhanced_backtest.py` V9-H config before and after → same results (system is advisory only)
2. **Risk ranking**: Create test portfolio with mix of winning/losing/stale positions → verify riskiest (losing + stale + crowded sector) rank first
3. **70/30 rule**: Create portfolio with 50% risky positions → verify alert fires with specific trim suggestions to get back to 30%
4. **Diversity alerts**: Create portfolio with 4 Tech stocks, 0 ETFs → verify sector + ETF alerts fire
5. **Cash alert**: Set `low_cash_pct=0.99` temporarily → confirm Telegram/Discord alert fires with trim suggestions
6. **Dedup**: Run twice within cooldown → confirm no duplicate alert
7. **Streamlit**: Open portfolio page → verify health section with risk column, sector pie, mode bar, 70/30 gauge
8. **End-to-end**: Run `scan_and_add()` with portfolio near max positions → confirm alert on skip includes diversity context

## Key Reuse Points
- `auto_portfolio.get_summary()` at line 700 — already computes cash, market_value, total_value
- `auto_portfolio.available_cash()` — cash calculation
- `sentiment.get_sector_for_ticker()` at line 74 — sector lookup (config map + yfinance fallback)
- `config.SENTIMENT['sector_etfs']` at line 336 — ETF symbol list per sector
- `notifier.py` `send_all()` — multi-channel notification
- `scan_feedback_agent.py` Discord embed pattern — color-coded embeds
- Existing `_sent_cache` / `.notification_cache.json` — dedup infrastructure
