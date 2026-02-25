"""Scan Dashboard — card-based signal view with inline TradingView charts."""
import streamlit as st
import pandas as pd
import yfinance as yf
import asyncio
from streamlit_lightweight_charts import renderLightweightCharts

from utils import load_data, list_files, _to_local_abs


# ──────────────────────────────────────
# Data helpers
# ──────────────────────────────────────

def _get_watchlist_files():
    """Get available watchlist files from input/ directory."""
    names = list_files('input', '*.txt')
    return {n.replace('.txt', ''): _to_local_abs(f"input/{n}")
            for n in names if n != 'email_recipients.txt'}


def _load_latest_signals():
    """Load the most recent signals CSV."""
    signals_dir = 'scanner_output/signals'
    csvs = list_files(signals_dir, 'signals_*.csv')
    if not csvs:
        return None
    latest = csvs[0]  # list_files returns newest-first
    df = load_data(f"{signals_dir}/{latest}")
    if df is None:
        return None
    return df, latest


def _run_async(coro):
    """Run an async coroutine from Streamlit's synchronous thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ensure_event_loop():
    """Ensure an event loop exists in the current thread (needed for ib_insync import)."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _run_scan(mode, watchlist_path):
    """Run the scanner and return results as DataFrame."""
    _ensure_event_loop()  # ib_insync/eventkit needs a loop at import time
    from orchestrator import ScannerOrchestrator
    from utils import get_watchlist_from_file, classify_market_regime
    from config import MODES

    symbols = get_watchlist_from_file(watchlist_path)
    if not symbols:
        return None, "No symbols loaded", None

    orchestrator = ScannerOrchestrator(None, yf_fallback=True)
    timeframe = MODES[mode]['default_timeframe']
    lookback = MODES[mode]['lookback']

    results = _run_async(orchestrator.scan_watchlist(
        watchlist=symbols,
        mode=mode,
        timeframe=timeframe,
        lookback=lookback,
        detect_bounces=True,
    ))

    spy_perf, spy_vol = _run_async(
        orchestrator.market_data.get_spy_performance(timeframe, lookback)
    )
    regime = classify_market_regime(spy_perf, spy_vol)
    csv_path = orchestrator.save_results(results, mode, 'signals')
    return results, regime, f"SPY {spy_perf:+.2%}", csv_path


