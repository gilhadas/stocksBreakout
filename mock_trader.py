"""
Mock Trading & Simulation Module
Allows testing all features without real trades or IB connection
"""

import logging
import asyncio
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import random
import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict
from pathlib import Path
import json

logger = logging.getLogger(__name__)


@dataclass
class MockTrade:
    """Represents a simulated trade"""
    trade_id: int
    symbol: str
    action: str
    quantity: int
    entry_price: float
    entry_time: datetime
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    stop_loss: float = 0
    take_profit: float = 0
    pnl: float = 0
    pnl_pct: float = 0
    status: str = 'OPEN'  # OPEN | CLOSED | STOPPED
    
    def to_dict(self):
        d = asdict(self)
        d['entry_time'] = self.entry_time.isoformat()
        if self.exit_time:
            d['exit_time'] = self.exit_time.isoformat()
        return d


class MockIBConnection:
    """
    Mock IB connection for testing without real connection
    Simulates market data and order execution
    """
    
    def __init__(self, mode: str = 'realistic'):
        """
        Args:
            mode: 'realistic' | 'optimistic' | 'pessimistic'
        """
        self.mode = mode
        self.connected = False
        self.client_id = 999
        
        # Simulated prices
        self.prices = {}
        self._init_prices()
    
    def _init_prices(self):
        """Initialize simulated prices"""
        # Common stocks with realistic prices
        self.prices = {
            'AAPL': 185.50,
            'MSFT': 420.30,
            'GOOGL': 145.20,
            'AMZN': 178.50,
            'NVDA': 520.30,
            'TSLA': 245.10,
            'META': 515.80,
            'SPY': 485.20,
        }
    
    async def connectAsync(self, host: str, port: int, clientId: int):
        """Mock connection"""
        await asyncio.sleep(0.1)  # Simulate connection delay
        self.connected = True
        self.client_id = clientId
        logger.info(f"🔧 Mock IB connection established (mode: {self.mode})")
    
    def disconnect(self):
        """Mock disconnect"""
        self.connected = False
        logger.info("🔧 Mock IB disconnected")
    
    def reqMarketDataType(self, data_type: int):
        """Mock market data type"""
        pass
    
    async def qualifyContractsAsync(self, contract):
        """Mock contract qualification"""
        await asyncio.sleep(0.05)
        return contract
    
    async def reqHistoricalDataAsync(self, contract, endDateTime, durationStr, 
                                     barSizeSetting, whatToShow, useRTH, formatDate):
        """Mock historical data"""
        await asyncio.sleep(0.1)
        
        symbol = contract.symbol
        
        # Generate mock bars
        bars = self._generate_mock_bars(symbol, durationStr, barSizeSetting)
        return bars
    
    def _generate_mock_bars(self, symbol: str, duration: str, bar_size: str):
        """Generate realistic mock price bars"""
        # Parse duration
        if 'D' in duration:
            days = int(duration.split()[0])
        else:
            days = 30
        
        # Get base price
        base_price = self.prices.get(symbol, 100.0)
        
        # Generate bars
        bars = []
        current_date = datetime.now() - timedelta(days=days)
        
        # Number of bars based on bar size
        if '1 day' in bar_size or 'day' in bar_size:
            num_bars = days
            time_delta = timedelta(days=1)
        elif 'min' in bar_size:
            mins = int(bar_size.split()[0])
            num_bars = (days * 390) // mins  # 390 trading mins per day
            time_delta = timedelta(minutes=mins)
        else:
            num_bars = days
            time_delta = timedelta(days=1)
        
        price = base_price
        
        from collections import namedtuple
        Bar = namedtuple('Bar', ['date', 'open', 'high', 'low', 'close', 'volume', 'barCount', 'average'])
        
        for i in range(num_bars):
            # Add realistic price movement
            daily_volatility = 0.02  # 2% daily volatility
            change = np.random.normal(0, daily_volatility)
            price = price * (1 + change)
            
            # Create OHLCV
            high = price * (1 + abs(np.random.normal(0, 0.005)))
            low = price * (1 - abs(np.random.normal(0, 0.005)))
            open_price = price * (1 + np.random.normal(0, 0.002))
            close = price
            volume = int(np.random.uniform(1000000, 5000000))
            
            bar = Bar(
                date=current_date,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                barCount=1,
                average=price
            )
            
            bars.append(bar)
            current_date += time_delta
        
        return bars
    
    def reqMktData(self, contract, genericTickList, snapshot, regulatorySnapshot):
        """Mock market data request"""
        symbol = contract.symbol
        price = self.prices.get(symbol, 100.0)
        
        # Create mock ticker
        ticker = type('Ticker', (), {
            'bid': price * 0.9995,
            'ask': price * 1.0005,
            'last': price
        })()
        
        return ticker
    
    def cancelMktData(self, contract):
        """Mock cancel market data"""
        pass
    
    def reqMktDepth(self, contract, numRows):
        """Mock market depth request"""
        pass
    
    def cancelMktDepth(self, contract):
        """Mock cancel depth"""
        pass
    
    def ticker(self, contract):
        """Mock ticker data"""
        symbol = contract.symbol
        price = self.prices.get(symbol, 100.0)
        
        # Create mock DOM (depth of market)
        bids = []
        asks = []
        
        for i in range(10):
            bid_price = price - (i * 0.01)
            ask_price = price + (i * 0.01)
            bid_size = int(np.random.uniform(100, 1000))
            ask_size = int(np.random.uniform(100, 1000))
            
            bids.append(type('DOMLevel', (), {
                'price': bid_price,
                'size': bid_size
            })())
            
            asks.append(type('DOMLevel', (), {
                'price': ask_price,
                'size': ask_size
            })())
        
        ticker = type('Ticker', (), {
            'domBids': bids,
            'domAsks': asks,
            'bid': price * 0.9995,
            'ask': price * 1.0005
        })()
        
        return ticker
    
    def placeOrder(self, contract, order):
        """Mock order placement"""
        symbol = contract.symbol
        
        # Create mock trade
        trade = type('Trade', (), {
            'order': order,
            'orderStatus': type('OrderStatus', (), {
                'status': 'Submitted',
                'filled': 0,
                'remaining': order.totalQuantity,
                'avgFillPrice': 0
            })()
        })()
        
        # Simulate fill based on mode
        asyncio.create_task(self._simulate_fill(trade, symbol))
        
        return trade
    
    async def _simulate_fill(self, trade, symbol: str):
        """Simulate order fill"""
        await asyncio.sleep(1)  # Simulate execution time
        
        price = self.prices.get(symbol, 100.0)
        
        # Simulate slippage based on mode
        if self.mode == 'optimistic':
            slippage = 0.0001  # 0.01%
        elif self.mode == 'pessimistic':
            slippage = 0.005   # 0.5%
        else:  # realistic
            slippage = 0.001   # 0.1%
        
        if trade.order.action == 'BUY':
            fill_price = price * (1 + slippage)
        else:
            fill_price = price * (1 - slippage)
        
        # Update status
        trade.orderStatus.status = 'Filled'
        trade.orderStatus.filled = trade.order.totalQuantity
        trade.orderStatus.remaining = 0
        trade.orderStatus.avgFillPrice = fill_price
        
        logger.info(f"🔧 Mock fill: {trade.order.action} {trade.order.totalQuantity} {symbol} @ ${fill_price:.2f}")
    
    def cancelOrder(self, order):
        """Mock order cancellation"""
        pass


