"""
Algorithmic Trading Module
Implements various algo trading strategies for order execution
"""

import logging
import asyncio
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from ib_insync import IB, Stock, Order, LimitOrder, MarketOrder
from enum import Enum

logger = logging.getLogger(__name__)


class AlgoType(Enum):
    """Available algorithm types"""
    TWAP = "TWAP"  # Time Weighted Average Price
    VWAP = "VWAP"  # Volume Weighted Average Price
    ICEBERG = "Iceberg"  # Hide order size
    ADAPTIVE = "Adaptive"  # IB's adaptive algo
    DARK_ICE = "DarkIce"  # Seek dark pools
    ARRIVAL_PRICE = "ArrivalPx"  # Minimize market impact
    PERCENT_VOL = "PctVol"  # Participate in volume
    
    
class AlgoTrader:
    """Handles algorithmic order execution via IB"""
    
    def __init__(self, ib_connection: IB):
        self.ib = ib_connection
        self.active_orders = {}
        self.execution_log = []
    
    async def execute_with_algo(
        self,
        symbol: str,
        action: str,
        quantity: int,
        algo_type: AlgoType = AlgoType.ADAPTIVE,
        urgency: str = 'Normal',
        **kwargs
    ) -> Dict:
        """
        Execute trade using specified algorithm
        
        Args:
            symbol: Stock symbol
            action: BUY or SELL
            quantity: Number of shares
            algo_type: Algorithm to use
            urgency: Patient | Normal | Urgent (for Adaptive)
            **kwargs: Additional algo-specific parameters
        
        Returns:
            Execution result dict
        """
        try:
            # Create contract
            contract = Stock(symbol, 'SMART', 'USD')
            await self.ib.qualifyContractsAsync(contract)
            
            # Create algo order
            order = self._create_algo_order(
                action, quantity, algo_type, urgency, **kwargs
            )
            
            # Place order
            trade = self.ib.placeOrder(contract, order)
            
            # Log
            logger.info(
                f"Placed {algo_type.value} order: {action} {quantity} {symbol}"
            )
            
            # Track order
            self.active_orders[trade.order.orderId] = {
                'symbol': symbol,
                'action': action,
                'quantity': quantity,
                'algo': algo_type.value,
                'trade': trade,
                'start_time': datetime.now()
            }
            
            return {
                'success': True,
                'order_id': trade.order.orderId,
                'symbol': symbol,
                'action': action,
                'quantity': quantity,
                'algo': algo_type.value
            }
        
        except Exception as e:
            logger.error(f"Algo order failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _create_algo_order(
        self,
        action: str,
        quantity: int,
        algo_type: AlgoType,
        urgency: str,
        **kwargs
    ) -> Order:
        """Create order with algorithm strategy"""
        
        if algo_type == AlgoType.TWAP:
            return self._create_twap_order(action, quantity, **kwargs)
        
        elif algo_type == AlgoType.VWAP:
            return self._create_vwap_order(action, quantity, **kwargs)
        
        elif algo_type == AlgoType.ICEBERG:
            return self._create_iceberg_order(action, quantity, **kwargs)
        
        elif algo_type == AlgoType.ADAPTIVE:
            return self._create_adaptive_order(action, quantity, urgency)
        
        elif algo_type == AlgoType.DARK_ICE:
            return self._create_darkice_order(action, quantity)
        
        elif algo_type == AlgoType.ARRIVAL_PRICE:
            return self._create_arrival_price_order(action, quantity, urgency)
        
        elif algo_type == AlgoType.PERCENT_VOL:
            return self._create_percent_vol_order(action, quantity, **kwargs)
        
        else:
            # Default to adaptive
            return self._create_adaptive_order(action, quantity, urgency)
    
    def _create_twap_order(self, action: str, quantity: int, **kwargs) -> Order:
        """
        Time Weighted Average Price
        Slices order evenly over specified time period
        """
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = 'LMT'
        order.lmtPrice = kwargs.get('limit_price', 0)  # 0 = market price
        
        # TWAP algo params
        order.algoStrategy = 'Twap'
        order.algoParams = [
            ('Strategy Type', kwargs.get('strategy_type', 'Marketable')),
            ('Start Time', kwargs.get('start_time', '')),
            ('End Time', kwargs.get('end_time', '')),
            ('Allow Past End Time', kwargs.get('allow_past_end', '1')),
        ]
        
        order.tif = 'DAY'
        return order
    
    def _create_vwap_order(self, action: str, quantity: int, **kwargs) -> Order:
        """
        Volume Weighted Average Price
        Matches historical volume distribution
        """
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = 'LMT'
        order.lmtPrice = kwargs.get('limit_price', 0)
        
        # VWAP algo params
        order.algoStrategy = 'Vwap'
        order.algoParams = [
            ('No Take Liq', kwargs.get('no_take_liq', '0')),
            ('Start Time', kwargs.get('start_time', '')),
            ('End Time', kwargs.get('end_time', '')),
            ('Allow Past End Time', kwargs.get('allow_past_end', '1')),
            ('Max Pct Vol', kwargs.get('max_pct_vol', '0.1')),  # 10% of volume
        ]
        
        order.tif = 'DAY'
        return order
    
    def _create_iceberg_order(self, action: str, quantity: int, **kwargs) -> Order:
        """
        Iceberg Order
        Shows only small portion of total size
        """
        display_size = kwargs.get('display_size', min(100, quantity // 10))
        
        order = LimitOrder(
            action=action,
            totalQuantity=quantity,
            lmtPrice=kwargs.get('limit_price', 0)
        )
        
        # Iceberg-specific
        order.displaySize = display_size
        order.tif = 'GTC'  # Good Till Canceled
        
        return order
    
    def _create_adaptive_order(self, action: str, quantity: int, urgency: str) -> Order:
        """
        Adaptive Algorithm (IB's smart algo)
        Automatically adjusts to market conditions
        """
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = 'LMT'
        order.lmtPrice = 0  # Will adapt
        
        # Adaptive algo params
        order.algoStrategy = 'Adaptive'
        order.algoParams = [
            ('adaptivePriority', urgency)  # Patient | Normal | Urgent
        ]
        
        order.tif = 'DAY'
        return order
    
    def _create_darkice_order(self, action: str, quantity: int) -> Order:
        """
        DarkIce Algorithm
        Seeks liquidity in dark pools before going to lit markets
        """
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = 'LMT'
        order.lmtPrice = 0
        
        order.algoStrategy = 'DarkIce'
        order.algoParams = [
            ('Display Size', '100'),
        ]
        
        order.tif = 'DAY'
        return order
    
    def _create_arrival_price_order(self, action: str, quantity: int, urgency: str) -> Order:
        """
        Arrival Price Algorithm
        Minimizes market impact while executing
        """
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = 'LMT'
        order.lmtPrice = 0
        
        # Map urgency to risk aversion
        risk_aversion = {
            'Patient': 'High',
            'Normal': 'Medium',
            'Urgent': 'Low'
        }.get(urgency, 'Medium')
        
        order.algoStrategy = 'ArrivalPx'
        order.algoParams = [
            ('Risk Aversion', risk_aversion),
            ('Start Time', ''),
            ('End Time', ''),
        ]
        
        order.tif = 'DAY'
        return order
    
    def _create_percent_vol_order(self, action: str, quantity: int, **kwargs) -> Order:
        """
        Percentage of Volume Algorithm
        Maintains % of market volume
        """
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = 'LMT'
        order.lmtPrice = kwargs.get('limit_price', 0)
        
        order.algoStrategy = 'PctVol'
        order.algoParams = [
            ('Pct Vol', str(kwargs.get('pct_vol', 10))),  # % of volume
            ('Start Time', kwargs.get('start_time', '')),
            ('End Time', kwargs.get('end_time', '')),
            ('No Take Liq', kwargs.get('no_take_liq', '0')),
        ]
        
        order.tif = 'DAY'
        return order
    
    async def monitor_execution(self, order_id: int, timeout: int = 3600) -> Dict:
        """
        Monitor algo order execution
        
        Args:
            order_id: Order ID to monitor
            timeout: Max seconds to wait
        
        Returns:
            Execution summary
        """
        if order_id not in self.active_orders:
            return {'error': 'Order not found'}
        
        order_info = self.active_orders[order_id]
        trade = order_info['trade']
        start = datetime.now()
        
        logger.info(f"Monitoring order {order_id} for {order_info['symbol']}")
        
        while (datetime.now() - start).seconds < timeout:
            await asyncio.sleep(5)  # Check every 5 seconds
            
            status = trade.orderStatus.status
            filled = trade.orderStatus.filled
            remaining = trade.orderStatus.remaining
            avg_price = trade.orderStatus.avgFillPrice
            
            logger.debug(
                f"Order {order_id}: {status} | "
                f"Filled: {filled}/{order_info['quantity']} @ {avg_price}"
            )
            
            # Check if complete
            if status in ['Filled', 'Cancelled', 'Inactive']:
                result = {
                    'order_id': order_id,
                    'symbol': order_info['symbol'],
                    'action': order_info['action'],
                    'requested': order_info['quantity'],
                    'filled': filled,
                    'remaining': remaining,
                    'avg_price': avg_price,
                    'status': status,
                    'algo': order_info['algo'],
                    'duration': (datetime.now() - order_info['start_time']).seconds
                }
                
                # Log execution
                self.execution_log.append(result)
                
                # Clean up
                del self.active_orders[order_id]
                
                logger.info(
                    f"Order {order_id} {status}: "
                    f"{filled}/{order_info['quantity']} @ ${avg_price}"
                )
                
                return result
        
        # Timeout
        return {
            'error': 'Timeout',
            'order_id': order_id,
            'status': trade.orderStatus.status,
            'filled': trade.orderStatus.filled
        }
    
    def get_execution_stats(self) -> Dict:
        """Get execution statistics"""
        if not self.execution_log:
            return {'total_orders': 0}
        
        total = len(self.execution_log)
        filled = sum(1 for x in self.execution_log if x['status'] == 'Filled')
        
        avg_fill_rate = sum(
            x['filled'] / x['requested'] for x in self.execution_log
        ) / total
        
        avg_duration = sum(x['duration'] for x in self.execution_log) / total
        
        return {
            'total_orders': total,
            'filled_orders': filled,
            'fill_rate': f"{filled/total*100:.1f}%",
            'avg_fill_percentage': f"{avg_fill_rate*100:.1f}%",
            'avg_duration_seconds': int(avg_duration)
        }
    
    async def cancel_order(self, order_id: int) -> bool:
        """Cancel active order"""
        if order_id not in self.active_orders:
            return False
        
        trade = self.active_orders[order_id]['trade']
        self.ib.cancelOrder(trade.order)
        
        logger.info(f"Cancelled order {order_id}")
        return True
    
    def get_active_orders(self) -> List[Dict]:
        """Get list of active algo orders"""
        return [
            {
                'order_id': oid,
                'symbol': info['symbol'],
                'action': info['action'],
                'quantity': info['quantity'],
                'algo': info['algo'],
                'status': info['trade'].orderStatus.status,
                'filled': info['trade'].orderStatus.filled,
                'elapsed': (datetime.now() - info['start_time']).seconds
            }
            for oid, info in self.active_orders.items()
        ]


# Helper function for webhook server integration
async def execute_signal_with_algo(
    ib: IB,
    signal: Dict,
    algo_type: AlgoType = AlgoType.ADAPTIVE,
    urgency: str = 'Normal'
) -> Dict:
    """
    Execute trading signal using algo
    
    Example usage in webhook_server.py:
        trader = AlgoTrader(ib)
        result = await execute_signal_with_algo(
            ib, signal, AlgoType.VWAP, 'Normal'
        )
    """
    trader = AlgoTrader(ib)
    
    result = await trader.execute_with_algo(
        symbol=signal['symbol'],
        action=signal['action'],
        quantity=signal['quantity'],
        algo_type=algo_type,
        urgency=urgency
    )
    
    if result['success']:
        # Monitor execution
        execution = await trader.monitor_execution(result['order_id'])
        return execution
    
    return result
