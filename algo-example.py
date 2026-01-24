#!/usr/bin/env python3
"""
Example: Using Algorithmic Trading with Breakout Scanner

This script demonstrates how to execute breakout signals using
various algorithmic order execution strategies.
"""

import asyncio
from ib_insync import IB
from algo_trader import AlgoTrader, AlgoType

async def example_basic_execution():
    """Basic algo execution example"""
    print("=" * 60)
    print("Example 1: Basic ADAPTIVE Algo Execution")
    print("=" * 60)
    
    # Connect to IB
    ib = IB()
    await ib.connectAsync('127.0.0.1', 7497, clientId=1)  # Paper trading
    
    # Create trader
    trader = AlgoTrader(ib)
    
    # Execute with ADAPTIVE algo (smart default)
    result = await trader.execute_with_algo(
        symbol='AAPL',
        action='BUY',
        quantity=100,
        algo_type=AlgoType.ADAPTIVE,
        urgency='Normal'
    )
    
    print(f"\nOrder placed: {result}")
    
    if result['success']:
        # Monitor execution
        print("\nMonitoring execution...")
        execution = await trader.monitor_execution(result['order_id'], timeout=300)
        
        print(f"\nExecution complete:")
        print(f"  Filled: {execution['filled']}/{execution['requested']}")
        print(f"  Avg Price: ${execution['avg_price']}")
        print(f"  Duration: {execution['duration']}s")
    
    ib.disconnect()


async def example_vwap_large_order():
    """VWAP algo for large order"""
    print("\n" + "=" * 60)
    print("Example 2: VWAP for Large Order")
    print("=" * 60)
    
    ib = IB()
    await ib.connectAsync('127.0.0.1', 7497, clientId=1)
    
    trader = AlgoTrader(ib)
    
    # Execute 1000 shares with VWAP over 4 hours
    result = await trader.execute_with_algo(
        symbol='NVDA',
        action='BUY',
        quantity=1000,
        algo_type=AlgoType.VWAP,
        start_time='10:00:00',
        end_time='14:00:00',
        max_pct_vol='0.1'  # 10% of volume
    )
    
    print(f"\nVWAP order placed: {result}")
    print("Order will execute throughout the day following volume pattern")
    
    # Check active orders
    active = trader.get_active_orders()
    print(f"\nActive orders: {len(active)}")
    
    ib.disconnect()


async def example_iceberg_hidden():
    """Iceberg order to hide size"""
    print("\n" + "=" * 60)
    print("Example 3: Iceberg Order (Hidden Size)")
    print("=" * 60)
    
    ib = IB()
    await ib.connectAsync('127.0.0.1', 7497, clientId=1)
    
    trader = AlgoTrader(ib)
    
    # Hide large order - show only 100 shares at a time
    result = await trader.execute_with_algo(
        symbol='TSLA',
        action='BUY',
        quantity=5000,
        algo_type=AlgoType.ICEBERG,
        display_size=100,
        limit_price=245.50
    )
    
    print(f"\nIceberg order placed: {result}")
    print("Market only sees 100 shares, but total order is 5000")
    
    ib.disconnect()


async def example_dark_ice_block():
    """DarkIce for block trade"""
    print("\n" + "=" * 60)
    print("Example 4: DarkIce (Dark Pool Liquidity)")
    print("=" * 60)
    
    ib = IB()
    await ib.connectAsync('127.0.0.1', 7497, clientId=1)
    
    trader = AlgoTrader(ib)
    
    # Seek dark pools first
    result = await trader.execute_with_algo(
        symbol='MSFT',
        action='BUY',
        quantity=2000,
        algo_type=AlgoType.DARK_ICE
    )
    
    print(f"\nDarkIce order placed: {result}")
    print("Will seek hidden liquidity in dark pools")
    
    ib.disconnect()


