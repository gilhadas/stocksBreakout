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
    'initial_capital': 100000,      # Starting capital in USD
    'max_position_pct': 0.10,       # 10% base, scaled by quality (PREMIUM=30%)
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
    'GOLD': 4.0,      # Up to 20% of capital (max conviction)
    'PREMIUM': 3.0,   # Up to 15% of capital
    'HIGH': 2.0,      # Up to 10% of capital
    'STANDARD': 1.0,  # 5% of capital (base)
}

# --- R:R Grade Configuration ---
RR_GRADE_CONFIG = {
    'A': {'min_rr': 3.0, 'reject': False},
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
        'lookback': 50,
        'vol_thresh': 1.2,
        'atr_mult': 0.8,
        'trend_type': 'SMA',
        'trend_period': 150,
        'sl_mult': 3.0,
        'tp_mult': 6.0,
        'min_consolidation_bars': 5,
        'min_rr': 2.5,
        'max_wick_atr': 0.4,
        'max_body_top_pct': 0.25,
        'default_timeframe': '1 day',  # Use daily, not weekly (IB uses '1W' not '1 week')
        'description': 'Position trading - weeks to months'
    },
    'swing': {
        'lookback': 15,     # Optimized: Shorter lookback improves performance (+4.1% vs +2.8%)
        'vol_thresh': 1.3,
        'atr_mult': 0.5,
        'trend_type': 'SMA',
        'trend_period': 150,
        'sl_mult': 4.0,     # Optimized: Wider stop (was 2.0) improves win rate
        'tp_mult': 8.0,     # Optimized: Higher target allows Trailing Stop to work
        'min_consolidation_bars': 3,
        'min_rr': 1.0,      # Optimized: High Win Rate (66%) allows lower RR (1:1)
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
        'sl_fixed_cents': 2,       # Fixed stop loss in cents (scalping: 1-2¢ from entry)
        'tp_fixed_cents': 6,       # Fixed take profit in cents (3:1 R:R)
        'min_consolidation_bars': 1,
        'min_rr': 1.0,
        'max_spread_pct': 0.1,
        'min_price': 5.0,
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
    }
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
        'min_score': 0.70,   # e.g. 70% confident bullish
        'min_net':   0.25,   # e.g. 5↑/2↓ out of 8 headlines
    },
    'premium_to_gold': {
        'min_score': 0.82,   # higher bar — GOLD is the top tier
        'min_net':   0.40,   # majority of headlines clearly bullish
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

