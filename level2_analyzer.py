"""
Level 2 (Market Depth) Analysis Module
Analyzes bid/ask depth and order flow for higher probability entries
"""

import logging
from typing import Optional, Dict, Tuple
import asyncio
from ib_insync import IB, Stock

logger = logging.getLogger(__name__)


class Level2Analyzer:
    """Analyzes Level 2 market depth data"""
    
    def __init__(self, ib_connection: IB):
        self.ib = ib_connection
    
    async def get_market_depth(self, symbol: str, exchange: str = 'SMART', 
                               currency: str = 'USD', num_rows: int = 10) -> Optional[Dict]:
        """
        Get Level 2 market depth data
        
        Returns:
            Dict with bid/ask depth analysis or None
        """
        try:
            contract = Stock(symbol, exchange, currency)
            await self.ib.qualifyContractsAsync(contract)
            
            # Request market depth
            self.ib.reqMktDepth(contract, numRows=num_rows)
            
            # Wait for data to populate
            await asyncio.sleep(1.5)
            
            # Get ticker with depth
            ticker = self.ib.ticker(contract)
            
            if not ticker.domBids or not ticker.domAsks:
                self.ib.cancelMktDepth(contract)
                return None
            
            # Analyze depth
            analysis = self._analyze_depth(ticker.domBids, ticker.domAsks)
            
            # Cancel depth subscription
            self.ib.cancelMktDepth(contract)
            
            return analysis
        
        except Exception as e:
            logger.debug(f"Failed to get market depth for {symbol}: {e}")
            return None
    
    def _analyze_depth(self, bids, asks) -> Dict:
        """
        Analyze bid/ask depth
        
        Returns analysis with:
        - bid_liquidity: Total bid size
        - ask_liquidity: Total ask size
        - bid_ask_ratio: Ratio of bid to ask liquidity
        - imbalance: Market pressure (positive = bullish, negative = bearish)
        - top_bid_size: Size at best bid
        - top_ask_size: Size at best ask
        - support_strength: How strong is bid support (0-100)
        - resistance_strength: How strong is ask resistance (0-100)
        """
        # Calculate total liquidity
        bid_liquidity = sum(b.size for b in bids)
        ask_liquidity = sum(a.size for a in asks)
        
        # Bid/Ask ratio
        bid_ask_ratio = bid_liquidity / ask_liquidity if ask_liquidity > 0 else 0
        
        # Market imbalance (-100 to +100)
        total = bid_liquidity + ask_liquidity
        imbalance = ((bid_liquidity - ask_liquidity) / total * 100) if total > 0 else 0
        
        # Top of book
        top_bid_size = bids[0].size if bids else 0
        top_ask_size = asks[0].size if asks else 0
        
        # Support/Resistance strength
        # Compare top 3 levels to total
        top3_bids = sum(b.size for b in bids[:3]) if len(bids) >= 3 else bid_liquidity
        top3_asks = sum(a.size for a in asks[:3]) if len(asks) >= 3 else ask_liquidity
        
        support_strength = (top3_bids / bid_liquidity * 100) if bid_liquidity > 0 else 0
        resistance_strength = (top3_asks / ask_liquidity * 100) if ask_liquidity > 0 else 0
        
        # Price levels
        best_bid = bids[0].price if bids else 0
        best_ask = asks[0].price if asks else 0
        
        return {
            'bid_liquidity': int(bid_liquidity),
            'ask_liquidity': int(ask_liquidity),
            'bid_ask_ratio': round(bid_ask_ratio, 2),
            'imbalance': round(imbalance, 2),
            'top_bid_size': int(top_bid_size),
            'top_ask_size': int(top_ask_size),
            'support_strength': round(support_strength, 1),
            'resistance_strength': round(resistance_strength, 1),
            'best_bid': best_bid,
            'best_ask': best_ask,
            'spread': round(best_ask - best_bid, 2) if best_bid and best_ask else 0
        }
    
    def evaluate_entry_quality(self, depth_analysis: Dict) -> Tuple[str, str]:
        """
        Evaluate entry quality based on Level 2 data
        
        Returns:
            (quality_score, reason)
            quality_score: EXCELLENT | GOOD | FAIR | POOR
        """
        if not depth_analysis:
            return 'UNKNOWN', 'No depth data available'
        
        imbalance = depth_analysis['imbalance']
        bid_ask_ratio = depth_analysis['bid_ask_ratio']
        support_strength = depth_analysis['support_strength']
        
        # EXCELLENT: Strong buying pressure
        if imbalance > 30 and bid_ask_ratio > 1.5 and support_strength > 60:
            return 'EXCELLENT', f'Strong bid support ({imbalance:.0f}% imbalance)'
        
        # GOOD: Moderate buying pressure
        if imbalance > 15 and bid_ask_ratio > 1.2:
            return 'GOOD', f'Good bid/ask ratio ({bid_ask_ratio:.2f})'
        
        # FAIR: Balanced or slight buying
        if imbalance > 0:
            return 'FAIR', 'Neutral depth'
        
        # POOR: Selling pressure
        return 'POOR', f'Selling pressure detected ({imbalance:.0f}% imbalance)'
    
    def check_breakout_confirmation(self, depth_analysis: Dict, price: float) -> bool:
        """
        Check if Level 2 confirms breakout
        
        Strong breakouts should have:
        - Buying pressure (positive imbalance)
        - More bids than asks
        - Price near or above best ask
        """
        if not depth_analysis:
            return True  # Don't reject if no depth data
        
        imbalance = depth_analysis['imbalance']
        best_ask = depth_analysis['best_ask']
        
        # Confirm if:
        # 1. Positive imbalance (more buyers)
        # 2. Price is at or above best ask (breaking through resistance)
        return imbalance > 0 and price >= best_ask * 0.999  # 0.1% tolerance
