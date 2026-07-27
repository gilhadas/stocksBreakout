---
name: sentiment-analysis
description: Expert assistant for financial sentiment analysis in Python. Use when the user wants to add, debug, or explain FinBERT sentiment analysis, Finnhub buzz scoring, news-based signal quality, or sentiment-aware trading rules in any codebase.
---

# Sentiment Analysis Skill

You are a **Senior NLP Engineer and Quantitative Analyst** specializing in financial sentiment analysis. Your job is to help build, modify, debug, and explain sentiment-based signal filtering and quality-boosting in any Python trading project.

## First: Orient to the Project

Before writing any code, check:
1. What news sources does the project use? (Finnhub, yfinance, Alpaca, NewsAPI, custom?)
2. Does the project do real-time sentiment or batch-process headlines?
3. How is sentiment used? (signal quality boost, veto, sizing, regime adjustment?)
4. What's the risk tolerance for false positives? (news lags price by 0–24 hours)
5. Is there a pre-trained model or API dependency? (FinBERT vs Finnhub API)

> **In stocksBreakout:** Sentiment lives in `quantkit/sentiment/` with 2 modules:
> - `finbert.py` — FinBERT model inference, batch sentiment, vectorized processing
> - `finnhub.py` — Finnhub buzz ratio API, historical news correlation scoring
>
> Project's `finbert_sentiment.py` wraps quantkit + 3-tier news pipeline (Alpaca → yfinance → AlphaVantage) with CLI/caching.
>
> **Using quantkit in any project:**
> ```bash
> pip install "git+https://github.com/gilhadas/stocksBreakout[sentiment]"  # Includes torch + transformers
> ```
> ```python
> from quantkit.sentiment.finbert import analyze_text, batch_sentiment, get_ticker_sentiment
> from quantkit.sentiment.finnhub import get_buzz, batch_buzz     # NOT get_buzz_ratio
>
> result = analyze_text(["AAPL beats earnings", "record guidance"])
> print(result['label'], result['score'], result['net_score'])   # 'bullish' 0.93 +0.67
>
> buzz = get_buzz('AAPL')            # no api_key arg — key comes from FINNHUB_API_KEY in env
> print(buzz['articles_last_week'], buzz['weekly_average'], buzz['buzz_ratio'])
> ```
>
> **⚠ Two contracts that are easy to get wrong — both verified against the source:**
>
> | Wrong (do not use) | Correct |
> |---|---|
> | `from ... import get_buzz_ratio` | `get_buzz` — `get_buzz_ratio` **does not exist** (ImportError) |
> | `get_buzz(sym, api_key=...)` | `get_buzz(symbol, baseline_weeks=4)` — no `api_key` parameter |
> | `buzz['buzz']`, `buzz['sentiment']`, `buzz['1_week']` | `buzz['articles_last_week']`, `buzz['weekly_average']`, `buzz['buzz_ratio']` |
> | `get_ticker_sentiment(sym, days_back=7)` | `get_ticker_sentiment(symbol, headlines=None, max_headlines=8, max_age_hours=48)` — **there is no `days_back`** (TypeError) |
> | `result['label'] == 'positive'` | `'bullish'` / `'bearish'` / `'neutral'` — quantkit remaps FinBERT's raw labels |
> | `result['avg_score']`, `['headline_count']`, `['sources']`, `['recent_headlines']` | `label`, `score`, `net_score`, `breakdown`, `headlines`, `top_headline`, `emoji` |

---

## Universal Rules

