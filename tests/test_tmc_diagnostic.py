"""
Deep diagnostic to understand why TMC's massive move isn't detected
"""

import asyncio

# Python 3.14 compatibility
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pandas as pd
from yfinance_adapter import YFinanceAdapter
from scanner import BreakoutDetector
from config import MODES

async def deep_diagnostic_tmc():
    """Detailed analysis of TMC to understand why no signal"""
    
    print("=" * 70)
    print(" TMC DEEP DIAGNOSTIC - Why No Signal?")
    print("=" * 70)
    
    # Fetch TMC data
    yf_adapter = YFinanceAdapter()
    df = yf_adapter.get_historical_data(
        'TMC',
        '1 day',
        start_date='2024-01-01',
        end_date='2025-12-31'
    )
    
    if df is None:
        print("❌ No data for TMC")
        return
    
    print(f"\n📊 Data Overview:")
    print(f"  Total bars: {len(df)}")
    print(f"  Date range: {df.index[0].date()} to {df.index[-1].date()}")
    print(f"  Start price: ${df['close'].iloc[0]:.2f}")
    print(f"  End price: ${df['close'].iloc[-1]:.2f}")
    print(f"  Total gain: {((df['close'].iloc[-1] / df['close'].iloc[0]) - 1) * 100:.1f}%")
    
    # Show price progression
    print(f"\n📈 Price Progression (2025):")
    df_2025 = df[df.index >= pd.to_datetime('2025-01-01')]
    
    # Sample key dates
    key_dates = [
        df_2025.index[0],   # Start
        df_2025.index[len(df_2025)//4],  # Q1
        df_2025.index[len(df_2025)//2],  # Mid
        df_2025.index[3*len(df_2025)//4],  # Q3
        df_2025.index[-1],  # End
    ]
    
    for date in key_dates:
        row = df.loc[date]
        print(f"  {date.date()}: ${row['close']:.2f} (vol: {row['volume']:,})")
    
    # Check swing mode criteria
    print(f"\n🔍 Swing Mode Criteria Check:")
    mode_config = MODES['swing']
    
    # 1. Trend (150 SMA)
    df['sma_150'] = df['close'].rolling(150).mean()
    latest_sma = df['sma_150'].iloc[-1]
    latest_close = df['close'].iloc[-1]
    above_sma = latest_close > latest_sma
    
    print(f"\n  1. Trend (150 SMA):")
    print(f"     Current price: ${latest_close:.2f}")
    print(f"     150 SMA: ${latest_sma:.2f}")
    print(f"     Above SMA: {above_sma} {'✅' if above_sma else '❌'}")
    
    # 2. Volume
    df['vol_avg'] = df['volume'].rolling(20).mean()
    vol_ratio = df['volume'].iloc[-1] / df['vol_avg'].iloc[-1]
    vol_threshold = mode_config['vol_thresh']
    
    print(f"\n  2. Volume:")
    print(f"     Current volume: {df['volume'].iloc[-1]:,}")
    print(f"     20-day avg: {df['vol_avg'].iloc[-1]:,.0f}")
    print(f"     Ratio: {vol_ratio:.2f}x")
    print(f"     Threshold: {vol_threshold}x")
    print(f"     Meets criteria: {vol_ratio >= vol_threshold} {'✅' if vol_ratio >= vol_threshold else '❌'}")
    
    # 3. ATR
    df['hl'] = df['high'] - df['low']
    df['hc'] = (df['high'] - df['close'].shift()).abs()
    df['lc'] = (df['low'] - df['close'].shift()).abs()
    df['tr'] = df[['hl', 'hc', 'lc']].max(axis=1)
    df['atr'] = df['tr'].rolling(14).mean()
    atr = df['atr'].iloc[-1]
    atr_pct = (atr / latest_close) * 100
    
    print(f"\n  3. ATR (Volatility):")
    print(f"     ATR: ${atr:.2f}")
    print(f"     ATR%: {atr_pct:.2f}%")
    
    # 4. Check for consolidation breakout
    lookback = mode_config['lookback']
    recent_high = df['high'].iloc[-lookback:].max()
    recent_low = df['low'].iloc[-lookback:].min()
    consolidation_range = recent_high - recent_low
    
    print(f"\n  4. Consolidation Pattern ({lookback} bars):")
    print(f"     Recent high: ${recent_high:.2f}")
    print(f"     Recent low: ${recent_low:.2f}")
    print(f"     Range: ${consolidation_range:.2f}")
    print(f"     Current vs high: {latest_close >= recent_high} {'✅' if latest_close >= recent_high else '❌'}")
    
    # Now run actual detector
    print(f"\n🔬 Running Actual Breakout Detector:")
    detector = BreakoutDetector()
    
    # Try on different dates to see if any triggered
    print(f"\n  Scanning all 2025 bars for signals...")
    signals_found = []
    
    for i in range(len(df_2025)):
        current_date = df_2025.index[i]
        df_up_to_date = df[df.index <= current_date]
        
        if len(df_up_to_date) < 50:
            continue
        
        signal = detector.detect(
            df_up_to_date,
            'TMC',
            'swing',
            '1 day',
            spy_perf=0.0,
            regime='NORMAL'
        )
        
        if signal:
            signals_found.append({
                'date': current_date,
                'price': signal['Price'],
                'stop': signal['Stop'],
                'target': signal['Target'],
                'quality': signal['Quality']
            })
    
    if signals_found:
        print(f"\n  ✅ Found {len(signals_found)} signals!")
        for sig in signals_found[:5]:  # Show first 5
            print(f"     {sig['date'].date()}: ${sig['price']:.2f} ({sig['quality']})")
    else:
        print(f"\n  ❌ No signals found in entire 2025 period")
        print(f"\n  This suggests the detector criteria are too strict or")
        print(f"  the stock doesn't meet the consolidation breakout pattern.")
    
    # Check rejection reasons
    print(f"\n📋 Rejection Reasons:")
    if hasattr(detector, 'rejection_reasons') and detector.rejection_reasons:
        for reason in detector.rejection_reasons[-5:]:  # Last 5
            print(f"  {reason['symbol']}: {reason['reason']}")
    else:
        print(f"  No rejection reasons logged")
    
    print("\n" + "=" * 70)

if __name__ == '__main__':
    asyncio.run(deep_diagnostic_tmc())
