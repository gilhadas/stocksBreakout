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

from config import MAX_HOLD_BARS, WIN_PROBABILITY

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
    mode: str = 'swing'
    hold_days: int = 0
    win_probability: float = 0.50
    
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
    
    def __init__(self, initial_capital: float = 100000, max_position_pct: float = 0.05, max_risk_pct: float = 0.01):
        """
        Args:
            initial_capital: Starting capital
            max_position_pct: Max % of capital per position (default 5%)
            max_risk_pct: Max % of capital to risk per trade (default 1%)
        """
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.max_position_pct = max_position_pct
        self.max_risk_pct = max_risk_pct
        self.trades: List[MockTrade] = []
        self.open_positions: Dict[int, MockTrade] = {}  # trade_id -> MockTrade
        self.trade_id_counter = 1
    
    # Position size multipliers by signal quality
    QUALITY_MULTIPLIERS = {
        'PREMIUM': 3.0,   # Up to 15% of capital
        'HIGH': 2.0,      # Up to 10% of capital
        'STANDARD': 1.0,  # 5% of capital (default)
    }

    def calculate_position_size(self, price: float, stop_loss: float,
                                quality: str = 'STANDARD',
                                win_probability: float = 0.50) -> int:
        """
        Calculate position size based on risk management rules, signal quality,
        and win probability.

        PREMIUM signals get 3x base size, HIGH gets 2x, STANDARD gets 1x.
        Win probability adjusts: HIGH prob = 1.2x, LOW prob = 0.7x.
        """
        multiplier = self.QUALITY_MULTIPLIERS.get(quality, 1.0)

        # Win probability sizing adjustment
        wp_cfg = WIN_PROBABILITY
        if win_probability >= wp_cfg['high_threshold']:
            multiplier *= wp_cfg['high_size_mult']
        elif win_probability < wp_cfg['low_threshold']:
            multiplier *= wp_cfg['low_size_mult']

        # Calculate max position value (scaled by quality + win prob)
        max_position_value = self.capital * self.max_position_pct * multiplier
        max_shares_by_position = int(max_position_value / price)

        # Calculate max shares based on risk limit (scaled by quality + win prob)
        risk_per_share = abs(price - stop_loss)
        if risk_per_share > 0:
            max_risk_amount = self.capital * self.max_risk_pct * multiplier
            max_shares_by_risk = int(max_risk_amount / risk_per_share)
        else:
            max_shares_by_risk = max_shares_by_position

        # Take the smaller of the two limits
        quantity = min(max_shares_by_position, max_shares_by_risk)

        # Ensure at least 1 share if we have enough capital
        if quantity < 1 and self.capital >= price:
            quantity = 1

        return quantity
    
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
        self.open_positions[trade.trade_id] = trade  # Use trade_id instead of symbol
        
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
        # Find trade by symbol (open_positions is keyed by trade_id)
        trade_id = None
        trade = None
        for tid, t in list(self.open_positions.items()):
            if t.symbol == symbol:
                trade_id = tid
                trade = t
                break
        
        if trade is None:
            logger.warning(f"No open position for {symbol}")
            return
        trade.exit_price = exit_price
        trade.exit_time = datetime.now()
        
        # Calculate P&L on remaining shares
        if trade.action == 'BUY':
            remaining_pnl = (exit_price - trade.entry_price) * trade.quantity
        else:
            remaining_pnl = (trade.entry_price - exit_price) * trade.quantity

        # Add to any accumulated scale-out PnL
        trade.pnl += remaining_pnl
        original_cost = trade.entry_price * (trade.quantity + getattr(trade, '_scaled_qty', 0))
        trade.pnl_pct = (trade.pnl / max(original_cost, 1e-6)) * 100
        
        # Determine status
        if reason == 'Stop Loss':
            trade.status = 'STOPPED'
        else:
            trade.status = 'CLOSED'
        
        # Add capital back
        proceeds = exit_price * trade.quantity
        self.capital += proceeds
        
        # Remove from open positions using trade_id
        del self.open_positions[trade_id]
        
        logger.info(
            f"📊 Mock trade closed: {symbol} @ ${exit_price:.2f} | "
            f"P&L: ${trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%) | Reason: {reason}"
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
    
    def __init__(self, start_date: str, end_date: str, initial_capital: float = 100000,
                 max_position_pct: float = 0.05, max_risk_pct: float = 0.01,
                 use_trailing_stop: bool = False, trailing_stop_atr_mult: float = 2.0,
                 trailing_stop_activation_pct: float = 0.0,
                 spy_hedge_enabled: bool = False, spy_allocation_pct: float = 0.0):
        """
        Args:
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
            initial_capital: Starting capital
            max_position_pct: Max % of capital per position (default 5%)
            max_risk_pct: Max % of capital to risk per trade (default 1%)
            use_trailing_stop: Enable ATR-based trailing stop (default False)
            trailing_stop_atr_mult: ATR multiplier for trailing stop (default 2.0)
            trailing_stop_activation_pct: Only activate trailing stop after this % profit (default 0.0)
            spy_hedge_enabled: Enable SPY hedge allocation (default False)
            spy_allocation_pct: Percentage of capital allocated to SPY (default 0.0)
        """
        self.start_date = pd.to_datetime(start_date)
        self.end_date = pd.to_datetime(end_date)
        self.current_date = self.start_date
        self.trader = MockTrader(initial_capital, max_position_pct, max_risk_pct)
        self.daily_equity = []
        self.use_trailing_stop = use_trailing_stop
        self.trailing_stop_atr_mult = trailing_stop_atr_mult
        self.trailing_stop_activation_pct = trailing_stop_activation_pct
        self.historical_data = {}  # Store historical data for each symbol
        # SPY hedge
        self.spy_hedge_enabled = spy_hedge_enabled
        self.spy_allocation_pct = spy_allocation_pct
        self.spy_shares = 0
        self.spy_entry_price = 0.0
    
    def run_simulation(self, signals: List[Dict], end_prices: Dict[str, float] = None,
                       historical_data: Dict[str, pd.DataFrame] = None):
        """
        Run simulation with historical signals
        
        Args:
            signals: List of signal dicts with 'date', 'symbol', 'action', etc.
            end_prices: Dict of symbol -> end price for closing positions at simulation end
            historical_data: Dict of symbol -> DataFrame with historical prices for trailing stops
        """
        logger.info(f"🔄 Running simulation: {self.start_date.date()} to {self.end_date.date()}")

        # Store end prices and historical data for closing positions
        self.end_prices = end_prices or {}
        self.historical_data = historical_data or {}

        # --- SPY HEDGE: Buy at start ---
        if self.spy_hedge_enabled and self.spy_allocation_pct > 0:
            spy_df = self.historical_data.get('SPY')
            if spy_df is not None and len(spy_df) > 0:
                spy_start = spy_df[spy_df.index >= self.start_date]
                if len(spy_start) > 0:
                    self.spy_entry_price = float(spy_start.iloc[0]['close'])
                    spy_capital = self.trader.capital * self.spy_allocation_pct
                    self.spy_shares = int(spy_capital / self.spy_entry_price)
                    if self.spy_shares > 0:
                        self.trader.capital -= self.spy_shares * self.spy_entry_price
                        logger.info(
                            f"🛡️ SPY hedge: bought {self.spy_shares} shares @ "
                            f"${self.spy_entry_price:.2f} ({self.spy_allocation_pct*100:.0f}% allocation)"
                        )

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
                # Calculate position size based on risk management, quality, and win prob
                quality = signal.get('quality', 'STANDARD')
                win_prob = signal.get('win_probability', 0.50)
                quantity = self.trader.calculate_position_size(
                    price=signal['price'],
                    stop_loss=signal.get('stop_loss', signal['price'] * 0.95),
                    quality=quality,
                    win_probability=win_prob
                )

                # Skip if quantity is 0 (insufficient capital)
                if quantity == 0:
                    logger.warning(f"Skipping {signal['symbol']} - insufficient capital for position")
                    continue

                trade = self.trader.enter_trade(
                    symbol=signal['symbol'],
                    action=signal['action'],
                    quantity=quantity,
                    price=signal['price'],
                    stop_loss=signal.get('stop_loss', signal['price'] * 0.95),
                    take_profit=signal.get('take_profit', signal['price'] * 1.10)
                )
                # Set V3 fields
                trade.mode = signal.get('mode', 'swing')
                trade.win_probability = win_prob
            
            # Check for exits (Stop Loss / Take Profit) - ALWAYS RUN
            if len(self.trader.open_positions) > 0:
                self._check_exits(current_date)
            
            # Check and update trailing stops if enabled
            if self.use_trailing_stop and len(self.trader.open_positions) > 0:
                self._update_trailing_stops(current_date)
            
            # Track equity
            self.daily_equity.append({
                'date': current_date,
                'equity': self.trader.capital + self._calculate_open_position_value(current_date)
            })
            
            current_date += timedelta(days=1)
        
        # Close all remaining positions
        self._close_all_positions()
        
        # Generate report
        return self.generate_report()
    
    def _calculate_open_position_value(self, current_date=None) -> float:
        """Calculate value of open positions including SPY hedge"""
        value = sum(
            t.entry_price * t.quantity
            for t in self.trader.open_positions.values()
        )

        # Include SPY hedge value
        if self.spy_hedge_enabled and self.spy_shares > 0:
            spy_price = self.spy_entry_price  # Default
            spy_df = self.historical_data.get('SPY')
            if spy_df is not None and current_date is not None:
                spy_bar = spy_df[spy_df.index.date == current_date.date()]
                if len(spy_bar) > 0:
                    spy_price = float(spy_bar.iloc[-1]['close'])
            value += self.spy_shares * spy_price

        return value
    
    def _check_exits(self, current_date: pd.Timestamp):
        """Check if any positions hit Stop Loss or Take Profit"""
        for trade_id, trade in list(self.trader.open_positions.items()):
            symbol = trade.symbol
            
            # Skip if trade was just entered today (avoid same-day exit)
            if trade.entry_time.date() == current_date.date():
                continue
            
            # Get historical data for this symbol
            if symbol not in self.historical_data:
                continue
            
            df = self.historical_data[symbol]
            
            # Get current price daily bar
            current_bar = df[df.index.date == current_date.date()]
            if len(current_bar) == 0:
                continue
            
            current_high = float(current_bar.iloc[-1]['high'])
            current_low = float(current_bar.iloc[-1]['low'])
            current_open = float(current_bar.iloc[-1]['open'])
            current_close = float(current_bar.iloc[-1]['close'])

            # --- MAX HOLD PERIOD EXIT (V3) ---
            days_held = (current_date - trade.entry_time).days
            max_hold = MAX_HOLD_BARS.get(trade.mode, 30)
            if max_hold > 0 and days_held >= max_hold:
                logger.info(f"⏰ Max hold ({max_hold}d) reached for {symbol}, exiting @ ${current_close:.2f}")
                self.trader.exit_trade(symbol, current_close, 'Max Hold Period')
                continue

            # Check if stop loss was hit
            if current_low <= trade.stop_loss:
                # Determine exit price (Stop Price or Open if gap down)
                exit_price = trade.stop_loss
                if current_open < trade.stop_loss:
                     exit_price = current_open

                logger.info(f"🛑 Stop hit for {symbol} @ ${exit_price:.2f}")
                self.trader.exit_trade(symbol, exit_price, 'Stop Loss')
                continue

            # Scale-out: partial exit at 1R profit
            risk = trade.entry_price - trade.stop_loss
            if risk > 0 and not getattr(trade, '_scaled_1r', False):
                target_1r = trade.entry_price + risk
                if current_high >= target_1r and trade.quantity >= 3:
                    partial_qty = trade.quantity // 3
                    trade.quantity -= partial_qty
                    trade._scaled_qty = getattr(trade, '_scaled_qty', 0) + partial_qty
                    pnl = (target_1r - trade.entry_price) * partial_qty
                    trade.pnl += pnl
                    self.trader.capital += target_1r * partial_qty
                    trade._scaled_1r = True
                    logger.debug(f"📊 Scale-out 1R: {symbol} sold {partial_qty} @ ${target_1r:.2f}")

            # Scale-out: partial exit at 2R profit
            if risk > 0 and getattr(trade, '_scaled_1r', False) and not getattr(trade, '_scaled_2r', False):
                target_2r = trade.entry_price + 2 * risk
                if current_high >= target_2r and trade.quantity >= 2:
                    partial_qty = trade.quantity // 2
                    trade.quantity -= partial_qty
                    trade._scaled_qty = getattr(trade, '_scaled_qty', 0) + partial_qty
                    pnl = (target_2r - trade.entry_price) * partial_qty
                    trade.pnl += pnl
                    self.trader.capital += target_2r * partial_qty
                    trade._scaled_2r = True
                    logger.debug(f"📊 Scale-out 2R: {symbol} sold {partial_qty} @ ${target_2r:.2f}")

            # Check if take profit was hit on remaining
            if current_high >= trade.take_profit:
                logger.info(f"🎯 Take profit hit for {symbol} @ ${trade.take_profit:.2f}")
                self.trader.exit_trade(symbol, trade.take_profit, 'Take Profit')

    def _update_trailing_stops(self, current_date: pd.Timestamp):
        """Update trailing stops based on current prices and ATR"""
        for trade_id, trade in list(self.trader.open_positions.items()):
            symbol = trade.symbol
            
            # Skip if trade was just entered today
            if trade.entry_time.date() == current_date.date():
                continue
                
            # Get historical data
            if symbol not in self.historical_data:
                continue
                
            df = self.historical_data[symbol]
            df_up_to_date = df[df.index <= current_date]
            
            if len(df_up_to_date) < 14:
                continue
            
            # Get current price
            current_bar = df_up_to_date[df_up_to_date.index.date == current_date.date()]
            if len(current_bar) == 0:
                continue
                
            current_price = float(current_bar.iloc[-1]['close'])
            
            # Check profit threshold activation
            profit_pct = (current_price - trade.entry_price) / trade.entry_price
            
            # Only update stop if profit condition met
            if profit_pct >= self.trailing_stop_activation_pct:
                # Calculate ATR (14-period)
                df_atr = df_up_to_date.tail(14).copy()
                df_atr['h-l'] = df_atr['high'] - df_atr['low']
                df_atr['h-pc'] = abs(df_atr['high'] - df_atr['close'].shift(1))
                df_atr['l-pc'] = abs(df_atr['low'] - df_atr['close'].shift(1))
                df_atr['tr'] = df_atr[['h-l', 'h-pc', 'l-pc']].max(axis=1)
                atr = df_atr['tr'].mean()
                
                # Calculate new trailing stop
                new_stop = current_price - (atr * self.trailing_stop_atr_mult)
                
                # Only update if new stop is higher than current stop
                if new_stop > trade.stop_loss:
                    old_stop = trade.stop_loss
                    trade.stop_loss = new_stop
                    logger.debug(f"📈 Trailing stop updated for {symbol}: ${old_stop:.2f} → ${new_stop:.2f} (ATR: ${atr:.2f})")
    
    def _close_all_positions(self):
        """Close all open positions at end of simulation (including SPY hedge)"""
        logger.info(f"📊 Closing {len(self.trader.open_positions)} remaining positions...")

        # Close SPY hedge first
        if self.spy_hedge_enabled and self.spy_shares > 0:
            spy_end_price = self.spy_entry_price  # Default
            spy_df = self.historical_data.get('SPY')
            if spy_df is not None and len(spy_df) > 0:
                spy_end_price = float(spy_df.iloc[-1]['close'])
            spy_pnl = (spy_end_price - self.spy_entry_price) * self.spy_shares
            self.trader.capital += self.spy_shares * spy_end_price
            logger.info(
                f"🛡️ SPY hedge closed: {self.spy_shares} shares @ ${spy_end_price:.2f} | "
                f"PnL: ${spy_pnl:+,.2f}"
            )
            self.spy_shares = 0

        for trade_id, trade in list(self.trader.open_positions.items()):
            symbol = trade.symbol

            # Get actual end price from simulation data if available
            end_price = trade.entry_price  # Default fallback

            # Try to get the actual end price from stored data
            if hasattr(self, 'end_prices') and symbol in self.end_prices:
                end_price = self.end_prices[symbol]

            # Close at actual end price
            self.trader.exit_trade(symbol, end_price, 'Simulation End')
    
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
