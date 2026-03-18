"""
Position Health & Portfolio Diversity
======================================
Advisory system that monitors portfolio health and notifies the user:
- Cash level monitoring (LOW / CRITICAL alerts)
- Stale position detection (flat/negative for N days)
- Risk-based ranking (riskiest positions = first trim candidates)
- Diversity scoring (sector, mode, ETF/stock, 70/30 risk balance)

IMPORTANT: This system is advisory only — it never auto-closes positions
or blocks V9-H entries. The edge from regime gate + quality filter is preserved.
"""
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from config import CASH_MANAGEMENT, NOTIFICATIONS, SENTIMENT

logger = logging.getLogger(__name__)

_NY_TZ = ZoneInfo('America/New_York')
_CACHE_PATH = Path('scanner_output/.health_alert_cache.json')

# Known ETFs beyond sector_etfs config
_KNOWN_ETFS = {
    'SPY', 'QQQ', 'IWM', 'DIA', 'IBIT', 'ETHA', 'GLD', 'TLT',
    'XLK', 'XLE', 'XLF', 'XLV', 'XLY', 'XLI', 'XLRE', 'XLB',
    'IBB', 'XME', 'ARKK', 'VTI', 'VOO', 'IVV', 'RSP', 'SOXX',
}


# ── ETF Detection ────────────────────────────────────────────────────────────

def _build_etf_set() -> set:
    """Build full ETF set from config sector_etfs + known list."""
    etfs = set(_KNOWN_ETFS)
    for sector_data in SENTIMENT.get('sector_etfs', {}).values():
        etf = sector_data.get('etf', '')
        if etf:
            etfs.add(etf.upper())
    return etfs

_ETF_SET = None

def is_etf(symbol: str) -> bool:
    global _ETF_SET
    if _ETF_SET is None:
        _ETF_SET = _build_etf_set()
    return symbol.upper() in _ETF_SET


# ── ATR% Calculation ──────────────────────────────────────────────────────────

def _fetch_atr_pcts(symbols: list[str], period: int = 14) -> dict:
    """
    Fetch ATR% (ATR / close price) for a list of symbols using yfinance.
    Returns {symbol: atr_pct} dict. Missing symbols are omitted.
    Uses Wilder's smoothed ATR (EWM with span=period).
    """
    result = {}
    if not symbols:
        return result
    try:
        import yfinance as yf
        # Batch download — 30 trading days gives enough warm-up for ATR(14)
        data = yf.download(symbols, period='30d', group_by='ticker',
                           progress=False, threads=True)
        if data.empty:
            return result

        for sym in symbols:
            try:
                if len(symbols) == 1:
                    df = data
                else:
                    df = data[sym]
                high  = df['High']
                low   = df['Low']
                close = df['Close']
                if len(close.dropna()) < period + 1:
                    continue
                # True Range
                prev_close = close.shift(1)
                tr = high.combine(prev_close, max) - low.combine(prev_close, min)
                # Wilder's EWM smoothing
                atr = tr.ewm(span=period, adjust=False).mean()
                last_atr = atr.iloc[-1]
                last_close = close.iloc[-1]
                if last_close > 0:
                    result[sym] = float(last_atr / last_close)
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"ATR fetch failed: {e}")
    return result


# ── Risk Score ────────────────────────────────────────────────────────────────

