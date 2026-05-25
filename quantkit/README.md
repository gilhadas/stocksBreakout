# quantkit — Quantitative Trading Toolkit

A reusable Python library of technical-analysis and portfolio-management tools
extracted from a professional breakout-scanning system.

## Install

```bash
# From the repo root (editable — live-reloads changes):
pip install -e .

# From GitHub:
pip install "git+https://github.com/gilhadas/stocksBreakout"

# With FinBERT financial sentiment (downloads ~420 MB model on first use):
pip install "git+https://github.com/gilhadas/stocksBreakout[sentiment]"

# With live data helpers (yfinance):
pip install "git+https://github.com/gilhadas/stocksBreakout[data]"
```

## Using quantkit in Your Project

### Installation from GitHub

In a dependent project, add to `requirements.txt` or `pyproject.toml`:

```bash
# requirements.txt:
git+https://github.com/gilhadas/stocksBreakout@main#egg=quantkit

# OR with optional extras:
git+https://github.com/gilhadas/stocksBreakout[sentiment,data]@main#egg=quantkit
```

Then install:
```bash
pip install -r requirements.txt
# OR
pip install ".[all]"  # if defined in your pyproject.toml
```

### Basic Project Structure

```
my_trading_project/
├── requirements.txt          # includes quantkit
├── config.py                 # project settings
├── my_scanner.py             # your code
├── data/                      # local price data (CSV, Parquet)
└── output/                    # results & logs
```

### Minimal Entry Point

```python
# my_scanner.py
import pandas as pd
from quantkit import indicators, patterns, regime

# Load your price data (must have lowercase OHLCV columns)
df = pd.read_csv('data/AAPL.csv', index_col='Date', parse_dates=True)

# Normalize column names if needed
df.columns = df.columns.str.lower()

# Calculate indicators
df = indicators.calculate_all_indicators(df, trend_type='SMA', trend_period=150)

# Detect chart patterns
chart_results = patterns.detect_patterns_from_df(df, ticker='AAPL')
has_bullish, has_bearish, target, names, vol_conf, vcp_q, vcp_data = \
    patterns.get_pattern_score(df)

# Check market regime (requires SPY data for context)
spy_df = pd.read_csv('data/SPY.csv', index_col='Date', parse_dates=True)
spy_df.columns = spy_df.columns.str.lower()
regime_type, metrics = regime.detect_regime(spy_df)

print(f"AAPL patterns: {names}")
print(f"Market regime: {regime_type}")
```

### Data Preparation

The **most common issue** is column naming. All quantkit modules expect **lowercase OHLCV**:

```python
import yfinance as yf

# yfinance gives you uppercase columns
df = yf.download('AAPL', period='1y')

# Convert to lowercase (required)
df = df.rename(columns=str.lower)
# or
df.columns = [c.lower() for c in df.columns]

# Now safe for quantkit
from quantkit.indicators import calculate_all_indicators
df = calculate_all_indicators(df)
```

### Handling Optional Dependencies

If you only need core indicators (not sentiment), you can skip the heavy dependencies:

```python
# This works — core quantkit with pandas/numpy only
from quantkit import indicators, patterns, fib

# This fails if torch/transformers not installed
from quantkit.sentiment import finbert  # ImportError if sentiment extra not installed
```

To gracefully handle optional modules:

```python
try:
    from quantkit.sentiment import finbert
    SENTIMENT_ENABLED = True
except ImportError:
    SENTIMENT_ENABLED = False
    print("FinBERT not available — install with: pip install quantkit[sentiment]")

if SENTIMENT_ENABLED:
    result = finbert.analyze_text(["stock rallies on earnings beat"])
```

### Common Patterns

**Pattern 1: Screening a universe**
```python
import pandas as pd
from quantkit import indicators, patterns

symbols = ['AAPL', 'MSFT', 'NVDA']
results = []

for symbol in symbols:
    df = pd.read_csv(f'data/{symbol}.csv', index_col='Date', parse_dates=True)
    df.columns = df.columns.str.lower()
    
    df = indicators.calculate_all_indicators(df)
    bullish, bearish, target, names, *_ = patterns.get_pattern_score(df)
    
    results.append({
        'Symbol': symbol,
        'Bullish': bullish,
        'Patterns': names,
        'Target': target,
    })

summary = pd.DataFrame(results)
print(summary[summary['Bullish'] == True])
```

**Pattern 2: Regime-aware exit logic**
```python
from quantkit.portfolio import ExitEvaluator, DEFAULT_EXIT_CONFIG
from quantkit.regime import detect_regime

# Fetch data
spy_df = ...  # SPY OHLCV
position_df = ...  # Your symbol's OHLCV

# Detect regime
regime_type, _ = detect_regime(spy_df)

# Adjust exit config based on regime
exit_cfg = DEFAULT_EXIT_CONFIG.copy()
if regime_type == 'bear':
    exit_cfg['max_hold_days'] = 10  # Tighter exits in bear markets
elif regime_type == 'bull':
    exit_cfg['trail_mult'] = 2.5    # Wider trails in bull markets

# Evaluate position
ev = ExitEvaluator()
action = ev.evaluate(
    position_df,
    symbol='AAPL',
    mode_cfg=exit_cfg,
    entry_price=175.0,
    stop_price=165.0,
    target_price=210.0,
    timeframe='1d',
)
print(f"Action: {action['Action']} ({action['Reason']})")
```

