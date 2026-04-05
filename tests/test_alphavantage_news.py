"""
Tests for alphavantage_news.py — Alpha Vantage NEWS_SENTIMENT integration.

Uses realistic mock data based on actual API responses from the week of
2026-03-31 to 2026-04-04 (last trading week).

Run:  python -m pytest tests/test_alphavantage_news.py -v
Live: ALPHA_VANTAGE_API_KEY=xxx python -m pytest tests/test_alphavantage_news.py -v -k live
"""

import os
import sys
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Realistic mock feed — based on actual AV responses from 2026-04-04/05
# ---------------------------------------------------------------------------

MOCK_FEED = [
    {
        "title": "Nvidia-Backed Arm and MercadoLibre: A Hedge Fund's 430% and 150% Upside Cases",
        "url": "https://example.com/arm-nvda",
        "time_published": "20260405T010917",
        "authors": ["Huanyao Fang"],
        "summary": "Hedge fund manager Philippe Laffont of Coatue Management projects significant growth for Arm (ARM) and MercadoLibre (MELI) by 2030.",
        "source": "TradingKey",
        "topics": [
            {"topic": "technology", "relevance_score": "1.000000"},
            {"topic": "financial_markets", "relevance_score": "0.912728"},
            {"topic": "earnings", "relevance_score": "0.818446"},
        ],
        "overall_sentiment_score": 0.293567,
        "overall_sentiment_label": "Somewhat-Bullish",
        "ticker_sentiment": [
            {"ticker": "NVDA", "relevance_score": "0.851640",
             "ticker_sentiment_score": "0.281746", "ticker_sentiment_label": "Somewhat-Bullish"},
            {"ticker": "MELI", "relevance_score": "0.992835",
             "ticker_sentiment_score": "0.332914", "ticker_sentiment_label": "Somewhat-Bullish"},
        ],
    },
    {
        "title": "Credo (CRDO) Is Up 6.5% After Settling AEC Patent Suits",
        "url": "https://example.com/crdo",
        "time_published": "20260404T213833",
        "authors": ["Simply Wall St"],
        "summary": "Credo Technology Group Holding Ltd (CRDO) saw its stock rise by 6.5% following the settlement of patent lawsuits with TE Connectivity and Molex.",
        "source": "Simply Wall Street",
        "topics": [
            {"topic": "technology", "relevance_score": "0.895448"},
            {"topic": "earnings", "relevance_score": "0.750503"},
        ],
        "overall_sentiment_score": 0.315781,
        "overall_sentiment_label": "Somewhat-Bullish",
        "ticker_sentiment": [
            {"ticker": "CRDO", "relevance_score": "1.000000",
             "ticker_sentiment_score": "0.523420", "ticker_sentiment_label": "Bullish"},
            {"ticker": "NVDA", "relevance_score": "0.667088",
             "ticker_sentiment_score": "0.314620", "ticker_sentiment_label": "Somewhat-Bullish"},
        ],
    },
    {
        "title": "Apple Reports Record Services Revenue in Q1 2026",
        "url": "https://example.com/aapl-q1",
        "time_published": "20260403T140000",
        "authors": ["Reuters"],
        "summary": "Apple Inc reported record services revenue of $26.3B driven by App Store and iCloud growth.",
        "source": "Reuters",
        "topics": [
            {"topic": "earnings", "relevance_score": "1.000000"},
            {"topic": "technology", "relevance_score": "0.900000"},
        ],
        "overall_sentiment_score": 0.410000,
        "overall_sentiment_label": "Bullish",
        "ticker_sentiment": [
            {"ticker": "AAPL", "relevance_score": "1.000000",
             "ticker_sentiment_score": "0.450000", "ticker_sentiment_label": "Bullish"},
        ],
    },
    {
        "title": "Tesla Deliveries Miss Expectations in Q1",
        "url": "https://example.com/tsla-miss",
        "time_published": "20260402T090000",
        "authors": ["Bloomberg"],
        "summary": "Tesla delivered 385,000 vehicles in Q1 2026, below analyst estimates of 410,000 amid rising competition in China and Europe.",
        "source": "Bloomberg",
        "topics": [
            {"topic": "manufacturing", "relevance_score": "0.850000"},
            {"topic": "earnings", "relevance_score": "0.920000"},
        ],
        "overall_sentiment_score": -0.250000,
        "overall_sentiment_label": "Somewhat-Bearish",
        "ticker_sentiment": [
            {"ticker": "TSLA", "relevance_score": "0.980000",
             "ticker_sentiment_score": "-0.380000", "ticker_sentiment_label": "Bearish"},
        ],
    },
    {
        "title": "Low-Relevance NVDA Mention in Broad Market Roundup",
        "url": "https://example.com/roundup",
        "time_published": "20260401T100000",
        "authors": ["Staff"],
        "summary": "Markets closed mixed. Briefly mentions NVDA among many tickers.",
        "source": "MarketWatch",
        "topics": [{"topic": "financial_markets", "relevance_score": "1.0"}],
        "overall_sentiment_score": 0.05,
        "overall_sentiment_label": "Neutral",
        "ticker_sentiment": [
            {"ticker": "NVDA", "relevance_score": "0.300000",
             "ticker_sentiment_score": "0.050000", "ticker_sentiment_label": "Neutral"},
        ],
    },
]

