"""
IB Execution Bridge
====================
Connects V9-H signal detection → IB order placement.

Features:
- Bracket orders: entry (limit) + stop-loss + take-profit
- Paper/Live mode with explicit flag
- Kill switch: disable all new orders instantly
- Daily loss limit: halt trading if daily P&L < threshold
- Position sync: reconcile portfolio.json ↔ IB account
- Notification on every fill via Discord/Telegram

Safety:
- Default: PAPER mode (port 7497). --live required for real orders.
- Kill switch file: scanner_output/.kill_switch — touch to halt all orders
- Max daily loss: stops new entries when breached (does NOT close existing)
- All orders logged to scanner_output/ib_execution_log.json

Usage:
    from ib_executor import IBExecutor
    executor = IBExecutor(ib_connection, live=False)
    result = executor.place_bracket_order('AAPL', 150.0, 142.5, 165.0, 10)
    executor.sync_positions()  # reconcile with portfolio.json

CLI:
    python ib_executor.py --sync          # sync IB positions → portfolio.json
    python ib_executor.py --status        # show open orders + positions
    python ib_executor.py --kill          # activate kill switch
    python ib_executor.py --unkill        # deactivate kill switch
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

_NY_TZ = ZoneInfo('America/New_York')
_KILL_SWITCH_PATH = 'scanner_output/.kill_switch'
_EXEC_LOG_PATH = 'scanner_output/ib_execution_log.json'

logger = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

IB_EXEC_CONFIG = {
    'enabled': False,               # Master switch — set True to enable live execution
    'paper_mode': True,             # True = paper port (7497), False = live (7496)
    'max_daily_loss_pct': 0.05,     # Halt new entries if daily loss > 5% of capital
    'max_daily_loss_usd': 500,      # Hard USD cap: halt if daily loss > $500
    'max_position_value': 2000,     # Max single position value ($) for $10K portfolio
    'max_concurrent_orders': 3,     # Max pending orders at once
    'order_type': 'LMT',           # LMT or MKT for entries
    'limit_offset_pct': 0.002,     # Limit price = signal price * (1 + offset) for buys
    'use_bracket': True,            # Place bracket (entry + stop + target) as one OCA group
    'notify_on_fill': True,         # Send Discord/Telegram on every fill
    'log_all_orders': True,         # Persist every order to JSON log
}


# ── Kill Switch ───────────────────────────────────────────────────────────────

def is_kill_switch_active() -> bool:
    return Path(_KILL_SWITCH_PATH).exists()


def activate_kill_switch(reason: str = 'manual'):
    Path(_KILL_SWITCH_PATH).write_text(
        json.dumps({'activated': datetime.now(_NY_TZ).isoformat(), 'reason': reason})
    )
    logger.warning(f"KILL SWITCH ACTIVATED: {reason}")


def deactivate_kill_switch():
    p = Path(_KILL_SWITCH_PATH)
    if p.exists():
        p.unlink()
    logger.info("Kill switch deactivated")


# ── Execution Log ─────────────────────────────────────────────────────────────

def _load_exec_log() -> list:
    p = Path(_EXEC_LOG_PATH)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return []
    return []


def _save_exec_log(log: list):
    Path(_EXEC_LOG_PATH).write_text(json.dumps(log, indent=2, default=str))


def _log_order(entry: dict):
    log = _load_exec_log()
    entry['timestamp'] = datetime.now(_NY_TZ).isoformat()
    log.append(entry)
    _save_exec_log(log)


# ── Daily P&L Tracking ───────────────────────────────────────────────────────

def get_daily_pnl() -> float:
    """Sum realized P&L from today's executions."""
    today = date.today().isoformat()
    log = _load_exec_log()
    daily = sum(
        e.get('realized_pnl', 0)
        for e in log
        if e.get('timestamp', '').startswith(today) and e.get('status') == 'FILLED'
    )
    return daily


def check_daily_loss_limit(capital: float) -> tuple[bool, str]:
    """Returns (is_breached, reason)."""
    daily_pnl = get_daily_pnl()
    cfg = IB_EXEC_CONFIG

    if daily_pnl < -cfg['max_daily_loss_usd']:
        return True, f"Daily loss ${abs(daily_pnl):.0f} exceeds ${cfg['max_daily_loss_usd']} USD limit"

    if capital > 0 and abs(daily_pnl) / capital > cfg['max_daily_loss_pct']:
        pct = abs(daily_pnl) / capital * 100
        return True, f"Daily loss {pct:.1f}% exceeds {cfg['max_daily_loss_pct']*100:.0f}% limit"

    return False, ''