class MockTrader:
    """
    Mock trading engine for testing strategies
    Tracks P&L, win rate, etc.
    """
    
    def __init__(self, initial_capital: float = 100000):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades: List[MockTrade] = []
        self.open_positions: Dict[str, MockTrade] = {}
        self.trade_id_counter = 1
    
    def enter_trade(self, symbol: str, action: str, quantity: int, 
                   price: float, stop_loss: float, take_profit: float) -> MockTrade:
        """Enter a mock trade"""
        trade = MockTrade(
            trade_id=self.trade_id_counter,
            symbol=symbol,
            action=action,
            quantity=quantity,
            entry_price=price,
            entry_time=datetime.now(),
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        
        self.trade_id_counter += 1
        self.trades.append(trade)
        self.open_positions[symbol] = trade
        
        # Deduct capital
        cost = price * quantity
        self.capital -= cost
        
        logger.info(
            f"📊 Mock trade opened: {action} {quantity} {symbol} @ ${price:.2f} "
            f"(SL: ${stop_loss:.2f}, TP: ${take_profit:.2f})"
        )
        
        return trade
    
    def exit_trade(self, symbol: str, exit_price: float, reason: str = 'Manual'):
        """Exit a mock trade"""
        if symbol not in self.open_positions:
            logger.warning(f"No open position for {symbol}")
            return
        
        trade = self.open_positions[symbol]
        trade.exit_price = exit_price
        trade.exit_time = datetime.now()
        
        # Calculate P&L
        if trade.action == 'BUY':
            pnl = (exit_price - trade.entry_price) * trade.quantity
        else:
            pnl = (trade.entry_price - exit_price) * trade.quantity
        
        trade.pnl = pnl
        trade.pnl_pct = (pnl / (trade.entry_price * trade.quantity)) * 100
        
        # Determine status
        if reason == 'Stop Loss':
            trade.status = 'STOPPED'
        else:
            trade.status = 'CLOSED'
        
        # Add capital back
        proceeds = exit_price * trade.quantity
        self.capital += proceeds
        
        # Remove from open positions
        del self.open_positions[symbol]
        
        logger.info(
            f"📊 Mock trade closed: {symbol} @ ${exit_price:.2f} | "
            f"P&L: ${pnl:+.2f} ({trade.pnl_pct:+.2f}%) | Reason: {reason}"
        )
        
        return trade
    
    def check_stops(self, current_prices: Dict[str, float]):
        """Check if any positions hit stops"""
        for symbol, trade in list(self.open_positions.items()):
            if symbol not in current_prices:
                continue
            
            current_price = current_prices[symbol]
            
            # Check stop loss
            if trade.action == 'BUY' and current_price <= trade.stop_loss:
                self.exit_trade(symbol, trade.stop_loss, 'Stop Loss')
            elif trade.action == 'SELL' and current_price >= trade.stop_loss:
                self.exit_trade(symbol, trade.stop_loss, 'Stop Loss')
            
            # Check take profit
            elif trade.action == 'BUY' and current_price >= trade.take_profit:
                self.exit_trade(symbol, trade.take_profit, 'Take Profit')
            elif trade.action == 'SELL' and current_price <= trade.take_profit:
                self.exit_trade(symbol, trade.take_profit, 'Take Profit')
    
    def get_stats(self) -> Dict:
        """Get trading statistics"""
        closed_trades = [t for t in self.trades if t.status != 'OPEN']
        
        if not closed_trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'total_pnl': 0,
                'total_return': 0
            }
        
        winning_trades = [t for t in closed_trades if t.pnl > 0]
        losing_trades = [t for t in closed_trades if t.pnl <= 0]
        
        total_pnl = sum(t.pnl for t in closed_trades)
        total_return = (self.capital - self.initial_capital) / self.initial_capital * 100
        
        return {
            'total_trades': len(closed_trades),
            'open_positions': len(self.open_positions),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': len(winning_trades) / len(closed_trades) * 100,
            'total_pnl': total_pnl,
            'total_return': total_return,
            'avg_win': np.mean([t.pnl for t in winning_trades]) if winning_trades else 0,
            'avg_loss': np.mean([t.pnl for t in losing_trades]) if losing_trades else 0,
            'initial_capital': self.initial_capital,
            'current_capital': self.capital,
        }
    
    def save_report(self, filename: str = 'mock_trading_report.json'):
        """Save trading report"""
        stats = self.get_stats()
        
        report = {
            'stats': stats,
            'trades': [t.to_dict() for t in self.trades]
        }
        
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"📊 Mock trading report saved to {filename}")
        return report