MOCK_API_RESPONSE = {
    "items": str(len(MOCK_FEED)),
    "sentiment_score_definition": "x <= -0.35: Bearish; ...",
    "relevance_score_definition": "0 < x <= 1",
    "feed": MOCK_FEED,
}


def _mock_requests_get(*args, **kwargs):
    """Mock requests.get that returns our test feed."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_API_RESPONSE
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


class TestExtractTickerSentiment(unittest.TestCase):
    """Unit tests for _extract_ticker_sentiment with real-shaped data."""

    def setUp(self):
        from alphavantage_news import _extract_ticker_sentiment
        self.extract = _extract_ticker_sentiment

    def test_nvda_aggregates_high_relevance_only(self):
        """NVDA appears in 3 articles but only 2 have relevance > 0.5."""
        result = self.extract("NVDA", MOCK_FEED)
        # Article 1: rel=0.851, Article 2: rel=0.667 → both pass
        # Article 5: rel=0.300 → filtered out
        self.assertEqual(result["articles"], 2)
        self.assertGreater(result["score"], 0.0)
        self.assertEqual(result["label"], "somewhat_bullish")

    def test_nvda_headlines_are_title_plus_summary(self):
        result = self.extract("NVDA", MOCK_FEED)
        self.assertEqual(len(result["headlines"]), 2)
        # First headline should be title + first sentence of summary
        self.assertIn("Nvidia-Backed Arm", result["headlines"][0])
        self.assertIn("Hedge fund manager", result["headlines"][0])

    def test_nvda_topics_deduplicated(self):
        result = self.extract("NVDA", MOCK_FEED)
        self.assertIn("technology", result["topics"])
        # No duplicates
        self.assertEqual(len(result["topics"]), len(set(result["topics"])))

    def test_aapl_single_bullish_article(self):
        result = self.extract("AAPL", MOCK_FEED)
        self.assertEqual(result["articles"], 1)
        self.assertEqual(result["label"], "bullish")
        self.assertAlmostEqual(result["score"], 0.45, places=2)
        self.assertIn("Apple Reports Record", result["top_headline"])

    def test_tsla_bearish_sentiment(self):
        result = self.extract("TSLA", MOCK_FEED)
        self.assertEqual(result["articles"], 1)
        self.assertEqual(result["label"], "bearish")
        self.assertLess(result["score"], -0.35)

    def test_unknown_ticker_returns_neutral(self):
        result = self.extract("ZZZZ", MOCK_FEED)
        self.assertEqual(result["articles"], 0)
        self.assertEqual(result["label"], "neutral")
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["headlines"], [])

    def test_crdo_high_confidence_bullish(self):
        result = self.extract("CRDO", MOCK_FEED)
        self.assertEqual(result["articles"], 1)
        self.assertEqual(result["label"], "bullish")
        self.assertGreater(result["score"], 0.5)
        self.assertIn("Simply Wall Street", result["sources"])


class TestScoreToLabel(unittest.TestCase):
    """Verify label thresholds match AV's documented definition."""

    def setUp(self):
        from alphavantage_news import _score_to_simple_label
        self.label = _score_to_simple_label

    def test_bullish_threshold(self):
        self.assertEqual(self.label(0.35), "bullish")
        self.assertEqual(self.label(0.90), "bullish")

    def test_somewhat_bullish(self):
        self.assertEqual(self.label(0.15), "somewhat_bullish")
        self.assertEqual(self.label(0.34), "somewhat_bullish")

    def test_neutral(self):
        self.assertEqual(self.label(0.0), "neutral")
        self.assertEqual(self.label(0.14), "neutral")
        self.assertEqual(self.label(-0.14), "neutral")

    def test_somewhat_bearish(self):
        self.assertEqual(self.label(-0.15), "somewhat_bearish")
        self.assertEqual(self.label(-0.34), "somewhat_bearish")

    def test_bearish_threshold(self):
        self.assertEqual(self.label(-0.35), "bearish")
        self.assertEqual(self.label(-0.80), "bearish")


