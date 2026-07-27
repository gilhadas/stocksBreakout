"""
Tests for auto_portfolio.refresh_prices_all_users (added 2026-07-27).

Context: adds were already multi-user via scan_and_add_all_users(), but refresh was
not. `refresh_prices()` with no user_id resolves to the DEFAULT book only
(scanner_output/portfolio/auto_portfolio.json), so wiring the bare call into cron
would have left every real per-user portfolio untouched — trails unraised, stops
unhonoured. See tests/test_crontab_parity.py for the schedule-side guard.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auto_portfolio as ap


class _User:
    def __init__(self, uid, email):
        self.id, self.email = uid, email


def _fake_db(users):
    """Patch the api.database/api.models imports done inside the function body."""
    db = types.SimpleNamespace(query=lambda _model: types.SimpleNamespace(all=lambda: users))
    database = types.ModuleType('api.database')
    database.get_db = lambda: iter([db])
    models = types.ModuleType('api.models')
    models.User = _User
    return {'api.database': database, 'api.models': models}


def test_refreshes_every_user_not_just_default():
    users = [_User('uid-a', 'a@x.com'), _User('uid-b', 'b@x.com'), _User('uid-c', 'c@x.com')]
    seen = []

    def fake_refresh(user_id=None):
        seen.append(user_id)
        return {'closed': [], 'updated': 2, 'data': {'positions': []}}

    with patch.dict(sys.modules, _fake_db(users)), \
         patch.object(ap, 'refresh_prices', side_effect=fake_refresh):
        result = ap.refresh_prices_all_users()

    assert seen == ['uid-a', 'uid-b', 'uid-c'], "must refresh each user's own book"
    assert set(result) == {'a@x.com', 'b@x.com', 'c@x.com'}


def test_strips_bulky_data_payload_from_result():
    """The 'data' key is the whole portfolio — keep it out of the cron log."""
    users = [_User('uid-a', 'a@x.com')]
    with patch.dict(sys.modules, _fake_db(users)), \
         patch.object(ap, 'refresh_prices',
                      return_value={'closed': ['XYZ'], 'updated': 1, 'data': {'huge': 'x' * 10_000}}):
        result = ap.refresh_prices_all_users()

    assert 'data' not in result['a@x.com']
    assert result['a@x.com'] == {'closed': ['XYZ'], 'updated': 1}


def test_one_bad_book_does_not_stop_the_rest():
    """Per-user isolation: a single broken portfolio must not dark the others."""
    users = [_User('uid-a', 'a@x.com'), _User('uid-bad', 'bad@x.com'), _User('uid-c', 'c@x.com')]

    def fake_refresh(user_id=None):
        if user_id == 'uid-bad':
            raise RuntimeError('corrupt json')
        return {'closed': [], 'updated': 1, 'data': {}}

    with patch.dict(sys.modules, _fake_db(users)), \
         patch.object(ap, 'refresh_prices', side_effect=fake_refresh):
        result = ap.refresh_prices_all_users()

    assert result['bad@x.com'] == {'error': 'corrupt json'}
    assert result['a@x.com']['updated'] == 1
    assert result['c@x.com']['updated'] == 1, "user after the failure must still run"


def test_falls_back_to_default_book_when_db_unavailable():
    """No DB (e.g. local run) must still refresh something, not silently do nothing."""
    broken = types.ModuleType('api.database')

    def _boom():
        raise RuntimeError('no database')
    broken.get_db = _boom

    with patch.dict(sys.modules, {'api.database': broken}), \
         patch.object(ap, 'refresh_prices',
                      return_value={'closed': [], 'updated': 3, 'data': {}}) as m:
        result = ap.refresh_prices_all_users()

    assert list(result) == ['default']
    assert result['default'] == {'closed': [], 'updated': 3}
    m.assert_called_once_with()          # default book: no user_id


def test_empty_user_list_is_not_an_error():
    with patch.dict(sys.modules, _fake_db([])), \
         patch.object(ap, 'refresh_prices') as m:
        assert ap.refresh_prices_all_users() == {}
        m.assert_not_called()


def test_is_importable_the_way_cron_calls_it():
    """docker/crontab does `from auto_portfolio import refresh_prices_all_users`."""
    from auto_portfolio import refresh_prices_all_users
    assert callable(refresh_prices_all_users)
