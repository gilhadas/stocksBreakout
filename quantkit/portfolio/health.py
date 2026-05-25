"""
quantkit.portfolio.health
=========================
Portfolio health & diversity assessment — advisory only, never auto-closes.

Monitors:
    • Cash level (LOW / CRITICAL thresholds)
    • Stale positions (flat/negative for N days)
    • Risk-based ranking (riskiest = first trim candidates)
    • Diversity: sector, mode, ETF/stock mix, 70/30 risk balance

Pass your own config dict or use DEFAULT_HEALTH_CONFIG as a template.

Quick start
-----------
    from quantkit.portfolio.health import assess_portfolio_health, DEFAULT_HEALTH_CONFIG

    positions = [
        {'symbol': 'AAPL', 'sector': 'Tech',  'mode': 'swing',
         'pnl_pct': 5.2, 'days_held': 10, 'risk_per_share': 2.1,
         'shares': 10, 'entry_price': 175.0},
        ...
    ]
    report = assess_portfolio_health(positions, cash=5000, total_equity=50000,
                                     config=DEFAULT_HEALTH_CONFIG)
    print(report['alerts'])
    print(report['diversity_score'])

Position dict required keys
---------------------------
    symbol         : str
    sector         : str  (e.g. 'Tech', 'Healthcare')
    mode           : str  (e.g. 'swing', 'daytrade')
    pnl_pct        : float  (unrealized P&L %)
    days_held      : int
    risk_per_share : float  (entry − stop)
    shares         : int / float
    entry_price    : float

Config keys
-----------
    cash_low_pct       : float  (alert when cash < this % of equity)
    cash_critical_pct  : float
    stale_days         : int    (flag positions held > N days with pnl < 0)
    max_single_pct     : float  (max % of equity in one position)
    ideal_etf_pct      : float  (target ETF allocation, e.g. 0.30)
    known_etfs         : set    (extra ETF symbols beyond sector ETFs)
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ── Default config ────────────────────────────────────────────────────────────

DEFAULT_HEALTH_CONFIG: Dict[str, Any] = {
    'cash_low_pct':      0.10,   # < 10% cash → LOW alert
    'cash_critical_pct': 0.05,   # < 5%  cash → CRITICAL alert
    'stale_days':        20,     # held > 20 days with pnl < 0 → stale
    'max_single_pct':    0.10,   # single position > 10% of equity
    'ideal_etf_pct':     0.30,   # target 30% in ETFs
    'known_etfs': {
        'SPY', 'QQQ', 'IWM', 'DIA', 'IBIT', 'ETHA', 'GLD', 'TLT',
        'XLK', 'XLE', 'XLF', 'XLV', 'XLY', 'XLI', 'XLRE', 'XLB',
        'IBB', 'XME', 'ARKK', 'VTI', 'VOO', 'IVV', 'RSP', 'SOXX',
    },
}


# ── ETF detection ─────────────────────────────────────────────────────────────

def is_etf(symbol: str, known_etfs: Optional[Set[str]] = None) -> bool:
    """Return True if *symbol* is in the known ETF set."""
    etfs = known_etfs or DEFAULT_HEALTH_CONFIG['known_etfs']
    return symbol.upper() in etfs


# ── Core assessment ───────────────────────────────────────────────────────────

def assess_portfolio_health(
    positions: List[Dict[str, Any]],
    cash: float,
    total_equity: float,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Analyse portfolio health and return advisory report.

    Parameters
    ----------
    positions    : List of position dicts (see module docstring for keys).
    cash         : Current cash balance.
    total_equity : Total portfolio value (cash + positions).
    config       : Override dict (merged over DEFAULT_HEALTH_CONFIG).

    Returns
    -------
    Dict with keys:
        alerts        : List[str]   — human-readable warning messages
        stale         : List[str]   — symbols flagged as stale
        trim_first    : List[str]   — highest-risk symbols to trim first
        concentration : List[str]   — symbols exceeding max single-position %
        diversity_score : float (0–1)
        sector_mix    : Dict[str, int]
        mode_mix      : Dict[str, int]
        cash_pct      : float
        etf_pct       : float
    """
    cfg = {**DEFAULT_HEALTH_CONFIG, **(config or {})}

    alerts:        List[str] = []
    stale:         List[str] = []
    concentration: List[str] = []

    # ── Cash check ────────────────────────────────────────────────────────────
    cash_pct = cash / max(total_equity, 1.0)
    if cash_pct < cfg['cash_critical_pct']:
        alerts.append(f"🔴 CRITICAL: cash is {cash_pct:.1%} of equity (< {cfg['cash_critical_pct']:.0%})")
    elif cash_pct < cfg['cash_low_pct']:
        alerts.append(f"🟡 LOW cash: {cash_pct:.1%} of equity (< {cfg['cash_low_pct']:.0%})")

    # ── Per-position checks ────────────────────────────────────────────────────
    risk_scores: List[tuple] = []

    for pos in positions:
        sym        = pos.get('symbol', '?')
        pnl_pct    = float(pos.get('pnl_pct', 0.0))
        days_held  = int(pos.get('days_held', 0))
        risk_ps    = float(pos.get('risk_per_share', 0.0))
        shares     = float(pos.get('shares', 0.0))
        entry      = float(pos.get('entry_price', 0.0))

        position_value = shares * entry
        pos_pct        = position_value / max(total_equity, 1.0)
        risk_dollars   = risk_ps * shares

        # Stale check
        if days_held > cfg['stale_days'] and pnl_pct < 0:
            stale.append(sym)
            alerts.append(f"⚠️  Stale: {sym} held {days_held}d, P&L {pnl_pct:+.1f}%")

        # Concentration check
        if pos_pct > cfg['max_single_pct']:
            concentration.append(sym)
            alerts.append(f"📊 Concentrated: {sym} is {pos_pct:.1%} of equity")

        risk_scores.append((sym, risk_dollars))

    # ── Risk ranking ──────────────────────────────────────────────────────────
    risk_scores.sort(key=lambda x: x[1], reverse=True)
    trim_first = [s for s, _ in risk_scores[:3]]

    # ── Diversity ─────────────────────────────────────────────────────────────
    etf_set     = cfg.get('known_etfs', set())
    sector_mix  = Counter(p.get('sector', 'Unknown') for p in positions)
    mode_mix    = Counter(p.get('mode', 'unknown') for p in positions)
    n_etf       = sum(1 for p in positions if is_etf(p.get('symbol', ''), etf_set))
    n_total     = len(positions)
    etf_pct     = n_etf / max(n_total, 1)

    # Diversity score components (all 0–1, averaged)
    sector_count   = len(sector_mix)
    sector_score   = min(sector_count / 5, 1.0)          # ≥5 sectors = perfect
    mode_count     = len(mode_mix)
    mode_score     = min(mode_count / 3, 1.0)            # ≥3 modes = perfect
    etf_ideal      = cfg['ideal_etf_pct']
    etf_score      = max(0.0, 1.0 - abs(etf_pct - etf_ideal) / etf_ideal)
    diversity_score = round((sector_score + mode_score + etf_score) / 3, 3)

    if diversity_score < 0.5:
        alerts.append(f"🔵 Low diversity score: {diversity_score:.2f} — consider spreading across sectors/modes")

    return {
        'alerts':           alerts,
        'stale':            stale,
        'trim_first':       trim_first,
        'concentration':    concentration,
        'diversity_score':  diversity_score,
        'sector_mix':       dict(sector_mix),
        'mode_mix':         dict(mode_mix),
        'cash_pct':         round(cash_pct, 4),
        'etf_pct':          round(etf_pct, 4),
    }
