"""TradingView Chart Page — candlestick charts with signal overlays."""
import streamlit as st
import pandas as pd
import yfinance as yf
from streamlit_lightweight_charts import renderLightweightCharts


def _fetch_chart_data(symbol, period='1y'):
    """Fetch OHLCV data from yfinance."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period)
    if df.empty:
        return None
    df = df.reset_index()
    df['time'] = df['Date'].dt.strftime('%Y-%m-%d')
    return df


def _build_chart_config(df, symbol, signal=None):
    """Build lightweight-charts config dict for Streamlit."""
    # Candlestick data
    candle_data = df[['time', 'Open', 'High', 'Low', 'Close']].rename(columns={
        'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close'
    }).to_dict('records')

    # Volume data
    volume_data = []
    for i, row in df.iterrows():
        color = 'rgba(38,166,154,0.5)' if row['Close'] >= row['Open'] else 'rgba(239,83,80,0.5)'
        volume_data.append({
            'time': row['time'],
            'value': int(row['Volume']),
            'color': color,
        })

    # SMA 150
    df['SMA150'] = df['Close'].rolling(150).mean()
    sma150_data = [
        {'time': row['time'], 'value': round(row['SMA150'], 2)}
        for _, row in df.iterrows()
        if pd.notna(row['SMA150'])
    ]

    # SMA 20
    df['SMA20'] = df['Close'].rolling(20).mean()
    sma20_data = [
        {'time': row['time'], 'value': round(row['SMA20'], 2)}
        for _, row in df.iterrows()
        if pd.notna(row['SMA20'])
    ]

    # Build series
    series = [
        {
            'type': 'Candlestick',
            'data': candle_data,
            'options': {
                'upColor': '#26a69a',
                'downColor': '#ef5350',
                'borderVisible': False,
                'wickUpColor': '#26a69a',
                'wickDownColor': '#ef5350',
            },
        },
        {
            'type': 'Line',
            'data': sma150_data,
            'options': {
                'color': '#2196F3',
                'lineWidth': 2,
                'title': 'SMA 150',
            },
        },
        {
            'type': 'Line',
            'data': sma20_data,
            'options': {
                'color': '#FF9800',
                'lineWidth': 1,
                'title': 'SMA 20',
            },
        },
        {
            'type': 'Histogram',
            'data': volume_data,
            'options': {
                'priceFormat': {'type': 'volume'},
                'priceScaleId': 'volume',
            },
            'priceScale': {
                'scaleMargins': {'top': 0.7, 'bottom': 0},
            },
        },
    ]

    # Add signal price lines if available
    if signal:
        markers = []
        last_date = df['time'].iloc[-1]

        # Entry marker
        markers.append({
            'time': last_date,
            'position': 'belowBar',
            'color': '#2196F3',
            'shape': 'arrowUp',
            'text': f"Entry ${signal.get('Price', 0):.2f}",
        })

        # Add markers to candlestick series
        series[0]['markers'] = markers

    chart_config = {
        'chart': {
            'layout': {
                'background': {'type': 'solid', 'color': '#131722'},
                'textColor': '#d1d4dc',
            },
            'grid': {
                'vertLines': {'color': '#1e222d'},
                'horzLines': {'color': '#1e222d'},
            },
            'crosshair': {'mode': 0},
            'timeScale': {'borderColor': '#485c7b'},
        },
        'series': series,
    }

    return chart_config


def render_chart_page():
    # Back button to return to Scan
    if st.button("Back to Scan"):
        st.session_state['_next_page'] = 'Scan'
        st.rerun()

    st.header("TradingView Chart")

    # Symbol input
    col1, col2 = st.columns([3, 1])

    default_symbol = st.session_state.get('chart_symbol', 'SPY')
    with col1:
        symbol = st.text_input("Symbol", value=default_symbol, key="chart_sym_input")
    with col2:
        period = st.selectbox("Period", ['3mo', '6mo', '1y', '2y'], index=2)

    signal = st.session_state.get('chart_signal')

    if not symbol:
        st.info("Enter a symbol to view chart.")
        return

    # Fetch data
    with st.spinner(f"Loading {symbol}..."):
        df = _fetch_chart_data(symbol.upper(), period)

    if df is None or df.empty:
        st.error(f"No data found for {symbol}")
        return

    # Signal info bar
    if signal and signal.get('Symbol', '').upper() == symbol.upper():
        cols = st.columns(6)
        cols[0].metric("Quality", signal.get('Quality', 'N/A'))
        cols[1].metric("Entry", f"${signal.get('Price', 0):.2f}")
        cols[2].metric("Stop", f"${signal.get('Stop', 0):.2f}")
        cols[3].metric("Target", f"${signal.get('Target', 0):.2f}")
        cols[4].metric("R:R", f"{signal.get('R:R', 0):.1f}")
        sma_dist = signal.get('SMA_Dist%', 0)
        cols[5].metric("SMA Dist%", f"{sma_dist:.1f}%",
                        delta=f"{'OK' if abs(sma_dist) <= 10 else 'Extended'}")

        # Draw horizontal lines info
        st.markdown(
            f"**Entry:** :blue[${signal.get('Price', 0):.2f}] | "
            f"**Stop:** :red[${signal.get('Stop', 0):.2f}] | "
            f"**Target:** :green[${signal.get('Target', 0):.2f}] | "
            f"**Pattern:** {signal.get('Patterns', 'N/A')}"
        )

    # Build and render chart
    chart_config = _build_chart_config(df, symbol, signal if signal and signal.get('Symbol', '').upper() == symbol.upper() else None)

    renderLightweightCharts([chart_config], key=f"chart_{symbol}_{period}")

    # Price stats
    latest = df.iloc[-1]
    sma150_val = df['Close'].rolling(150).mean().iloc[-1]
    if pd.notna(sma150_val):
        dist = ((latest['Close'] - sma150_val) / sma150_val) * 100
        st.caption(
            f"{symbol} | Close: ${latest['Close']:.2f} | "
            f"SMA 150: ${sma150_val:.2f} | Distance: {dist:+.1f}%"
        )