def compute_risk_score(pos: dict, sector_counts: dict, mode_counts: dict,
                       total_value: float, atr_pcts: dict | None = None) -> dict:
    """
    Compute risk score (0-100) for a single position. Higher = riskier.

    Args:
        atr_pcts: Optional dict {symbol: atr_pct} with ATR% values.
                  If None, uses stop distance as ATR proxy.

    Returns the position dict augmented with 'risk_score' and 'risk_factors'.
    """
    cfg = CASH_MANAGEMENT
    now = datetime.now(_NY_TZ)

    entry  = pos['entry_price']
    stop   = pos.get('stop', entry * 0.95)
    target = pos.get('target', entry * 1.15)
    cur    = pos.get('current_price', entry)

    # ── Factor 1: Days stale (20%) ────────────────────────────────────────
    try:
        date_added = datetime.strptime(pos['date_added'], '%Y-%m-%d')
    except (KeyError, ValueError):
        date_added = now
    days_held = max(0, (now - date_added.replace(tzinfo=_NY_TZ)).days)
    gain_pct  = (cur - entry) / entry if entry else 0

    if gain_pct <= cfg['stale_threshold_pct'] and days_held >= cfg['stale_days']:
        stale_score = min(1.0, days_held / 60)  # 60 days = max
    else:
        stale_score = 0.0

    # ── Factor 2: Stop proximity (15%) ────────────────────────────────────
    dist_stop   = max(0, (cur - stop) / cur) if cur else 0
    dist_target = max(0, (target - cur) / cur) if cur else 0
    denom = dist_stop + dist_target
    stop_prox = (1.0 - dist_stop / denom) if denom > 0 else 0.5

    # ── Factor 3: Unrealized P&L (15%) ────────────────────────────────────
    pnl_score = max(0.0, min(1.0, -gain_pct / 0.10)) if gain_pct < 0 else 0.0

    # ── Factor 4: Quality penalty (10%) ───────────────────────────────────
    quality = pos.get('quality', 'STANDARD').upper()
    quality_score = cfg['quality_risk_penalty'].get(quality, 1.0)

    # ── Factor 5: R:R consumed (5%) ───────────────────────────────────────
    total_range = target - stop
    if total_range > 0:
        consumed = cur - stop
        rr_consumed = 1.0 - max(0, min(1.0, consumed / total_range))
    else:
        rr_consumed = 0.5

    # ── Factor 6: ATR% volatility (15%) ──────────────────────────────────
    # Higher ATR% = more volatile = riskier position.
    # Uses actual ATR% if provided, otherwise uses stop distance as proxy
    # (stops are typically set at entry - ATR*mult in the scanner).
    sym = pos['symbol']
    if atr_pcts and sym in atr_pcts:
        atr_pct = atr_pcts[sym]
    else:
        # Proxy: stop distance as % of entry price (scanner sets stops from ATR)
        atr_pct = (entry - stop) / entry if entry else 0.05
    # Normalize: 2% ATR = low risk (0.0), 8%+ ATR = high risk (1.0)
    atr_score = max(0.0, min(1.0, (atr_pct - 0.02) / 0.06))

    # ── Factor 7: Sector crowding (10%) ───────────────────────────────────
    sector = pos.get('sector', '')
    sector_count = sector_counts.get(sector, 1) if sector else 1
    sector_score = min(1.0, sector_count / 5)

    # ── Factor 8: Mode crowding (5%) ─────────────────────────────────────
    mode = pos.get('mode', '')
    mode_count = mode_counts.get(mode, 1)
    mode_score = min(1.0, mode_count / cfg['max_per_mode'])

    # ── Factor 9: Size mismatch (5%) ─────────────────────────────────────
    pos_value = cur * pos.get('shares', 0)
    value_pct = pos_value / total_value if total_value > 0 else 0
    size_score = min(1.0, value_pct / cfg['max_single_position_pct'])

    # ── Weighted total ────────────────────────────────────────────────────
    risk_score = 100 * (
        0.20 * stale_score +
        0.15 * stop_prox +
        0.15 * pnl_score +
        0.10 * quality_score +
        0.05 * rr_consumed +
        0.15 * atr_score +
        0.10 * sector_score +
        0.05 * mode_score +
        0.05 * size_score
    )

    pos['risk_score'] = round(risk_score, 1)
    pos['risk_factors'] = {
        'days_held':       days_held,
        'gain_pct':        round(gain_pct * 100, 2),
        'stale':           days_held >= cfg['stale_days'] and gain_pct <= cfg['stale_threshold_pct'],
        'stop_proximity':  round(stop_prox, 3),
        'pnl_score':       round(pnl_score, 3),
        'atr_pct':         round(atr_pct * 100, 2),
        'atr_score':       round(atr_score, 3),
        'sector_count':    sector_count,
        'mode_count':      mode_count,
        'value_pct':       round(value_pct * 100, 1),
    }
    return pos