class SimulationMode:
    """
    Historical simulation mode
    Tests strategy on historical data
    """
    
    def __init__(self, start_date: str, end_date: str, initial_capital: float = 100000):
        """
        Args:
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
            initial_capital: Starting capital
        """
        self.start_date = pd.to_datetime(start_date)
        self.end_date = pd.to_datetime(end_date)
        self.current_date = self.start_date
        self.trader = MockTrader(initial_capital)
        self.daily_equity = []
    
    def run_simulation(self, signals: List[Dict]):
        """
        Run simulation with historical signals
        
        Args:
            signals: List of signal dicts with 'date', 'symbol', 'action', etc.
        """
        logger.info(f"🔄 Running simulation: {self.start_date.date()} to {self.end_date.date()}")
        
        # Sort signals by date
        signals_df = pd.DataFrame(signals)
        signals_df['date'] = pd.to_datetime(signals_df['date'])
        signals_df = signals_df.sort_values('date')
        
        # Simulate day by day
        current_date = self.start_date
        
        while current_date <= self.end_date:
            # Get signals for this date
            day_signals = signals_df[signals_df['date'].dt.date == current_date.date()]
            
            # Execute signals
            for _, signal in day_signals.iterrows():
                self.trader.enter_trade(
                    symbol=signal['symbol'],
                    action=signal['action'],
                    quantity=signal.get('quantity', 100),
                    price=signal['price'],
                    stop_loss=signal.get('stop_loss', signal['price'] * 0.95),
                    take_profit=signal.get('take_profit', signal['price'] * 1.10)
                )
            
            # Track equity
            self.daily_equity.append({
                'date': current_date,
                'equity': self.trader.capital + self._calculate_open_position_value()
            })
            
            current_date += timedelta(days=1)
        
        # Close all remaining positions
        self._close_all_positions()
        
        # Generate report
        return self.generate_report()
    
    def _calculate_open_position_value(self) -> float:
        """Calculate value of open positions"""
        # In simulation, assume positions maintain entry price
        value = sum(
            t.entry_price * t.quantity 
            for t in self.trader.open_positions.values()
        )
        return value
    
    def _close_all_positions(self):
        """Close all open positions at end of simulation"""
        for symbol, trade in list(self.trader.open_positions.items()):
            # Close at entry price (neutral)
            self.trader.exit_trade(symbol, trade.entry_price, 'Simulation End')
    
    def generate_report(self) -> Dict:
        """Generate simulation report"""
        stats = self.trader.get_stats()
        
        # Calculate additional metrics
        equity_df = pd.DataFrame(self.daily_equity)
        
        if len(equity_df) > 0:
            equity_df['returns'] = equity_df['equity'].pct_change()
            
            sharpe = (
                equity_df['returns'].mean() / equity_df['returns'].std() * np.sqrt(252)
                if equity_df['returns'].std() > 0 else 0
            )
            
            max_drawdown = (
                (equity_df['equity'] / equity_df['equity'].cummax() - 1).min() * 100
            )
        else:
            sharpe = 0
            max_drawdown = 0
        
        report = {
            **stats,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_drawdown,
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'days_simulated': (self.end_date - self.start_date).days
        }
        
        logger.info("📊 Simulation Report:")
        logger.info(f"  Total Return: {stats['total_return']:+.2f}%")
        logger.info(f"  Win Rate: {stats['win_rate']:.1f}%")
        logger.info(f"  Sharpe Ratio: {sharpe:.2f}")
        logger.info(f"  Max Drawdown: {max_drawdown:.2f}%")
        
        return report