class TestBatchNewsSentiment(unittest.TestCase):
    """Test batch call with mocked API — simulates last week's scan."""

    @patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "test_key_123"})
    @patch("alphavantage_news.requests.get", side_effect=_mock_requests_get)
    def test_batch_single_api_call(self, mock_get):
        """Batch for 3 symbols should make exactly 1 HTTP request."""
        from alphavantage_news import batch_news_sentiment
        results = batch_news_sentiment(["NVDA", "AAPL", "TSLA"],
                                       limit_per_symbol=5, hours_back=168)
        # Should have called requests.get exactly once
        self.assertEqual(mock_get.call_count, 1)
        # All 3 symbols in results
        self.assertIn("NVDA", results)
        self.assertIn("AAPL", results)
        self.assertIn("TSLA", results)

    @patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "test_key_123"})
    @patch("alphavantage_news.requests.get", side_effect=_mock_requests_get)
    def test_batch_populates_headline_cache(self, mock_get):
        """batch_news_sentiment must fill _headline_cache for FinBERT."""
        from alphavantage_news import (batch_news_sentiment, _headline_cache,
                                       _cache_lock)
        # Clear cache
        with _cache_lock:
            _headline_cache.clear()

        batch_news_sentiment(["NVDA", "AAPL"], limit_per_symbol=5)

        with _cache_lock:
            self.assertIn("NVDA", _headline_cache)
            self.assertIn("AAPL", _headline_cache)
            self.assertGreater(len(_headline_cache["NVDA"]), 0)

    @patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "test_key_123"})
    @patch("alphavantage_news.requests.get", side_effect=_mock_requests_get)
    def test_fetch_av_headlines_reads_cache(self, mock_get):
        """fetch_av_headlines should use cache — no extra API call."""
        from alphavantage_news import (batch_news_sentiment, fetch_av_headlines,
                                       _headline_cache, _cache_lock)
        # Warm the cache
        batch_news_sentiment(["NVDA"], limit_per_symbol=5)
        call_count_after_batch = mock_get.call_count

        # Now fetch headlines — should NOT trigger another API call
        headlines = fetch_av_headlines("NVDA", limit=5)
        self.assertEqual(mock_get.call_count, call_count_after_batch)
        self.assertGreater(len(headlines), 0)

    def test_batch_empty_symbols(self):
        from alphavantage_news import batch_news_sentiment
        results = batch_news_sentiment([])
        self.assertEqual(results, {})

    @patch.dict(os.environ, {}, clear=True)
    def test_no_api_key_returns_neutral(self):
        """Without ALPHA_VANTAGE_API_KEY, should gracefully return neutral."""
        # Remove key if set
        os.environ.pop("ALPHA_VANTAGE_API_KEY", None)
        from alphavantage_news import get_news_sentiment
        result = get_news_sentiment("NVDA")
        self.assertEqual(result["label"], "neutral")
        self.assertEqual(result["articles"], 0)


