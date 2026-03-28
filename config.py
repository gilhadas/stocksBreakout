"""
Configuration module for breakout scanner
Contains all mode settings and regime configurations
"""
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()


def _load_email_recipients() -> str:
    """Load recipient emails from input/email_recipients.txt if it exists,
    falling back to NOTIFY_RECIPIENTS env var or a hardcoded default."""
    recipients_file = Path(__file__).parent / 'input' / 'email_recipients.txt'
    if recipients_file.exists():
        emails = []
        for line in recipients_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                emails.append(line)
        if emails:
            return ', '.join(emails)
    return os.environ.get('NOTIFY_RECIPIENTS', 'gil.hadas@gmail.com')


# --- Portfolio Configuration ---
PORTFOLIO = {
    'initial_capital': 10000,       # Starting capital in USD
    'max_position_pct': 0.10,       # 10% base, scaled by quality × ATR adjustment
    'max_risk_pct': 0.02,           # 2% risk per trade (backtested optimal)
    'use_trailing_stop': False,     # Legacy trailing stops — disabled (V9 replaces this)
    'trailing_stop_atr_mult': 4.0,  # Legacy: Wide trail prevents shakeouts
    'trailing_stop_activation_pct': 0.10, # Legacy: Only trail after +10% profit
    'tp_as_trail': True,            # V9: TP activates trailing stop instead of hard exit (+75.57%, -6.45% DD)
    'tp_trail_atr_mult': 2.0,      # V9: 2.0 ATR trailing distance after TP hit
    'use_scoring_system': True,     # Use weighted scoring instead of all-or-nothing
    'use_pullback_entries': True,   # Enable pullback re-entry signals
    'max_concurrent_positions': 10, # Max open positions at once
}

# --- Signal Scoring Configuration ---
# V2: Composite momentum + conviction scoring (replaces 3 binary checks)
SCORING_WEIGHTS = {
    'vol_confirm': 16,          # Volume is king (unchanged)
    'trend_ok': 2,              # Trend is king — optimizer reduced (dist/support more predictive)
    'momentum_strong': 3,       # Composite RSI+MACD+ADX+ROC score >= 50
    'dist_confirm': 24,         # Distance from MA — optimizer top signal
    'candle_ok': 19,            # Candle structure — optimizer raised
    'rr_ok': 2,                 # R:R ratio — optimizer reduced
    'no_vol_divergence': 6,
    'conviction_strong': 4,     # Breakout conviction score >= 40
    'rs_ok': 16,                # Relative strength vs SPY — optimizer raised
    'consolidation': 12,        # Consolidation quality
    'has_bullish_pattern': 13,  # V3: Pattern confirmation bonus
    'near_52w_high': 17,        # V5: Within 5% of 52-week high — optimizer raised
    'rsi_divergence': 14,       # V5: RSI bullish divergence — optimizer raised
    'sector_momentum': 8,       # V5: Sector ETF momentum
    'pattern_vol_confirmed': 12,# V6: Pattern confirmed by volume — optimizer raised
    'momentum_surge': 2,        # V7: Explosive gap/intraday move + high volume — optimizer reduced
    'minervini_template': 0,    # V8: Minervini Stage 2 — optimizer eliminated
    'vcp_quality': 15,          # V10: VCP proportional score (0.0-1.0)
    'sr_breakout': 11,          # V11: Breaking above tested resistance (≥2 touches)
    'at_key_support': 24,       # V11: Price hugging a key support zone — optimizer top signal
    'trendline_break': 9,       # V11b: Breaking above angled resistance trendline (≥3 swing highs)
}

SCORE_THRESHOLDS = {
    'GOLD': 99,      # was 90 — optimizer tightened GOLD (fewer but higher conviction)
    'PREMIUM': 69,   # was 80 — optimizer lowered (more PREMIUM signals pass)
    'HIGH': 65,      # unchanged
    'STANDARD': 50,  # was 60 — optimizer lowered
}

QUALITY_SIZING = {
    'GOLD': 2.0,      # Up to 20% of capital (was 4.0 — reduced for risk control)
    'PREMIUM': 2.0,   # Up to 20% of capital (was 3.0 — ATR adjustment handles the rest)
    'HIGH': 1.5,      # Up to 15% of capital
    'STANDARD': 1.0,  # 10% of capital (base)
}