# ── IB Executor Class ─────────────────────────────────────────────────────────

class IBExecutor:
    """Bridges V9-H signals → IB bracket orders."""

    def __init__(self, ib_connection, live: bool = False):
        self.ib = ib_connection
        self.live = live
        self.pending_orders: Dict[int, dict] = {}  # order_id → info

    @property
    def is_connected(self) -> bool:
        return self.ib is not None and self.ib.isConnected()

    def _preflight_checks(self, capital: float = 0) -> tuple[bool, str]:
        """Run all safety checks before placing an order."""
        if not IB_EXEC_CONFIG['enabled']:
            return False, "IB execution disabled (IB_EXEC_CONFIG['enabled'] = False)"

        if is_kill_switch_active():
            return False, "Kill switch is active — no new orders"

        if not self.is_connected:
            return False, "Not connected to IB"

        breached, reason = check_daily_loss_limit(capital)
        if breached:
            return False, f"Daily loss limit breached: {reason}"

        if len(self.pending_orders) >= IB_EXEC_CONFIG['max_concurrent_orders']:
            return False, f"Max concurrent orders ({IB_EXEC_CONFIG['max_concurrent_orders']}) reached"

        return True, ''

    async def place_bracket_order(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        shares: int,
        quality: str = 'PREMIUM',
        mode: str = 'swing',
    ) -> dict:
        """
        Place a bracket order: entry + stop-loss + take-profit.

        Returns dict with order_id, status, and details.
        """
        from ib_insync import Stock, Order, LimitOrder, StopOrder

        # Load capital from auto_portfolio for loss limit check
        try:
            import auto_portfolio
            data = auto_portfolio.load()
            capital = data.get('capital', IB_EXEC_CONFIG.get('max_position_value', 10000))
        except Exception:
            capital = 10000

        ok, reason = self._preflight_checks(capital)
        if not ok:
            logger.warning(f"Order blocked for {symbol}: {reason}")
            return {'success': False, 'symbol': symbol, 'reason': reason}

        # ATR-adjusted position sizing
        try:
            from config import ATR_SIZING, QUALITY_SIZING
            if ATR_SIZING.get('enabled'):
                from auto_portfolio import _compute_atr_adjustment
                atr_adj = _compute_atr_adjustment(symbol)
                quality_mult = QUALITY_SIZING.get(quality, 1.0)
                max_pos = capital * 0.10 * quality_mult * atr_adj
                hard_cap = capital * ATR_SIZING.get('max_single_position_pct', 0.20)
                max_pos = min(max_pos, hard_cap, IB_EXEC_CONFIG['max_position_value'])
                adjusted_shares = max(1, int(max_pos / entry_price))
                if adjusted_shares < shares:
                    shares = adjusted_shares
                    logger.info(f"ATR-adjusted {symbol} to {shares} shares (atr_adj={atr_adj:.2f})")
        except Exception as e:
            logger.warning(f"ATR sizing failed for {symbol}: {e}")

        # Position value guard (hard cap)
        cost = entry_price * shares
        if cost > IB_EXEC_CONFIG['max_position_value']:
            shares = max(1, int(IB_EXEC_CONFIG['max_position_value'] / entry_price))
            cost = entry_price * shares
            logger.info(f"Reduced {symbol} to {shares} shares (${cost:.0f}) — max position value")

        try:
            contract = Stock(symbol, 'SMART', 'USD')
            await self.ib.qualifyContractsAsync(contract)

            # Entry order
            if IB_EXEC_CONFIG['order_type'] == 'LMT':
                limit_px = round(entry_price * (1 + IB_EXEC_CONFIG['limit_offset_pct']), 2)
                entry_order = LimitOrder('BUY', shares, limit_px)
            else:
                from ib_insync import MarketOrder
                entry_order = MarketOrder('BUY', shares)

            entry_order.tif = 'DAY'
            entry_order.transmit = not IB_EXEC_CONFIG['use_bracket']  # hold if bracket

            if IB_EXEC_CONFIG['use_bracket']:
                # OCA group for bracket
                oca_group = f"V9H_{symbol}_{datetime.now(_NY_TZ).strftime('%H%M%S')}"

                # Stop-loss order
                stop_order = StopOrder('SELL', shares, round(stop_price, 2))
                stop_order.parentId = 0  # will be set after entry placed
                stop_order.tif = 'GTC'
                stop_order.ocaGroup = oca_group
                stop_order.ocaType = 1  # cancel other on fill
                stop_order.transmit = False

                # Take-profit order
                tp_order = LimitOrder('SELL', shares, round(target_price, 2))
                tp_order.parentId = 0
                tp_order.tif = 'GTC'
                tp_order.ocaGroup = oca_group
                tp_order.ocaType = 1
                tp_order.transmit = True  # transmit all together

                # Place parent first
                entry_trade = self.ib.placeOrder(contract, entry_order)
                parent_id = entry_trade.order.orderId

                # Attach children
                stop_order.parentId = parent_id
                tp_order.parentId = parent_id

                stop_trade = self.ib.placeOrder(contract, stop_order)
                tp_trade = self.ib.placeOrder(contract, tp_order)

                order_info = {
                    'symbol': symbol,
                    'action': 'BUY',
                    'shares': shares,
                    'entry_price': entry_price,
                    'stop_price': stop_price,
                    'target_price': target_price,
                    'quality': quality,
                    'mode': mode,
                    'parent_order_id': parent_id,
                    'stop_order_id': stop_trade.order.orderId,
                    'tp_order_id': tp_trade.order.orderId,
                    'status': 'SUBMITTED',
                    'live': self.live,
                }
            else:
                entry_trade = self.ib.placeOrder(contract, entry_order)
                order_info = {
                    'symbol': symbol,
                    'action': 'BUY',
                    'shares': shares,
                    'entry_price': entry_price,
                    'quality': quality,
                    'mode': mode,
                    'parent_order_id': entry_trade.order.orderId,
                    'status': 'SUBMITTED',
                    'live': self.live,
                }

            self.pending_orders[entry_trade.order.orderId] = order_info

            if IB_EXEC_CONFIG['log_all_orders']:
                _log_order(order_info)

            label = "LIVE" if self.live else "PAPER"
            logger.info(
                f"[{label}] Bracket order placed: BUY {shares} {symbol} "
                f"@ ${entry_price:.2f} | SL ${stop_price:.2f} | TP ${target_price:.2f}"
            )

            # Notify
            if IB_EXEC_CONFIG['notify_on_fill']:
                _notify_order(order_info, 'PLACED')

            return {'success': True, **order_info}

        except Exception as e:
            logger.error(f"Order failed for {symbol}: {e}")
            err_info = {'success': False, 'symbol': symbol, 'error': str(e)}
            if IB_EXEC_CONFIG['log_all_orders']:
                _log_order({**err_info, 'status': 'ERROR'})
            return err_info

    async def close_position_ib(self, symbol: str, shares: int,
                                 reason: str = 'manual') -> dict:
        """Place a market sell order to close a position."""
        from ib_insync import Stock, MarketOrder

        if not self.is_connected:
            return {'success': False, 'reason': 'Not connected to IB'}

        if is_kill_switch_active() and reason != 'kill_switch':
            return {'success': False, 'reason': 'Kill switch active'}

        try:
            contract = Stock(symbol, 'SMART', 'USD')
            await self.ib.qualifyContractsAsync(contract)

            order = MarketOrder('SELL', shares)
            order.tif = 'DAY'
            trade = self.ib.placeOrder(contract, order)

            info = {
                'symbol': symbol,
                'action': 'SELL',
                'shares': shares,
                'reason': reason,
                'order_id': trade.order.orderId,
                'status': 'SUBMITTED',
                'live': self.live,
            }

            if IB_EXEC_CONFIG['log_all_orders']:
                _log_order(info)

            logger.info(f"Close order: SELL {shares} {symbol} ({reason})")
            return {'success': True, **info}

        except Exception as e:
            logger.error(f"Close order failed for {symbol}: {e}")
            return {'success': False, 'symbol': symbol, 'error': str(e)}

    async def sync_positions(self) -> dict:
        """
        Reconcile IB account positions with auto_portfolio.json.
        Returns differences found.
        """
        if not self.is_connected:
            return {'success': False, 'reason': 'Not connected'}

        import auto_portfolio

        # Get IB positions
        ib_positions = self.ib.positions()
        ib_map = {}
        for pos in ib_positions:
            sym = pos.contract.symbol
            ib_map[sym] = {
                'symbol': sym,
                'shares': int(pos.position),
                'avg_cost': float(pos.avgCost),
                'market_value': float(pos.position * pos.avgCost),
            }

        # Get portfolio positions
        data = auto_portfolio.load()
        port_map = {p['symbol']: p for p in data['positions']}

        # Find differences
        ib_only = set(ib_map.keys()) - set(port_map.keys())
        port_only = set(port_map.keys()) - set(ib_map.keys())
        both = set(ib_map.keys()) & set(port_map.keys())

        mismatches = []
        for sym in both:
            ib_shares = ib_map[sym]['shares']
            port_shares = port_map[sym]['shares']
            if ib_shares != port_shares:
                mismatches.append({
                    'symbol': sym,
                    'ib_shares': ib_shares,
                    'portfolio_shares': port_shares,
                })

        result = {
            'success': True,
            'ib_positions': len(ib_map),
            'portfolio_positions': len(port_map),
            'in_ib_only': list(ib_only),
            'in_portfolio_only': list(port_only),
            'share_mismatches': mismatches,
            'synced': len(both) - len(mismatches),
        }

        if ib_only or port_only or mismatches:
            logger.warning(f"Position sync mismatches: {result}")
            _notify_sync(result)
        else:
            logger.info(f"Positions in sync: {len(both)} positions match")

        return result

    async def cancel_all_pending(self) -> int:
        """Cancel all pending orders."""
        if not self.is_connected:
            return 0

        open_orders = self.ib.openOrders()
        cancelled = 0
        for order in open_orders:
            try:
                self.ib.cancelOrder(order)
                cancelled += 1
            except Exception as e:
                logger.error(f"Failed to cancel order {order.orderId}: {e}")

        logger.info(f"Cancelled {cancelled} pending orders")
        return cancelled

    def get_status(self) -> dict:
        """Get current executor status."""
        return {
            'enabled': IB_EXEC_CONFIG['enabled'],
            'connected': self.is_connected,
            'live': self.live,
            'kill_switch': is_kill_switch_active(),
            'daily_pnl': get_daily_pnl(),
            'pending_orders': len(self.pending_orders),
            'daily_loss_limit': IB_EXEC_CONFIG['max_daily_loss_usd'],
        }


