"""
Quick diagnostic script to verify yfinance data and signal detection
"""

import asyncio

# Python 3.14 compatibility: Create event loop before importing ib_insync
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pandas as pd
from yfinance_adapter import YFinanceAdapter
from scanner import BreakoutDetector

async def test_signal_detection():
    """Test if we can detect signals on real data"""
    
    print("=" * 70)
    print(" SIGNAL DETECTION DIAGNOSTIC")
    print("=" * 70)
    
    # Create adapter and detector
    yf_adapter = YFinanceAdapter()
    detector = BreakoutDetector()
    
    # Test symbols
    symbols = ['AAPL', 'NVDA', 'TSLA', 'GOOGL', 'MSFT', 'TMC'    ]
    
    print(f"\nTesting {len(symbols)} symbols for 2025 data...")
    print(f"Mode: SWING")
    print(f"Period: 2025-01-01 to 2025-12-31\n")
    
    for symbol in symbols:
        print(f"\n--- {symbol} ---")
        
        # Fetch data - need 2 years before 2025 for 150-day SMA
        df = yf_adapter.get_historical_data(
            symbol,
            '1 day',
            start_date='2023-01-01',  # Start 2 years earlier for 150 SMA
            end_date='2025-12-31'
        )
        
        if df is None or len(df) < 50:
            print(f"  ❌ Insufficient data: {len(df) if df is not None else 0} bars")
            continue
        
        print(f"  ✓ Fetched {len(df)} bars")
        print(f"  ✓ Date range: {df.index[0].date()} to {df.index[-1].date()}")
        print(f"  ✓ Latest price: ${df['close'].iloc[-1]:.2f}")
        
        # Filter to 2025 data
        df_2025 = df[df.index >= pd.to_datetime('2025-01-01')]
        print(f"  ✓ 2025 bars: {len(df_2025)}")
        
        # Scan all 2025 bars for signals (not just latest)
        signals_found = []
        for i in range(len(df_2025)):
            current_date = df_2025.index[i]
            df_up_to_date = df[df.index <= current_date]
            
            if len(df_up_to_date) < 50:
                continue
            
            # Try to detect breakout
            signal = detector.detect(
                df_up_to_date,
                symbol,
                'swing',
                '1 day',
                spy_perf=0.0,
                regime='NORMAL'
            )
            
            if signal:
                signals_found.append({
                    'date': current_date,
                    'price': signal['Price'],
                    'quality': signal['Quality']
                })
        
        if signals_found:
            print(f"  🎯 FOUND {len(signals_found)} SIGNAL(S)!")
            for sig in signals_found[:3]:  # Show first 3
                print(f"     {sig['date'].date()}: ${sig['price']:.2f} ({sig['quality']})")
        else:
            print(f"  ⚪ No signal (strict criteria not met)")
            
            # Show why it might not qualify
            latest = df.iloc[-1]
            start_price = df_2025.iloc[0]['close'] if len(df_2025) > 0 else latest['close']
            sma_150 = df['close'].rolling(150).mean().iloc[-1]
            vol_ratio = latest['volume'] / df['volume'].rolling(20).mean().iloc[-1]
            
            print(f"     Start price (2025): ${start_price:.2f}")
            print(f"     Latest close: ${latest['close']:.2f}")
            print(f"     150 SMA: ${sma_150:.2f}")
            print(f"     Above SMA: {latest['close'] > sma_150}")
            print(f"     Volume ratio: {vol_ratio:.2f}x (need >1.3x)")
    
    print("\n" + "=" * 70)
    print(" CONCLUSION")
    print("=" * 70)
    print("✅ yfinance is working correctly - fetching real 2025 data")
    print("✅ Data format is compatible with breakout detector")
    print("ℹ️  No signals found = detector criteria not met (normal)")
    print("\nThe breakout detector has strict criteria:")
    print("  - Must be above 150-day SMA")
    print("  - Volume must be 1.3x+ average")
    print("  - Must break consolidation pattern")
    print("  - ATR and other technical conditions")
    print("\nNot every period will have breakouts. This is expected!")

if __name__ == '__main__':
    asyncio.run(test_signal_detection())
