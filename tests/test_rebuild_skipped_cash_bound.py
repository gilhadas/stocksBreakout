"""
"Find Missed Trades" must not replay the whole archive, and must not read twice.

WHY THIS EXISTS
---------------
`rebuild_skipped_cash` was the second, unfixed instance of the §18 defect. While
`scan_and_add` got an age bound in `bc06c62`, this function kept walking the
ENTIRE signal archive — and walked it **twice**, because a first pass existed
only to collect the set of symbols that appear anywhere in the archive, which is
a subset of what the second pass already read.

At 860 files that is **1,720 S3 GETs** plus a yfinance quote per unique symbol,
triggered by a button in the Streamlit dashboard — a container with a 320 MiB
memory cap. It was strictly worse than §18 ever was, because of the network
calls layered on top.

Two invariants: bounded by age, and each file read at most once.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

import auto_portfolio as ap
import utils


@pytest.fixture(autouse=True)
def _cache_off():
    ap._SCAN_FILE_CACHE = None
    yield
    ap._SCAN_FILE_CACHE = None


def _fname(days_ago: int) -> str:
    day = (datetime.now(ap._NY_TZ).date() - timedelta(days=days_ago))
    return f"signals_swing_{day.strftime('%Y%m%d')}_103303.csv"


def _frame(symbol: str) -> pd.DataFrame:
    return pd.DataFrame([{
        'Symbol': symbol, 'Quality': 'PREMIUM', 'Type': 'BOUNCE',
        'Price': 100.0, 'Stop': 95.0, 'Target': 112.0,
    }])


def _wire(monkeypatch, names):
    """Stub the archive; return the list that records every load."""
    loaded: list[str] = []
    monkeypatch.setattr(utils, 'list_files', lambda *a, **k: list(names))

    def _load(path):
        fname = path.rsplit('/', 1)[-1]
        loaded.append(fname)
        return _frame(f"SY{len(loaded):04d}")

    monkeypatch.setattr(utils, 'load_data', _load)
    monkeypatch.setattr(ap, 'load', lambda **k: ap._empty())
    monkeypatch.setattr(ap, '_save', lambda d, **k: None)
    # Entry must differ from the CSV price: when they are equal, the function
    # treats it as "yfinance fell back to the CSV value" and fires a LIVE 5-day
    # yfinance fetch per symbol. Returning price*0.99 keeps the test hermetic —
    # and is the realistic case, since the entry is priced on the signal's date.
    monkeypatch.setattr(ap, '_fetch_entry_and_current',
                        lambda s, d, p: (round(float(p) * 0.99, 2), float(p)))
    monkeypatch.setattr(ap, '_compute_priority_score', lambda *a, **k: 50.0)
    return loaded


def test_the_archive_is_bounded_by_age(monkeypatch):
    """A 400-day archive must not be replayed to find this quarter's misses."""
    names = [_fname(n) for n in (400, 200, 120, 95, 89, 30, 1)]
    loaded = _wire(monkeypatch, names)

    ap.rebuild_skipped_cash()

    assert _fname(400) not in loaded
    assert _fname(95) not in loaded, "just outside the 90d window but still read"
    assert _fname(89) in loaded, "inside the window but skipped"
    assert _fname(1) in loaded


def test_each_file_is_read_at_most_once(monkeypatch):
    """The two-pass walk doubled every GET for no additional information."""
    names = [_fname(n) for n in range(0, 30)]
    loaded = _wire(monkeypatch, names)

    ap.rebuild_skipped_cash()

    assert len(loaded) == len(set(loaded)), (
        f"{len(loaded)} loads for {len(set(loaded))} distinct files — "
        f"the archive is still being walked more than once")


def test_the_bound_can_be_widened_explicitly(monkeypatch):
    """Deliberate deep rebuilds stay possible; the default is what changed."""
    names = [_fname(n) for n in (400, 200, 1)]
    loaded = _wire(monkeypatch, names)

    ap.rebuild_skipped_cash(max_age_days=0)   # 0 == unbounded, the old behaviour

    assert set(loaded) == set(names)


def test_the_default_window_is_a_named_constant():
    """So the UI can state it honestly instead of claiming 'all signal files'."""
    assert ap.MISSED_TRADE_MAX_AGE_DAYS == 90