# ── Notifications ─────────────────────────────────────────────────────────────

def _notify_order(order_info: dict, event: str):
    """Send order notification via Discord/Telegram."""
    try:
        import requests
        from config import NOTIFICATIONS

        sym = order_info['symbol']
        label = "LIVE" if order_info.get('live') else "PAPER"
        shares = order_info.get('shares', 0)
        entry = order_info.get('entry_price', 0)
        stop = order_info.get('stop_price', 0)
        target = order_info.get('target_price', 0)
        quality = order_info.get('quality', '')

        msg = (
            f"[{label}] {event}: {order_info['action']} {shares} {sym}\n"
            f"Entry: ${entry:.2f} | Stop: ${stop:.2f} | Target: ${target:.2f}\n"
            f"Quality: {quality} | Cost: ${entry * shares:.0f}"
        )

        # Discord
        webhook_url = NOTIFICATIONS.get('discord', {}).get('webhook_url', '')
        if webhook_url:
            color = 0x00FF00 if event == 'FILLED' else 0xFFA500 if event == 'PLACED' else 0xFF0000
            payload = {
                'embeds': [{
                    'title': f"IB Order {event}",
                    'description': msg,
                    'color': color,
                }]
            }
            requests.post(webhook_url, json=payload, timeout=10)

        # Telegram
        tg_cfg = NOTIFICATIONS.get('telegram', {})
        if tg_cfg.get('enabled') and tg_cfg.get('bot_token') and tg_cfg.get('chat_id'):
            tg_url = f"https://api.telegram.org/bot{tg_cfg['bot_token']}/sendMessage"
            requests.post(tg_url, json={
                'chat_id': tg_cfg['chat_id'],
                'text': f"📊 IB {event}\n{msg}",
                'parse_mode': 'HTML',
            }, timeout=10)

    except Exception as e:
        logger.warning(f"Order notification failed: {e}")