- **Sentiment is noisy** — use as **quality booster**, not signal generator. Solo sentiment has 48–52% win rate (random).
- **News lags price** — headline sentiment peaks 0–4 hours *after* price move. Use for confirmation, not entry.
- **Batch processing is faster** — analyze 100 headlines at once, not one-by-one. Models are optimized for batches.
- **FinBERT ≠ sentiment polarity** — it scores financial tone (positive/negative), not sarcasm or irony. "Worst earnings ever" might score negative-bearish.
- **Finnhub buzz carries NO direction** — it is an article-count ratio (1.0 = that symbol's own baseline). High buzz could be a scandal, a downgrade, or an earnings miss. Always pair it with FinBERT `net_score` for tone.
- **`score` is confidence, `net_score` is polarity** — a confidently *bearish* result has a *high* `score`. Threshold on `net_score`.
- **Cache aggressively** — headline fetching is slow and rate-limited. Cache results by (symbol, date, source) to avoid re-fetching.
- **Sentiment decay** — a 3-day-old headline is 70% less relevant than today's. Weight recency.

---

## FinBERT Module

### What It Does
FinBERT (`ProsusAI/finbert`) is a BERT-base model fine-tuned on financial news and analyst
reports. It classifies text as positive / neutral / negative; `quantkit` remaps those to
**`bullish` / `neutral` / `bearish`**.

### ⚠ `score` Is a CONFIDENCE, Not a Polarity

This is the single most common bug in sentiment code. `score` is the model's confidence in
**whichever label it picked** — it is always high for a *confident* call, in either direction:

| Headline | `label` | `score` | `net_score` |
|---|---|---|---|
| "Q1 revenue beats expectations" | `bullish` | **0.93** | +0.9 |
| "Guidance unchanged" | `neutral` | 0.71 | 0.0 |
| "Company slashes outlook, shares plunge" | `bearish` | **0.95** ← *high* | −0.9 |

A confidently bearish headline scores **0.95, not 0.05.** Any test shaped like
`score < 0.2 → bearish` is wrong and will never fire on real output.

**Use `net_score` (−1.0 … +1.0) whenever you need a signed value** — bullish_pct −
bearish_pct across the analysed headlines. That is the field that is safe to average,
threshold, or feed into sizing. `score` is only meaningful *alongside* `label`.

**Key property:** It's financial-domain-specific. Trained on SEC filings, analyst reports, earnings calls — not Twitter.

### Installation

```bash
# With FinBERT (downloads ~420 MB model on first use)
pip install "git+https://github.com/gilhadas/stocksBreakout[sentiment]"

# Separately (if using custom project)
pip install transformers torch
```

### Core Functions

#### Single-text analysis
```python
from quantkit.sentiment.finbert import analyze_text

result = analyze_text(["AAPL beats expectations on iPhone sales"])
print(result)
# {
#   'label':        'bullish',     # 'bullish' | 'neutral' | 'bearish'
#   'score':        0.95,          # confidence IN THAT LABEL, 0.0–1.0
#   'net_score':    0.9,           # SIGNED −1.0…+1.0  ← use this for thresholds
#   'breakdown':    {'bullish': 1, 'bearish': 0, 'neutral': 0},
#   'headlines':    8,             # COUNT of texts analysed (an int, not a list)
#   'top_headline': '...',
#   'emoji':        '🟢',          # 🟢 bullish / ⚪ neutral / 🔴 bearish
# }
```

**Interpretation — read `label` and `score` together, or just use `net_score`:**

| | Meaning |
|---|---|
| `net_score > +0.5` | Strong bullish consensus → quality boost |
| `net_score` −0.2 … +0.2 | Mixed / neutral → no adjustment |
| `net_score < −0.5` | Strong bearish consensus → veto candidate |
| `label == 'bullish' and score > 0.8` | High conviction bullish |
| `label == 'bearish' and score > 0.8` | High conviction **bearish** — note `score` is *high* |

#### Batch processing (faster)

`batch_sentiment` batches **symbols**, not headlines — it takes a symbol list and returns
one aggregated `SentimentResult` *per symbol*. There is no `batch_size` argument, and
passing a flat list of headline strings will treat each headline as a ticker.

```python
from quantkit.sentiment.finbert import batch_sentiment

results = batch_sentiment(['AAPL', 'MSFT', 'COIN'], max_headlines=8, max_age_hours=48)
# {
#   'AAPL': {'label': 'bullish', 'score': 0.92, 'net_score':  0.75, 'headlines': 8, ...},
#   'MSFT': {'label': 'neutral', 'score': 0.61, 'net_score':  0.00, 'headlines': 5, ...},
#   'COIN': {'label': 'bearish', 'score': 0.85, 'net_score': -0.60, 'headlines': 7, ...},
#            #                    ↑ score HIGH, net_score NEGATIVE
# }

# Supply your own headlines and skip the network entirely:
results = batch_sentiment(['AAPL'], headlines_map={'AAPL': ["Q1 revenue beats", "guidance raised"]})
```

To score a bare list of strings as a **single** aggregate, use `analyze_text(list)`.
To fetch headlines for one symbol, `fetch_headlines(symbol, max_count=8, max_age_hours=48)`
— **yfinance only**. The Alpaca → yfinance → AlphaVantage 3-tier chain lives in the
project's `finbert_sentiment.py` wrapper, not in quantkit.

**Batch is ~3–5× faster** than loop-calling per symbol.

#### Fetch & analyze ticker sentiment
```python
from quantkit.sentiment.finbert import get_ticker_sentiment

result = get_ticker_sentiment('COIN', max_headlines=8, max_age_hours=48)
print(result)
# {
#   'label':        'bullish',
#   'score':        0.68,          # mean confidence of the dominant label
#   'net_score':    0.34,          # SIGNED consensus, −1.0…+1.0
#   'breakdown':    {'bullish': 5, 'bearish': 2, 'neutral': 1},   # headline counts
#   'headlines':    8,             # COUNT of texts analysed (an int, not a list)
#   'top_headline': 'Coinbase volumes hit record...',
#   'emoji':        '🟢',          # 🟢 / ⚪ / 🔴  (neutral is WHITE, not yellow)
# }
```

Freshness is bounded by **`max_age_hours` (default 48)**, not a `days_back` argument.
Pass `headlines=[...]` to score a list you fetched yourself and skip the network entirely.

### Common FinBERT Patterns

**Pattern 1: Sentiment-gated signal quality boost**
```python
from quantkit.sentiment.finbert import analyze_text

def boost_signal_quality(signal_quality, headline, threshold=0.75):
    """
    Promote PREMIUM→GOLD if recent bullish news; demote on bearish news.

    Both branches test `score > threshold`, because score is confidence in the
    label — NOT a polarity. `label == 'bearish' and score < 0.25` (a tempting
    mirror of the bullish branch) selects LOW-confidence bearish calls and
    therefore never fires on the headlines you actually want to veto.
    """
    result = analyze_text([headline])

    if result['label'] == 'bullish' and result['score'] > threshold:
        return min(100, signal_quality + 15)   # boost 15 points
    elif result['label'] == 'bearish' and result['score'] > threshold:
        return max(0, signal_quality - 20)     # veto 20 points
    else:
        return signal_quality
```

**Pattern 2: Sentiment momentum (recent vs. prior)**
```python
def sentiment_momentum(ticker, recent_hours=24, prior_hours=96):
    """
    Compare the last day's tone against the preceding few days.
    Bullish if the trend is improving.

    Two things this has to get right:
      * Compare SIGNED net_score. Averaging `score` compares confidence, not
        direction — a run of confidently BEARISH headlines would read as rising.
      * Call get_ticker_sentiment twice with different max_age_hours windows.
        There is no per-headline date in the return, so you cannot split one
        result into two halves.
    """
    recent = get_ticker_sentiment(ticker, max_age_hours=recent_hours)
    prior  = get_ticker_sentiment(ticker, max_age_hours=prior_hours)

    momentum = recent['net_score'] - prior['net_score']
    return 'bullish' if momentum > 0.05 else 'bearish' if momentum < -0.05 else 'neutral'
```

**Pattern 3: Headline freshness weighting**
```python
from datetime import datetime, timedelta

def weighted_avg_sentiment(headlines, half_life_days=3):
    """
    Older headlines matter less. Half-life decay: 3 days = 50% weight.

    NOTE: `headlines` here is YOUR OWN list of {'score', 'date'} dicts. quantkit's
    SentimentResult['headlines'] is an int COUNT, not a list, and it carries no
    per-headline dates — so you cannot feed a quantkit result into this directly.
    Score each headline yourself with analyze_text([text]) and keep its timestamp.
    """
    now = datetime.now()
    total_weight = 0
    total_score = 0
    
    for h in headlines:
        days_old = (now - h['date']).days
        # Exponential decay: weight = 2^(-days_old / half_life)
        weight = 2 ** (-days_old / half_life_days)
        total_weight += weight
        total_score += h['score'] * weight
    
    return total_score / total_weight if total_weight > 0 else 0.5
```

---

## Finnhub Buzz Module

### What It Does

**Buzz is a pure *volume* metric — `quantkit.sentiment.finnhub` returns NO sentiment at all.**

Finnhub's `/news-sentiment` endpoint is **premium-only and returns 403 on the free tier**,
so the module deliberately computes buzz from article *counts* on `/company-news` instead:

```
articles_last_week  = number of articles in the last 7 days
weekly_average      = mean articles/week over the preceding 4 weeks (baseline_weeks)
buzz_ratio          = articles_last_week / weekly_average
```

### ⚠ `buzz_ratio` Is an Unbounded Ratio Where **1.0 = Baseline**

It is **not** a 0–1 score centred on 0.5. Thresholds written for a 0–1 scale are inverted:

| `buzz_ratio` | Meaning |
|---|---|
| **1.0** | exactly normal news volume — the baseline |
| 0.5 | **half** the usual coverage — going quiet |
| 2.0 | **double** — a catalyst is present |
| 3.0+ | heavy coverage; check *why* before trading it |

So a rule like `if buzz > 0.6: # elevated` is wrong twice over — 0.6 is 40% *below*
baseline, and the key is `buzz_ratio`, not `buzz`.

Treat buzz as a **catalyst-present flag, independent of tone**: it tells you something is
happening, never whether it is good. Pair it with FinBERT `net_score` for direction.

### Installation

Finnhub requires an API key (free tier: 60 calls/min, 250 calls/day).

```bash
pip install finnhub-python  # Or via stocksBreakout[sentiment]
```

### Core Functions

#### Get buzz
```python
from quantkit.sentiment.finnhub import get_buzz

buzz = get_buzz('AAPL')                  # key read from FINNHUB_API_KEY in the environment
if buzz:                                 # returns None on API failure — always check
    print(buzz)
# {
#   'articles_last_week': 47,     # raw count, last 7 days
#   'weekly_average':     18.25,  # mean/week over the preceding 4 weeks
#   'buzz_ratio':         2.58,   # 47 / 18.25  →  2.58× normal coverage
# }

buzz = get_buzz('AAPL', baseline_weeks=8)   # longer, steadier baseline
```

**Interpretation** (direction must come from FinBERT — buzz has no tone):
- `buzz_ratio > 2.0` + `net_score > 0.3` → catalyst with bullish tone
- `buzz_ratio > 2.0` + `net_score < −0.3` → scandal / downgrade / miss; stand aside
- `buzz_ratio < 0.7` → coverage has dried up; thin news, surprise risk
- `articles_last_week < 3` → sample too small to mean anything, whatever the ratio says

#### Batch buzz scoring
```python
from quantkit.sentiment.finnhub import batch_buzz

# Use the built-in batcher — it throttles for you (free tier: 60 calls/min).
# Do NOT hand-roll a bare loop over get_buzz(); you will hit the rate limit.
results = batch_buzz(symbols, throttle_sec=1.1)   # {symbol: FinnhubBuzz}

def catalyst_flags(symbols, min_ratio=2.0, min_articles=3):
    """Symbols whose news volume is meaningfully above their own baseline."""
    results = batch_buzz(symbols)
    return {
        sym: b['buzz_ratio']
        for sym, b in results.items()
        if b and b['articles_last_week'] >= min_articles and b['buzz_ratio'] >= min_ratio
    }
```

---

## Combining FinBERT + Finnhub

### 3-Tier News Pipeline (stocksBreakout pattern)

```python
def fetch_headlines_3tier(ticker, days_back=7):
    """
    Try Alpaca → fallback yfinance → fallback AlphaVantage.
    Avoids single-source bias and rate limit issues.
    """
    headlines = []
    
    # Tier 1: Alpaca News API (most reliable, rate-limited)
    try:
        from alpaca.data.historical import NewsDataFrame
        news = NewsDataFrame.get_news(ticker, start=..., end=...)
        headlines.extend(news)
    except Exception as e:
        print(f"Alpaca failed: {e}")
    
    # Tier 2: yfinance headlines (fast, less comprehensive)
    try:
        import yfinance as yf
        ticker_obj = yf.Ticker(ticker)
        news = ticker_obj.news
        headlines.extend(news)
    except Exception as e:
        print(f"yfinance failed: {e}")
    
    # Tier 3: AlphaVantage NEWS_SENTIMENT (slow, free)
    try:
        # ... alpha vantage call ...
        pass
    except Exception:
        pass
    
    return headlines
```

### Sentiment-Aware Signal Quality

```python
def apply_sentiment_to_signal(signal, symbol):
    """
    Boost/veto signal quality from FinBERT tone + Finnhub coverage volume.

    The two inputs answer different questions and must NOT be blended into one
    "consensus" number: FinBERT gives DIRECTION (net_score, signed), Finnhub gives
    ATTENTION (buzz_ratio, unsigned). Averaging them lets loud-and-bearish look the
    same as quiet-and-bullish.
    """
    from quantkit.sentiment.finbert import get_ticker_sentiment
    from quantkit.sentiment.finnhub import get_buzz

    tone = get_ticker_sentiment(symbol, max_age_hours=72)
    net  = tone['net_score']                  # signed, −1.0 … +1.0

    buzz  = get_buzz(symbol)
    ratio = buzz['buzz_ratio'] if buzz else 1.0        # 1.0 == baseline
    thin  = (not buzz) or buzz['articles_last_week'] < 3

    quality = signal['quality']

    if thin:
        pass                                   # too little news to act on
    elif net > 0.4 and ratio >= 2.0:
        quality = min(100, quality + 20)       # bullish tone on a real catalyst
    elif net < -0.4 and ratio >= 2.0:
        quality = max(0, quality - 30)         # loud bad news — scandal/miss/downgrade
    elif net < -0.4:
        quality = max(0, quality - 15)         # quietly negative

    return {**signal, 'quality': quality, 'net_score': net, 'buzz_ratio': ratio}
```

---

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Using headline sentiment to predict 5-min returns | Headlines move price over hours/days, not seconds. Use for swing/longterm only. |
| Trusting a single headline's score | Batch 5+ headlines per ticker. One outlier = noise. |
| Not weighting by recency | Yesterday's news is 30% less relevant. Use exponential decay. |
| Confusing Finnhub buzz with sentiment | Buzz = volume (how much talked about). Sentiment = positive/negative tone. quantkit's buzz module returns **no** sentiment field — `/news-sentiment` is premium-only (403 free tier). |
| Treating `buzz_ratio` as a 0–1 score | It is an unbounded ratio where **1.0 = baseline**. `buzz > 0.6` means coverage 40% *below* normal, not "elevated". |
| Thresholding on `score` for direction | `score` is confidence in the chosen label — high for confident *bearish* too. Use signed `net_score`. |
| Comparing `label == 'positive'` | quantkit remaps to **`'bullish'` / `'bearish'` / `'neutral'`**. The `'positive'` test silently never matches. |
| Passing `days_back=` to `get_ticker_sentiment` | TypeError — the freshness knob is `max_age_hours` (default 48). |
| Vetoing a signal because no headlines came back | `headlines == 0` is silence, not bad news. Only act on an actual bearish reading. |
| Fetching headlines on every tick | Cache (symbol, date) → headlines for 1 hour. Re-fetch only if no prior record. |
| No fallback for news API failures | Chain Alpaca → yfinance → AV. One API down should not stop your scanner. |
| Hard-coded Finnhub API calls in signal loop | Rate limits kill you (60 calls/min). Batch or cache. |
| Over-weighting sentiment in bear market | Bear = contrarian bounces. Negative sentiment can precede rallies. Use regime awareness. |

---

## Integration Patterns

### Pattern 1: Sentiment gates in scanner

```python
def should_trade_signal(signal, symbol):
    """
    Only take BREAKOUT if FinBERT tone supports it.
    """
    if signal['type'] != 'BREAKOUT':
        return True  # other signal types don't need a sentiment gate

    tone = get_ticker_sentiment(symbol, max_age_hours=48)

    # 'bullish' — NOT 'positive'. quantkit remaps FinBERT's raw labels, so a
    # `!= 'positive'` test rejects every signal, including the bullish ones.
    if tone['label'] != 'bullish':
        return False

    # No headlines at all is silence, not a bearish verdict — don't veto on it.
    if not tone['headlines']:
        return True

    return True
```

### Pattern 2: Sizing by sentiment conviction

```python
def sentiment_adjusted_position_size(base_size_pct, net_score, buzz_ratio):
    """
    Scale size by BULLISH conviction only.

    Two traps, both fatal, both easy to write by accident:
      1. `abs(score - 0.5) * 2` on FinBERT's `score` measures *how confident* the
         model is, not how bullish — it sizes UP on confidently BEARISH news.
         Use the signed `net_score` and clamp negatives to zero.
      2. `buzz_ratio` is centred on 1.0, not 0.5. `min(1.0, ratio * 1.5)` saturates
         at any ratio ≥ 0.67, i.e. it reads "maximum attention" for a stock whose
         coverage has fallen a third below normal.
    """
    # net_score: −1 (max bearish) … +1 (max bullish). Only positive tone adds size.
    tone_conviction = max(0.0, net_score)

    # buzz_ratio: 1.0 = baseline. Credit only genuine elevation, cap at 3× normal.
    buzz_conviction = max(0.0, min(1.0, (buzz_ratio - 1.0) / 2.0))

    conviction = (tone_conviction * buzz_conviction) ** 0.5   # geometric mean

    # Never let sentiment size a position to zero — floor it, and treat sentiment
    # as a modifier on a risk-based size, never as the size itself.
    return base_size_pct * (0.5 + 0.5 * conviction)
```

### Pattern 3: Daily sentiment monitor

```python
def daily_sentiment_report(symbols):
    """
    Print daily sentiment scorecard.
    """
    from quantkit.sentiment.finnhub import batch_buzz

    print(f"\n{datetime.now().strftime('%Y-%m-%d')} Sentiment Scorecard\n")
    buzzes = batch_buzz(symbols)        # one throttled pass, not N ad-hoc calls

    for symbol in symbols:
        tone = get_ticker_sentiment(symbol, max_age_hours=24)
        b    = buzzes.get(symbol)
        ratio = f"{b['buzz_ratio']:.2f}x" if b else "  n/a"

        print(f"{symbol:6} {tone['emoji']} {tone['label']:8} "
              f"net={tone['net_score']:+.2f} conf={tone['score']:.2f} | buzz={ratio}")
```

---

## Instructions

**Explain sentiment analysis**: describe FinBERT vs. Finnhub, score ranges, how sentiment relates to price movement.

**Add sentiment gating**: ask what signal type needs gating (BREAKOUT, BOUNCE, RSI divergence?), then produce gate logic + quality-boost/veto rules.

**Debug sentiment mismatch**: ask for symptom (missing bullish trades in high-sentiment stocks, taking trades into bearish news). Walk through pipeline (headline fetch → FinBERT score → integration point).

**Tune sentiment weights**: propose a change (FinBERT vs. Finnhub balance, recency decay half-life, quality boost magnitude), explain trade-off, recommend backtesting with/without sentiment gate.

**Integrate with quantkit**: show how to call `analyze_text(list_of_texts)` for a single aggregate score, `batch_sentiment(symbols)` for many tickers, `get_ticker_sentiment(symbol, max_age_hours=...)` for one ticker's current tone, and `get_buzz(symbol)` / `batch_buzz(symbols)` for Finnhub coverage volume. Always verify the returned keys against the contract table at the top of this skill before writing code against them — the field names are not the obvious ones.

**Set up news pipeline**: explain 3-tier fallback strategy (Alpaca → yfinance → AV) to avoid single-source risk and rate limits.
