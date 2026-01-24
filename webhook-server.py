#!/usr/bin/env python3
"""
Example Webhook Server for Automated Trading
Receives signals from breakout scanner and can execute trades via IB

WARNING: This is an example. Use at your own risk.
Always test thoroughly before using with real money.
"""

from flask import Flask, request, jsonify
import logging
from datetime import datetime
import json
from typing import Dict, List
import asyncio
from ib_insync import IB, Stock, MarketOrder, LimitOrder
from algo_trader import AlgoTrader, AlgoType, execute_signal_with_algo

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
CONFIG = {
    'ib_host': '127.0.0.1',
    'ib_port': 7497,  # Paper trading port
    'ib_client_id': 2,
    'auth_token': 'your_secret_token_here',  # Change this!
    'auto_execute': False,  # Set to True to enable auto-trading
    'use_algo_trading': True,  # Use algorithmic execution
    'default_algo': 'ADAPTIVE',  # TWAP | VWAP | ICEBERG | ADAPTIVE | DARK_ICE | ARRIVAL_PRICE
    'algo_urgency': 'Normal',  # Patient | Normal | Urgent
    'max_position_size': 100,  # Maximum shares per position
    'max_daily_trades': 10,  # Maximum trades per day
}

# Track daily trades
daily_trades = {'count': 0, 'date': datetime.now().date()}


def verify_auth(request):
    """Verify authentication token"""
    auth_header = request.headers.get('Authorization', '')
    
    if not auth_header.startswith('Bearer '):
        return False
    
    token = auth_header[7:]  # Remove 'Bearer '
    return token == CONFIG['auth_token']


def reset_daily_counter():
    """Reset daily trade counter if new day"""
    global daily_trades
    today = datetime.now().date()
    
    if daily_trades['date'] != today:
        daily_trades = {'count': 0, 'date': today}
        logger.info("Daily trade counter reset")


async def execute_trade(signal: Dict) -> Dict:
    """
    Execute trade via Interactive Brokers
    
    Returns:
        Dict with execution status
    """
    try:
        # Connect to IB
        ib = IB()
        await ib.connectAsync(
            CONFIG['ib_host'],
            CONFIG['ib_port'],
            clientId=CONFIG['ib_client_id']
        )
        
        # Determine order size
        quantity = min(signal['quantity'], CONFIG['max_position_size'])
        
        result = {}
        
        # Use algorithmic trading if enabled
        if CONFIG['use_algo_trading']:
            # Map algo name to enum
            algo_map = {
                'TWAP': AlgoType.TWAP,
                'VWAP': AlgoType.VWAP,
                'ICEBERG': AlgoType.ICEBERG,
                'ADAPTIVE': AlgoType.ADAPTIVE,
                'DARK_ICE': AlgoType.DARK_ICE,
                'ARRIVAL_PRICE': AlgoType.ARRIVAL_PRICE,
                'PERCENT_VOL': AlgoType.PERCENT_VOL,
            }
            
            algo_type = algo_map.get(
                CONFIG['default_algo'],
                AlgoType.ADAPTIVE
            )
            
            logger.info(
                f"Executing with {algo_type.value} algo: "
                f"{signal['action']} {quantity} {signal['symbol']}"
            )
            
            # Execute with algo
            result = await execute_signal_with_algo(
                ib,
                {
                    'symbol': signal['symbol'],
                    'action': signal['action'],
                    'quantity': quantity
                },
                algo_type=algo_type,
                urgency=CONFIG['algo_urgency']
            )
            
        else:
            # Traditional limit order execution
            contract = Stock(signal['symbol'], 'SMART', 'USD')
            await ib.qualifyContractsAsync(contract)
            
            # Create order
            if signal['action'] == 'BUY':
                limit_price = signal['price'] * 1.001  # 0.1% above
                order = LimitOrder('BUY', quantity, limit_price)
            else:
                limit_price = signal['price'] * 0.999  # 0.1% below
                order = LimitOrder('SELL', quantity, limit_price)
            
            # Place order
            trade = ib.placeOrder(contract, order)
            
            # Wait for fill (max 60 seconds)
            await asyncio.sleep(2)
            
            # Check status
            status = trade.orderStatus.status
            filled = trade.orderStatus.filled
            
            result = {
                'success': status in ['Filled', 'PartiallyFilled'],
                'status': status,
                'filled': filled,
                'symbol': signal['symbol'],
                'action': signal['action'],
                'price': limit_price
            }
        
        # Disconnect
        ib.disconnect()
        
        return result
    
    except Exception as e:
        logger.error(f"Trade execution failed: {e}")
        return {
            'success': False,
            'error': str(e)
        }