class TestHeadlineCacheTTL(unittest.TestCase):
    """Test that stale cache is bypassed."""

    @patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "test_key_123"})
    @patch("alphavantage_news.requests.get", side_effect=_mock_requests_get)
    def test_stale_cache_triggers_fresh_call(self, mock_get):
        import alphavantage_news as av
        # Fill cache then backdate timestamp
        av.batch_news_sentiment(["NVDA"], limit_per_symbol=5)
        with av._cache_lock:
            av._cache_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)  # very stale

        call_count_before = mock_get.call_count
        av.fetch_av_headlines("NVDA", limit=5)
        # Should have made a fresh call since cache is stale
        self.assertGreater(mock_get.call_count, call_count_before)


class TestAPIErrorHandling(unittest.TestCase):
    """Verify graceful degradation on API errors."""

    @patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "test_key_123"})
    @patch("alphavantage_news.requests.get")
    def test_api_error_message(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "Error Message": "Invalid API call. Check docs."
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        from alphavantage_news import get_news_sentiment
        result = get_news_sentiment("NVDA")
        self.assertEqual(result["label"], "neutral")
        self.assertEqual(result["articles"], 0)

    @patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "test_key_123"})
    @patch("alphavantage_news.requests.get")
    def test_rate_limit_note(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "Note": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        from alphavantage_news import get_news_sentiment
        result = get_news_sentiment("NVDA")
        self.assertEqual(result["label"], "neutral")

    @patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "test_key_123"})
    @patch("alphavantage_news.requests.get")
    def test_network_timeout(self, mock_get):
        mock_get.side_effect = Exception("Connection timed out")

        from alphavantage_news import get_news_sentiment
        result = get_news_sentiment("NVDA")
        self.assertEqual(result["label"], "neutral")
        self.assertEqual(result["articles"], 0)

    @patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "test_key_123"})
    @patch("alphavantage_news.requests.get")
    def test_empty_feed(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": "0", "feed": []}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        from alphavantage_news import get_news_sentiment
        result = get_news_sentiment("ZZZZ")
        self.assertEqual(result["articles"], 0)


class TestScannerIntegration(unittest.TestCase):
    """Test that AV fields appear on signal dicts (mocked pipeline)."""

    @patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": "test_key_123"})
    @patch("alphavantage_news.requests.get", side_effect=_mock_requests_get)
    def test_signal_enrichment_keys(self, mock_get):
        """Simulate what breakout_scanner does: batch call → enrich signals."""
        from alphavantage_news import batch_news_sentiment

        # Fake scanner signals
        signals = [
            {"Symbol": "NVDA", "Quality": "PREMIUM", "Score": 75},
            {"Symbol": "AAPL", "Quality": "HIGH", "Score": 67},
            {"Symbol": "TSLA", "Quality": "GOLD", "Score": 99},
        ]

        quality_syms = [s["Symbol"] for s in signals]
        av_results = batch_news_sentiment(quality_syms, limit_per_symbol=5, hours_back=168)

        # Enrich signals (same logic as breakout_scanner.py)
        for sig in signals:
            sym = sig["Symbol"]
            if sym in av_results:
                av = av_results[sym]
                if av["articles"] > 0:
                    sig["AV_Sentiment"] = av["label"]
                    sig["AV_Score"] = av["score"]
                    sig["AV_Articles"] = av["articles"]
                    sig["AV_Headline"] = av["top_headline"][:100]
                    sig["AV_Topics"] = ",".join(av["topics"][:5])

        # NVDA should have AV data (2 high-relevance articles)
        nvda = next(s for s in signals if s["Symbol"] == "NVDA")
        self.assertIn("AV_Sentiment", nvda)
        self.assertEqual(nvda["AV_Sentiment"], "somewhat_bullish")
        self.assertEqual(nvda["AV_Articles"], 2)

        # AAPL should have AV data (1 bullish article)
        aapl = next(s for s in signals if s["Symbol"] == "AAPL")
        self.assertEqual(aapl["AV_Sentiment"], "bullish")
        self.assertIn("Apple Reports Record", aapl["AV_Headline"])

        # TSLA should have bearish AV data
        tsla = next(s for s in signals if s["Symbol"] == "TSLA")
        self.assertEqual(tsla["AV_Sentiment"], "bearish")
        self.assertLess(tsla["AV_Score"], -0.35)


# ---------------------------------------------------------------------------
# Live integration test — only runs when ALPHA_VANTAGE_API_KEY is set
# ---------------------------------------------------------------------------

@unittest.skipUnless(
    os.environ.get("ALPHA_VANTAGE_API_KEY"),
    "ALPHA_VANTAGE_API_KEY not set — skipping live test"
)
class TestLiveAlphaVantage(unittest.TestCase):
    """Live API tests — validates real data structure from last trading week."""

    def test_live_single_ticker(self):
        from alphavantage_news import get_news_sentiment
        result = get_news_sentiment("NVDA", limit=5, hours_back=168)

        # Should get at least some articles for NVDA
        self.assertGreater(result["articles"], 0,
                           "NVDA should have news articles in the last week")
        self.assertIn(result["label"],
                      ["bullish", "somewhat_bullish", "neutral",
                       "somewhat_bearish", "bearish"])
        self.assertIsInstance(result["score"], float)
        self.assertTrue(-1.0 <= result["score"] <= 1.0)
        self.assertGreater(len(result["headlines"]), 0)
        self.assertGreater(len(result["top_headline"]), 0)

    def test_live_batch_multiple_tickers(self):
        from alphavantage_news import batch_news_sentiment
        symbols = ["NVDA", "AAPL", "MSFT"]
        results = batch_news_sentiment(symbols, limit_per_symbol=3, hours_back=168)

        self.assertEqual(len(results), 3)
        for sym in symbols:
            self.assertIn(sym, results)
            r = results[sym]
            self.assertIn("label", r)
            self.assertIn("score", r)
            self.assertIn("articles", r)
            self.assertIn("headlines", r)

        # At least one should have articles
        total_articles = sum(r["articles"] for r in results.values())
        self.assertGreater(total_articles, 0,
                           "At least one of NVDA/AAPL/MSFT should have news")

    def test_live_cache_populated_after_batch(self):
        from alphavantage_news import (batch_news_sentiment, fetch_av_headlines,
                                       _headline_cache, _cache_lock)
        batch_news_sentiment(["AAPL"], limit_per_symbol=3, hours_back=168)
        headlines = fetch_av_headlines("AAPL", limit=3)
        # Should come from cache
        with _cache_lock:
            self.assertIn("AAPL", _headline_cache)

    def test_live_headlines_for_finbert(self):
        """Headlines should be formatted as 'title. summary_snippet'."""
        from alphavantage_news import get_news_sentiment
        result = get_news_sentiment("MSFT", limit=5, hours_back=168)
        for headline in result["headlines"]:
            self.assertIsInstance(headline, str)
            self.assertGreater(len(headline), 10,
                               "Headline should be substantial text")


if __name__ == "__main__":
    unittest.main()
