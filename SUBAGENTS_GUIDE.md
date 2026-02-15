# Subagents Guide for stocksBreakout

Subagents are specialized AI agents that handle autonomous, multi-step analysis tasks. They are more powerful than Skills and suitable for complex research, optimization, and deep analysis.

## 🤖 Available Subagents

### 1. **signal-analyzer** — Deep Signal Quality Analysis
**Purpose**: Comprehensive analysis of signal performance and quality
**Complexity**: High (autonomous research + analysis)

#### Typical Use Cases:
```
"Analyze all PREMIUM signals from Feb 2026, identify false positives"
"Compare which technical patterns work best for swing trading"
"Determine optimal quality threshold for new positions"
```

#### What It Does:
- Scans all signal CSV files in scanner_output/signals/
- Cross-references each signal against actual price movement
- Calculates actual win rate, return, and drawdown per signal
- Groups signals by pattern, sector, quality level
- Identifies which combinations work best
- Generates improvement recommendations

#### How to Invoke:
```python
# In Claude Code or Claude API
agent = Task(
    subagent_type="general-purpose",
    description="Analyze signal quality",
    prompt="""Analyze all PREMIUM swing trade signals from Feb 2026:
    1. Read signal files from scanner_output/signals/
    2. For each signal, check if the breakout was successful
    3. Calculate: actual return, max profit, drawdown
    4. Group by technical pattern (Ascending Triangle, Bull Flag, etc.)
    5. Identify which patterns have highest win rate
    6. Recommend filters to improve signal quality"""
)
```

#### Expected Output:
```json
{
  "total_signals": 45,
  "premium_signals": 32,
  "win_rate": "68%",
  "by_pattern": {
    "ascending_triangle": {"win_rate": "72%", "avg_return": "5.3%"},
    "bull_flag": {"win_rate": "65%", "avg_return": "4.1%"}
  },
  "recommendations": [
    "Ascending Triangles work best for swing",
    "Reject signals with low volume ratio",
    "Increase ADX threshold from 20 to 25"
  ]
}
```

---

### 2. **strategy-optimizer** — Parameter & Configuration Search
**Purpose**: Find optimal scanner parameters for your market and timeframe
**Complexity**: Very High (parallelized backtesting)

#### Typical Use Cases:
```
"Find the best RSI_OVERBOUGHT threshold for daytrade mode"
"What ATR multiplier gives highest Sharpe ratio?"
"Compare all combinations of RSI + MACD + ADX for swing"
```

#### What It Does:
- Sweeps through parameter ranges (grid search or genetic algorithm)
- For each parameter set, runs historical backtest
- Ranks results by: Sharpe, return, win rate, max drawdown
- Generates performance curves showing parameter sensitivity
- Identifies optimal values and confidence levels
- Suggests which parameters matter most

#### How to Invoke:
```python
agent = Task(
    subagent_type="general-purpose",
    description="Optimize strategy parameters",
    prompt="""Optimize swing trading parameters for 2024 data:
    1. Parameter space:
       - RSI_OVERBOUGHT: 65, 70, 75, 80
       - ADX_THRESHOLD: 15, 20, 25, 30
       - CONSOLIDATION_PERCENT: 1.0, 1.5, 2.0, 2.5
    2. For each combination, run backtest on 2024 data
    3. Calculate Sharpe, return, win rate, max DD
    4. Rank by Sharpe ratio
    5. Show top 5 configurations with comparison"""
)
```

#### Expected Output:
```json
{
  "parameter_sweep": {
    "total_combinations": 64,
    "tested": 64,
    "top_configurations": [
      {
        "rank": 1,
        "params": {"rsi_ob": 75, "adx": 25, "cons": 1.5},
        "metrics": {"sharpe": 1.42, "return": "28%", "win_rate": "68%"}
      }
    ]
  },
  "sensitivity_analysis": {
    "rsi_overbought": "High sensitivity - 10pt change = 3% return",
    "adx_threshold": "Medium sensitivity - 5pt change = 1% return"
  }
}
```

---

### 3. **market-regime-analyst** — Market Condition Intelligence
**Purpose**: Understand how market regimes affect your strategy performance
**Complexity**: High (multi-source data analysis)

#### Typical Use Cases:
```
"Analyze performance broken down by market regime"
"When do we perform best - bull, bear, or choppy markets?"
"Forecast next week's regime and optimal strategy"
```

#### What It Does:
- Fetches historical SPY data, calculates regime for each period
- Backtests signals separately for CHOPPY, NORMAL, EXPANSION
- Shows which strategies work in which regimes
- Identifies regime transitions and performance drops
- Forecasts next regime and expected performance
- Recommends mode/position sizing adjustments