@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Webhook endpoint for receiving trading signals
    
    Expected payload:
    {
        "timestamp": "2026-01-24T10:30:00",
        "source": "breakout_scanner",
        "signals": [
            {
                "symbol": "AAPL",
                "action": "BUY",
                "price": 185.50,
                "stop_loss": 180.00,
                "take_profit": 195.00,
                "quantity": 100,
                "mode": "swing",
                "quality": "PREMIUM"
            }
        ]
    }
    """
    # Verify authentication
    if not verify_auth(request):
        logger.warning(f"Unauthorized webhook attempt from {request.remote_addr}")
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Get payload
    try:
        payload = request.get_json()
    except Exception as e:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    # Validate payload
    if not payload or 'signals' not in payload:
        return jsonify({'error': 'Missing signals'}), 400
    
    logger.info(f"Received webhook with {len(payload['signals'])} signals")
    
    # Reset daily counter if needed
    reset_daily_counter()
    
    # Process signals
    results = []
    
    for signal in payload['signals']:
        # Log signal
        logger.info(
            f"Signal: {signal['action']} {signal['symbol']} @ ${signal['price']} "
            f"(Quality: {signal.get('quality', 'N/A')})"
        )
        
        # Check daily limit
        if daily_trades['count'] >= CONFIG['max_daily_trades']:
            logger.warning("Daily trade limit reached")
            results.append({
                'symbol': signal['symbol'],
                'status': 'SKIPPED',
                'reason': 'Daily limit reached'
            })
            continue
        
        # Execute if auto-trading enabled
        if CONFIG['auto_execute']:
            logger.info(f"Executing trade for {signal['symbol']}...")
            
            # Execute trade (async)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(execute_trade(signal))
            loop.close()
            
            if result['success']:
                daily_trades['count'] += 1
                logger.info(f"✓ Trade executed: {signal['symbol']}")
            else:
                logger.error(f"✗ Trade failed: {signal['symbol']}")
            
            results.append(result)
        else:
            # Dry run mode
            logger.info(f"[DRY RUN] Would execute: {signal['action']} {signal['symbol']}")
            results.append({
                'symbol': signal['symbol'],
                'status': 'DRY_RUN',
                'message': 'Auto-execute disabled'
            })
    
    # Save to log file
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'payload': payload,
        'results': results
    }
    
    with open('webhook_log.jsonl', 'a') as f:
        f.write(json.dumps(log_entry) + '\n')
    
    return jsonify({
        'status': 'processed',
        'signals_received': len(payload['signals']),
        'results': results,
        'daily_trades': daily_trades['count']
    })


@app.route('/status', methods=['GET'])
def status():
    """Health check endpoint"""
    return jsonify({
        'status': 'running',
        'auto_execute': CONFIG['auto_execute'],
        'daily_trades': daily_trades['count'],
        'max_daily_trades': CONFIG['max_daily_trades']
    })


@app.route('/config', methods=['GET'])
def get_config():
    """Get current configuration (auth required)"""
    if not verify_auth(request):
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Return config without sensitive data
    return jsonify({
        'auto_execute': CONFIG['auto_execute'],
        'max_position_size': CONFIG['max_position_size'],
        'max_daily_trades': CONFIG['max_daily_trades'],
        'daily_trades': daily_trades
    })


if __name__ == '__main__':
    print("=" * 60)
    print("Webhook Server for Automated Trading")
    print("=" * 60)
    print(f"Auto-execute: {CONFIG['auto_execute']}")
    print(f"Algo Trading: {CONFIG['use_algo_trading']}")
    if CONFIG['use_algo_trading']:
        print(f"Default Algo: {CONFIG['default_algo']}")
        print(f"Urgency: {CONFIG['algo_urgency']}")
    print(f"IB Port: {CONFIG['ib_port']} (Paper trading)")
    print(f"Max position size: {CONFIG['max_position_size']}")
    print(f"Max daily trades: {CONFIG['max_daily_trades']}")
    print("")
    print("⚠️  WARNING: This is an example server.")
    print("    Always test thoroughly before enabling auto-execute!")
    print("=" * 60)
    print("")
    
    # Run server
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False  # Set to True for development
    )