# --- ATR-Adjusted Position Sizing ---
# Scales position size inversely with stock volatility.
# High ATR% → smaller position. Low ATR% → capped at 1.0 (no oversizing).
# Formula: atr_adj = clamp(reference_atr / stock_atr, min_adj, max_adj)
# Final:   position = capital × base_pct × quality_mult × atr_adj, capped at max_position_pct
ATR_SIZING = {
    'enabled': True,
    'reference_atr_pct': 0.025,    # 2.5% = median ATR% for liquid US stocks
    'min_adjustment': 0.3,         # Floor: never shrink below 30% of base size
    'max_adjustment': 1.0,         # Cap: never oversize (low-vol stocks stay at base)
    'max_single_position_pct': 0.20,  # Hard cap: no position > 20% of capital
    'atr_period': 14,              # ATR lookback (Wilder's)
    'atr_history_days': 30,        # Days of price data for ATR calc
}

# --- Cash Management & Portfolio Diversity ---
# Advisory system: notifies and suggests, never auto-closes or blocks V9-H entries.
CASH_MANAGEMENT = {
    'low_cash_pct': 0.15,            # Alert when cash < 15% of total value
    'critical_cash_pct': 0.05,       # Urgent alert when cash < 5%
    'stale_days': 5,                 # Days flat/negative = "stale"
    'stale_threshold_pct': 0.0,      # Gain % below this = stale
    'notify_cooldown_hours': 12,     # Don't re-alert same condition within N hours
    'quality_risk_penalty': {        # Lower quality = higher risk score
        'GOLD': 0.0, 'PREMIUM': 0.3, 'HIGH': 0.6, 'STANDARD': 1.0,
    },
    # ── Diversity Limits ──
    'max_per_sector': 3,             # Max positions in same sector
    'max_per_mode': 5,               # Max positions in same mode (swing/daytrade/longterm)
    'ideal_etf_pct': 0.20,          # Target 20% of positions in ETFs for stability
    'max_single_position_pct': 0.20, # Largest position should not exceed 20% of portfolio value
    # ── 70/30 Risk Balance ──
    'safe_allocation_pct': 0.70,     # 70% of portfolio value in safe positions (risk < 40)
    'risky_threshold': 40,           # Risk score >= this = "risky" position
}

# --- R:R Grade Configuration ---
RR_GRADE_CONFIG = {
    'A': {'min_rr': 0.6626, 'reject': False},
    'B': {'min_rr': 2.0, 'reject': False},
    'C': {'min_rr': 1.5, 'reject': False},
    'D': {'min_rr': 0.0, 'reject': True},  # R:R < 1.5 = reject
}

# --- Momentum Override (bypasses consolidation for high-momentum breakouts) ---
MOMENTUM_OVERRIDE = {
    'min_momentum': 90,       # Momentum_Score >= 90 (near-perfect)
    'min_vol_ratio': 2.5,     # Volume >= 2.5× average
    'max_rsi': 75,            # RSI < 75 (not overbought)
}

# --- Max Hold Period (bars) ---
MAX_HOLD_BARS = {
    'swing': 30,
    'longterm': 60,
    'daytrade': 1,
    'scalping': 0,   # No max hold for scalping
}

# --- SPY Hedge Configuration ---
SPY_HEDGE = {
    'enabled': True,           # Enable SPY hedge (V3 default)
    'min_allocation': 0.40,    # Fixed 40% allocation (Balanced mode)
    'max_allocation': 0.40,    # Fixed 40% allocation
    'rebalance_days': 5,       # Rebalance every 5 trading days
}

# --- BB Trend Filter ---
BB_TREND_FILTER = {
    'enabled': True,
    'reject_bearish': True,    # Reject breakouts during bearish BB trend
}

# --- V4: Over-Extension Filter ---
# Penalizes/rejects breakouts too far above the SMA trend line (mean-reversion risk)
V4_OVEREXTENSION_FILTER = {
    'enabled': True,
    'max_sma_dist_pct': {       # % distance from SMA thresholds
        'swing':    {'mild': 10, 'heavy': 20, 'reject': 25},
        'longterm': {'mild': 15, 'heavy': 25, 'reject': 35},
    },
    # daytrade/scalping use different trend lines (EMA9/VWAP), not applicable
}