#### How to Invoke:
```python
agent = Task(
    subagent_type="general-purpose",
    description="Analyze performance by market regime",
    prompt="""Analyze trading performance by market regime for 2024:
    1. Define regimes using SPY: CHOPPY, NORMAL, EXPANSION
    2. For each day in 2024, classify the regime
    3. Backtest signals separately for each regime
    4. Calculate metrics: return, sharpe, win rate per regime
    5. Identify when regime transitions happen
    6. Recommend position sizing for each regime
    7. Forecast next month's regime"""
)
```

#### Expected Output:
```json
{
  "regime_analysis": {
    "choppy": {"days": 80, "return": "2%", "sharpe": 0.4, "win_rate": "48%"},
    "normal": {"days": 150, "return": "8%", "sharpe": 1.0, "win_rate": "58%"},
    "expansion": {"days": 135, "return": "18%", "sharpe": 1.5, "win_rate": "68%"}
  },
  "regime_transitions": [
    {"date": "2024-03-15", "from": "choppy", "to": "normal", "impact": "Sharpe improved by 0.4"}
  ],
  "position_sizing_recommendation": {
    "choppy": "50% of normal size",
    "normal": "100% (baseline)",
    "expansion": "150% - can increase"
  }
}
```

---

### 4. **portfolio-risk-manager** — Risk Assessment & Sizing
**Purpose**: Calculate portfolio risk and recommend position sizes
**Complexity**: High (Greek calculations, correlation analysis)

#### Typical Use Cases:
```
"What's my current portfolio risk and max loss?"
"How much can I allocate to a new signal given current positions?"
"Calculate optimal Kelly Criterion position size"
```

#### What It Does:
- Loads all open positions from CSV files
- Calculates Greeks (delta, gamma) per position
- Analyzes correlation between positions
- Simulates max loss scenarios (1-in-100 day)
- Calculates Kelly Criterion optimal position sizing
- Recommends position adjustments to manage risk
- Alerts if exposure is too high

#### How to Invoke:
```python
agent = Task(
    subagent_type="general-purpose",
    description="Calculate portfolio risk",
    prompt="""Analyze current portfolio risk:
    1. Load open positions from input/positions_swing_mock.csv
    2. For each position, calculate:
       - Current P&L
       - Distance to stop loss
       - Sharpe and win probability
    3. Calculate portfolio metrics:
       - Total exposure
       - Correlation between positions
       - Max loss (1-in-100 day scenario)
    4. Calculate Kelly Criterion optimal size for new signal
    5. Recommend which positions to close first if risk is too high"""
)
```

#### Expected Output:
```json
{
  "portfolio_metrics": {
    "total_exposure": "$45,000",
    "current_pnl": "+$3,200 (+7.1%)",
    "max_loss_scenario": "-$8,500 (-18.9%)",
    "leverage": 1.25
  },
  "position_analysis": [
    {"symbol": "TPL", "exposure": "$6,800", "pnl": "+850", "kelly_factor": "4%"}
  ],
  "recommendations": [
    "Portfolio risk is within limits (max DD < 20%)",
    "Can allocate 3.5% to new signal",
    "Correlation is low - good diversification"
  ]
}
```

---

### 5. **news-research-agent** — Fundamental & Sentiment Research
**Purpose**: Research market news affecting your positions
**Complexity**: Very High (web research + synthesis)

#### Typical Use Cases:
```
"Research why tech stocks are down this week"
"Find news on semiconductor sector for our PREMIUM signals"
"Analyze sector sentiment for consumer discretionary"
```

#### What It Does:
- Web searches for relevant financial news
- Reads articles from financial news sources (Yahoo Finance, Seeking Alpha, etc.)
- Extracts sentiment and key themes
- Cross-references with your open positions
- Identifies catalysts and risks
- Synthesizes into trading implications

#### How to Invoke:
```python
agent = Task(
    subagent_type="general-purpose",
    description="Research market news",
    prompt="""Research current market conditions and news:
    1. Search for "tech stocks decline February 2026"
    2. Find articles on Fed policy, macro trends
    3. Check sentiment on sectors we're exposed to
    4. Identify catalyst events coming up
    5. Analyze impact on our PREMIUM signals
    6. Recommend defensive positions if risk is elevated"""
)
```

#### Expected Output:
```json
{
  "market_news": {
    "headline_theme": "Tech selloff on Fed rate outlook",
    "sentiment": "Negative",
    "key_catalysts": ["FOMC meeting Feb 20", "CPI data Feb 12"]
  },
  "sector_impact": {
    "technology": {"sentiment": "Negative", "recommendations": "Reduce exposure"},
    "healthcare": {"sentiment": "Neutral", "recommendations": "Hold"}
  },
  "position_alerts": [
    {"symbol": "DDOG", "sector": "Technology", "risk": "High"}
  ]
}
```

---