async def example_from_scanner_signal():
    """Execute signal from scanner with algo"""
    print("\n" + "=" * 60)
    print("Example 5: Execute Scanner Signal with Algo")
    print("=" * 60)
    
    # Simulate signal from breakout scanner
    signal = {
        'symbol': 'AAPL',
        'action': 'BUY',
        'quantity': 500,
        'price': 185.50,
        'stop_loss': 180.00,
        'take_profit': 195.00,
        'mode': 'swing',
        'quality': 'PREMIUM',
        'level2_quality': 'EXCELLENT',
        'level2_imbalance': 35.5
    }
    
    print(f"Signal received: {signal['symbol']} {signal['quality']}")
    print(f"Level 2: {signal['level2_quality']} (Imbalance: {signal['level2_imbalance']}%)")
    
    ib = IB()
    await ib.connectAsync('127.0.0.1', 7497, clientId=1)
    
    trader = AlgoTrader(ib)
    
    # Choose algo based on signal quality
    if signal['quality'] == 'PREMIUM':
        algo_type = AlgoType.ARRIVAL_PRICE
        urgency = 'Urgent'  # Fast execution for premium signals
        print("\nUsing ARRIVAL_PRICE algo with URGENT priority")
    else:
        algo_type = AlgoType.ADAPTIVE
        urgency = 'Normal'
        print("\nUsing ADAPTIVE algo with NORMAL priority")
    
    result = await trader.execute_with_algo(
        symbol=signal['symbol'],
        action=signal['action'],
        quantity=signal['quantity'],
        algo_type=algo_type,
        urgency=urgency
    )
    
    print(f"\nExecution result: {result}")
    
    if result['success']:
        execution = await trader.monitor_execution(result['order_id'])
        
        # Calculate execution quality
        slippage = abs(execution['avg_price'] - signal['price']) / signal['price'] * 100
        
        print(f"\nExecution Analysis:")
        print(f"  Target: ${signal['price']}")
        print(f"  Filled: ${execution['avg_price']}")
        print(f"  Slippage: {slippage:.3f}%")
        print(f"  Quality: {'EXCELLENT' if slippage < 0.1 else 'GOOD' if slippage < 0.3 else 'FAIR'}")
    
    ib.disconnect()


async def example_compare_algos():
    """Compare different algos on same order"""
    print("\n" + "=" * 60)
    print("Example 6: Compare Algorithm Performance")
    print("=" * 60)
    
    ib = IB()
    await ib.connectAsync('127.0.0.1', 7497, clientId=1)
    
    trader = AlgoTrader(ib)
    
    # Test same order with different algos
    test_order = {
        'symbol': 'SPY',
        'action': 'BUY',
        'quantity': 100
    }
    
    algos_to_test = [
        (AlgoType.ADAPTIVE, 'Patient'),
        (AlgoType.ADAPTIVE, 'Normal'),
        (AlgoType.ADAPTIVE, 'Urgent'),
    ]
    
    results = []
    
    for algo, urgency in algos_to_test:
        print(f"\nTesting {algo.value} with {urgency} urgency...")
        
        result = await trader.execute_with_algo(
            **test_order,
            algo_type=algo,
            urgency=urgency
        )
        
        if result['success']:
            execution = await trader.monitor_execution(result['order_id'], timeout=120)
            results.append({
                'algo': algo.value,
                'urgency': urgency,
                'avg_price': execution['avg_price'],
                'duration': execution['duration'],
                'filled': execution['filled']
            })
    
    # Compare results
    print("\n" + "=" * 60)
    print("Performance Comparison:")
    print("=" * 60)
    
    for r in results:
        print(f"{r['algo']:15} {r['urgency']:10} ${r['avg_price']:.2f}  {r['duration']:3}s  {r['filled']} filled")
    
    # Get overall stats
    stats = trader.get_execution_stats()
    print(f"\nOverall Statistics:")
    print(f"  Total orders: {stats['total_orders']}")
    print(f"  Fill rate: {stats['fill_rate']}")
    print(f"  Avg duration: {stats['avg_duration_seconds']}s")
    
    ib.disconnect()


async def main():
    """Run all examples"""
    print("\n" + "🤖 ALGORITHMIC TRADING EXAMPLES" + "\n")
    print("These examples demonstrate various algo execution strategies")
    print("Make sure TWS/IB Gateway is running on paper trading!")
    print("")
    
    try:
        # Run examples
        await example_basic_execution()
        await asyncio.sleep(2)
        
        await example_vwap_large_order()
        await asyncio.sleep(2)
        
        await example_iceberg_hidden()
        await asyncio.sleep(2)
        
        await example_dark_ice_block()
        await asyncio.sleep(2)
        
        await example_from_scanner_signal()
        await asyncio.sleep(2)
        
        # This one takes longer
        # await example_compare_algos()
        
        print("\n" + "=" * 60)
        print("✓ All examples completed successfully!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nMake sure:")
        print("  1. TWS or IB Gateway is running")
        print("  2. Paper trading account is selected")
        print("  3. API is enabled in TWS settings")


if __name__ == "__main__":
    asyncio.run(main())