# --- V10: VCP (Volatility Contraction Pattern) Configuration ---
# Detects Minervini-style progressively shallower pullbacks + volume dry-up before breakout
VCP_CONFIG = {
    'enabled': True,
    'min_contractions': 2,           # Min pullback count (textbook 3-4, but 2+ is practical)
    'first_pullback_max_pct': 35.0,  # First pullback can be up to 35%
    'first_pullback_min_pct': 5.0,   # At least 5% to be meaningful
    'final_tight_range_pct': 5.0,    # Final area quality scoring (tighter = higher quality)
    'vol_dryup_threshold': 0.75,     # Volume in later contractions <= 75% of earlier
    'pivot_proximity_pct': 8.0,      # Quality scoring: closer to pivot = higher score
    'max_chase_pct': 5.0,            # Don't chase if > 5% above pivot
    'stop_buffer_pct': 1.0,          # Stop = low of final contraction - 1% buffer
    'bar_windows': {
        'longterm': 90,              # ~4.5 months daily bars
        'swing': 60,                 # ~3 months daily bars
        'daytrade': 120,             # ~2 days of 15-min bars
        'scalping': 60,
    },
    'mode_overrides': {
        'daytrade': {
            'first_pullback_max_pct': 15.0,
            'first_pullback_min_pct': 2.0,
            'final_tight_range_pct': 1.5,
        },
        'scalping': {
            'first_pullback_max_pct': 8.0,
            'first_pullback_min_pct': 1.0,
            'final_tight_range_pct': 0.8,
        },
    },
}

# --- Win Probability Estimation ---
WIN_PROBABILITY = {
    'base_probability': 0.30,  # 30% base
    'max_bonus': 0.45,         # Up to +45% bonus from confluence
    'confluence_signals': 7,   # Number of confluence signals checked
    'high_threshold': 0.65,    # >= 65% = HIGH conviction
    'low_threshold': 0.50,     # < 50% = LOW conviction
    'high_size_mult': 1.2,     # HIGH prob = 1.2x position size
    'low_size_mult': 0.7,      # LOW prob = 0.7x position size
}

# --- Mode Configurations ---
MODES = {
    'longterm': {
        'lookback': 13,
        'vol_thresh': 3.073,
        'atr_mult': 0.0709,
        'trend_type': 'SMA',
        'trend_period': 150,
        'sl_mult': 3.011,
        'tp_mult': 5.471,
        'min_consolidation_bars': 5,
        'min_rr': 2.5,
        'max_wick_atr': 1.923,
        'max_body_top_pct': 0.6828,
        'default_timeframe': '1 day',  # Use daily, not weekly (IB uses '1W' not '1 week')
        'description': 'Position trading - weeks to months'
    },
    'swing': {
        'lookback': 15,     # Optimized: Shorter lookback improves performance (+4.1% vs +2.8%)
        'vol_thresh': 0.9,
        'atr_mult': 0.75,
        'trend_type': 'SMA',
        'trend_period': 150,
        'sl_mult': 3.0,
        'tp_mult': 10.0,
        'min_consolidation_bars': 3,
        'min_rr': 0.55,
        'max_wick_atr': 0.5,
        'max_body_top_pct': 0.3,
        'default_timeframe': '1 day',
        'description': 'Swing trading - days to weeks'
    },
    'daytrade': {
        'lookback': 15,
        'vol_thresh': 1.3,
        'atr_mult': 0.25,
        'trend_type': 'EMA',
        'trend_period': 9,
        'sl_mult': 1.5,
        'tp_mult': 3.0,
        'min_consolidation_bars': 2,
        'min_rr': 1.5,
        'max_wick_atr': 0.75,
        'max_body_top_pct': 0.4,
        'default_timeframe': '15 mins',
        'description': 'Day trading - intraday only'
    },
    'scalping': {
        'lookback': 5,
        'vol_thresh': 2.0,
        'atr_mult': 0.15,
        'trend_type': 'VWAP',
        'trend_period': None,
        'sl_mult': 0.5,
        'tp_mult': 1.0,
        'sl_fixed_cents': 5,       # Fixed stop loss in cents (scalping: 1-2¢ from entry)
        'tp_fixed_cents': 6,       # Fixed take profit in cents (3:1 R:R)
        'min_consolidation_bars': 1,
        'min_rr': 1.0,
        'max_spread_pct': 0.1,
        'min_price': 10.53,
        'max_price': 500.0,
        'max_wick_atr': 1.0,
        'max_body_top_pct': 0.5,
        'default_timeframe': '1 min',
        'description': 'Scalping - seconds to minutes'
    }
}