# ── Diversity Analysis ────────────────────────────────────────────────────────

def analyze_diversity(positions: list, total_value: float) -> dict:
    """
    Analyze portfolio diversity across 4 dimensions.
    Returns breakdown dicts and alert messages.
    """
    cfg = CASH_MANAGEMENT
    alerts = []

    # 1. Sector concentration
    sector_counts = Counter(p.get('sector', 'Unknown') or 'Unknown' for p in positions)
    for sector, count in sector_counts.most_common():
        if count > cfg['max_per_sector']:
            alerts.append({
                'type': 'DIVERSITY',
                'severity': 'WARNING',
                'message': f"Sector '{sector}' has {count} positions (max {cfg['max_per_sector']})",
                'sector': sector,
                'count': count,
            })

    # 2. Mode balance
    mode_counts = Counter(p.get('mode', 'unknown') for p in positions)
    for mode, count in mode_counts.most_common():
        if count > cfg['max_per_mode']:
            alerts.append({
                'type': 'DIVERSITY',
                'severity': 'WARNING',
                'message': f"Mode '{mode}' has {count} positions (max {cfg['max_per_mode']})",
            })

    # 3. ETF vs Stock balance
    etf_positions = [p for p in positions if is_etf(p['symbol'])]
    etf_count = len(etf_positions)
    etf_pct = etf_count / len(positions) if positions else 0
    if positions and etf_pct < 0.10:
        alerts.append({
            'type': 'DIVERSITY',
            'severity': 'INFO',
            'message': f"ETF allocation is {etf_pct:.0%} ({etf_count}/{len(positions)}) — target {cfg['ideal_etf_pct']:.0%}",
        })

    # 4. 70/30 risk balance (computed after risk scores are assigned)
    risky_threshold = cfg['risky_threshold']
    safe_value = sum(
        p.get('current_price', p['entry_price']) * p.get('shares', 0)
        for p in positions if p.get('risk_score', 0) < risky_threshold
    )
    risky_value = sum(
        p.get('current_price', p['entry_price']) * p.get('shares', 0)
        for p in positions if p.get('risk_score', 0) >= risky_threshold
    )
    risky_pct = risky_value / total_value if total_value > 0 else 0
    safe_pct  = safe_value / total_value if total_value > 0 else 0
    target_safe = cfg['safe_allocation_pct']

    if risky_pct > (1 - target_safe):
        risky_positions = sorted(
            [p for p in positions if p.get('risk_score', 0) >= risky_threshold],
            key=lambda p: p.get('risk_score', 0), reverse=True
        )
        trim_names = ', '.join(
            f"{p['symbol']} (risk {p['risk_score']}, "
            f"${p.get('current_price', p['entry_price']) * p.get('shares', 0):,.0f})"
            for p in risky_positions[:3]
        )
        alerts.append({
            'type': 'RISK_BALANCE',
            'severity': 'WARNING',
            'message': (
                f"Risky positions are {risky_pct:.0%} of portfolio "
                f"(${risky_value:,.0f}) — target max {1 - target_safe:.0%}. "
                f"Consider trimming: {trim_names}"
            ),
        })

    # 5. Single position concentration
    for p in positions:
        pos_value = p.get('current_price', p['entry_price']) * p.get('shares', 0)
        if total_value > 0 and pos_value / total_value > cfg['max_single_position_pct']:
            alerts.append({
                'type': 'DIVERSITY',
                'severity': 'WARNING',
                'message': (
                    f"{p['symbol']} is {pos_value / total_value:.0%} of portfolio "
                    f"(${pos_value:,.0f}) — max {cfg['max_single_position_pct']:.0%}"
                ),
            })

    return {
        'sector_counts':  dict(sector_counts),
        'mode_counts':    dict(mode_counts),
        'etf_count':      etf_count,
        'etf_pct':        round(etf_pct, 3),
        'safe_pct':       round(safe_pct, 3),
        'risky_pct':      round(risky_pct, 3),
        'alerts':         alerts,
    }


