"""
Scalp Virtual Portfolio
=======================
Automatically tracks scalping breakout alerts from the feedback agent.

Rules
-----
- Position size: 10% of initial capital ($10K per trade by default)
- Buy: when scan_feedback_agent fires BREAKOUT event
- Sell: when scan_feedback_agent fires EXIT event (TRAIL_STOP or FAILED)
- Auto-sell EOD: close all remaining positions at 4:00 PM ET (scalpers don't hold overnight)
- Stops: fixed-cent based (2¢ fail, 3¢ trail) matching SCALP_TRAIL_CENTS / SCALP_FAIL_CENTS
- Dedup: skip if symbol already in open positions
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

_NY_TZ = ZoneInfo('America/New_York')
_PORTFOLIO_PATH = 'scanner_output/portfolio/scalp_portfolio.json'

INITIAL_CAPITAL = 10_000
POSITION_SIZE_PCT = 0.10  # 10% of capital per trade

logger = logging.getLogger(__name__)


# ── Persistence ──────────────────────────────────────────────────────────────

def _empty() -> dict:
    return {
        'capital':      INITIAL_CAPITAL,
        'positions':    [],
        'closed':       [],
        'last_updated': None,
    }


def load() -> dict:
    from utils import load_json
    data = load_json(_PORTFOLIO_PATH)
    if data is not None:
        return data
    return _empty()


def _save(data: dict):
    from utils import save_json
    data['last_updated'] = datetime.now(_NY_TZ).isoformat()
    save_json(data, _PORTFOLIO_PATH)


# ── Helpers ──────────────────────────────────────────────────────────────────

def available_cash(data: dict) -> float:
    invested = sum(p['cost'] for p in data['positions'])
    return data['capital'] - invested


def open_symbols(data: dict) -> set:
    return {p['symbol'] for p in data['positions']}


def get_summary(data: dict) -> dict:
    positions = data.get('positions', [])
    closed = data.get('closed', [])
    cap = data['capital']
    invested = sum(p['cost'] for p in positions)
    cash = cap - invested
    market_value = sum(p.get('current_price', p['entry_price']) * p['shares']
                       for p in positions)
    unrealized = round(market_value - invested, 2)
    realized = round(sum(t.get('pnl', 0) for t in closed), 2)
    return {
        'capital':       cap,
        'cash':          round(cash, 2),
        'market_value':  round(market_value, 2),
        'unrealized':    unrealized,
        'realized':      realized,
        'open_count':    len(positions),
        'closed_count':  len(closed),
    }


# ── Buy (on BREAKOUT alert) ─────────────────────────────────────────────────

def add_position(symbol: str, entry_price: float, stop: float, target: float,
                 quality: str = '', vol_ratio: float = 0.0,
                 position_pct: float = None) -> dict:
    """
    Add a scalping position. Called when feedback agent fires BREAKOUT.
    Returns {'added': bool, 'reason': str, 'data': data}.
    """
    pos_pct = position_pct or POSITION_SIZE_PCT
    data = load()
    syms = open_symbols(data)

    if symbol in syms:
        return {'added': False, 'reason': 'duplicate', 'data': data}

    position_value = data['capital'] * pos_pct
    shares = max(1, int(position_value / entry_price))
    cost = round(shares * entry_price, 2)
    cash = available_cash(data)

    if cost > cash:
        return {'added': False, 'reason': 'no_cash', 'data': data}

    now_str = datetime.now(_NY_TZ).strftime('%Y-%m-%d %H:%M')
    data['positions'].append({
        'symbol':       symbol,
        'date_added':   now_str,
        'mode':         'scalping',
        'quality':      quality,
        'entry_price':  round(entry_price, 4),
        'stop':         round(stop, 4),
        'target':       round(target, 4),
        'shares':       shares,
        'cost':         cost,
        'current_price': entry_price,
        'vol_ratio':    round(vol_ratio, 1),
    })
    _save(data)
    logger.info(f"Scalp portfolio: BUY {symbol} {shares} @ ${entry_price:.2f} "
                f"(stop=${stop:.2f}, target=${target:.2f})")
    return {'added': True, 'reason': 'ok', 'data': data}


# ── Sell (on EXIT alert or manual) ───────────────────────────────────────────

def close_position(symbol: str, exit_price: float,
                   reason: str = 'manual') -> dict:
    """
    Close a scalping position. Called when feedback agent fires EXIT.
    Returns {'closed': bool, 'pnl': float, 'data': data}.
    """
    data = load()
    pos = None
    remaining = []

    for p in data['positions']:
        if p['symbol'] == symbol and pos is None:
            pos = p
        else:
            remaining.append(p)

    if pos is None:
        return {'closed': False, 'pnl': 0, 'data': data}

    pnl = round((exit_price - pos['entry_price']) * pos['shares'], 2)
    pnl_pct = round((exit_price - pos['entry_price']) / pos['entry_price'] * 100, 2)
    now_str = datetime.now(_NY_TZ).strftime('%Y-%m-%d %H:%M')

    data['closed'].append({
        **pos,
        'date_closed':  now_str,
        'exit_price':   round(exit_price, 4),
        'pnl':          pnl,
        'pnl_pct':      pnl_pct,
        'close_reason': reason,
    })
    data['positions'] = remaining
    data['capital'] += pnl  # realized P&L flows back into capital
    _save(data)
    logger.info(f"Scalp portfolio: SELL {symbol} @ ${exit_price:.2f} "
                f"pnl=${pnl:+.2f} ({pnl_pct:+.1f}%) reason={reason}")
    return {'closed': True, 'pnl': pnl, 'data': data}


# ── Close all (EOD) ─────────────────────────────────────────────────────────

def close_all_eod() -> dict:
    """Close all open scalping positions at current price (end of day)."""
    import yfinance as yf

    data = load()
    if not data['positions']:
        return {'closed': [], 'data': data}

    # Fetch current prices
    symbols = [p['symbol'] for p in data['positions']]
    prices = {}
    for sym in symbols:
        try:
            hist = yf.Ticker(sym.replace(' ', '-')).history(period='1d')
            if hist is not None and not hist.empty:
                prices[sym] = float(hist['Close'].dropna().iloc[-1])
        except Exception:
            pass

    closed_syms = []
    now_str = datetime.now(_NY_TZ).strftime('%Y-%m-%d %H:%M')

    total_pnl = 0
    for p in data['positions']:
        sym = p['symbol']
        exit_px = prices.get(sym, p.get('current_price', p['entry_price']))
        pnl = round((exit_px - p['entry_price']) * p['shares'], 2)
        pnl_pct = round((exit_px - p['entry_price']) / p['entry_price'] * 100, 2)
        data['closed'].append({
            **p,
            'date_closed':  now_str,
            'exit_price':   round(exit_px, 4),
            'pnl':          pnl,
            'pnl_pct':      pnl_pct,
            'close_reason': 'eod',
        })
        total_pnl += pnl
        closed_syms.append(sym)

    data['positions'] = []
    data['capital'] += total_pnl  # realized P&L flows back into capital
    _save(data)
    return {'closed': closed_syms, 'data': data}


# ── Refresh prices ───────────────────────────────────────────────────────────

def refresh_prices() -> dict:
    """Fetch current prices for open positions. No auto-close (agent handles exits)."""
    import yfinance as yf

    data = load()
    if not data['positions']:
        return {'updated': 0, 'data': data}

    symbols = [p['symbol'] for p in data['positions']]
    for sym in symbols:
        try:
            hist = yf.Ticker(sym.replace(' ', '-')).history(period='1d')
            if hist is not None and not hist.empty:
                price = float(hist['Close'].dropna().iloc[-1])
                for p in data['positions']:
                    if p['symbol'] == sym:
                        p['current_price'] = round(price, 4)
                        break
        except Exception:
            pass

    _save(data)
    return {'updated': len(symbols), 'data': data}


# ── Reset ────────────────────────────────────────────────────────────────────

def reset():
    _save(_empty())
