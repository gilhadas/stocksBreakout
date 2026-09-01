"""
book_compare.py — metrics for the live control-vs-autoswap A/B.

The experiment (see fork_books.py) runs two books per user off one signal
stream, differing only in whether swap suggestions are executed. This module
turns the two book JSONs into a comparable answer.

Two readouts, in order of how fast they become trustworthy:

1. **Per-swap attribution** (`swap_attribution`) — for each executed swap, what
   the closed name did afterwards versus what the opened name did. One decision
   at a time, readable in weeks. This is the primary readout.

2. **Book-level equity metrics** (`book_metrics`) — return, Sharpe, max
   drawdown, win rate since the fork. Honest but slow: both books trade the same
   signals on the same days, so their series are highly correlated and the
   difference is a small residual. Do not read a few weeks of this as a verdict.

Everything is measured FROM THE FORK DATE. Pre-fork history is shared by
construction (the variant is a clone), so including it would dilute both arms
with identical numbers and understate whatever difference exists.
"""
import json
import math
from datetime import datetime

import auto_portfolio as ap
from auto_portfolio import BOOKS, DEFAULT_BOOK, _NY_TZ

TRADING_DAYS = 252


# ── Series helpers ───────────────────────────────────────────────────────────

def _daily_returns(curve: list[dict]) -> list[float]:
    out = []
    for prev, cur in zip(curve, curve[1:]):
        p = prev.get('total_value') or 0
        c = cur.get('total_value') or 0
        if p > 0:
            out.append(c / p - 1.0)
    return out


def sharpe(curve: list[dict]) -> float | None:
    """Annualized Sharpe (rf=0) from an equity curve. None if too few points."""
    rets = _daily_returns(curve)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return round(mean / sd * math.sqrt(TRADING_DAYS), 2)


def max_drawdown(curve: list[dict]) -> float | None:
    """Worst peak-to-trough decline, as a negative percentage."""
    if len(curve) < 2:
        return None
    peak, worst = None, 0.0
    for pt in curve:
        v = pt.get('total_value') or 0
        if v <= 0:
            continue
        peak = v if peak is None else max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1.0)
    return round(worst * 100, 2)


# ── Book-level metrics ───────────────────────────────────────────────────────

def _closed_since(data: dict, since: str | None) -> list[dict]:
    trades = data.get('closed', [])
    if not since:
        return trades
    return [t for t in trades if str(t.get('date_closed') or '')[:10] >= since]


def book_metrics(data: dict, since: str | None = None) -> dict:
    """Summarize one book. `since` defaults to the book's own fork date."""
    since = since or (data.get('fork') or {}).get('date')
    summary = ap.get_summary(data)

    curve = [p for p in data.get('equity_history', [])
             if not since or str(p.get('date', ''))[:10] >= since]
    trades = _closed_since(data, since)
    wins = [t for t in trades if (t.get('pnl') or 0) > 0]
    realized = round(sum(t.get('pnl') or 0 for t in trades), 2)

    holds = []
    for t in trades:
        try:
            a = datetime.strptime(str(t.get('date_added'))[:10], '%Y-%m-%d').date()
            b = datetime.strptime(str(t.get('date_closed'))[:10], '%Y-%m-%d').date()
            holds.append((b - a).days)
        except (ValueError, TypeError):
            continue

    ret_pct = None
    if len(curve) >= 2 and (curve[0].get('total_value') or 0) > 0:
        ret_pct = round((curve[-1]['total_value'] / curve[0]['total_value'] - 1) * 100, 2)

    swaps = [t for t in trades if t.get('close_reason') == 'swap']

    return {
        'since':            since,
        'total_value':      summary.get('total_value'),
        'cash':             summary.get('cash'),
        'open_positions':   summary.get('open_count'),
        'return_pct':       ret_pct,
        'return_pct_alltime': summary.get('return_pct'),
        'realized_since':   realized,
        'unrealized':       summary.get('unrealized'),
        'closed_since':     len(trades),
        'wins_since':       len(wins),
        'win_rate':         round(len(wins) / len(trades) * 100, 1) if trades else None,
        'avg_hold_days':    round(sum(holds) / len(holds), 1) if holds else None,
        'swap_exits':       len(swaps),
        'sharpe':           sharpe(curve),
        'max_drawdown_pct': max_drawdown(curve),
        'equity_points':    len(curve),
        'equity_curve':     curve,
    }


# ── Per-swap attribution — the primary readout ───────────────────────────────