# --- Regime Configurations ---
REGIME_CONFIG = {
    'CHOPPY': {
        'vol_mult': 1.3,
        'atr_mult': 1.3,
        'description': 'Low momentum, high noise',
        'spy_perf_threshold': 0.01,
        'spy_vol_threshold': 1.0
    },
    'EXPANSION': {
        'vol_mult': 0.9,
        'atr_mult': 0.9,
        'description': 'High momentum, trending',
        'spy_perf_threshold': 0.05,
        'spy_vol_threshold': 2.0
    },
    'NORMAL': {
        'vol_mult': 1.0,
        'atr_mult': 1.0,
        'description': 'Standard conditions',
        'spy_perf_threshold': None,
        'spy_vol_threshold': None
    },
    'BEARISH': {
        'vol_mult': 1.0,
        'atr_mult': 1.0,
        'description': 'Mild pullback (SPY -0.5% to -1.5%) — block BOUNCE/SMA20_CROSS',
        'spy_perf_threshold': None,
        'spy_vol_threshold': None
    },
    'RED_MARKET': {
        'vol_mult': 1.0,
        'atr_mult': 1.0,
        'description': 'Strong downtrend (SPY < -1.5%) — keep trading, +55.8% P&L share',
        'spy_perf_threshold': None,
        'spy_vol_threshold': None
    }
}

# --- V9-H Regime Gate (live signal filter) ---
# Derived from backtest: 2022 bear (-18.7%), 2023 bull (+26.7%), 2024 bull (+26.1%)
# V9-H beats V9-C in all 3 years: -15.1% / +81.7% / +35.1% vs -26.5% / +89.6% / +16.2%
# 3yr compound: +108% vs +62% (V9-C) vs +30% (SPY)
#
# Rule 1 — bear_macro (SPY < SMA200): structural bear market
#   → GOLD breakouts only; block all BOUNCE and SMA20_CROSS
# Rule 2 — BEARISH regime (SPY down 0.5–1.5% over lookback): 22.2% WR, -3.28% expectancy
#   → Block BOUNCE and SMA20_CROSS; PREMIUM+ breakouts still allowed
# Rule 3 — All other regimes (NORMAL, EXPANSION, RED_MARKET, CHOPPY): trade normally
#   Note: RED_MARKET (SPY down >1.5%) is KEPT — it contributed +55.8% of P&L in backtest
V9H_REGIME_GATE = {
    'enabled': False,   # V9-C mode: no regime gating — backtest_200 (4yr/200sym) shows V9-C PREMIUM+ beats V9-H
    # 15-day SPY return thresholds (fractions) for regime classification
    'red_market_thresh': -0.015,   # SPY < -1.5% over lookback → RED_MARKET (keep trading)
    'bearish_thresh':    -0.005,   # SPY < -0.5% over lookback → BEARISH (block bounce/sma20)
    'choppy_perf_abs':    0.005,   # |SPY| < 0.5% AND vol < choppy_vol → CHOPPY
    'choppy_vol':         0.35,    # ATR% threshold for CHOPPY
    'expansion_thresh':   0.020,   # SPY > +2.0% over lookback → EXPANSION
    # bear_macro: SPY closing below its 200-day SMA → structural bear
    'sma200_lookback':    200,     # days for SMA200 calculation
    # Regime persistence: require N consecutive scans confirming a new regime before switching.
    # Smooths out noise (HMM-like temporal memory). RED_MARKET transitions are always immediate.
    'persistence_threshold': 2,    # scans needed to confirm regime change (0 = disabled)
    # Post-regime-change cooldown: suppress non-GOLD signals for N hours after a confirmed
    # regime transition. Prevents whipsaw re-entries during regime instability (HMM hysteresis).
    'cooldown_hours':        3,    # hours to suppress signals after regime change (0 = disabled)
    'cooldown_exempt_quality': ['GOLD'],  # signal qualities exempt from cooldown
    # Narrow exception: allow PREMIUM signals through bear_macro when vol+move are extreme.
    # Targets gap-up momentum surges (SATL, COIN-type) that have strong edge regardless of SPY.
    # Half-size and daily cap keep bear-macro risk controlled.
    'momentum_surge_exception': {
        'enabled':           True,
        'min_vol_ratio':     3.0,   # ≥3x average volume (same as momentum_surge threshold)
        'min_move_pct':      5.0,   # Gap% OR daily move ≥5% (same as momentum_surge)
        'allowed_qualities': ['PREMIUM', 'GOLD'],
        'blocked_types':     ['BOUNCE', 'SMA20_CROSS'],  # never relax weak signal types
        'max_per_day':       2,     # cap exception entries per scan session
        'pos_size_mult':     0.5,   # 50% normal position size in bear macro
    },
}

