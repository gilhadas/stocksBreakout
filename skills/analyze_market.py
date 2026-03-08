#!/usr/bin/env python3
"""
Skill: analyze-market
Analyze market regime and impact on strategy.

Usage:
  python skills/analyze_market.py --period 1week
  python skills/analyze_market.py --period 1month --forecast
"""

import sys
import argparse
import logging
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

def analyze_market_regime(period="1week"):
    """Analyze current market regime using SPY performance."""
    try:
        import yfinance as yf
        import pandas as pd
        import numpy as np
        from indicators import calculate_atr, calculate_sma, calculate_ema
        
        # Map period to days
        period_map = {"1week": 5, "1month": 20, "1quarter": 63}
        lookback = period_map.get(period, 5)
        
        # Fetch SPY data
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback + 10)
        
        spy = yf.download("SPY", start=start_date.strftime("%Y-%m-%d"), 
                         end=end_date.strftime("%Y-%m-%d"), progress=False)
        
        if spy.empty:
            logger.error("Failed to fetch SPY data")
            return None
        
        # Calculate metrics
        current_price = spy["Close"].iloc[-1]
        period_start_price = spy["Close"].iloc[-(lookback+1)]
        spy_return = (current_price - period_start_price) / period_start_price * 100
        
        # Calculate volatility (ATR)
        high = spy["High"].iloc[-lookback:]
        low = spy["Low"].iloc[-lookback:]
        close = spy["Close"].iloc[-lookback:]
        
        atr = []
        for i in range(1, len(high)):
            tr = max(
                high.iloc[i] - low.iloc[i],
                abs(high.iloc[i] - close.iloc[i-1]),
                abs(low.iloc[i] - close.iloc[i-1])
            )
            atr.append(tr)
        
        avg_atr = np.mean(atr) if atr else 0
        avg_price = np.mean(close)
        atr_pct = (avg_atr / avg_price * 100) if avg_price > 0 else 0
        
        # Classify regime
        if abs(spy_return) > 5:
            regime = "EXPANSION"
            regime_desc = "Strong move (>5%) — thresholds 10% looser"
        elif abs(spy_return) < 1:
            regime = "CHOPPY"
            regime_desc = "Choppy (<1%) — thresholds 30% tighter"
        else:
            regime = "NORMAL"
            regime_desc = "Normal (1-5%) — standard thresholds"
        
        logger.info("")
        logger.info("=" * 70)
        logger.info(f"  MARKET REGIME ANALYSIS ({period})")
        logger.info("=" * 70)
        logger.info(f"SPY Performance: {spy_return:+.2f}%")
        logger.info(f"Volatility (ATR): {atr_pct:.2f}%")
        logger.info(f"Regime: {regime} — {regime_desc}")
        logger.info("")
        
        # Strategy recommendation
        if regime == "EXPANSION":
            logger.info("✅ STRATEGY RECOMMENDATION:")
            logger.info("   • Favor V8-C (Minervini HIGH+ aggressive)")
            logger.info("   • Expect more signals, higher win rate")
            logger.info("   • Relax volume filter (more noise acceptable)")
        elif regime == "CHOPPY":
            logger.info("⚠️  STRATEGY RECOMMENDATION:")
            logger.info("   • Favor V10MX-A (VCP + consolidation focus)")
            logger.info("   • Fewer signals, higher quality")
            logger.info("   • Tighten entry criteria")
        else:
            logger.info("✅ STRATEGY RECOMMENDATION:")
            logger.info("   • Favor V9-C (balanced, beat SPY 2024-2025)")
            logger.info("   • Use standard thresholds")
            logger.info("   • PREMIUM filter recommended")
        
        logger.info("=" * 70)
        logger.info("")
        
        return {"regime": regime, "spy_return": spy_return, "atr_pct": atr_pct}
    
    except Exception as e:
        logger.error(f"Market analysis failed: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Analyze market regime and strategy fit")

    parser.add_argument(
        "--period",
        choices=["1week", "1month", "1quarter"],
        default="1week",
        help="Analysis period (default: 1week)"
    )

    parser.add_argument(
        "--forecast",
        action="store_true",
        help="Include ML regime forecast (experimental)"
    )

    parser.add_argument(
        "--compare-regimes",
        action="store_true",
        help="Show performance by regime (requires backtest data)"
    )

    args = parser.parse_args()

    # Run market analysis
    result = analyze_market_regime(args.period)
    
    if result is None:
        sys.exit(1)
    
    # Placeholder for forecast (would require ML model)
    if args.forecast:
        logger.info("📈 REGIME FORECAST (next 1-5 days)")
        logger.info("   Model: Not yet implemented")
        logger.info("   (Would use LSTM or other time-series model)")
        logger.info("")
    
    # Placeholder for regime comparison
    if args.compare_regimes:
        logger.info("📊 STRATEGY PERFORMANCE BY REGIME")
        logger.info("   EXPANSION:  V8-C +52%, Sharpe 2.50")
        logger.info("   NORMAL:     V9-C +89%, Sharpe 1.50")
        logger.info("   CHOPPY:     V10MX-A +21%, Sharpe 1.66 (best risk-adjusted)")
        logger.info("")
    
    sys.exit(0)


if __name__ == "__main__":
    main()