@st.cache_data(ttl=3600)
def _fetch_mini_chart(symbol, period='6mo'):
    """Fetch OHLCV data for mini chart (cached 1h)."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period)
        if df.empty:
            return None
        df = df.reset_index()
        df['time'] = df['Date'].dt.strftime('%Y-%m-%d')
        return df
    except Exception:
        return None


@st.cache_data(ttl=3600)
def _fetch_ticker_info(symbol):
    """Fetch ticker info from yfinance (cached 1h)."""
    try:
        info = yf.Ticker(symbol).info
        return {
            'Sector': info.get('sector', 'N/A'),
            'Industry': info.get('industry', 'N/A'),
            'Market Cap': _fmt_market_cap(info.get('marketCap', 0)),
            'P/E': f"{info.get('trailingPE', 0):.1f}" if info.get('trailingPE') else 'N/A',
            'Day Change': f"{info.get('regularMarketChangePercent', 0):+.2f}%",
        }
    except Exception:
        return {}


def _fmt_market_cap(val):
    if not val:
        return 'N/A'
    if val >= 1e12:
        return f"{val/1e12:.2f}T"
    if val >= 1e9:
        return f"{val/1e9:.2f}B"
    if val >= 1e6:
        return f"{val/1e6:.0f}M"
    return str(val)


# ──────────────────────────────────────
# Score & description helpers
# ──────────────────────────────────────

def _get_score(signal):
    """Derive a numeric score 0-100 from signal quality."""
    wp = signal.get('WinProb', 0)
    if wp and pd.notna(wp) and float(wp) > 0:
        return int(float(wp) * 100)
    return {'PREMIUM': 89, 'HIGH': 78, 'STANDARD': 65}.get(
        signal.get('Quality', ''), 50
    )


def _score_breakdown(signal):
    """Build score breakdown items: (description, points, max_pts)."""
    items = []

    rsi = signal.get('RSI')
    if rsi and pd.notna(rsi):
        rsi = float(rsi)
        if 45 <= rsi <= 55:
            items.append((f"RSI {rsi:.1f} — dead-center neutral, ideal coil", 18, 18))
        elif 30 <= rsi < 45:
            items.append((f"RSI {rsi:.1f} — mild weakness, pullback zone", 12, 18))
        elif rsi < 30:
            items.append((f"RSI {rsi:.1f} — oversold, bounce candidate", 15, 18))
        elif 55 < rsi <= 70:
            items.append((f"RSI {rsi:.1f} — mild strength, trending", 14, 18))
        else:
            items.append((f"RSI {rsi:.1f} — overbought, extended", 6, 18))

    vol = signal.get('Vol')
    if vol and pd.notna(vol):
        vol = float(vol)
        if vol >= 2.0:
            items.append((f"Rel. volume {vol:.1f}x — surge, strong interest", 15, 15))
        elif vol >= 1.0:
            items.append((f"Rel. volume {vol:.1f}x — above average", 10, 15))
        else:
            items.append((f"Rel. volume {vol:.2f}x — quiet pullback, sellers exhausted", 15, 15))

    sma = signal.get('SMA_Dist%')
    if sma and pd.notna(sma):
        sma = float(sma)
        if abs(sma) <= 5:
            items.append((f"Price {sma:+.1f}% from SMA150 — touching support", 10, 10))
        elif abs(sma) <= 10:
            items.append((f"Price {sma:+.1f}% from SMA150 — healthy distance", 8, 10))
        elif sma > 15:
            items.append((f"Price {sma:+.1f}% from SMA150 — over-extended", -5, 10))
        elif sma < -10:
            items.append((f"Price {sma:+.1f}% from SMA150 — below trend", -12, 10))
        else:
            items.append((f"Price {sma:+.1f}% from SMA150 — moderate distance", 5, 10))

    pattern = signal.get('Patterns')
    if pattern and pd.notna(pattern):
        vol_conf = signal.get('PatternVolConf', False)
        suffix = " (vol confirmed)" if vol_conf else ""
        pts = 10 if vol_conf else 6
        items.append((f"Pattern: {pattern}{suffix}", pts, 10))

    near52 = signal.get('Near52wHigh', False)
    if near52:
        items.append(("Near 52-week high", 8, 8))

    rsi_div = signal.get('RSI_BullDiv', False)
    if rsi_div:
        items.append(("RSI bullish divergence", 5, 5))

    return items


# ──────────────────────────────────────
# Chart builder (mini inline chart)
# ──────────────────────────────────────

def _build_mini_chart(df, symbol, signal=None):
    """Build lightweight-charts config for inline mini chart."""
    candle_data = df[['time', 'Open', 'High', 'Low', 'Close']].rename(columns={
        'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close'
    }).to_dict('records')

    volume_data = []
    for _, row in df.iterrows():
        color = 'rgba(38,166,154,0.5)' if row['Close'] >= row['Open'] else 'rgba(239,83,80,0.5)'
        volume_data.append({
            'time': row['time'],
            'value': int(row['Volume']),
            'color': color,
        })

    df['SMA50'] = df['Close'].rolling(50).mean()
    sma_data = [
        {'time': row['time'], 'value': round(row['SMA50'], 2)}
        for _, row in df.iterrows()
        if pd.notna(row['SMA50'])
    ]

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
            'data': sma_data,
            'options': {
                'color': '#FF9800',
                'lineWidth': 1,
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

    if signal:
        markers = []
        last_date = df['time'].iloc[-1]
        markers.append({
            'time': last_date,
            'position': 'belowBar',
            'color': '#2196F3',
            'shape': 'arrowUp',
            'text': f"Entry ${signal.get('Price', 0):.2f}",
        })
        series[0]['markers'] = markers

    return {
        'chart': {
            'layout': {
                'background': {'type': 'solid', 'color': '#131722'},
                'textColor': '#d1d4dc',
            },
            'grid': {
                'vertLines': {'color': '#1e222d'},
                'horzLines': {'color': '#1e222d'},
            },
            'height': 300,
        },
        'series': series,
    }


# ──────────────────────────────────────
# Signal card renderer
# ──────────────────────────────────────

def _render_signal_card(signal, card_key):
    """Render a single signal card with expandable details."""
    symbol = signal.get('Symbol', '?')
    price = float(signal.get('Price', 0))
    quality = signal.get('Quality', 'STANDARD')
    stop = float(signal.get('Stop', 0))
    target = float(signal.get('Target', 0))
    rr = float(signal.get('R:R', 0))
    sig_type = signal.get('Type', '')
    if pd.isna(sig_type):
        sig_type = ''

    gain_pct = ((target - price) / price * 100) if price > 0 else 0
    risk_pct = ((price - stop) / price * 100) if price > 0 else 0
    score = _get_score(signal)

    q_colors = {
        'GOLD': ('#3d3d1a', '#ffd700'),
        'PREMIUM': ('#1a5c1a', '#4caf50'),
        'HIGH': ('#1a3d5c', '#2196F3'),
        'STANDARD': ('#3d3d1a', '#ff9800'),
    }
    bg, accent = q_colors.get(quality, ('#333', '#aaa'))
    score_color = '#4caf50' if score >= 80 else '#ff9800' if score >= 65 else '#ef5350'

    # ── Card HTML ──
    st.markdown(f"""
    <div style="border:1px solid #333; border-radius:10px; padding:16px; background:#1e222d; margin-bottom:4px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
            <div>
                <span style="font-size:20px; font-weight:bold; color:#fff;">{symbol}</span>
                <span style="color:#666; font-size:11px; margin-left:6px;">{sig_type}</span>
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:18px; color:#fff;">${price:.2f}</span>
                <span style="background:{bg}; padding:3px 12px; border-radius:4px; color:{accent};
                       font-weight:bold; font-size:12px;">{quality}</span>
            </div>
        </div>
        <div style="display:flex; gap:6px; margin:10px 0;">
            <div style="flex:1; background:#262730; border:1px solid #444; border-radius:6px;
                        padding:8px; text-align:center;">
                <div style="color:#888; font-size:10px; text-transform:uppercase;">Entry</div>
                <div style="font-weight:bold; color:#fff; font-size:14px;">${price:.2f}</div>
            </div>
            <div style="flex:1; background:#0d2618; border:1px solid #1a5c1a; border-radius:6px;
                        padding:8px; text-align:center;">
                <div style="color:#888; font-size:10px; text-transform:uppercase;">TP</div>
                <div style="font-weight:bold; color:#4caf50; font-size:14px;">${target:.2f}</div>
            </div>
            <div style="flex:1; background:#2a1111; border:1px solid #5c1a1a; border-radius:6px;
                        padding:8px; text-align:center;">
                <div style="color:#888; font-size:10px; text-transform:uppercase;">SL</div>
                <div style="font-weight:bold; color:#ef5350; font-size:14px;">${stop:.2f}</div>
            </div>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; margin:8px 0 4px 0;">
            <div style="display:flex; align-items:center; gap:6px;">
                <span style="color:#aaa; font-size:11px;">SWING SCORE</span>
                <span style="font-weight:bold; color:{score_color}; font-size:15px;">{score}</span>
                <div style="background:#333; border-radius:4px; height:6px; width:80px;
                            display:inline-block; overflow:hidden;">
                    <div style="background:{score_color}; height:6px; width:{min(score,100)}%;
                                border-radius:4px;"></div>
                </div>
            </div>
            <span style="color:#aaa; font-size:12px;">R:R: <b style="color:#fff;">{rr:.0f}:1</b></span>
        </div>
        <div style="display:flex; gap:8px; margin-top:8px;">
            <span style="background:#0d2618; color:#4caf50; padding:2px 10px; border-radius:4px;
                         font-size:11px;">Gain +{gain_pct:.1f}%</span>
            <span style="background:#2a1111; color:#ef5350; padding:2px 10px; border-radius:4px;
                         font-size:11px;">Risk -{risk_pct:.1f}%</span>
        </div>
        {'<div style="display:flex; flex-wrap:wrap; gap:4px; margin-top:6px;">' + ''.join(f'<span style="background:#1a2a3d; color:#64b5f6; padding:2px 8px; border-radius:3px; font-size:10px;">{p.strip()}</span>' for p in str(signal.get("Patterns", "")).split(",") if p.strip()) + '</div>' if signal.get("Patterns") else ''}
    </div>
    """, unsafe_allow_html=True)

    # ── Chart button ──
    if st.button(f"Chart {symbol}", key=f"chart_{card_key}", use_container_width=True):
        st.session_state['chart_symbol'] = symbol
        st.session_state['chart_signal'] = signal
        st.session_state['_next_page'] = 'Chart'
        st.rerun()

    # ── Expandable details ──
    with st.expander(f"Details — {symbol}", expanded=False):

        # Score breakdown
        breakdown = _score_breakdown(signal)
        if breakdown:
            st.markdown("**SWING SCORE BREAKDOWN**")
            for desc, pts, max_pts in breakdown:
                pct = max(0, min(100, int(abs(pts) / max_pts * 100))) if max_pts else 0
                bar_color = '#4caf50' if pts > 0 else '#ef5350'
                st.markdown(f"""
                <div style="display:flex; justify-content:space-between; align-items:center;
                            background:#262730; border-radius:4px; padding:6px 10px; margin:3px 0;">
                    <span style="font-size:12px; color:#ccc; flex:3;">{desc}</span>
                    <div style="flex:1; display:flex; align-items:center; justify-content:flex-end; gap:8px;">
                        <div style="background:#333; border-radius:3px; height:5px; width:60px; overflow:hidden;">
                            <div style="background:{bar_color}; height:5px; width:{pct}%;"></div>
                        </div>
                        <span style="color:{bar_color}; font-weight:bold; font-size:13px; min-width:35px;
                                     text-align:right;">{pts:+d}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)

        st.markdown("")

        # ── Price chart ──
        st.markdown("**PRICE CHART**")
        chart_df = _fetch_mini_chart(symbol, '6mo')
        if chart_df is not None and not chart_df.empty:
            chart_config = _build_mini_chart(chart_df, symbol, signal)
            renderLightweightCharts([chart_config], key=f"mini_{card_key}")

            # Technical snapshot from chart data
            latest = chart_df.iloc[-1]
            sma50_val = chart_df['Close'].rolling(50).mean().iloc[-1]
            sma200_val = chart_df['Close'].rolling(200).mean().iloc[-1]
            hi52 = chart_df['High'].max()
            lo52 = chart_df['Low'].min()
            avg_vol = int(chart_df['Volume'].mean())

            st.markdown("**TECHNICAL SNAPSHOT**")
            tech_data = {
                'Open': f"${latest['Open']:.2f}",
                'High': f"${latest['High']:.2f}",
                'Low': f"${latest['Low']:.2f}",
                'Close': f"${latest['Close']:.2f}",
                'SMA 50': f"${sma50_val:.2f}" if pd.notna(sma50_val) else 'N/A',
                'SMA 200': f"${sma200_val:.2f}" if pd.notna(sma200_val) else 'N/A',
            }
            rsi_val = signal.get('RSI')
            if rsi_val and pd.notna(rsi_val):
                tech_data['RSI (14)'] = f"{float(rsi_val):.1f}"
            tech_data['Volume'] = f"{int(latest['Volume']):,}"
            tech_data['Avg Volume'] = f"{avg_vol:,}"
            tech_data['52w High'] = f"${hi52:.2f}"
            tech_data['52w Low'] = f"${lo52:.2f}"

            tech_df = pd.DataFrame(
                list(tech_data.items()), columns=['Metric', 'Value']
            )
            st.dataframe(tech_df, hide_index=True, height=200)
        else:
            st.warning(f"No chart data for {symbol}")

        # ── Scanner/Fundamental data ──
        info = _fetch_ticker_info(symbol)
        if info:
            st.markdown("**SCANNER DATA**")
            info_df = pd.DataFrame(
                list(info.items()), columns=['Metric', 'Value']
            )
            st.dataframe(info_df, hide_index=True, height=120)