# V9-H Sector Exception: allow PREMIUM+ breakouts in sectors with strong
# relative strength, even when the broad regime gate would block.
# Targets sector-rotation days (e.g., energy rallying while SPY sells off).
SECTOR_EXCEPTION = {
    'enabled': False,          # Off until backtested
    'min_rs_5d': 2.0,          # Sector must outperform SPY by ≥+2% (5-day RS)
    'min_buzz': 5,             # Minimum sector buzz score (0-10)
    'min_quality': 'PREMIUM',  # Only PREMIUM or GOLD signals qualify
    'max_entries_per_day': 2,  # Cap sector-exception entries per scan
}

# Economic calendar: reduce position sizing on FOMC, CPI, NFP days.
ECONOMIC_CALENDAR = {
    'enabled': True,
    'fomc_sizing_mult': 0.50,   # 50% position size on FOMC days
    'cpi_sizing_mult': 0.75,    # 75% on CPI release days
    'nfp_sizing_mult': 0.75,    # 75% on Non-Farm Payroll days
}

# VIX-based position sizing: reduce exposure in high-volatility environments.
VIX_CONFIG = {
    'enabled': True,
    'elevated': 25,                   # VIX > 25 → elevated volatility
    'extreme': 35,                    # VIX > 35 → extreme fear
    'sizing_mult_elevated': 0.75,     # 75% position size when elevated
    'sizing_mult_extreme': 0.50,      # 50% position size when extreme
}

# --- General Settings ---
MIN_DOLLAR_VOLUME = 5_000_000  # Minimum daily dollar volume
MAX_CONCURRENT_REQUESTS = 5     # IB rate limiting
SCAN_DELAY = 0.03              # Delay between symbol scans (seconds)

# --- IB Connection Settings ---
IB_PAPER_PORT = 7497
IB_LIVE_PORT = 7496
IB_HOST = '127.0.0.1'
IB_CLIENT_ID = 1

# --- Alpaca Data Settings ---
# Free account at alpaca.markets — used as fallback data source when IB unavailable
# Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env
ALPACA_API_KEY    = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY', '')

# --- Data Request Settings ---
DATA_DURATION = {
    'daily': '365 D',
    'weekly': '730 D',  # 2 years for weekly
    'intraday': '10 D',
    'scalping': '2 D'
}

# --- Output Settings ---
OUTPUT_DIR = 'scanner_output'  # Directory for all output files

# --- Sentiment & Sector Analysis ---
SENTIMENT = {
    'enabled': True,
    'tavily_api_key': os.environ.get('TAVILY_API_KEY', ''),
    'sector_etfs': {
        'Technology': {'etf': 'XLK', 'leaders': ['AAPL', 'MSFT', 'NVDA']},
        'Energy': {'etf': 'XLE', 'leaders': ['XOM', 'CVX', 'COP']},
        'Finance': {'etf': 'XLF', 'leaders': ['JPM', 'BAC', 'WFC']},
        'Healthcare': {'etf': 'XLV', 'leaders': ['UNH', 'JNJ', 'LLY']},
        'Consumer': {'etf': 'XLY', 'leaders': ['AMZN', 'TSLA', 'HD']},
        'Industrial': {'etf': 'XLI', 'leaders': ['CAT', 'BA', 'GE']},
        'Real Estate': {'etf': 'XLRE', 'leaders': ['PLD', 'AMT', 'EQIX']},
        'Materials': {'etf': 'XLB', 'leaders': ['LIN', 'APD', 'SHW']},
    },
}