def _notify_sync(result: dict):
    """Notify on position sync mismatches."""
    try:
        import requests
        from config import NOTIFICATIONS

        lines = ["⚠️ IB Position Sync Mismatch"]
        if result['in_ib_only']:
            lines.append(f"In IB only: {', '.join(result['in_ib_only'])}")
        if result['in_portfolio_only']:
            lines.append(f"In portfolio only: {', '.join(result['in_portfolio_only'])}")
        for m in result.get('share_mismatches', []):
            lines.append(f"{m['symbol']}: IB={m['ib_shares']} vs Portfolio={m['portfolio_shares']}")

        msg = '\n'.join(lines)

        webhook_url = NOTIFICATIONS.get('discord', {}).get('webhook_url', '')
        if webhook_url:
            requests.post(webhook_url, json={
                'embeds': [{'title': 'Position Sync', 'description': msg, 'color': 0xFF6600}]
            }, timeout=10)

    except Exception as e:
        logger.warning(f"Sync notification failed: {e}")


# ── Integration: auto_portfolio → IB ─────────────────────────────────────────

async def execute_signal(ib_connection, signal: dict, live: bool = False) -> dict:
    """
    Execute a V9-H signal through IB.

    Args:
        ib_connection: Active IB connection
        signal: dict with symbol, entry_price, stop, target, shares, quality, mode
        live: True for live orders, False for paper

    Returns execution result dict.
    """
    executor = IBExecutor(ib_connection, live=live)

    return await executor.place_bracket_order(
        symbol=signal['symbol'],
        entry_price=signal['entry_price'],
        stop_price=signal['stop'],
        target_price=signal['target'],
        shares=signal['shares'],
        quality=signal.get('quality', 'PREMIUM'),
        mode=signal.get('mode', 'swing'),
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description='IB Execution Bridge')
    parser.add_argument('--status', action='store_true', help='Show executor status')
    parser.add_argument('--sync', action='store_true', help='Sync IB positions with portfolio')
    parser.add_argument('--kill', action='store_true', help='Activate kill switch')
    parser.add_argument('--unkill', action='store_true', help='Deactivate kill switch')
    parser.add_argument('--log', action='store_true', help='Show execution log')
    parser.add_argument('--enable', action='store_true', help='Enable IB execution')
    parser.add_argument('--disable', action='store_true', help='Disable IB execution')
    parser.add_argument('--live', action='store_true', help='Use live port (default: paper)')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    if args.kill:
        activate_kill_switch('CLI command')
        print("Kill switch ACTIVATED — no new orders will be placed")
        return

    if args.unkill:
        deactivate_kill_switch()
        print("Kill switch deactivated — trading resumed")
        return

    if args.enable:
        IB_EXEC_CONFIG['enabled'] = True
        print("IB execution ENABLED (in-memory only — set in config for persistence)")
        return

    if args.disable:
        IB_EXEC_CONFIG['enabled'] = False
        print("IB execution DISABLED")
        return

    if args.log:
        log = _load_exec_log()
        if not log:
            print("No execution log entries")
            return
        today = date.today().isoformat()
        today_entries = [e for e in log if e.get('timestamp', '').startswith(today)]
        print(f"\n  Execution Log — {len(log)} total, {len(today_entries)} today")
        print(f"  Daily P&L: ${get_daily_pnl():,.2f}\n")
        for e in log[-10:]:
            ts = e.get('timestamp', '?')[:19]
            sym = e.get('symbol', '?')
            action = e.get('action', '?')
            status = e.get('status', '?')
            shares = e.get('shares', 0)
            price = e.get('entry_price', 0)
            label = "LIVE" if e.get('live') else "PAPER"
            print(f"  [{ts}] {label} {action} {shares} {sym} @ ${price:.2f} → {status}")
        return

    if args.status:
        print(f"\n  IB Executor Status")
        print(f"  {'─' * 40}")
        print(f"  Enabled:        {IB_EXEC_CONFIG['enabled']}")
        print(f"  Kill switch:    {'ACTIVE' if is_kill_switch_active() else 'off'}")
        print(f"  Mode:           {'LIVE' if args.live else 'PAPER'}")
        print(f"  Daily P&L:      ${get_daily_pnl():,.2f}")
        print(f"  Daily loss max: ${IB_EXEC_CONFIG['max_daily_loss_usd']}")
        print(f"  Max position:   ${IB_EXEC_CONFIG['max_position_value']}")
        print(f"  Order type:     {IB_EXEC_CONFIG['order_type']}")
        print(f"  Bracket:        {IB_EXEC_CONFIG['use_bracket']}")

        log = _load_exec_log()
        today = date.today().isoformat()
        today_entries = [e for e in log if e.get('timestamp', '').startswith(today)]
        print(f"  Today's orders: {len(today_entries)}")
        print()
        return

    if args.sync:
        print("Sync requires an active IB connection. Use from scan_feedback_agent or breakout_scanner.")
        return

    parser.print_help()


if __name__ == '__main__':
    main()
