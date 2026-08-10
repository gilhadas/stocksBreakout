"""
Regression tests for the /portfolio/suggest-swaps NaN crash.

Observed live in scanner_output/api_server.err:
    ValueError: Out of range float values are not JSON compliant: nan
    when serializing dict item 'open_win_prob'
    when serializing list item 0
    when serializing dict item 'swaps'

Root cause: a skipped_cash entry can carry float('nan') for win_prob (e.g. a
signal type never stamped by WinProb calibration, or a CSV with a blank cell
read through pandas). NaN is NOT valid JSON per RFC 8259, but Python's
json.dump() emits it anyway as a non-standard extension — so it round-trips
silently through the portfolio JSON until a stricter serializer (FastAPI's
response encoder) rejects it.

AC-NAN-01  A NaN win_prob comes back as None, and the result is JSON-serializable
           under strict (RFC-compliant) rules
AC-NAN-02  Transitively-computed fields are also cleaned — NaN is truthy in
           Python, so `pos.get('entry_price', 0) or 1` does NOT guard against it
           and close_pnl_pct would inherit the NaN
AC-NAN-03  Non-NaN values pass through untouched (no collateral damage)
AC-NAN-04  The notify=True path survives cleaned None values — they can't
           satisfy the numeric format specs in the notification builder
"""
import json
import math
from datetime import datetime

import pytest

import auto_portfolio


def _today():
    return datetime.now(auto_portfolio._NY_TZ).strftime('%Y-%m-%d')


def _portfolio(skipped_overrides=None, position_overrides=None):
    """A minimal portfolio that WILL produce exactly one swap suggestion.

    Position is weak (down 10%), the skipped signal is fresh, up since signal,
    has sane target geometry, and a priority_score far above the position's
    implied score so the >=20pt delta gate passes.
    """
    position = {
        'symbol': 'WEAK', 'entry_price': 100.0, 'current_price': 90.0,
        'stop': 85.0, 'target': 120.0, 'quality': 'PREMIUM',
        'date_added': _today(), 'shares': 10,
    }
    position.update(position_overrides or {})

    skipped = {
        'symbol': 'FRESH', 'date_added': _today(), 'missed_pnl_pct': 5.0,
        'entry_price': 50.0, 'target': 60.0, 'stop': 47.0,
        'priority_score': 999.0, 'quality': 'GOLD', 'win_prob': 80.0,
        'win_grade': 'A', 'rr': 3.0, 'vol': 2.0, 'type': 'BOUNCE',
        'skip_reason': 'insufficient cash',
    }
    skipped.update(skipped_overrides or {})

    return {'positions': [position], 'skipped_cash': [skipped]}


def _run(monkeypatch, portfolio, notify=False):
    # **kwargs, not a fixed signature: load() now also takes `book`, and a stub
    # that pins today's exact parameter list turns any future signature change
    # into a TypeError inside the code under test rather than a real failure.
    monkeypatch.setattr(auto_portfolio, 'load', lambda *a, **k: portfolio)
    return auto_portfolio.suggest_swaps(notify=notify)


def test_baseline_produces_one_swap(monkeypatch):
    """Guard the fixture itself — if this stops producing a swap, the NaN
    tests below would vacuously pass on an empty list."""
    swaps = _run(monkeypatch, _portfolio())
    assert len(swaps) == 1, "fixture must produce exactly one swap suggestion"
    assert swaps[0]['open_symbol'] == 'FRESH'
    assert swaps[0]['close_symbol'] == 'WEAK'


def test_nan_win_prob_becomes_none_and_is_json_serializable(monkeypatch):
    swaps = _run(monkeypatch, _portfolio({'win_prob': float('nan')}))
    assert len(swaps) == 1
    assert swaps[0]['open_win_prob'] is None, \
        "NaN win_prob must be cleaned to None, not left as NaN"

    # allow_nan=False enforces real RFC 8259 rules — this is what the API's
    # response serializer effectively does, and what crashed in production.
    json.dumps(swaps, allow_nan=False)


def test_nan_in_computed_field_is_also_cleaned(monkeypatch):
    """NaN is truthy, so `pos.get('entry_price', 0) or 1` does not guard it —
    a NaN entry_price propagates into pnl_pct and thus close_pnl_pct.

    Note the stop is deliberately close (87 vs current 90 => 3.3% buffer): the
    position has to qualify as weak via the STOP-PROXIMITY path, because the
    P&L path (`pnl_pct <= -2.0`) is itself NaN-poisoned and NaN comparisons
    are always False. This is the general shape of NaN handling here — the
    gates fail closed, so a NaN usually drops the swap entirely rather than
    corrupting it; the reachable corruption vectors are fields that pass
    through without participating in a comparison.
    """
    swaps = _run(monkeypatch, _portfolio(
        position_overrides={'entry_price': float('nan'), 'stop': 87.0}))
    assert len(swaps) == 1, \
        "position must still qualify as weak via stop proximity"
    assert swaps[0]['close_pnl_pct'] is None, \
        "a NaN-derived computed field must also be cleaned"
    json.dumps(swaps, allow_nan=False)


def test_nan_in_gate_field_drops_swap_rather_than_corrupting(monkeypatch):
    """Documents the fail-closed behaviour: a NaN in a field that participates
    in an admission gate (here priority_score, which drives the >=20pt delta
    check) makes that comparison False, so no swap is emitted at all. Safe —
    worth pinning so a future 'fix' doesn't accidentally let NaN through the
    gates instead."""
    swaps = _run(monkeypatch, _portfolio({'priority_score': float('nan')}))
    assert swaps == [], "NaN priority_score must drop the swap, not emit a corrupt one"


def test_normal_values_pass_through_untouched(monkeypatch):
    swaps = _run(monkeypatch, _portfolio())
    assert len(swaps) == 1
    s = swaps[0]
    assert s['open_win_prob'] == 80.0
    assert s['open_rr'] == 3.0
    assert s['open_quality'] == 'GOLD'
    assert isinstance(s['close_pnl_pct'], float) and not math.isnan(s['close_pnl_pct'])
    assert s['close_pnl_pct'] == pytest.approx(-10.0)


def test_notify_path_survives_cleaned_none(monkeypatch):
    """A None (from a cleaned NaN) cannot satisfy the ':+.1f'/'.0f' format specs
    in the notification builder. Without the _fmt guard, the broad try/except
    around that block would swallow the ENTIRE notification batch silently."""
    sent = {}

    class _FakeNotifier:
        def send_all(self, subject, message, **kwargs):
            sent['subject'] = subject
            sent['message'] = message

    import notifier
    monkeypatch.setattr(notifier, 'Notifier', lambda *a, **k: _FakeNotifier())

    swaps = _run(monkeypatch, _portfolio({
        'missed_pnl_pct': float('nan'),   # feeds open_momentum -> ':.1f'
    }), notify=True)

    assert len(swaps) == 1
    assert swaps[0]['open_momentum'] is None
    assert 'message' in sent, \
        "notification must still be sent when a formatted field was cleaned to None"
    assert 'N/A' in sent['message'], \
        "cleaned None should render as N/A in the notification text"