# --- FinBERT Quality Promotion ---
# Bullish FinBERT sentiment promotes a signal one tier up (HIGH→PREMIUM or PREMIUM→GOLD).
# Two thresholds must both be met:
#   min_score : FinBERT confidence for the dominant bullish label (0–1)
#   min_net   : (bullish_count - bearish_count) / total headlines (-1 to +1)
# PREMIUM→GOLD uses a higher bar because GOLD bypasses the scanner's 5 hard gates.
FINBERT_PROMOTION = {
    'enabled': True,
    'high_to_premium': {
        'min_score': 0.80,   # raised from 0.70 — need strong confidence
        'min_net':   0.40,   # raised from 0.25 — need clear bullish majority
        'min_headlines': 2,  # NEW: at least 2 bullish headlines (not 1-of-1)
    },
    'premium_to_gold': {
        'min_score': 0.88,   # raised from 0.82 — GOLD must be high-conviction
        'min_net':   0.60,   # raised from 0.40 — strong bullish consensus
        'min_headlines': 3,  # NEW: at least 3 headlines analyzed
    },
}

# --- Sector Baskets (momentum trigger for correlated groups) ---
# When a trigger ETF moves >= trigger_pct in a single day (either direction),
# the entire sector basket is added to momentum_watch so Phase 2 re-scans them.
# Useful for crypto plays that lack classic consolidation but move together.
SECTOR_BASKETS = {
    'crypto': {
        'trigger_etf': 'IBIT',   # BlackRock Bitcoin ETF — best proxy for crypto sector
        'trigger_pct': 3.0,       # Trigger when IBIT moves ±3% intraday/daily
        'symbols': [
            'COIN', 'MSTR', 'HOOD', 'HUT', 'IREN', 'BITF', 'CIFR',
            'APLD', 'CLSK', 'RIOT', 'MARA', 'CORZ', 'BMNR', 'GLXY',
        ],
    },
}

NOTIFICATIONS = {
    # Email notifications via SMTP
    'email': {
        'enabled': True,  # Set to True to enable
        'smtp_server': 'smtp.gmail.com',
        'smtp_port': 587,
        'sender_email': 'gil.hadas@gmail.com',
        'sender_password': os.environ.get('GMAIL_APP_PASSWORD', ''),
        'recipient_email': _load_email_recipients()  # reads from input/email_recipients.txt
    },
    
    # Telegram notifications
    'telegram': {
        'enabled': True,
        'bot_token': os.environ.get('TELEGRAM_BOT_TOKEN', ''),  # Get from @BotFather
        'chat_id': os.environ.get('TELEGRAM_CHAT_ID', ''),  # Your chat ID
    },
    
    # Discord notifications via webhook
    'discord': {
        'enabled': True,  # Set to True to enable
        'webhooks': {
            'signals': 'https://discordapp.com/api/webhooks/1464758074941112353/dzvWJZwybS21NQ432EVRzZXBdYuymCcLu85vjYnDheDbyqECN_psBneA7neqUNQfTndX',
            'exits': 'https://discord.com/api/webhooks/1464758104267817220/sXhuVFWv7JcRjzpAvmsJyDxEH7U4b0qJnxPtHyOU0cTH1eK6RudbopxpglJZkMCcYek_',
            'errors': 'https://discordapp.com/api/webhooks/YOUR_ERRORS_WEBHOOK_ID/YOUR_ERRORS_WEBHOOK_TOKEN',
            'alerts': 'https://discord.com/api/webhooks/1480682368393281557/1y4udRzqXXy8sJCkIjyEr0TsPh7Boa6D7v70qsp0YBGYrl32rV3Lz7njbe6PWfeaxXvU',
        },
        # Legacy single webhook (deprecated — use 'webhooks' above)
        'webhook_url': 'https://discordapp.com/api/webhooks/1464758074941112353/dzvWJZwybS21NQ432EVRzZXBdYuymCcLu85vjYnDheDbyqECN_psBneA7neqUNQfTndX',
    }
}

