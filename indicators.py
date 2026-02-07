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


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index"""
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
    """Calculate MACD, Signal line, and Histogram"""
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average Directional Index (trend strength)"""
    plus_dm = df['high'].diff()
    minus_dm = -df['low'].diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    atr = calculate_atr(df, period)

    plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, 1e-10))
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, 1e-10))

    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1e-10))
    adx = dx.rolling(period).mean()
    return adx


def calculate_roc(df: pd.DataFrame, period: int = 10) -> pd.Series:
    """Calculate Rate of Change — price velocity indicator"""
    prev_close = df['close'].shift(period)
    return ((df['close'] - prev_close) / prev_close.replace(0, 1e-10)) * 100


def calculate_momentum_score(df: pd.DataFrame) -> pd.Series:
    """
    Composite momentum score (0-100) replacing 3 binary checks.
    Components: RSI (0-30), MACD (0-35), ADX (0-20), ROC (0-15)
    """
    rsi = calculate_rsi(df)
    macd_line, signal_line, macd_hist = calculate_macd(df)
    adx = calculate_adx(df)
    roc = calculate_roc(df)

    # RSI component (0-30): Peak at RSI 55, drops toward 30 and 80
    rsi_score = pd.Series(0.0, index=df.index)
    # Ideal RSI: 45-65 = full points, drops linearly outside
    rsi_score = np.where(
        (rsi >= 45) & (rsi <= 65), 30.0,
        np.where(rsi < 45, np.maximum(0, 30 - (45 - rsi) * 1.5),
                 np.maximum(0, 30 - (rsi - 65) * 2.0))
    )
    rsi_score = pd.Series(rsi_score, index=df.index)

    # MACD component (0-35): histogram > 0 AND accelerating
    macd_prev_hist = macd_hist.shift(1)
    macd_score = pd.Series(0.0, index=df.index)
    macd_score = np.where(
        (macd_hist > 0) & (macd_hist > macd_prev_hist), 35.0,  # Positive & accelerating
        np.where(macd_hist > 0, 20.0,  # Positive but decelerating
                 np.where(macd_hist > macd_prev_hist, 10.0, 0.0))  # Negative but recovering
    )
    macd_score = pd.Series(macd_score, index=df.index)

    # ADX component (0-20): Scaled for ADX 15-40 range
    adx_score = np.clip((adx - 15) / (40 - 15) * 20, 0, 20)
    adx_score = pd.Series(adx_score, index=df.index)

    # ROC component (0-15): ROC > 0 and < 20 (not overextended)
    roc_score = np.where(
        (roc > 0) & (roc <= 10), 15.0,
        np.where((roc > 10) & (roc <= 20), 10.0,
                 np.where(roc > 20, 5.0, 0.0))
    )
    roc_score = pd.Series(roc_score, index=df.index)

    total = rsi_score + macd_score + adx_score + roc_score
    return total.clip(0, 100)


def calculate_breakout_conviction(df: pd.DataFrame) -> pd.Series:
    """
    Breakout conviction score (0-100) — measures HOW the breakout happened.
    Components: close position (0-30), volume surge (0-30), gap (0-20), green streak (0-20)
    """
    # Close position (0-30): How close to the high of the bar
    bar_range = df['high'] - df['low']
    close_position = (df['close'] - df['low']) / bar_range.replace(0, 1e-10)
    close_score = (close_position * 30).clip(0, 30)

    # Volume surge (0-30)
    vol_ratio = calculate_volume_ratio(df)
    vol_score = np.where(
        vol_ratio >= 2.0, 30.0,
        np.where(vol_ratio >= 1.5, 20.0,
                 np.where(vol_ratio >= 1.2, 10.0, 0.0))
    )
    vol_score = pd.Series(vol_score, index=df.index)

    # Gap up (0-20): Opening gap > 1%
    prev_close = df['close'].shift(1)
    gap_pct = ((df['open'] - prev_close) / prev_close.replace(0, 1e-10)) * 100
    gap_score = np.where(
        gap_pct >= 2.0, 20.0,
        np.where(gap_pct >= 1.0, 15.0,
                 np.where(gap_pct >= 0.5, 10.0, 0.0))
    )
    gap_score = pd.Series(gap_score, index=df.index)

    # Green bars streak (0-20): Consecutive green bars in last 3 days
    green = (df['close'] > df['open']).astype(int)
    green_1 = green
    green_2 = green.shift(1).fillna(0)
    green_3 = green.shift(2).fillna(0)
    green_count = green_1 + green_2 + green_3
    green_score = np.where(
        green_count >= 3, 20.0,
        np.where(green_count >= 2, 13.0,
                 np.where(green_count >= 1, 7.0, 0.0))
    )
    green_score = pd.Series(green_score, index=df.index)

    total = close_score + vol_score + gap_score + green_score
    return total.clip(0, 100)


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

    # Momentum indicators
    df['RSI'] = calculate_rsi(df)
    macd, macd_signal, macd_hist = calculate_macd(df)
    df['MACD'] = macd
    df['MACD_Signal'] = macd_signal
    df['MACD_Hist'] = macd_hist
    df['ADX'] = calculate_adx(df)

    # Composite scores (V2)
    df['ROC'] = calculate_roc(df)
    df['Momentum_Score'] = calculate_momentum_score(df)
    df['Conviction_Score'] = calculate_breakout_conviction(df)

    return df