# ── Main Health Evaluation ────────────────────────────────────────────────────

def evaluate_portfolio_health(data: dict) -> dict:
    """
    Full portfolio health assessment. Advisory only — never modifies positions.

    Args:
        data: auto_portfolio data dict (from auto_portfolio.load())

    Returns dict with cash_status, stale_positions, risk_ranking, diversity, alerts.
    """
    import auto_portfolio

    cfg      = CASH_MANAGEMENT
    summary  = auto_portfolio.get_summary(data)
    cash     = summary['cash']
    total_val = summary['total_value']
    positions = data.get('positions', [])

    # ── Cash status ───────────────────────────────────────────────────────
    cash_pct = cash / total_val if total_val > 0 else 1.0
    if cash_pct < cfg['critical_cash_pct']:
        cash_status = 'CRITICAL'
    elif cash_pct < cfg['low_cash_pct']:
        cash_status = 'LOW'
    else:
        cash_status = 'OK'

    # ── Enrich sectors if missing ─────────────────────────────────────────
    for p in positions:
        if not p.get('sector'):
            try:
                from sentiment import get_sector_for_ticker
                p['sector'] = get_sector_for_ticker(p['symbol'])
            except Exception:
                p['sector'] = 'Unknown'

    # ── Fetch ATR% for all positions (batch, single yfinance call) ───────
    atr_pcts = _fetch_atr_pcts([p['symbol'] for p in positions])

    # ── Compute risk scores ───────────────────────────────────────────────
    sector_counts = Counter(p.get('sector', 'Unknown') or 'Unknown' for p in positions)
    mode_counts   = Counter(p.get('mode', 'unknown') for p in positions)

    for p in positions:
        compute_risk_score(p, sector_counts, mode_counts, total_val, atr_pcts)

    # ── Sort by risk (highest first = first to trim) ──────────────────────
    risk_ranking = sorted(positions, key=lambda p: p.get('risk_score', 0), reverse=True)

    # ── Stale positions ───────────────────────────────────────────────────
    stale = [p for p in positions if p.get('risk_factors', {}).get('stale', False)]

    # ── Diversity analysis (needs risk_score computed first for 70/30) ────
    diversity = analyze_diversity(positions, total_val)

    # ── Build alerts ──────────────────────────────────────────────────────
    alerts = []

    # Cash alerts
    if cash_status in ('LOW', 'CRITICAL'):
        skipped = data.get('skipped_cash', [])
        top_trim = risk_ranking[:3]
        trim_lines = '\n'.join(
            f"  {i+1}. {p['symbol']}  Risk: {p['risk_score']}  "
            f"{p['risk_factors']['gain_pct']:+.1f}%  "
            f"{'stale ' + str(p['risk_factors']['days_held']) + 'd' if p['risk_factors']['stale'] else ''}"
            for i, p in enumerate(top_trim)
        )
        severity = 'CRITICAL' if cash_status == 'CRITICAL' else 'WARNING'
        alerts.append({
            'type':     'CASH',
            'severity': severity,
            'message': (
                f"Cash: ${cash:,.0f} ({cash_pct:.1%} of ${total_val:,.0f})\n"
                f"Open positions: {len(positions)}/{CASH_MANAGEMENT.get('max_concurrent', 10)}\n"
                f"Signals skipped (cash): {len(skipped)}\n\n"
                f"Top candidates to free cash (by risk):\n{trim_lines}"
            ),
        })

    # Stale alerts
    if stale:
        stale_lines = '\n'.join(
            f"  {p['symbol']}  {p['risk_factors']['days_held']}d  "
            f"{p['risk_factors']['gain_pct']:+.1f}%  "
            f"stop ${p.get('stop', 0):.2f}"
            for p in sorted(stale, key=lambda x: x.get('risk_score', 0), reverse=True)
        )
        alerts.append({
            'type':     'STALE',
            'severity': 'INFO',
            'message':  f"{len(stale)} stale position(s) ({cfg['stale_days']}+ days flat/negative):\n{stale_lines}",
        })

    # Diversity alerts
    alerts.extend(diversity['alerts'])

    return {
        'cash_status':     cash_status,
        'cash_pct':        round(cash_pct, 3),
        'cash':            round(cash, 2),
        'total_value':     round(total_val, 2),
        'stale_positions': stale,
        'risk_ranking':    risk_ranking,
        'diversity':       diversity,
        'alerts':          alerts,
    }


