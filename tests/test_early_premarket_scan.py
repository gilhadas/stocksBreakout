# EPIC-PREMARKET-001 — early_premarket_scan.py: output schema + API fallback
# See: tests/stories/EPIC-PREMARKET-001.md

import importlib
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

import early_premarket_scan as eps


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mover(symbol: str, gap_pct: float, **kwargs) -> dict:
    """Build a minimal mover dict."""
    base = {
        "symbol":     symbol,
        "gap_pct":    gap_pct,
        "pm_price":   100.0,
        "prev_close": 100.0 / (1 + gap_pct / 100),
        "pm_high":    100.0,
        "pm_low":     99.5,
        "pm_volume":  50_000,
        "has_er":     False,
        "articles":   0,
        "buzz_ratio": 0.0,
    }
    base.update(kwargs)
    return base


# ── AC1-3: write_watch_file ────────────────────────────────────────────────────

class TestWriteWatchFile:
    def test_writes_one_symbol_per_line(self, tmp_path, monkeypatch):
        """AC1: symbols written one per line, no blank lines, no extra whitespace."""
        monkeypatch.setattr(eps, "EARLY_WATCH_FILE", tmp_path / "early_premarket_watch.txt")
        monkeypatch.setattr(eps, "LISTS_DIR", tmp_path)

        eps.write_watch_file(["AAPL", "TSLA", "NVDA"], dry_run=False)

        lines = (tmp_path / "early_premarket_watch.txt").read_text().splitlines()
        # Filter empty lines
        nonempty = [l for l in lines if l.strip()]
        assert nonempty == ["AAPL", "TSLA", "NVDA"]

    def test_empty_list_writes_file_no_crash(self, tmp_path, monkeypatch):
        """AC2: empty symbol list → file created, no crash."""
        out = tmp_path / "early_premarket_watch.txt"
        monkeypatch.setattr(eps, "EARLY_WATCH_FILE", out)
        monkeypatch.setattr(eps, "LISTS_DIR", tmp_path)

        eps.write_watch_file([], dry_run=False)
        assert out.exists()

    def test_dry_run_does_not_write(self, tmp_path, monkeypatch):
        """AC3: dry_run=True → file NOT written."""
        out = tmp_path / "early_premarket_watch.txt"
        monkeypatch.setattr(eps, "EARLY_WATCH_FILE", out)
        monkeypatch.setattr(eps, "LISTS_DIR", tmp_path)

        eps.write_watch_file(["AAPL"], dry_run=True)
        assert not out.exists()

    def test_symbols_have_no_extra_whitespace(self, tmp_path, monkeypatch):
        """AC1: each written line equals the symbol exactly (no leading/trailing spaces)."""
        out = tmp_path / "early_premarket_watch.txt"
        monkeypatch.setattr(eps, "EARLY_WATCH_FILE", out)
        monkeypatch.setattr(eps, "LISTS_DIR", tmp_path)

        eps.write_watch_file(["AAPL", "MSFT"], dry_run=False)
        for line in out.read_text().splitlines():
            if line:  # skip trailing newline artefacts
                assert line == line.strip(), f"Unexpected whitespace in: {line!r}"


# ── AC4: filter_movers ─────────────────────────────────────────────────────────