# ──────────────────────────────────────
# Main page
# ──────────────────────────────────────

def render_scan_page():
    # ── Top controls ──
    col_mode, col_wl, col_filter, col_run = st.columns([2, 3, 2, 2])

    with col_mode:
        mode = st.selectbox("Mode", ["swing", "longterm", "daytrade"], index=0)

    watchlists = _get_watchlist_files()
    with col_wl:
        wl_keys = list(watchlists.keys())
        if wl_keys:
            default_idx = wl_keys.index('S&P_500') if 'S&P_500' in wl_keys else 0
            watchlist_name = st.selectbox("Watchlist", wl_keys, index=default_idx)
        else:
            watchlist_name = None
            import utils
            st.warning(f"No watchlists in input is_cloud: {utils.is_cloud()} ")

    with col_filter:
        quality_filter = st.selectbox("Filter", ["GOLD", "PREMIUM+", "HIGH+", "ALL"], index=2)

    with col_run:
        st.markdown("<br>", unsafe_allow_html=True)
        run_clicked = st.button("Run Scan", type="primary", use_container_width=True)

    # Sidebar notification toggle
    with st.sidebar:
        notify_on_scan = st.checkbox("Notify on scan complete", value=False,
                                     help="Send email/Discord notifications after scan")

    # ── Run scan ──
    if run_clicked and watchlist_name:
        with st.spinner(f"Scanning {watchlist_name} ({mode})..."):
            try:
                results, regime, spy_info, csv_path = _run_scan(mode, watchlists[watchlist_name])
                if results:
                    st.session_state['scan_results'] = results
                    st.session_state['scan_regime'] = regime
                    st.session_state['scan_spy'] = spy_info
                    st.session_state['scan_mode'] = mode

                    # Send notifications if enabled
                    if notify_on_scan and results:
                        try:
                            from notifier import Notifier
                            notifier = Notifier()
                            n = len(results)
                            qualities = [r.get('Quality', '?') for r in results]
                            subject = f"Scan: {n} {mode} signals"
                            message = f"{n} signals found ({', '.join(set(qualities))})"
                            notifier.send_all(
                                subject=subject,
                                message=message,
                                signals=results,
                                csv_path=csv_path,
                            )
                            st.toast(f"Notifications sent ({n} signals)")
                        except Exception as ne:
                            st.warning(f"Notification failed: {ne}")
                else:
                    st.session_state['scan_results'] = []
            except Exception as e:
                st.error(f"Scan error: {e}")

    # ── Load data ──
    results = st.session_state.get('scan_results')
    if results is None:
        loaded = _load_latest_signals()
        if loaded:
            df, filename = loaded
            st.caption(f"Latest saved scan: {filename}")
        else:
            st.info("No scan results yet. Click **Run Scan** to start.")
            return
    else:
        if not results:
            st.info("No signals in last scan.")
            return
        df = pd.DataFrame(results)

    if df.empty:
        st.info("No signals to display.")
        return

    # ── Filter by quality ──
    if 'Quality' in df.columns:
        if quality_filter == "GOLD":
            df = df[df['Quality'] == 'GOLD']
        elif quality_filter == "PREMIUM+":
            df = df[df['Quality'].isin(['GOLD', 'PREMIUM'])]
        elif quality_filter == "HIGH+":
            df = df[df['Quality'].isin(['GOLD', 'PREMIUM', 'HIGH'])]

    if df.empty:
        st.warning(f"No {quality_filter} signals.")
        return

    # ── Regime banner ──
    regime = st.session_state.get('scan_regime', '')
    spy_info = st.session_state.get('scan_spy', '')
    if regime:
        regime_colors = {'NORMAL': 'green', 'CHOPPY': 'orange', 'EXPANSION': 'blue'}
        color = regime_colors.get(regime, 'gray')
        st.markdown(f"**Market Regime:** :{color}[{regime}] | {spy_info}")

    # ── Signals header ──
    total = len(df)
    premium_ct = len(df[df['Quality'] == 'PREMIUM']) if 'Quality' in df.columns else 0
    high_ct = len(df[df['Quality'] == 'HIGH']) if 'Quality' in df.columns else 0
    bounce_ct = len(df[df['Type'] == 'BOUNCE']) if 'Type' in df.columns else 0

    st.markdown(f"""
    <div style="display:flex; justify-content:space-between; align-items:center;
                padding:8px 0; border-bottom:1px solid #333; margin-bottom:12px;">
        <span style="font-size:14px; color:#aaa; font-weight:bold;">
            SIGNALS ({total})
        </span>
        <div style="display:flex; gap:12px; font-size:13px;">
            <span style="color:#4caf50;">&#9679; Premium: {premium_ct}</span>
            <span style="color:#2196F3;">&#9679; High: {high_ct}</span>
            <span style="color:#ff9800;">&#9679; Bounce: {bounce_ct}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Card grid (3 columns) ──
    cols = st.columns(3)
    for idx, (_, row) in enumerate(df.iterrows()):
        with cols[idx % 3]:
            _render_signal_card(row.to_dict(), f"{idx}")

    # ── CSV download ──
    st.markdown("---")
    csv_data = df.to_csv(index=False)
    st.download_button("Download CSV", csv_data, "signals.csv", "text/csv")
