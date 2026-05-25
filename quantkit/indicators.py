"""
Technical indicators calculation module
"""

from typing import Dict, List

import pandas as pd
import numpy as np


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range using Wilder's smoothing (EMA with alpha=1/period)."""
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    # Wilder's smoothing: alpha = 1/period (com = period-1 in pandas)
    return true_range.ewm(com=period - 1, adjust=False).mean()


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
            lambda x: (x['typical_price'] * x['volume']).cumsum() / x['volume'].cumsum(),
            include_groups=False,
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


def calculate_bb_trend(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> tuple:
    """
    Calculate Bollinger Band trend position.
    Returns: (bb_position, bb_trend)
    - bb_position: 0-100 scale of where price sits within bands
    - bb_trend: 'bullish' (>=80), 'bearish' (<=20), or 'neutral'
    """
    upper, lower, _, _, _ = calculate_bollinger_bands(df, period, std_dev)
    band_width = upper - lower
    bb_position = ((df['close'] - lower) / band_width.replace(0, 1e-10)) * 100
    bb_position = bb_position.clip(0, 100)

    bb_trend = pd.Series('neutral', index=df.index)
    bb_trend = bb_trend.where(bb_position < 80, 'bullish')
    bb_trend = bb_trend.where(bb_position > 20, 'bearish')

    return bb_position, bb_trend


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
    safe_atr = atr if (atr and not pd.isna(atr) and atr > 0) else 1e-6
    body_top = max(open_price, close)
    upper_wick = high - body_top
    body_top_pct = (high - close) / rng

    is_valid = (
        (upper_wick / safe_atr) <= max_wick_atr and
        body_top_pct <= max_body_top_pct
    )
    
    return is_valid, upper_wick, body_top_pct


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate RSI using Wilder's EMA — matches TradingView's standard RSI indicator."""
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calculate_stochastic_rsi(df: pd.DataFrame, rsi_period: int = 14,
                             stoch_period: int = 14,
                             k_smooth: int = 3, d_smooth: int = 3) -> tuple:
    """
    Calculate Stochastic RSI — momentum oscillator that measures RSI
    relative to its own high-low range.

    Returns (stoch_k, stoch_d) where:
      stoch_k = %K line (smoothed stochastic of RSI, 0-100)
      stoch_d = %D line (signal, SMA of %K)

    Overbought > 80, Oversold < 20.
    """
    rsi = calculate_rsi(df, rsi_period)
    rsi_min = rsi.rolling(stoch_period).min()
    rsi_max = rsi.rolling(stoch_period).max()
    rsi_range = (rsi_max - rsi_min).replace(0, 1e-10)
    stoch_rsi = (rsi - rsi_min) / rsi_range * 100
    stoch_k = stoch_rsi.rolling(k_smooth).mean()
    stoch_d = stoch_k.rolling(d_smooth).mean()
    return stoch_k, stoch_d


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


def calculate_aroon(df: pd.DataFrame, n: int = 25) -> pd.DataFrame:
    """Aroon Up/Down/Oscillator over n periods. Osc > +50 = strong uptrend."""
    roll_high = df['high'].rolling(n + 1)
    roll_low  = df['low'].rolling(n + 1)
    # argmax/argmin position (0=oldest, n=newest) directly encodes recency
    aroon_up   = roll_high.apply(lambda x: int(x.argmax()) / n * 100, raw=True)
    aroon_down = roll_low.apply(lambda x: int(x.argmin()) / n * 100, raw=True)
    return pd.DataFrame(
        {'aroon_up': aroon_up, 'aroon_down': aroon_down, 'aroon_osc': aroon_up - aroon_down},
        index=df.index,
    )


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


def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 20) -> tuple:
    """
    Detect RSI divergence (bullish and bearish) — fully vectorized.
    - Bullish: price makes new N-bar low while RSI remains above its N-bar low
    - Bearish: price makes new N-bar high while RSI remains below its N-bar high

    Rolling windows exclude the current bar (shift(1)) so we look back into
    prior bars — matching the original intent.

    Returns: (bullish_div: pd.Series[bool], bearish_div: pd.Series[bool])
    """
    rsi = calculate_rsi(df)
    close = df['close']

    # Extremes of previous `lookback` bars (shift(1) excludes current bar)
    roll_price_min = close.shift(1).rolling(lookback).min()
    roll_price_max = close.shift(1).rolling(lookback).max()
    roll_rsi_min   = rsi.shift(1).rolling(lookback).min()
    roll_rsi_max   = rsi.shift(1).rolling(lookback).max()

    # Bullish: price undercuts the window low but RSI holds above its window low
    bullish = (
        (close < roll_price_min) &
        (rsi > roll_rsi_min) &
        rsi.notna() & roll_rsi_min.notna()
    )

    # Bearish: price exceeds the window high but RSI stays below its window high
    bearish = (
        (close > roll_price_max) &
        (rsi < roll_rsi_max) &
        rsi.notna() & roll_rsi_max.notna()
    )

    return bullish.fillna(False), bearish.fillna(False)


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

    # BB Trend
    bb_position, bb_trend = calculate_bb_trend(df)
    df['BB_Position'] = bb_position
    df['BB_Trend'] = bb_trend

    # Momentum indicators
    df['RSI'] = calculate_rsi(df)
    macd, macd_signal, macd_hist = calculate_macd(df)
    df['MACD'] = macd
    df['MACD_Signal'] = macd_signal
    df['MACD_Hist'] = macd_hist
    df['ADX'] = calculate_adx(df)

    # EMA 9 / EMA 21 — fast trend for scalping & daytrade
    df['EMA_9']  = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()

    # Stochastic RSI — momentum oscillator (overbought > 80, oversold < 20)
    df['StochRSI_K'], df['StochRSI_D'] = calculate_stochastic_rsi(df)

    # Composite scores (V2)
    df['ROC'] = calculate_roc(df)
    df['Momentum_Score'] = calculate_momentum_score(df)
    df['Conviction_Score'] = calculate_breakout_conviction(df)

    # RSI divergence (V5)
    df['RSI_Bull_Div'], df['RSI_Bear_Div'] = detect_rsi_divergence(df)

    return df


def compute_volume_profile(df: pd.DataFrame, lookback: int = 60,
                           num_bins: int = 50) -> Dict:
    """
    Compute Volume-at-Price profile from OHLCV data.

    Bins the price range into `num_bins` buckets, distributes each bar's
    volume across the bins its high-low range touches (uniform allocation),
    then identifies the Point of Control (VPOC) and Value Area (70% of volume).

    Parameters
    ----------
    df        : OHLCV DataFrame (columns: high, low, close, volume)
    lookback  : bars to include (default 60 ≈ 3 months daily)
    num_bins  : resolution of the price histogram (default 50)

    Returns
    -------
    dict with:
      vpoc            : float — price level with the highest traded volume
      value_area_high : float — upper bound of the 70% value area
      value_area_low  : float — lower bound of the 70% value area
      high_volume_nodes : list[float] — bin centers with volume > 1.5× median
      low_volume_nodes  : list[float] — bin centers with volume < 0.5× median
      bin_centers       : np.ndarray — all bin center prices
      bin_volumes       : np.ndarray — volume at each bin
    """
    data = df.iloc[-lookback:] if len(df) > lookback else df
    if len(data) < 10:
        return {
            'vpoc': float(data['close'].iloc[-1]),
            'value_area_high': float(data['high'].max()),
            'value_area_low': float(data['low'].min()),
            'high_volume_nodes': [],
            'low_volume_nodes': [],
            'bin_centers': np.array([]),
            'bin_volumes': np.array([]),
        }

    price_min = float(data['low'].min())
    price_max = float(data['high'].max())
    if price_max <= price_min:
        price_max = price_min + 0.01

    bin_edges = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    bin_volumes = np.zeros(num_bins)

    # Vectorized: for each bar, spread volume across bins its range touches
    highs = data['high'].values
    lows = data['low'].values
    volumes = data['volume'].values

    for i in range(len(data)):
        bar_low, bar_high, bar_vol = lows[i], highs[i], volumes[i]
        if bar_vol <= 0 or np.isnan(bar_vol):
            continue
        # Find bins overlapping this bar's range
        first_bin = np.searchsorted(bin_edges, bar_low, side='right') - 1
        last_bin = np.searchsorted(bin_edges, bar_high, side='left')
        first_bin = max(0, first_bin)
        last_bin = min(num_bins - 1, last_bin)
        n_bins_touched = last_bin - first_bin + 1
        if n_bins_touched > 0:
            bin_volumes[first_bin:last_bin + 1] += bar_vol / n_bins_touched

    # VPOC: bin with max volume
    vpoc_idx = int(np.argmax(bin_volumes))
    vpoc = float(bin_centers[vpoc_idx])

    # Value Area: 70% of total volume, expanding outward from VPOC
    total_vol = bin_volumes.sum()
    va_target = total_vol * 0.70
    va_vol = bin_volumes[vpoc_idx]
    va_low_idx, va_high_idx = vpoc_idx, vpoc_idx

    while va_vol < va_target and (va_low_idx > 0 or va_high_idx < num_bins - 1):
        expand_low = bin_volumes[va_low_idx - 1] if va_low_idx > 0 else 0
        expand_high = bin_volumes[va_high_idx + 1] if va_high_idx < num_bins - 1 else 0
        if expand_low >= expand_high and va_low_idx > 0:
            va_low_idx -= 1
            va_vol += bin_volumes[va_low_idx]
        elif va_high_idx < num_bins - 1:
            va_high_idx += 1
            va_vol += bin_volumes[va_high_idx]
        else:
            va_low_idx -= 1
            va_vol += bin_volumes[va_low_idx]

    # High/low volume nodes
    median_vol = float(np.median(bin_volumes[bin_volumes > 0])) if bin_volumes.sum() > 0 else 1.0
    hvn = [float(bin_centers[i]) for i in range(num_bins) if bin_volumes[i] > 1.5 * median_vol]
    lvn = [float(bin_centers[i]) for i in range(num_bins) if 0 < bin_volumes[i] < 0.5 * median_vol]

    return {
        'vpoc': vpoc,
        'value_area_high': float(bin_centers[va_high_idx]),
        'value_area_low': float(bin_centers[va_low_idx]),
        'high_volume_nodes': hvn,
        'low_volume_nodes': lvn,
        'bin_centers': bin_centers,
        'bin_volumes': bin_volumes,
    }


def calculate_minervini_template(
    df: pd.DataFrame,
    high_52w: float = None,
    low_52w: float = None,
) -> tuple:
    """
    Evaluate Mark Minervini's Stage 2 Trend Template (8 conditions).

    Conditions
    ----------
    C1  Price > SMA150 AND Price > SMA200
    C2  SMA150 > SMA200
    C3  SMA200 slope is positive (trending up over last 20 trading days)
    C4  SMA50 > SMA150 AND SMA50 > SMA200
    C5  Price > SMA50
    C6  Price is at least 25% above its 52-week low
    C7  Price is within 25% of its 52-week high  (price / 52w_high >= 0.75)
    C8  Relative Strength: stock 252-day return > 0  (positive annual momentum,
        a simplified proxy for IBD RS Rating >= 70)

    Parameters
    ----------
    df        : DataFrame with a 'close' column (daily bars recommended)
    high_52w  : pre-computed 52-week high (optional, computed from df if None)
    low_52w   : pre-computed 52-week low  (optional, computed from df if None)

    Returns
    -------
    (conditions: dict[str, bool], score: int 0-8)
    Empty dict and 0 if insufficient data (<50 bars).
    """
    if df is None or len(df) < 50:
        return {}, 0

    close = df['close']
    price = float(close.iloc[-1])

    # ── Moving averages ───────────────────────────────────────────────────────
    sma50  = float(close.rolling(50).mean().iloc[-1])  if len(df) >= 50  else None
    sma150 = float(close.rolling(150).mean().iloc[-1]) if len(df) >= 150 else None
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(df) >= 200 else None

    # SMA200 slope: compare today's SMA200 to value 20 bars ago
    sma200_trending_up = False
    if sma200 is not None and len(df) >= 221:
        sma200_series  = close.rolling(200).mean()
        sma200_20d_ago = sma200_series.iloc[-21]
        if not pd.isna(sma200_20d_ago):
            sma200_trending_up = sma200 > float(sma200_20d_ago)

    # ── 52-week high / low ────────────────────────────────────────────────────
    if high_52w is None:
        high_52w = float(close.rolling(252).max().iloc[-1]) if len(df) >= 252 else float(close.max())
    if low_52w is None:
        low_52w  = float(close.rolling(252).min().iloc[-1]) if len(df) >= 252 else float(close.min())

    # ── Relative Strength (simplified) ───────────────────────────────────────
    # True IBD RS Rating requires comparing vs. 10 000 stocks; we use the
    # stock's own 252-day return as a proxy: positive annual return ≈ RS ≥ 70.
    rs_positive = False
    if len(df) >= 252:
        price_252_ago = float(close.iloc[-252])
        if price_252_ago > 0:
            rs_positive = price > price_252_ago   # stock is up on the year

    # ── Evaluate all 8 conditions ─────────────────────────────────────────────
    def _above(*args):
        return all(v is not None and not pd.isna(v) for v in args)

    conditions = {
        'c1_price_above_sma150_200': (
            _above(sma150, sma200) and price > sma150 and price > sma200
        ),
        'c2_sma150_above_sma200': (
            _above(sma150, sma200) and sma150 > sma200
        ),
        'c3_sma200_trending_up': sma200_trending_up,
        'c4_sma50_above_sma150_200': (
            _above(sma50, sma150, sma200) and sma50 > sma150 and sma50 > sma200
        ),
        'c5_price_above_sma50': (
            _above(sma50) and price > sma50
        ),
        'c6_above_52w_low_25pct': (
            low_52w > 0 and price >= low_52w * 1.25
        ),
        'c7_within_25pct_of_52w_high': (
            high_52w > 0 and price >= high_52w * 0.75
        ),
        'c8_rs_positive': rs_positive,
    }

    score = sum(conditions.values())
    return conditions, score