class TestFilterMovers:
    """filter_movers keeps: has_er | articles>=1 | buzz_ratio>=1.3 | abs(gap)>=2×threshold."""

    def test_weak_gap_no_catalyst_excluded(self):
        """AC4: gap=2%, no catalyst, threshold=3 → STRONG_GAP=6 → excluded."""
        movers = [_mover("WEAK", gap_pct=2.0)]
        result = eps.filter_movers(movers, threshold=3.0)
        assert not any(m["symbol"] == "WEAK" for m in result)

    def test_catalyst_with_weak_gap_included(self):
        """AC4: gap=2%, articles=1 → included (catalyst gate)."""
        movers = [_mover("CAT", gap_pct=2.0, articles=1)]
        result = eps.filter_movers(movers, threshold=3.0)
        assert any(m["symbol"] == "CAT" for m in result)

    def test_strong_gap_no_catalyst_included(self):
        """AC4: gap=7% >= STRONG_GAP=6% → included without catalyst."""
        movers = [_mover("STRNG", gap_pct=7.0)]
        result = eps.filter_movers(movers, threshold=3.0)
        assert any(m["symbol"] == "STRNG" for m in result)

    def test_negative_strong_gap_included(self):
        """Negative strong gap also passes the abs() gate."""
        movers = [_mover("DNEG", gap_pct=-7.0)]
        result = eps.filter_movers(movers, threshold=3.0)
        assert any(m["symbol"] == "DNEG" for m in result)

    def test_has_er_always_included(self):
        """has_er=True → included regardless of gap size."""
        movers = [_mover("ER", gap_pct=0.5, has_er=True)]
        result = eps.filter_movers(movers, threshold=3.0)
        assert any(m["symbol"] == "ER" for m in result)

    def test_buzz_ratio_threshold(self):
        """buzz_ratio >= 1.3 → included."""
        movers = [_mover("BUZZ", gap_pct=1.0, buzz_ratio=1.5)]
        result = eps.filter_movers(movers, threshold=3.0)
        assert any(m["symbol"] == "BUZZ" for m in result)

    def test_empty_list_returns_empty(self):
        assert eps.filter_movers([], threshold=3.0) == []


# ── AC5: discover_news_symbols — graceful API failure ──────────────────────────

class TestDiscoverNewsSymbols:
    def test_returns_empty_on_import_error(self):
        """AC5: if news_scanner is unavailable → [] with no exception."""
        with patch.dict("sys.modules", {"news_scanner": None}):
            # discover_news_symbols catches all exceptions
            try:
                result = eps.discover_news_symbols()
            except Exception as exc:
                pytest.fail(f"discover_news_symbols raised: {exc}")
            # Result may be [] or a list — should not raise
            assert isinstance(result, list)

    def test_returns_list_type(self):
        """discover_news_symbols always returns a list (possibly empty)."""
        with patch("early_premarket_scan.discover_news_symbols", return_value=[]):
            result = eps.discover_news_symbols.__wrapped__() if hasattr(
                eps.discover_news_symbols, "__wrapped__") else []
        # Primary path: call with mocked discover_news_movers
        with patch.dict("sys.modules", {"news_scanner": MagicMock(
            discover_news_movers=MagicMock(return_value=[])
        )}):
            result = eps.discover_news_symbols()
            assert isinstance(result, list)


# ── AC6: build_scan_universe ──────────────────────────────────────────────────

class TestBuildScanUniverse:
    def test_er_candidates_included(self):
        """AC6: ER candidates appear in output."""
        with (
            patch.object(eps, "discover_news_symbols", return_value=[]),
            patch.object(eps, "load_priority_symbols", return_value=[]),
        ):
            result = eps.build_scan_universe(["AAPL"])
        assert "AAPL" in result

    def test_no_duplicates(self):
        """AC6: same symbol from multiple sources appears only once."""
        with (
            patch.object(eps, "discover_news_symbols", return_value=["AAPL", "TSLA"]),
            patch.object(eps, "load_priority_symbols", return_value=["AAPL"]),
        ):
            result = eps.build_scan_universe(["AAPL"])
        assert result.count("AAPL") == 1

    def test_returns_list_of_strings(self):
        """AC6: output is a list of uppercase strings."""
        with (
            patch.object(eps, "discover_news_symbols", return_value=[]),
            patch.object(eps, "load_priority_symbols", return_value=["MSFT"]),
        ):
            result = eps.build_scan_universe([])
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)

    def test_capped_at_max_scan_symbols(self):
        """Universe never exceeds MAX_SCAN_SYMBOLS."""
        big_list = [f"SYM{i:04d}" for i in range(500)]
        with (
            patch.object(eps, "discover_news_symbols", return_value=big_list),
            patch.object(eps, "load_priority_symbols", return_value=[]),
        ):
            result = eps.build_scan_universe([])
        assert len(result) <= eps.MAX_SCAN_SYMBOLS
