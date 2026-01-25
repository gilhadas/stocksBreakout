"""
Configuration module for breakout scanner
Contains all mode settings and regime configurations
"""

# --- Portfolio Configuration ---
PORTFOLIO = {
    'initial_capital': 100000,      # Starting capital in USD
    'max_position_pct': 0.05,       # Max 5% of capital per position
    'max_risk_pct': 0.01,           # Max 1% of capital risk per trade
    'use_trailing_stop': True,      # Enable ATR-based trailing stop
    'trailing_stop_atr_mult': 3.0,  # Trail by 3x ATR (wider stop, more breathing room)
    'trailing_stop_activation_pct': 0.05, # Only activate trailing stop after 5% profit
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
        'lookback': 20,
        'vol_thresh': 1.3,
        'atr_mult': 0.5,
        'trend_type': 'SMA',
        'trend_period': 150,
        'sl_mult': 2.0,
        'tp_mult': 4.0,
        'min_consolidation_bars': 3,
        'min_rr': 2.0,
        'max_wick_atr': 0.5,
        'max_body_top_pct': 0.3,
        'default_timeframe': '1 day',
        'description': 'Swing trading - days to weeks'
    },
    'daytrade': {
        'lookback': 10,
        'vol_thresh': 1.1,
        'atr_mult': 0.2,
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

