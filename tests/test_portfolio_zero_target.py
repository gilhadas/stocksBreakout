#!/usr/bin/env python3
"""
Regression test: Portfolio.add_position() must not persist a Stop/Target of
0.0 when a scan-signal CSV row has a *literal* 0 for that column (as opposed
to the key being absent). `dict.get(key, default)` only falls back on a
missing key, not a falsy value — a stored 0.0 target later crashed
pages/portfolio_page.py's "Edit Position" widget, which requires
min_value=0.01 (StreamlitValueBelowMinError).

Run:
    python -m pytest tests/test_portfolio_zero_target.py -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import portfolio as portfolio_module


def _fresh_portfolio(tmp_path, monkeypatch):
    """Isolated Portfolio instance backed by a tmp dir instead of scanner_output/."""
    portfolio_dir = tmp_path / 'portfolio'
    monkeypatch.setattr(portfolio_module, 'PORTFOLIO_DIR', str(portfolio_dir))
    monkeypatch.setattr(portfolio_module, 'SNAPSHOTS_DIR', str(portfolio_dir / 'snapshots'))
    monkeypatch.setattr(portfolio_module, 'PORTFOLIO_FILE', str(portfolio_dir / 'portfolio.json'))
    return portfolio_module.Portfolio(capital=10000)


class TestZeroStopTargetFallback:
    def test_literal_zero_target_and_stop_fall_back_to_default(self, tmp_path, monkeypatch):
        p = _fresh_portfolio(tmp_path, monkeypatch)
        pos = p.add_position(
            {'Symbol': 'TEST', 'Price': 100.0, 'Stop': 0, 'Target': 0}, shares=1)
        assert pos['stop'] == pytest.approx(95.0)
        assert pos['target'] == pytest.approx(110.0)

    def test_missing_stop_target_keys_fall_back_to_default(self, tmp_path, monkeypatch):
        p = _fresh_portfolio(tmp_path, monkeypatch)
        pos = p.add_position({'Symbol': 'TEST2', 'Price': 50.0}, shares=1)
        assert pos['stop'] == pytest.approx(47.5)
        assert pos['target'] == pytest.approx(55.0)

    def test_valid_stop_target_are_preserved(self, tmp_path, monkeypatch):
        p = _fresh_portfolio(tmp_path, monkeypatch)
        pos = p.add_position(
            {'Symbol': 'TEST3', 'Price': 100.0, 'Stop': 90.0, 'Target': 130.0}, shares=1)
        assert pos['stop'] == 90.0
        assert pos['target'] == 130.0