**Pattern 3: Fibonacci bounce scoring**
```python
from quantkit.fib import detect_swing, score_bounce

for symbol in ['AAPL', 'MSFT']:
    df = pd.read_csv(f'data/{symbol}.csv', index_col='Date', parse_dates=True)
    df.columns = df.columns.str.lower()
    
    swing = detect_swing(df)
    if swing:
        result = score_bounce(df, swing)
        if result['bounce_score'] >= 70:
            print(f"{symbol}: Strong bounce setup at {result['nearest_fib']}")
    else:
        print(f"{symbol}: No completed swing detected")
```

## Modules

| Module | Contents | Dependencies |
|--------|----------|-------------|
| `quantkit.indicators` | ATR, RSI, MACD, BB, VWAP, ADX, Aroon, StochRSI, Volume Profile, Minervini, composite scores | `pandas`, `numpy` |
| `quantkit.patterns` | 16 chart patterns + 11 candlesticks + VCP + S/R levels | `pandas`, `numpy` |
| `quantkit.fib` | Fibonacci retracement bounce scoring (0–100) | `pandas`, `numpy` |
| `quantkit.regime` | Market regime detection (bull/bear/mixed) | `pandas` |
| `quantkit.sentiment` | FinBERT sentiment + Finnhub buzz ratio | `transformers`, `torch` (optional) |
| `quantkit.portfolio` | Exit evaluation + portfolio health advisory | `pandas`, `numpy` |

## Quick Examples

### Technical Indicators

```python
import quantkit.indicators as ind

# DataFrame must have lowercase OHLCV columns: open, high, low, close, volume
df = ind.calculate_all_indicators(df, trend_type='EMA', trend_period=21, timeframe='1d')

print(df[['RSI', 'MACD', 'ATR', 'Momentum_Score']].tail())
```

### Chart Patterns

```python
from quantkit.patterns import detect_patterns_from_df, get_pattern_score

patterns = detect_patterns_from_df(df, ticker='AAPL')
has_bullish, has_bearish, target, names, vol_conf, vcp_q, vcp_data = get_pattern_score(df)
```

### Fibonacci Bounce Scoring

```python
from quantkit.fib import detect_swing, score_bounce

swing = detect_swing(df)          # finds swing high + preceding swing low
if swing:
    result = score_bounce(df, swing)
    print(result['bounce_score'], result['nearest_fib'])
    # 75  '61.8%'
```

### Regime Detection

```python
from quantkit.regime import detect_regime, suggest_params

regime, metrics = detect_regime(spy_df)   # 'bull' | 'bear' | 'mixed'
params = suggest_params('swing', regime)
print(params['quality_filter'], params['tp_mult'])
```

### FinBERT Sentiment

```python
from quantkit.sentiment.finbert import analyze_text, get_ticker_sentiment

# From pre-fetched headlines (no network needed):
result = analyze_text(["NVDA beats earnings estimates", "record revenue guidance"])
print(result['label'], result['score'])        # 'bullish'  0.93

# Full pipeline (requires yfinance for headline fetch):
result = get_ticker_sentiment('COIN')
print(result['emoji'], result['label'])        # 🟢 bullish
```

### Portfolio Exit Evaluation

```python
from quantkit.portfolio import ExitEvaluator, DEFAULT_EXIT_CONFIG

ev = ExitEvaluator()
result = ev.evaluate(
    df, symbol='AAPL',
    mode_cfg=DEFAULT_EXIT_CONFIG,
    entry_price=175.0,
    stop_price=165.0,
    target_price=210.0,
    timeframe='1d',
)
print(result['Action'], result['Reason'])     # HOLD | TRAIL | EXIT_FULL | EXIT_PARTIAL
```

### Portfolio Health

```python
from quantkit.portfolio import assess_portfolio_health

report = assess_portfolio_health(positions, cash=5000, total_equity=50000)
print(report['alerts'])
print(f"Diversity: {report['diversity_score']:.2f}, ETF%: {report['etf_pct']:.1%}")
```

## Column Convention

All modules expect **lowercase OHLCV columns**: `open`, `high`, `low`, `close`, `volume`.

To convert from yfinance (uppercase):
```python
df = df.rename(columns={'Open':'open','High':'high','Low':'low',
                        'Close':'close','Volume':'volume'})
```

## Key Algorithms

### Wilder Smoothing (ATR & RSI)
Both indicators use Wilder's EMA to exactly match TradingView:
- ATR: `ewm(com=period-1, adjust=False)` — matches `ta.atr(14)` in Pine Script
- RSI: `ewm(alpha=1/period, adjust=False)` — matches `ta.rsi(close, 14)` in Pine Script

### Pattern Return Schema
All chart pattern detectors return `Optional[Dict]` with:
```python
{
    'name': str, 'type': str,
    'bullish': bool | None, 'bearish': bool | None,
    'confidence': float,      # 0.50–0.95, capped at 0.95
    'volume_confirmed': bool,
    'current_price': float,
    'risk_level': str,        # 'low' | 'medium' | 'high'
    # + pattern-specific keys
}
```

### Fibonacci Score Breakdown
| Component | Points | Condition |
|-----------|--------|-----------|
| Classic level | +30 | Within 2% of 38.2%, 50%, or 61.8% |
| SMA confluence | +25 | SMA50/150/200 within 1.5% of that level |
| Stage 2 | +15 | SMA50 > SMA150 > SMA200 AND price > SMA200 |
| RSI reset | +15 | RSI 35–50 |
| Volume expansion | +10 | 3-day avg ≥ 1.2× 20-day avg |
| Golden pocket | +5 | Level is 50% or 61.8% |
