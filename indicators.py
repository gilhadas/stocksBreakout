"""
Technical indicators calculation module
"""

import pandas as pd
import numpy as np


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range"""
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean()


def calculate_volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Calculate volume ratio vs moving average"""
    vol_ma = df['volume'].rolling(period).mean()
    return df['volume'] / vol_ma


def calculate_vwap(df: pd.DataFrame, timeframe: str) -> pd.Series:
    """
    Calculate VWAP (Volume Weighted Average Price)
    Resets daily for intraday timeframes
    """
    df = df.copy()
    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
    
    # Reset VWAP daily for intraday bars
    if hasattr(df.index, 'date'):
        df['date_only'] = df.index.date
        vwap = df.groupby('date_only').apply(
            lambda x: (x['typical_price'] * x['volume']).cumsum() / x['volume'].cumsum()
        ).reset_index(level=0, drop=True)
    else:
        # Single day or continuous
        vwap = (df['typical_price'] * df['volume']).cumsum() / df['volume'].cumsum()
    
    return vwap


def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> tuple:
    """
    Calculate Bollinger Bands
    Returns: (upper, lower, width, avg_width, is_consolidating)
    """
    sma = df['close'].rolling(period).mean()
    std = df['close'].rolling(period).std()
    
    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)
    width = ((upper - lower) / sma) * 100
    avg_width = width.rolling(20).mean()
    is_consolidating = width < (avg_width * 0.6)
    
    return upper, lower, width, avg_width, is_consolidating


def calculate_trend_line(df: pd.DataFrame, trend_type: str, period: int) -> pd.Series:
    """
    Calculate trend line based on type
    Types: SMA, EMA, or VWAP
    """
    if trend_type == 'EMA':
        return df['close'].ewm(span=period, adjust=False).mean()
    elif trend_type == 'SMA':
        return df['close'].rolling(period).mean()
    else:
        raise ValueError(f"Unknown trend type: {trend_type}")


def calculate_gap_percent(df: pd.DataFrame) -> float:
    """Calculate opening gap percentage"""
    if len(df) < 2:
        return 0.0
    prev_close = df['close'].iloc[-2]
    current_open = df['open'].iloc[-1]
    gap_pct = ((current_open - prev_close) / prev_close) * 100
    return float(gap_pct)


def check_volume_divergence(df: pd.DataFrame, lookback: int = 10) -> bool:
    """
    Check for volume divergence
    Returns True if price increasing but volume decreasing
    """
    if len(df) < lookback:
        return False
    
    latest = df.iloc[-1]
    recent_vol = df['volume'].tail(5).mean()
    prev_vol = df['volume'].iloc[-lookback:-5].mean()
    price_increasing = latest['close'] > df['close'].iloc[-5]
    
    return price_increasing and recent_vol < prev_vol * 0.7


def check_candle_structure(latest: pd.Series, atr: float, 
                          max_wick_atr: float, max_body_top_pct: float) -> tuple:
    """
    Check candle structure quality
    Returns: (is_valid, upper_wick, body_top_pct)
    """
    high = latest['high']
    low = latest['low']
    close = latest['close']
    open_price = latest['open']
    
    rng = max(high - low, 1e-6)
    body_top = max(open_price, close)
    upper_wick = high - body_top
    body_top_pct = (high - close) / rng
    
    is_valid = (
        (upper_wick / atr) <= max_wick_atr and
        body_top_pct <= max_body_top_pct
    )
    
    return is_valid, upper_wick, body_top_pct


def calculate_all_indicators(df: pd.DataFrame, trend_type: str, 
                             trend_period: int, timeframe: str) -> pd.DataFrame:
    """
    Calculate all technical indicators for a dataframe
    """
    df = df.copy()
    
    # ATR
    df['ATR'] = calculate_atr(df)
    
    # Volume
    df['Vol_MA'] = df['volume'].rolling(20).mean()
    df['Vol_Ratio'] = calculate_volume_ratio(df)
    
    # Trend Line
    if trend_type == 'VWAP':
        df['Trend_Line'] = calculate_vwap(df, timeframe)
    else:
        df['Trend_Line'] = calculate_trend_line(df, trend_type, trend_period)
    
    # VWAP for intraday
    if 'min' in timeframe or 'hour' in timeframe:
        df['vwap'] = calculate_vwap(df, timeframe)
    else:
        df['vwap'] = np.nan
    
    # Bollinger Bands
    upper, lower, width, avg_width, is_consol = calculate_bollinger_bands(df)
    df['BB_Upper'] = upper
    df['BB_Lower'] = lower
    df['BB_Width'] = width
    df['Avg_BB_Width'] = avg_width
    df['Is_Consolidating'] = is_consol
    
    return df