### 6. **level2-analyzer** — Order Book & Microstructure
**Purpose**: Analyze order book depth and execution quality
**Complexity**: Very High (real-time data + ML analysis)

#### Typical Use Cases:
```
"Check bid-ask spreads for all PREMIUM signals"
"Analyze order book depth - can we execute cleanly?"
"Identify stocks with poor liquidity"
```

#### What It Does:
- Fetches Level 2 market data for stock symbols
- Analyzes bid-ask spreads and depth
- Checks if liquidity is sufficient for position size
- Identifies slippage risk
- Predicts execution quality
- Alerts if stock is too illiquid for trading

#### How to Invoke:
```python
agent = Task(
    subagent_type="general-purpose",
    description="Analyze liquidity and spreads",
    prompt="""Check liquidity for all PREMIUM signals:
    1. Get Level 2 data for: TPL, DDOG, AXON, VST, DGX
    2. For each symbol, calculate:
       - Bid-ask spread %
       - Depth at 5 levels
       - Time to fill 1000 shares
    3. Identify illiquid stocks (spread > 0.5%)
    4. Recommend position size limits based on liquidity
    5. Alert if any stock is too illiquid"""
)
```

#### Expected Output:
```json
{
  "liquidity_analysis": {
    "TPL": {"spread": "0.03%", "depth": "$5M", "executable": true},
    "VST": {"spread": "0.12%", "depth": "$800k", "risk": "Monitor"}
  },
  "recommendations": {
    "high_liquidity": ["TPL", "DDOG"],
    "caution": ["VST"],
    "avoid": []
  }
}
```

---

## 📊 Using Subagents from Claude Code

### Basic Example:
```python
from anthropic import Anthropic

client = Anthropic()

# Create a conversation with subagent
response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=4000,
    system="""You are a financial analysis specialist for the stocksBreakout
    trading system. Analyze the provided data and generate actionable trading insights.""",
    messages=[
        {
            "role": "user",
            "content": """Analyze the signal quality for swing trading in February 2026:
            1. Read scanner_output/signals/signals_swing_20260211_*.csv
            2. For each signal, check if it was a winning trade
            3. Calculate win rate by technical pattern
            4. Recommend which patterns to trust most"""
        }
    ]
)

print(response.content[0].text)
```

### Advanced: Parallel Subagent Tasks
```python
from anthropic import Anthropic
import asyncio

async def analyze_multiple_aspects():
    client = Anthropic()

    # Run multiple analyses in parallel
    tasks = [
        client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": "Analyze signal quality..."}]
        ),
        client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": "Calculate portfolio risk..."}]
        ),
        client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": "Research market news..."}]
        )
    ]

    results = await asyncio.gather(*tasks)
    return results
```

---

## 🎯 Decision Tree: Skill vs Subagent

| Task | Use Skill | Use Subagent |
|------|-----------|-------------|
| Run a single scan | ✅ /scan | ❌ |
| Backtest one config | ✅ /backtest | ❌ |
| Monitor positions | ✅ /monitor | ❌ |
| Compare 50 parameter sets | ❌ | ✅ strategy-optimizer |
| Analyze news + research | ❌ | ✅ news-research-agent |
| Deep signal analysis | ❌ | ✅ signal-analyzer |
| Calculate portfolio risk | ✅ /position-report | ✅ portfolio-risk-manager |
| Check market regime | ✅ /analyze-market | ✅ market-regime-analyst |

---

## 📈 Workflow: Daily + Weekly + Monthly

### Daily (5 min)
```bash
/scan --mode swing
/monitor
/analyze-market
```

### Weekly (30 min)
```bash
/position-report --risk
/backtest --period last-week --mode swing
# Use subagent: signal-analyzer
# Use subagent: market-regime-analyst
```

### Monthly (2 hours)
```bash
/backtest --period last-month --compare v1-vs-v2
# Use subagent: strategy-optimizer (full parameter sweep)
# Use subagent: portfolio-risk-manager (rebalancing)
# Use subagent: news-research-agent (macro outlook)
```

---

## 🔗 Integration with Claude Code CLI

You can invoke subagents directly from your terminal:

```bash
# Ask Claude to analyze signals
claude ask "Analyze signal quality from signals_swing_20260211_*.csv"

# Ask for optimization recommendations
claude ask "What parameters should we optimize for 2024 performance?"

# Get portfolio advice
claude ask "Calculate optimal position sizing for a new $5000 signal"
```

---

## 📚 Reference Files
- [signals/](scanner_output/signals/) — Signal CSV files for analysis
- [positions_swing_mock.csv](input/positions_swing_mock.csv) — Current swing positions
- [positions_daytrade_mock.csv](input/positions_daytrade_mock.csv) — Current daytrade positions
- [config.py](config.py) — Scanner parameters
- [indicators.py](indicators.py) — Technical indicator definitions