# ── Notification Formatting ──────────────────────────────────────────────────

_DISCORD_COLORS = {
    'CASH':         0xf39c12,  # orange
    'STALE':        0xf1c40f,  # yellow
    'DIVERSITY':    0x3498db,  # blue
    'RISK_BALANCE': 0xe74c3c,  # red
}

def _format_discord_embed(alert: dict) -> dict:
    alert_type = alert['type']
    severity   = alert['severity']
    prefix     = 'CRITICAL' if severity == 'CRITICAL' else 'PORTFOLIO'
    return {
        'title':       f"[{prefix}] {alert_type.replace('_', ' ').title()}",
        'description': alert['message'],
        'color':       _DISCORD_COLORS.get(alert_type, 0x888888),
    }


def _format_telegram_message(alert: dict) -> str:
    severity = alert['severity']
    icon = {'CRITICAL': '\u26a0\ufe0f', 'WARNING': '\u26a0', 'INFO': '\u2139\ufe0f'}.get(severity, '')
    return f"{icon} {alert['type']}: {alert['message']}"


# ── Cooldown Cache ────────────────────────────────────────────────────────────

def _load_alert_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_alert_cache(cache: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _should_alert(alert_type: str) -> bool:
    """Check cooldown — returns True if we should send this alert type."""
    cache = _load_alert_cache()
    key = f"health:{alert_type}"
    last_ts = cache.get(key, 0)
    cooldown_sec = CASH_MANAGEMENT['notify_cooldown_hours'] * 3600
    return (time.time() - last_ts) > cooldown_sec


def _mark_alerted(alert_type: str) -> None:
    cache = _load_alert_cache()
    cache[f"health:{alert_type}"] = time.time()
    _save_alert_cache(cache)


# ── Send Notifications ────────────────────────────────────────────────────────

def _send_health_discord(alerts: list[dict]) -> None:
    """Post health alerts to Discord 'alerts' webhook."""
    try:
        cfg = NOTIFICATIONS.get('discord', {})
        if not cfg.get('enabled'):
            return
        webhooks = cfg.get('webhooks', {})
        webhook_url = webhooks.get('alerts') or cfg.get('webhook_url')
        if not webhook_url:
            return
    except Exception:
        return

    for alert in alerts:
        embed = _format_discord_embed(alert)
        try:
            r = requests.post(webhook_url, json={'embeds': [embed]}, timeout=8)
            if r.status_code == 204:
                logger.info(f"Discord health alert sent: {alert['type']}")
            else:
                logger.warning(f"Discord returned {r.status_code} for health alert")
        except Exception as e:
            logger.error(f"Discord health alert failed: {e}")


def _send_health_telegram(alerts: list[dict]) -> None:
    """Send health alerts to Telegram."""
    try:
        cfg = NOTIFICATIONS.get('telegram', {})
        if not cfg.get('enabled'):
            return
        bot_token = cfg.get('bot_token', '')
        chat_id   = cfg.get('chat_id', '')
        if not bot_token or not chat_id:
            return
    except Exception:
        return

    for alert in alerts:
        text = _format_telegram_message(alert)
        try:
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={'chat_id': chat_id, 'text': text},
                timeout=8,
            )
            logger.info(f"Telegram health alert sent: {alert['type']}")
        except Exception as e:
            logger.error(f"Telegram health alert failed: {e}")


# ── Main Entry Point ─────────────────────────────────────────────────────────