def read_swap_ledger(user_id: str | None = None, book: str | None = None) -> list[dict]:
    """Read swap_ledger.jsonl, optionally filtered. Malformed lines are skipped."""
    from utils import _to_local_abs
    recs = []
    try:
        with open(_to_local_abs(ap._SWAP_LEDGER_PATH)) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return []
    if user_id is not None:
        recs = [r for r in recs if r.get('user_id') == user_id]
    if book is not None:
        recs = [r for r in recs if r.get('book') == book]
    return recs


def swap_attribution(user_id: str | None = None,
                     book: str = 'autoswap',
                     price_lookup=None) -> dict:
    """Score every executed swap against its counterfactual.

    For each swap: the opened name's return since the swap versus what the
    closed name returned over the same window had it been held. A swap is
    "right" when the replacement outperformed the position it displaced.

    This is the measurement that makes the experiment readable quickly. The
    equity curves need months; this needs a handful of swaps.

    Args:
        price_lookup: callable(symbol) -> price. Defaults to the close basis, so
            attribution is priced the same way the automated swaps themselves
            were (§12 Task 1). Injectable so tests stay off the network.

    Returns per-swap rows plus an aggregate. `verdict` is None while a swap is
    still too fresh to price — never guess.
    """
    price_lookup = price_lookup or ap._fetch_close_basis_price
    rows, cache = [], {}

    def _px(sym):
        if sym not in cache:
            cache[sym] = price_lookup(sym)
        return cache[sym]

    for rec in read_swap_ledger(user_id=user_id, book=book):
        close_sym, open_sym = rec.get('close_symbol'), rec.get('open_symbol')
        close_at, open_at = rec.get('close_price'), rec.get('open_price')
        now_closed, now_open = _px(close_sym), _px(open_sym)

        held_ret = ((now_closed / close_at - 1) * 100
                    if now_closed and close_at else None)
        swap_ret = ((now_open / open_at - 1) * 100
                    if now_open and open_at else None)
        edge = (round(swap_ret - held_ret, 2)
                if held_ret is not None and swap_ret is not None else None)

        rows.append({
            'ts':            rec.get('ts'),
            'close_symbol':  close_sym,
            'open_symbol':   open_sym,
            'close_price':   close_at,
            'open_price':    open_at,
            'held_return_pct': round(held_ret, 2) if held_ret is not None else None,
            'swap_return_pct': round(swap_ret, 2) if swap_ret is not None else None,
            'edge_pct':      edge,
            'verdict':       None if edge is None else ('better' if edge > 0 else 'worse'),
            'score_improvement': rec.get('score_improvement'),
        })

    scored = [r for r in rows if r['edge_pct'] is not None]
    better = [r for r in scored if r['edge_pct'] > 0]
    return {
        'book':        book,
        'swaps':       rows,
        'n_total':     len(rows),
        'n_scored':    len(scored),
        'n_better':    len(better),
        'hit_rate':    round(len(better) / len(scored) * 100, 1) if scored else None,
        'avg_edge_pct': round(sum(r['edge_pct'] for r in scored) / len(scored), 2)
                        if scored else None,
        'total_edge_pct': round(sum(r['edge_pct'] for r in scored), 2) if scored else None,
    }


# ── Top-level ────────────────────────────────────────────────────────────────

def compare_books(user_id: str | None = None, price_lookup=None) -> dict:
    """Full A/B payload for one user: per-book metrics + swap attribution."""
    books = {}
    fork_date = None
    for name in BOOKS:
        data = ap.load(user_id=user_id, book=name)
        fork_date = fork_date or (data.get('fork') or {}).get('date')
        books[name] = {'label': BOOKS[name]['label'], **book_metrics(data)}

    control = books.get(DEFAULT_BOOK, {})
    deltas = {}
    for name, m in books.items():
        if name == DEFAULT_BOOK:
            continue
        deltas[name] = {
            k: (None if m.get(k) is None or control.get(k) is None
                else round(m[k] - control[k], 2))
            for k in ('return_pct', 'sharpe', 'max_drawdown_pct',
                      'realized_since', 'total_value')
        }

    return {
        'user_id':     user_id,
        'fork_date':   fork_date,
        'generated_at': datetime.now(_NY_TZ).isoformat(),
        'books':       books,
        'vs_control':  deltas,
        'attribution': {
            name: swap_attribution(user_id=user_id, book=name, price_lookup=price_lookup)
            for name in BOOKS if BOOKS[name]['auto_swap']
        },
    }
