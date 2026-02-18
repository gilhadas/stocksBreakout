"""
Core Portfolio Manager
JSON-based virtual portfolio with position tracking, P&L, and performance snapshots.
Storage: scanner_output/portfolio/portfolio.json
Snapshots: scanner_output/portfolio/snapshots/snap_YYYY-MM-DD.json
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

_NY_TZ = ZoneInfo('America/New_York')

import numpy as np

from config import OUTPUT_DIR, PORTFOLIO, QUALITY_SIZING

logger = logging.getLogger(__name__)

PORTFOLIO_DIR = Path(OUTPUT_DIR) / 'portfolio'
SNAPSHOTS_DIR = PORTFOLIO_DIR / 'snapshots'
PORTFOLIO_FILE = PORTFOLIO_DIR / 'portfolio.json'


def _atomic_write(path: Path, data: dict):
    """Write JSON atomically: write to .tmp then os.replace()."""
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, default=str))
    os.replace(tmp, path)


class Portfolio:
    """
    Virtual portfolio that persists to the filesystem.

    Key operations:
        add_position(signal, shares, sector)
        close_position(symbol, exit_price)
        update_prices(price_map)
        daily_snapshot()
        get_summary() / get_performance()
    """

    def __init__(self, capital: float = None):
        PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        if PORTFOLIO_FILE.exists():
            self._data = json.loads(PORTFOLIO_FILE.read_text())
        else:
            init_capital = capital or PORTFOLIO['initial_capital']
            self._data = {
                'initial_capital': init_capital,
                'cash': init_capital,
                'positions': {},       # {symbol: position_dict}
                'trade_history': [],    # closed trades
                'created': datetime.now(_NY_TZ).isoformat(),
                'last_updated': datetime.now(_NY_TZ).isoformat(),
            }
            self._save()

        logger.debug(f"Portfolio loaded: cash=${self._data['cash']:,.2f}, "
                     f"{len(self._data['positions'])} open positions")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _save(self):
        self._data['last_updated'] = datetime.now(_NY_TZ).isoformat()
        _atomic_write(PORTFOLIO_FILE, self._data)

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------
    def add_position(self, signal: dict, shares: int = None,
                     sector: str = '') -> dict:
        """
        Add a position from a scanner signal.

        Args:
            signal: signal dict from scanner (Symbol, Price, Stop, Target, Quality, Mode, R:R)
            shares: number of shares (auto-sized if None)
            sector: sector string (optional)

        Returns:
            position dict that was added
        """
        symbol = signal.get('Symbol', signal.get('symbol', ''))
        if not symbol:
            raise ValueError("Signal must have 'Symbol' key")

        if symbol in self._data['positions']:
            raise ValueError(f"{symbol} already in portfolio")

        price = float(signal.get('Price', signal.get('price', 0)))
        if price <= 0:
            raise ValueError(f"Invalid price for {symbol}: {price}")

        quality = signal.get('Quality', 'STANDARD')
        mode = signal.get('Mode', signal.get('mode', 'swing'))

        if shares is None:
            shares = self.position_size_for_signal(signal)

        # Hard cap: 10% of total portfolio value per transaction
        total_value = self._data['cash'] + sum(
            p['current_price'] * p['shares']
            for p in self._data['positions'].values()
        )
        max_cost = total_value * 0.10
        max_shares_cap = int(max_cost / price)
        if shares > max_shares_cap:
            shares = max_shares_cap

        cost = price * shares
        if cost > self._data['cash']:
            max_shares = int(self._data['cash'] / price)
            if max_shares <= 0:
                raise ValueError(f"Insufficient cash (${self._data['cash']:,.2f}) for {symbol} @ ${price}")
            shares = max_shares
            cost = price * shares

        self._data['cash'] -= cost

        position = {
            'symbol': symbol,
            'shares': shares,
            'entry_price': price,
            'entry_date': datetime.now(_NY_TZ).strftime('%Y-%m-%d'),
            'stop': float(signal.get('Stop', price * 0.95)),
            'target': float(signal.get('Target', price * 1.10)),
            'mode': mode,
            'quality': quality,
            'current_price': price,
            'sector': sector,
            'support': price * 0.95,   # updated on price refresh
            'resistance': price * 1.05,
            'rr': float(signal.get('R:R', 0)),
            'unrealized_pnl': 0.0,
            'unrealized_pnl_pct': 0.0,
            'cost_basis': cost,
        }

        self._data['positions'][symbol] = position
        self._save()

        logger.info(f"Portfolio: added {shares} shares of {symbol} @ ${price:.2f} "
                    f"(${cost:,.2f}) [{quality}]")
        return position

    def close_position(self, symbol: str, exit_price: float) -> dict:
        """
        Close a position and record the trade.

        Returns:
            trade history dict
        """
        if symbol not in self._data['positions']:
            raise ValueError(f"{symbol} not in portfolio")

        pos = self._data['positions'].pop(symbol)
        proceeds = exit_price * pos['shares']
        pnl = proceeds - pos['cost_basis']
        pnl_pct = (pnl / pos['cost_basis']) * 100 if pos['cost_basis'] > 0 else 0

        self._data['cash'] += proceeds

        trade = {
            'symbol': symbol,
            'shares': pos['shares'],
            'entry_price': pos['entry_price'],
            'entry_date': pos['entry_date'],
            'exit_price': exit_price,
            'exit_date': datetime.now(_NY_TZ).strftime('%Y-%m-%d'),
            'pnl': round(pnl, 2),
            'pnl_pct': round(pnl_pct, 2),
            'mode': pos['mode'],
            'quality': pos['quality'],
            'sector': pos.get('sector', ''),
            'hold_days': (datetime.now(_NY_TZ).replace(tzinfo=None) - datetime.strptime(pos['entry_date'], '%Y-%m-%d')).days,
        }
        self._data['trade_history'].append(trade)
        self._save()

        logger.info(f"Portfolio: closed {symbol} @ ${exit_price:.2f} | "
                    f"P&L: ${pnl:+,.2f} ({pnl_pct:+.1f}%)")
        return trade

    def update_prices(self, price_map: Dict[str, float] = None):
        """
        Update current prices for all positions.
        If price_map is None, fetches via yfinance.
        """
        if price_map is None:
            price_map = self._fetch_current_prices()

        for symbol, pos in self._data['positions'].items():
            current = price_map.get(symbol)
            if current is None:
                continue

            pos['current_price'] = current
            pnl = (current - pos['entry_price']) * pos['shares']
            pnl_pct = ((current - pos['entry_price']) / pos['entry_price']) * 100
            pos['unrealized_pnl'] = round(pnl, 2)
            pos['unrealized_pnl_pct'] = round(pnl_pct, 2)

        # Refresh support/resistance from recent data
        self._update_support_resistance(price_map)
        self._save()

    def _fetch_current_prices(self) -> Dict[str, float]:
        """Fetch current prices for all open positions via yfinance."""
        prices = {}
        symbols = list(self._data['positions'].keys())
        if not symbols:
            return prices

        try:
            import yfinance as yf
            tickers_str = ' '.join(s.replace(' ', '-') for s in symbols)
            data = yf.download(tickers_str, period='1d', progress=False)
            if data is not None and not data.empty:
                if len(symbols) == 1:
                    prices[symbols[0]] = float(data['Close'].iloc[-1])
                else:
                    for sym in symbols:
                        yf_sym = sym.replace(' ', '-')
                        try:
                            prices[sym] = float(data['Close'][yf_sym].iloc[-1])
                        except (KeyError, IndexError):
                            pass
        except Exception as e:
            logger.debug(f"Price fetch failed: {e}")

        return prices

    def _update_support_resistance(self, price_map: Dict[str, float]):
        """Update support/resistance using 20-day high/low via yfinance."""
        symbols = [s for s in self._data['positions'] if s in price_map]
        if not symbols:
            return

        try:
            import yfinance as yf
            for sym in symbols:
                yf_sym = sym.replace(' ', '-')
                hist = yf.Ticker(yf_sym).history(period='1mo')
                if hist is not None and len(hist) >= 5:
                    self._data['positions'][sym]['support'] = round(float(hist['Low'].tail(20).min()), 2)
                    self._data['positions'][sym]['resistance'] = round(float(hist['High'].tail(20).max()), 2)
        except Exception as e:
            logger.debug(f"Support/resistance update failed: {e}")

    # ------------------------------------------------------------------
    # Snapshots & Performance
    # ------------------------------------------------------------------
    def daily_snapshot(self):
        """Save today's equity snapshot for WTD/YTD calculations."""
        summary = self.get_summary()
        snap = {
            'date': datetime.now(_NY_TZ).strftime('%Y-%m-%d'),
            'total_value': summary['total_value'],
            'cash': summary['cash'],
            'market_value': summary['market_value'],
            'total_pnl': summary['total_pnl'],
            'total_pnl_pct': summary['total_pnl_pct'],
            'positions_count': len(self._data['positions']),
        }

        snap_file = SNAPSHOTS_DIR / f"snap_{snap['date']}.json"
        _atomic_write(snap_file, snap)
        logger.info(f"Portfolio snapshot saved: ${snap['total_value']:,.2f}")
        return snap

    def get_summary(self) -> dict:
        """Get portfolio summary: cash, market value, P&L, daily/WTD/YTD."""
        positions = self._data['positions']
        market_value = sum(
            p['current_price'] * p['shares'] for p in positions.values()
        )
        total_value = self._data['cash'] + market_value
        initial = self._data['initial_capital']
        total_pnl = total_value - initial
        total_pnl_pct = (total_pnl / initial) * 100 if initial > 0 else 0

        # Period P&L from snapshots
        daily_pnl = self._period_pnl(days=1)
        wtd_pnl = self._period_pnl(days=7)
        ytd_pnl = self._ytd_pnl()

        return {
            'cash': round(self._data['cash'], 2),
            'market_value': round(market_value, 2),
            'total_value': round(total_value, 2),
            'total_pnl': round(total_pnl, 2),
            'total_pnl_pct': round(total_pnl_pct, 2),
            'daily_pnl': daily_pnl,
            'wtd_pnl': wtd_pnl,
            'ytd_pnl': ytd_pnl,
            'positions_count': len(positions),
            'initial_capital': initial,
        }

    def _period_pnl(self, days: int) -> float:
        """Calculate P&L change over N days from snapshots."""
        target_date = datetime.now(_NY_TZ).replace(tzinfo=None) - timedelta(days=days)
        # Find nearest snapshot on or before target date
        for i in range(days + 5):
            check = target_date - timedelta(days=i)
            snap_file = SNAPSHOTS_DIR / f"snap_{check.strftime('%Y-%m-%d')}.json"
            if snap_file.exists():
                snap = json.loads(snap_file.read_text())
                current_value = self._data['cash'] + sum(
                    p['current_price'] * p['shares']
                    for p in self._data['positions'].values()
                )
                return round(current_value - snap['total_value'], 2)
        return 0.0

    def _ytd_pnl(self) -> float:
        """Calculate year-to-date P&L."""
        year_start = datetime(datetime.now(_NY_TZ).year, 1, 1)
        # Find first snapshot of the year
        for i in range(10):
            check = year_start + timedelta(days=i)
            snap_file = SNAPSHOTS_DIR / f"snap_{check.strftime('%Y-%m-%d')}.json"
            if snap_file.exists():
                snap = json.loads(snap_file.read_text())
                current_value = self._data['cash'] + sum(
                    p['current_price'] * p['shares']
                    for p in self._data['positions'].values()
                )
                return round(current_value - snap['total_value'], 2)
        return 0.0

    def get_performance(self) -> dict:
        """
        Calculate performance metrics from snapshots.
        Returns: equity_curve, sharpe, max_dd, win_rate, avg_hold_days
        """
        # Load all snapshots sorted by date
        snaps = []
        for f in sorted(SNAPSHOTS_DIR.glob('snap_*.json')):
            try:
                snaps.append(json.loads(f.read_text()))
            except Exception:
                continue

        equity_curve = [(s['date'], s['total_value']) for s in snaps]

        # Daily returns
        values = [s['total_value'] for s in snaps]
        if len(values) < 2:
            returns = []
        else:
            returns = [(values[i] - values[i-1]) / values[i-1]
                       for i in range(1, len(values)) if values[i-1] > 0]

        # Sharpe ratio (annualized, assume 252 trading days)
        sharpe = 0.0
        if returns:
            mean_r = np.mean(returns)
            std_r = np.std(returns) if len(returns) > 1 else 1e-10
            sharpe = round((mean_r / max(std_r, 1e-10)) * np.sqrt(252), 2)

        # Max drawdown
        max_dd = 0.0
        peak = 0
        for v in values:
            peak = max(peak, v)
            dd = (peak - v) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        max_dd = round(max_dd * 100, 2)

        # Win rate from trade history
        trades = self._data.get('trade_history', [])
        wins = sum(1 for t in trades if t['pnl'] > 0)
        win_rate = round((wins / len(trades)) * 100, 1) if trades else 0.0

        # Average hold days
        hold_days = [t.get('hold_days', 0) for t in trades]
        avg_hold = round(np.mean(hold_days), 1) if hold_days else 0.0

        return {
            'equity_curve': equity_curve,
            'sharpe': sharpe,
            'max_drawdown_pct': max_dd,
            'win_rate': win_rate,
            'total_trades': len(trades),
            'avg_hold_days': avg_hold,
            'daily_returns': returns[-30:] if returns else [],  # last 30 for chart
        }

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------
    def position_size_for_signal(self, signal: dict) -> int:
        """
        Calculate shares to buy for a signal based on quality tier and risk.

        Uses PORTFOLIO config: max_position_pct, max_risk_pct
        Uses QUALITY_SIZING multiplier per tier
        """
        price = float(signal.get('Price', signal.get('price', 0)))
        stop = float(signal.get('Stop', price * 0.95))
        quality = signal.get('Quality', 'STANDARD')

        if price <= 0:
            return 0

        total_value = self._data['cash'] + sum(
            p['current_price'] * p['shares']
            for p in self._data['positions'].values()
        )

        # Position size from risk budget
        risk_per_share = max(price - stop, price * 0.01)
        risk_budget = total_value * PORTFOLIO['max_risk_pct']
        shares_from_risk = int(risk_budget / risk_per_share)

        # Hard cap: every transaction max 10% of total portfolio value
        max_alloc = total_value * 0.10
        shares_from_alloc = int(max_alloc / price)

        # Take the smaller of the two
        shares = min(shares_from_risk, shares_from_alloc)

        # Can't exceed available cash
        max_from_cash = int(self._data['cash'] / price)
        shares = min(shares, max_from_cash)

        # Check max concurrent positions
        max_pos = PORTFOLIO.get('max_concurrent_positions', 10)
        if len(self._data['positions']) >= max_pos:
            logger.warning(f"Max positions ({max_pos}) reached")
            return 0

        return max(0, shares)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    def get_positions(self) -> List[dict]:
        """Return list of open positions."""
        return list(self._data['positions'].values())

    def get_trade_history(self) -> List[dict]:
        """Return list of closed trades."""
        return self._data.get('trade_history', [])

    def get_position(self, symbol: str) -> Optional[dict]:
        """Get a single open position by symbol."""
        return self._data['positions'].get(symbol)

    def update_stop(self, symbol: str, new_stop: float) -> dict:
        """Update stop loss for an open position."""
        if symbol not in self._data['positions']:
            raise ValueError(f"{symbol} not in portfolio")
        pos = self._data['positions'][symbol]
        old_stop = pos['stop']
        pos['stop'] = new_stop
        self._save()
        logger.info(f"Portfolio: {symbol} stop updated: ${old_stop:.2f} -> ${new_stop:.2f}")
        return pos

    def update_target(self, symbol: str, new_target: float) -> dict:
        """Update target price for an open position."""
        if symbol not in self._data['positions']:
            raise ValueError(f"{symbol} not in portfolio")
        pos = self._data['positions'][symbol]
        old_target = pos['target']
        pos['target'] = new_target
        self._save()
        logger.info(f"Portfolio: {symbol} target updated: ${old_target:.2f} -> ${new_target:.2f}")
        return pos

    def _fetch_single_price(self, symbol: str) -> Optional[float]:
        """Fetch current price for a single symbol via yfinance."""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol.replace(' ', '-'))
            data = ticker.history(period='1d')
            if data is not None and not data.empty:
                return float(data['Close'].iloc[-1])
        except Exception as e:
            logger.debug(f"Price fetch failed for {symbol}: {e}")
        return None

    def buy_at_market(self, symbol: str, shares: int, stop: float, target: float,
                      mode: str = 'swing', quality: str = 'STANDARD') -> dict:
        """
        Buy a position at current market price (fetched via yfinance).
        Convenience wrapper around add_position() for manual entries.
        """
        price = self._fetch_single_price(symbol)
        if price is None or price <= 0:
            raise ValueError(f"Could not fetch current price for {symbol}")

        signal = {
            'Symbol': symbol,
            'Price': price,
            'Stop': stop,
            'Target': target,
            'Quality': quality,
            'Mode': mode,
            'R:R': round((target - price) / max(price - stop, 0.01), 2),
        }

        sector = ''
        try:
            from sentiment import get_sector_for_ticker
            sector = get_sector_for_ticker(symbol)
        except Exception:
            pass

        return self.add_position(signal, shares, sector=sector)

    def get_positions_as_exit_format(self) -> List[dict]:
        """
        Convert portfolio positions to the format expected by exit evaluator and monitor.
        Returns list of dicts: {symbol, mode, entry, stop, target, timeframe}
        """
        from config import MODES
        result = []
        for pos in self._data['positions'].values():
            mode = pos.get('mode', 'swing')
            result.append({
                'symbol': pos['symbol'],
                'mode': mode,
                'entry': pos['entry_price'],
                'stop': pos['stop'],
                'target': pos['target'],
                'timeframe': MODES.get(mode, MODES['swing'])['default_timeframe'],
            })
        return result

    @property
    def cash(self) -> float:
        return self._data['cash']

    @property
    def positions_count(self) -> int:
        return len(self._data['positions'])

    # ------------------------------------------------------------------
    # Daily email report
    # ------------------------------------------------------------------
    def send_daily_report(self):
        """Generate and send daily portfolio status email via Notifier."""
        from notifier import Notifier

        # Update prices first
        self.update_prices()
        self.daily_snapshot()

        summary = self.get_summary()
        perf = self.get_performance()
        positions = self.get_positions()

        # Build report body
        lines = []
        lines.append(f"Portfolio Daily Report — {datetime.now(_NY_TZ).strftime('%Y-%m-%d')}")
        lines.append("=" * 55)
        lines.append("")

        # Summary
        lines.append("ACCOUNT SUMMARY")
        lines.append(f"  Total Value:    ${summary['total_value']:>12,.2f}")
        lines.append(f"  Cash:           ${summary['cash']:>12,.2f}")
        lines.append(f"  Market Value:   ${summary['market_value']:>12,.2f}")
        lines.append(f"  Total P&L:      ${summary['total_pnl']:>+12,.2f}  ({summary['total_pnl_pct']:+.2f}%)")
        lines.append(f"  Daily P&L:      ${summary['daily_pnl']:>+12,.2f}")
        lines.append(f"  WTD P&L:        ${summary['wtd_pnl']:>+12,.2f}")
        lines.append(f"  YTD P&L:        ${summary['ytd_pnl']:>+12,.2f}")
        lines.append("")

        # Performance
        lines.append("PERFORMANCE")
        lines.append(f"  Sharpe Ratio:   {perf['sharpe']:.2f}")
        lines.append(f"  Max Drawdown:   {perf['max_drawdown_pct']:.2f}%")
        lines.append(f"  Win Rate:       {perf['win_rate']:.1f}%  ({perf['total_trades']} trades)")
        lines.append(f"  Avg Hold Days:  {perf['avg_hold_days']:.1f}")
        lines.append("")

        # Open positions
        lines.append(f"OPEN POSITIONS ({len(positions)})")
        lines.append("-" * 55)
        if positions:
            lines.append(f"{'Symbol':<8} {'Qty':>5} {'Entry':>8} {'Current':>8} {'P&L%':>8} {'Quality':<8}")
            for p in sorted(positions, key=lambda x: x.get('unrealized_pnl_pct', 0), reverse=True):
                lines.append(
                    f"{p['symbol']:<8} {p['shares']:>5} "
                    f"${p['entry_price']:>7.2f} ${p['current_price']:>7.2f} "
                    f"{p.get('unrealized_pnl_pct', 0):>+7.2f}% {p['quality']:<8}"
                )
        else:
            lines.append("  (no open positions)")
        lines.append("")

        # Recent closed trades (last 5)
        history = self.get_trade_history()
        if history:
            recent = history[-5:]
            lines.append(f"RECENT TRADES (last {len(recent)} of {len(history)})")
            lines.append("-" * 55)
            lines.append(f"{'Symbol':<8} {'Entry':>8} {'Exit':>8} {'P&L':>10} {'Days':>5}")
            for t in reversed(recent):
                lines.append(
                    f"{t['symbol']:<8} ${t['entry_price']:>7.2f} ${t['exit_price']:>7.2f} "
                    f"${t['pnl']:>+9.2f} {t.get('hold_days', 0):>5}"
                )
            lines.append("")

        body = "\n".join(lines)

        # Send via Notifier
        notifier = Notifier()
        pnl_icon = "+" if summary['total_pnl'] >= 0 else ""
        subject = (
            f"Portfolio Report: ${summary['total_value']:,.0f} "
            f"({pnl_icon}{summary['total_pnl_pct']:.1f}%) | "
            f"{len(positions)} positions"
        )
        notifier.send_all(subject=subject, message=body)
        logger.info(f"Daily report sent: {subject}")
        return body
