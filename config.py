"""
Configuration module for breakout scanner
Contains all mode settings and regime configurations
"""

# --- Portfolio Configuration ---
PORTFOLIO = {
    'initial_capital': 100000,      # Starting capital in USD
    'max_position_pct': 0.10,       # 10% base, scaled by quality (PREMIUM=30%)
    'max_risk_pct': 0.02,           # 2% risk per trade (backtested optimal)
    'use_trailing_stop': False,     # Trailing stops cut winners — backtesting shows 2x better returns without
    'trailing_stop_atr_mult': 4.0,  # Optimized: Wide trail prevents shakeouts
    'trailing_stop_activation_pct': 0.10, # Optimized: Only trail after +10% profit
    'use_scoring_system': True,     # Use weighted scoring instead of all-or-nothing
    'use_pullback_entries': True,   # Enable pullback re-entry signals
    'max_concurrent_positions': 10, # Max open positions at once
}

# --- Signal Scoring Configuration ---
# V2: Composite momentum + conviction scoring (replaces 3 binary checks)
SCORING_WEIGHTS = {
    'vol_confirm': 16,          # Volume is king
    'trend_ok': 16,             # Trend is king
    'momentum_strong': 13,      # Composite RSI+MACD+ADX+ROC score >= 50
    'dist_confirm': 10,
    'candle_ok': 8,
    'rr_ok': 10,
    'no_vol_divergence': 5,
    'conviction_strong': 8,     # Breakout conviction score >= 40
    'rs_ok': 8,
    'consolidation': 8,
    'has_bullish_pattern': 10,  # V3: Pattern confirmation bonus
}

SCORE_THRESHOLDS = {
    'PREMIUM': 80,
    'HIGH': 65,
    'STANDARD': 60,
}

QUALITY_SIZING = {
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
    'tavily_api_key': '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',  # Set via TAVILY_API_KEY env var or here
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

NOTIFICATIONS = {
    # Email notifications via SMTP
    'email': {
        'enabled': True,  # Set to True to enable
        'smtp_server': 'smtp.gmail.com',
        'smtp_port': 587,
        'sender_email': 'gil.hadas@gmail.com',
        'sender_password': 'xjby qirq zotu jqdk',  # Use Gmail App Password
        'recipient_email': 'gil.hadas@gmail.com',
    },
    
    # Telegram notifications
    'telegram': {
        'enabled': False,
        'bot_token': '',  # Get from @BotFather
        'chat_id': '',  # Your chat ID
    },
    
    # Discord notifications via webhook
    'discord': {
        'enabled': True,  # Set to True to enable
        'webhook_url': 'https://discordapp.com/api/webhooks/1464758074941112353/dzvWJZwybS21NQ432EVRzZXBdYuymCcLu85vjYnDheDbyqECN_psBneA7neqUNQfTndX',
    }
}