def run_health_check(data: dict | None = None) -> dict:
    """
    Load portfolio, evaluate health, send notifications if thresholds met.
    Advisory only — never modifies positions or blocks entries.

    Args:
        data: Optional pre-loaded auto_portfolio data dict.
              If None, loads from disk.

    Returns health assessment dict.
    """
    if data is None:
        import auto_portfolio
        data = auto_portfolio.load()

    if not data.get('positions'):
        logger.debug("No open positions — skipping health check")
        return {'cash_status': 'OK', 'alerts': []}

    health = evaluate_portfolio_health(data)

    # Filter alerts by cooldown
    alerts_to_send = []
    for alert in health['alerts']:
        alert_type = alert['type']
        if alert['severity'] == 'CRITICAL' or _should_alert(alert_type):
            alerts_to_send.append(alert)
            _mark_alerted(alert_type)

    if alerts_to_send:
        logger.info(f"Sending {len(alerts_to_send)} health alert(s)")
        _send_health_discord(alerts_to_send)
        _send_health_telegram(alerts_to_send)
    else:
        logger.debug("No health alerts to send (all within cooldown or OK)")

    return health


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    parser = argparse.ArgumentParser(description='Portfolio Health Check (advisory)')
    parser.add_argument('--dry-run', action='store_true', help='Evaluate but do not send notifications')
    parser.add_argument('--json', action='store_true', help='Print results as JSON')
    args = parser.parse_args()

    import auto_portfolio
    data = auto_portfolio.load()

    if not data.get('positions'):
        print("No open positions.")
        raise SystemExit(0)

    health = evaluate_portfolio_health(data)

    if args.json:
        # Serialize without position objects (too verbose)
        out = {k: v for k, v in health.items() if k != 'risk_ranking'}
        out['risk_ranking'] = [
            {'symbol': p['symbol'], 'risk_score': p['risk_score'],
             'factors': p['risk_factors']}
            for p in health['risk_ranking']
        ]
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"\n{'='*60}")
        print(f"  PORTFOLIO HEALTH CHECK")
        print(f"{'='*60}")
        print(f"  Cash: ${health['cash']:,.0f} ({health['cash_pct']:.1%}) — {health['cash_status']}")
        print(f"  Total value: ${health['total_value']:,.0f}")
        print(f"  Positions: {len(data['positions'])}")

        div = health['diversity']
        print(f"\n  Diversity:")
        print(f"    Sectors: {div['sector_counts']}")
        print(f"    Modes:   {div['mode_counts']}")
        print(f"    ETFs:    {div['etf_count']} ({div['etf_pct']:.0%})")
        print(f"    Safe/Risky: {div['safe_pct']:.0%} / {div['risky_pct']:.0%}")

        print(f"\n  Risk Ranking (riskiest first):")
        for p in health['risk_ranking']:
            rf = p['risk_factors']
            stale_tag = ' STALE' if rf['stale'] else ''
            atr_tag   = f"  ATR {rf.get('atr_pct', 0):.1f}%"
            print(f"    {p['symbol']:6s}  Risk: {p['risk_score']:5.1f}  "
                  f"{rf['gain_pct']:+6.1f}%  {rf['days_held']}d  "
                  f"{rf['value_pct']:.0f}% of portfolio{atr_tag}{stale_tag}")

        if health['alerts']:
            print(f"\n  Alerts:")
            for a in health['alerts']:
                print(f"    [{a['severity']}] {a['type']}: {a['message']}")
        else:
            print(f"\n  No alerts.")
        print()

    if not args.dry_run:
        # Send notifications
        alerts_to_send = []
        for alert in health['alerts']:
            if alert['severity'] == 'CRITICAL' or _should_alert(alert['type']):
                alerts_to_send.append(alert)
                _mark_alerted(alert['type'])

        if alerts_to_send:
            print(f"Sending {len(alerts_to_send)} notification(s)...")
            _send_health_discord(alerts_to_send)
            _send_health_telegram(alerts_to_send)
            print("Done.")
        else:
            print("No notifications to send.")
