"""
Automated swaps must be priced on the CLOSE basis, like every other exit.

WHY THIS EXISTS
---------------
The champion exit is CLOSE-based, and that is not a stylistic preference — it is
the single largest measured effect in this system. The 2026-07-02 restore
isolation put low/intraday triggering at 2022 −24.8% against −10.75% close-based,
and CLAUDE.md §12 Task 1 shipped `_close_basis_history` specifically so the 10:00
cron could no longer exit a position on an intraday dip that the day closed back
above.

`execute_swap` predates that work and prices both legs with `_fetch_live_price`,
which returns the last row of a 2-day history — before 16:00 ET that row is
today's PARTIAL bar, i.e. an intraday quote.

For a human clicking "Swap" that is correct: they intend a live fill. For the
automated auto-swap book it is a confound. The A/B is supposed to isolate ONE
variable — whether swapping happens — and intraday-priced exits would make the
treatment arm differ in *how exits are priced* as well, reintroducing exactly the
noise the champion validation rejected. Any difference measured would then be
partly an artefact of pricing, and unattributable.
"""
from __future__ import annotations

import pandas as pd
import pytest

import auto_portfolio as ap


def test_automated_path_requests_the_close_basis(monkeypatch):
    seen = []
    monkeypatch.setattr(ap, 'execute_swap',
                        lambda c, o, **k: seen.append(k.get('price_basis')) or
                        {'ok': True, 'closed': {'exit_price': 1.0}, 'opened': {'entry_price': 2.0}})
    monkeypatch.setattr(ap, '_append_swap_ledger', lambda rec: None)

    ap._execute_swap_batch([{'close_symbol': 'A', 'open_symbol': 'B'}],
                           user_id='U1', book='autoswap', budget=5,
                           logger=_NullLogger())
    assert seen == ['close'], "the automated arm must not price legs intraday"


def test_human_path_still_defaults_to_live():
    """A person clicking Swap intends a live fill — that behaviour is preserved."""
    import inspect
    sig = inspect.signature(ap.execute_swap)
    assert sig.parameters['price_basis'].default == 'live'


def test_close_basis_price_uses_the_last_completed_bar(monkeypatch):
    """Mid-session, today's partial bar must be ignored."""
    idx = pd.to_datetime(['2026-08-06', '2026-08-07', '2026-08-10'])
    hist = pd.DataFrame({'Close': [100.0, 110.0, 95.0]}, index=idx)   # 95 = today, partial

    trimmed = pd.DataFrame({'Close': [100.0, 110.0]}, index=idx[:2])
    monkeypatch.setattr(ap, '_close_basis_history', lambda h, now: trimmed)

    class _T:
        def __init__(self, sym): pass
        def history(self, period=None): return hist

    monkeypatch.setattr('yfinance.Ticker', _T)
    assert ap._fetch_close_basis_price('AAA') == 110.0


def test_close_basis_price_is_none_on_empty_history(monkeypatch):
    class _T:
        def __init__(self, sym): pass
        def history(self, period=None): return pd.DataFrame()

    monkeypatch.setattr('yfinance.Ticker', _T)
    assert ap._fetch_close_basis_price('AAA') is None


def test_swap_price_dispatches_on_basis(monkeypatch):
    monkeypatch.setattr(ap, '_fetch_live_price', lambda s: 1.0)
    monkeypatch.setattr(ap, '_fetch_close_basis_price', lambda s: 2.0)
    assert ap._swap_price('X', 'live') == 1.0
    assert ap._swap_price('X', 'close') == 2.0
    # Anything unrecognised falls back to live rather than returning None —
    # a None price aborts the swap with a confusing "price unavailable".
    assert ap._swap_price('X', 'whatever') == 1.0


def test_both_legs_use_the_same_basis(monkeypatch):
    """A swap priced close-on-exit but live-on-entry would book a fictional spread."""
    asked = []
    monkeypatch.setattr(ap, '_swap_price', lambda s, basis: asked.append((s, basis)) or 10.0)
    monkeypatch.setattr(ap, 'load', lambda *a, **k: {
        'positions': [{'symbol': 'OLD', 'entry_price': 10.0, 'stop': 9.0,
                       'target': 12.0, 'shares': 1, 'cost': 10.0}],
        'skipped_cash': [{'symbol': 'NEW', 'stop': 9.0, 'target': 13.0,
                          'mode': 'swing', 'quality': 'PREMIUM', 'vol': 1.0}],
        'capital': 1000.0, 'closed': [],
    })
    monkeypatch.setattr(ap, 'close_position', lambda *a, **k: {'symbol': 'OLD', 'pnl': 0.0})
    monkeypatch.setattr(ap, 'add_position_direct', lambda **k: {'added': True})
    monkeypatch.setattr(ap, '_save', lambda *a, **k: None)

    ap.execute_swap('OLD', 'NEW', user_id='U1', book='autoswap', price_basis='close')
    assert [b for _, b in asked] == ['close', 'close']


class _NullLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
