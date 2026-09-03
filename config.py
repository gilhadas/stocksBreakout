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
    'momentum_surge': 15,       # V7: Explosive gap/intraday move + high volume — raised to catch single-stock surges
    'minervini_template': 0,    # V8: Minervini Stage 2 — optimizer eliminated
    'vcp_quality': 15,          # V10: VCP proportional score (0.0-1.0)
    'sr_breakout': 11,          # V11: Breaking above tested resistance (≥2 touches)
    'at_key_support': 24,       # V11: Price hugging a key support zone — optimizer top signal
    'trendline_break': 9,       # V11b: Breaking above angled resistance trendline (≥3 swing highs)
    # V1 legacy checks (explicit weights — were phantom 5 via default fallback)
    'vwap_ok': 5,               # V1: Price above VWAP
    'rsi_favorable': 5,         # V1: RSI in favorable zone
    'macd_favorable': 5,        # V1: MACD bullish
    'adx_trending': 5,          # V1: ADX showing trend strength
    # Shared checks
    'not_overextended': 5,      # V4: Not blown off from SMA (swing/longterm only)
    'aroon_confirm': 5,         # V12: Aroon oscillator confirms uptrend (osc > threshold)
    'tension_index': 10,        # V14: "Coiled spring" composite (compression + volume
                                #      consensus + market/sector confirmation + fractal
                                #      alignment) — proportional 0.0-1.0. Modest start to
                                #      avoid double-counting consolidation/vcp/rs; let the
                                #      optimizer retune once live data accumulates.
    'supertrend_bull': 15,      # V15: Supertrend (ATR-band) agrees with the long — the
                                #      canonical scalping whipsaw filter. Scalping/daytrade
                                #      only (gated by SUPERTREND_CONFIG); heavy weight so a
                                #      counter-trend scalp is demoted, not just nudged.
}

# Aroon indicator settings
AROON_N                  = 25   # standard lookback period
AROON_CONFIRM_THRESHOLD  = 50   # oscillator > 50 = strong uptrend confirmation

# V15: Supertrend filter — ATR-band trend overlay (quantkit.calculate_supertrend).
# Applied as the `supertrend_bull` scoring check on scalping/daytrade signals:
# require the Supertrend direction to be bullish (price above the line). Classic
# scalping triad = VWAP + Supertrend + StochRSI (the first and third already exist).
SUPERTREND_CONFIG = {
    'enabled':     False,  # V15 dormant: no validated edge for scalping (entry filter
                           # bull−bear −0.001%; trailing-stop worse) — 2026-06 validation.
                           # Keep indicator/tests/harnesses; flip True only if re-validated.
    'modes':       ('scalping', 'daytrade'),  # whipsaw control matters most intraday
    'period':      10,    # ATR lookback (10 = standard; lower = faster/noisier)
    'multiplier':  2.0,   # band width in ATRs (2.0 = scalping-tight; 3.0 = classic)
}

# V14: Tension Index — the "coiled spring" composite (quantkit/tension.py).
# Keys mirror quantkit.tension.TensionConfig; only overrides need to be listed.
# Sector context resolves the ticker's own sector ETF via get_sector_for_ticker
# + SENTIMENT['sector_etfs'] (XLK/XLE/XLV/…), defaulting to SPY when unknown —
# fully sector-agnostic.
TENSION_CONFIG = {
    'enabled': False,   # V14 dormant: no measured edge in swing (never fires, 100% BOUNCE)
                        # or daytrade (corr +0.000, 877 trades, 2026-06-22 validation).
                        # Code/tests/SYSTEM_SPEC kept for future live-data + Optuna retune.
    # Top-level blend (must sum to 1.0)
    'w_compression':  0.30,   # volatility "point of silence"
    'w_volume':       0.30,   # consensus vs the Value Area
    'w_confirmation': 0.20,   # market/sector rolling correlation + beta + RS
    'w_fractal':      0.20,   # 5-min structure agrees with the daily trend
    # Hard-gate multipliers applied to the raw index
    'gate_fractal':   0.50,   # LTF breakout fights the daily trend
    'gate_fakeout':   0.60,   # risk-off + high-correlation breakout
    # Downgrade a signal one quality tier when a fractal contradiction is detected
    'downgrade_on_contradiction': True,
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

# Thematic boosted sizing (opt-in via --boosted-sizing flag on breakout_scanner.py).
# Validated Apr 2026 on 54 thematic symbols × 12mo: +11.7% return vs current FinBERT,
# Sharpe unchanged (+0.01). Do NOT apply to general watchlists — same config costs
# -0.17 Sharpe on 200-symbol universe (return +6.7% but Sharpe 4.66 → 4.50).
# Applied at runtime by overriding ATR_SIZING + QUALITY_SIZING + MockTrader multipliers.
THEMATIC_BOOSTED_SIZING = {
    'max_single_position_pct': 0.20,   # override ATR_SIZING cap (10% → 20%)
    'GOLD_mult':    4.0,               # override QUALITY_SIZING GOLD (2.0 → 4.0)
    'PREMIUM_mult': 3.0,               # override QUALITY_SIZING PREMIUM (2.0 → 3.0)
}

# --- ATR Always-On Trailing Stop (champion exit, validated 2026-05-07) ---
# Trail activates from entry bar 1: stop = max(fixed_floor, price - ATR_TRAIL_MULT × ATR14).
# Fixed stop_loss acts as absolute floor for the first ATR_TRAIL_FLOOR_BARS trading bars
# (while ATR history warms up). After warmup, only the trail applies.
ATR_TRAIL_MULT       = 2.0   # ATR×2.0 — sweep winner: +234% 5yr vs +137% post-TP
ATR_TRAIL_FLOOR_BARS = 14    # Wilder's ATR(14) needs 14 bars to be meaningful

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
    'max_single_position_pct': 0.10,  # Hard cap: no position > 10% of capital
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
    'max_per_mode': 7,               # Max positions in same mode (swing/daytrade/longterm)
                                     # Raised from 5 (Apr 2026) — 5×3=15 was binding
                                     # ahead of cash; 7×3=21 lets more high-conviction
                                     # signals land while cash naturally caps total.
    'ideal_etf_pct': 0.20,          # Target 20% of positions in ETFs for stability
    'max_single_position_pct': 0.10, # Largest position should not exceed 10% of portfolio value
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

# Grade-to-score mapping for rr_ok check (proportional 0.0-1.0, used in scoring)
# These values are multiplied by SCORING_WEIGHTS['rr_ok'] to get final contribution.
# Optimizable by weight_optimizer.py.
RR_GRADE_SCORES = {'A': 0.53, 'B': 1.0, 'C': 0.57, 'D': 0.0}

# --- Momentum Override (bypasses consolidation for high-momentum breakouts) ---
MOMENTUM_OVERRIDE = {
    'min_momentum': 90,       # Momentum_Score >= 90 (near-perfect)
    'min_vol_ratio': 2.5,     # Volume >= 2.5× average
    'max_rsi': 75,            # RSI < 75 (not overbought)
}

# --- Bounce Bear Gate ---
# Block BOUNCE+RED_MARKET entries when SPY has been below its 200-day SMA for
# >= N consecutive trading days. 15 days distinguishes sustained bear markets
# (2022: 57% of days ≥15d) from brief corrections (2023/2025/2026: <15d).
BOUNCE_BEAR_GATE = 15

# --- Trend Confirmation Detector ---
# Catches "rip → continuation" momentum that the consolidation breakout detector
# misses. Requires fresh SMA150 + MACD + volume + RSI alignment without needing
# narrow Bollinger Band consolidation. High-conviction-only: GOLD/PREMIUM only.
# Modes: longterm + swing only — daytrade disabled (intraday MACD is too noisy).
TREND_CONFIRM = {
    'enabled':              True,
    'enabled_modes':        ['longterm', 'swing'],
    'enabled_paths':        ['A'],       # Path B disabled: fires 3.4× more in choppy markets, dilutes edge.
                                         # See note in scanner.detect_trend_confirm.
    'sma_cross_lookback':   10,    # bars within which SMA150 cross must have happened
    'sma_slope_lookback':   20,    # bars over which SMA150 slope is measured
    'macd_cross_lookback':  10,    # bars within which MACD bull cross must have happened
    'rsi_min':              55,
    'rsi_max':              72,
    'vol_ratio_min':        1.2,   # latest bar volume / 20-bar avg (clean breakouts often 1.2-1.5x)
    'vol_ratio_gold':       1.8,   # bonus: GOLD requires this much (true volume surge)
    'blow_off_max':         0.20,  # (price − SMA50) / SMA50 must stay below this
    'rs_min':               0.0,   # 20-day relative strength vs SPY (0 = not lagging)
    'rr_target_mult':       2.5,   # target = entry + RR × (entry − stop)
    'min_rr':               2.0,   # reject if computed R:R below this (stop too far)
    # --- Persistent-setup path (Path B) ---
    # Mega-cap institutional accumulation often rallies WITHOUT volume spikes
    # (AMD, NVDA, MU in April 2026 all rallied 5-15 days with vol < 1.2x).
    # If the trend has been mature for N consecutive days with only vol or
    # slope missing, fire as PREMIUM (high-conviction by persistence, not by
    # single-day spike).
    'persistent_lookback':  5,     # check last N bars
    'persistent_min_days':  4,     # need this many of N to qualify
}

# --- Pinned/Compressed Range Veto ---
# A stock trading in an abnormally tight range for an extended window (e.g. a
# cash-merger target pinned near the deal price) has collapsed volatility and
# no real trend — the opposite of a Stage 2 breakout — yet SMA/MACD/RSI can
# still spuriously align near a flat price and clear the top quality tier.
# CLAUDE.md §27 (2026-08-12): PRA/JHG/HOLX/STEL scored GOLD/TREND_CONFIRM while
# merger-arb pinned; this is the "best lead out of that session" — a
# signal-generation fix rather than another admission/ranking tweak (§13.5's
# meta-finding: ranking levers keep coming back null).
# Dormant until validated via the --reject-pinned-range backtest ablation
# (judge on the --realistic-sizing arm per the §11 standing rule).
PINNED_RANGE_CONFIG = {
    'enabled':         True,    # live 2026-08-21 — zero regression on 2 backtest universes;
                                 # see CLAUDE.md §28 for why the veto couldn't be positively
                                 # validated (trigger names drop out of yfinance history once delisted)
    'lookback_days':   60,      # trading days of high/low range checked
    'max_range_pct':   10.0,    # (lookback high − lookback low) / close, in percent
    'max_atr_pct':     1.5,     # current ATR / close, in percent — both must fire together
}

# --- Slow-Grind Detector ---
# Every existing detector needs either a sharp breakout candle (detect()),
# an oversold snap-back (detect_bounce), a fresh SMA20 cross, a near-unbroken
# consecutive-green streak (detect_continuation, 3+ days with NO red candle),
# or a full multi-gate confirmation in one bar (detect_trend_confirm). A stock
# that grinds steadily upward with the occasional red day mixed in — new highs
# most days, never a single dramatic move — satisfies none of them.
# 2026-08 case: NOW +31.5% and PLTR/IGV's earliest days ran without a signal
# because the move was a grind, not a breakout (confirmed live: detect()
# logged "no price break" on every checked date — the close kept setting only
# marginal new highs rather than a decisive break above resistance).
# Dormant until validated via backtest (--slow-grind CLI flag forces it on for
# an ablation run regardless of this default, mirroring --no-tc's pattern).
SLOW_GRIND_CONFIG = {
    'enabled':            False,   # OFF by default — new detector, unvalidated
    'lookback_days':      15,      # ~3 trading weeks
    'min_cum_return_pct': 10.0,    # net gain over the lookback
    'min_up_day_ratio':   0.55,    # majority up days, NOT a near-unbroken streak
    'near_high_pct':      2.0,     # today's close must be within this % of the lookback high
    'rsi_min':            50.0,    # healthy uptrend floor
    'rsi_max':            75.0,    # below detect_continuation's 80 blow-off guard — a grind
                                    # this overbought is more likely stalling than continuing
    'min_vol_ratio':      1.0,     # modest — no spike required, but not on dying volume
    'atr_stop_mult':      2.0,     # stop = max(lookback low, close - mult*ATR)
    'target_rr':          2.5,     # fixed R:R, matching TREND_CONFIRM's convention
}

# --- Selective Mode (high-conviction, ~100 trades/yr) ---
# Toggleable filter for the auto-portfolio admission stage. When enabled,
# stacks on top of the existing V9-H Quality+Minervini mask and the cross-day
# pooled cap. Designed to land at ~2 trades/week by excluding daytrade entirely
# (5-yr canonical backtest: ≤15-day holds = −2,851% sum PnL @ 10% wr; >15-day
# holds = +6,438% @ 66% wr — entire +195% edge comes from long holds) and
# capping daily admissions to 1.
# See tools/analyze_winning_patterns.py for the supporting evidence.
SELECTIVE_MODE = {
    'enabled':                False,                                 # OFF by default; flip to enable
    'min_quality':            ['GOLD', 'PREMIUM'],                   # drop HIGH/STANDARD
    'allowed_modes':          ['longterm', 'swing'],                 # daytrade EXCLUDED
    'allowed_signal_types':   ['BOUNCE', 'CONTINUATION', 'TREND_CONFIRM'],  # SMA20_CROSS dropped (canonical: 10 trades, −30% sum)
    'min_rr':                 2.0,                                   # conservative (no canonical evidence to tune higher)
    'min_winprob':            0.55,                                  # only applied when WinProb column present
    'min_minervini':          7,                                     # match existing baseline
    'max_adds_per_scan':      2,                                     # dominant volume lever; 2/day × ~50% active days ≈ ~100–130/yr on 200-sym list
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

# --- Empirical WinProb Calibration ---
# Lookup of win rates by SIGNAL_TYPE|QUALITY fitted from champion-exit backtest
# trade logs (calibrate_winprob.py → scanner_output/winprob_calibration.json).
# Where a bucket exists, it replaces the confluence heuristic in detect() and
# stamps WinProb onto BOUNCE/CONTINUATION/SMA20_CROSS/TREND_CONFIRM signals
# (which previously had none — they ranked as WinProb=0 in the admission sort).
# Ranking impact: auto_portfolio + backtest pooled cap sort Quality → WinProb → R:R.
WINPROB_CALIBRATION = {
    'enabled': True,
    'path': 'scanner_output/winprob_calibration.json',
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
    },
    'SURGE': {
        'vol_mult': 0.7,
        'atr_mult': 0.7,
        'description': 'Broad market surge day — relaxed filters, size-controlled',
        'spy_perf_threshold': 0.01,
        'spy_vol_threshold': 2.0
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
    'enabled': False,   # V9-C mode: BOUNCE-GOLD only; no regime blocking; V9-H full gating disabled
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

# --- Surge Day Mode (broad market gap-up detection) ---
# On broad market surge days (SPY gaps up + many stocks gapping), relax scanner
# filters to catch obvious movers that fail consolidation/RSI gates.
# Triggered when BOTH conditions met: SPY gap >= threshold AND breadth >= threshold.
# All changes gated behind is_surge=True — zero impact on normal days.
SURGE_DAY_CONFIG = {
    'enabled': True,
    # --- Detection thresholds ---
    'spy_gap_min_pct': 1.0,           # SPY pre-market gap >= 1.0%
    'breadth_min_gappers': 15,        # At least 15 symbols in premarket_watch.txt
    'spy_intraday_fallback_pct': 1.5, # Fallback: SPY intraday move >= 1.5% (no premarket data)
    # --- Relaxed filter thresholds ---
    'vol_ratio_min': 1.5,             # momentum_surge vol: 1.5x (was 3.0x)
    'move_thresh_pct': 3.0,           # momentum_surge move: 3% (was 5%)
    'mo_max_rsi': 85,                 # MOMENTUM_OVERRIDE RSI cap (was 75)
    'mo_min_vol': 1.5,                # MOMENTUM_OVERRIDE vol (was 2.5)
    # --- Score adjustments ---
    'score_thresholds': {
        'GOLD': 90,                   # Was 99
        'PREMIUM': 60,                # Was 69
        'HIGH': 55,                   # Was 65
        'STANDARD': 40,               # Was 50
    },
    # --- Safety rails ---
    'max_signals_per_scan': 10,       # Cap total surge signals (prevent alert flood)
    'min_quality': 'HIGH',            # Minimum quality to emit on surge day
    'pos_size_mult': 0.7,             # 70% position sizing (risk control on volatile days)
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

# --- Alpha Vantage News Sentiment ---
ALPHA_VANTAGE_API_KEY = os.environ.get('ALPHA_VANTAGE_API_KEY', '')

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
    # GOLD means "passed hard structural gates" for the detectors that define
    # one: BREAKOUT (Type '' or 'Momentum', scanner.py detect() gold_gates —
    # R:R>=3, trend line, vol_ratio>=2, near 52w high, hot sector) and
    # TREND_CONFIRM (vol_ratio_gold + golden cross). BOUNCE/CONTINUATION/
    # SMA20_CROSS/PULLBACK/SLOW_GRIND have NO native GOLD tier at all — for
    # those, sentiment alone must not mint a label that downstream code
    # (regime-restricted admission, BOUNCE notification gate, priority
    # ranking, quality_risk_penalty) treats as hard-gated-safe. Only these
    # types may be promoted PREMIUM->GOLD by FinBERT; every other type stays
    # capped at PREMIUM regardless of sentiment strength.
    'premium_to_gold_types': {'', 'Momentum', 'TREND_CONFIRM'},
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

# --- Thematic Watchlists (dedicated scheduled scans) ---
# Each list is scanned independently by its own cron entry (see cron_jobs.txt).
# Symbols are kept in input/thematic_*.txt as the source of truth for the scanner;
# this dict is a programmatic reference only.
THEMATIC_WATCHLISTS = {
    'tsmc_supply_chain': {
        'file': 'input/thematic_tsmc.txt',
        'rationale': 'TSMC CapEx / AI build-out beneficiaries: cooling/power, front-end equipment, OSAT, EDA',
    },
    'space': {
        'file': 'input/thematic_space.txt',
        'rationale': 'Satellite / launch / space infrastructure names',
    },
    'quantum': {
        'file': 'input/thematic_quantum.txt',
        'rationale': 'Pure-play quantum computing names',
    },
    'nuclear': {
        'file': 'input/thematic_nuclear.txt',
        'rationale': 'Nuclear energy / SMR / uranium — AI data-center power demand play',
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
    
    # Discord notifications via webhook — URLs (embedded tokens) come from .env,
    # never hardcoded here (this file is committed to git).
    'discord': {
        'enabled': False,  # Telegram is the live alert channel; Discord disabled
        'webhooks': {
            'signals': os.environ.get('DISCORD_WEBHOOK_SIGNALS', ''),
            'exits':   os.environ.get('DISCORD_WEBHOOK_EXITS', ''),
            'errors':  os.environ.get('DISCORD_WEBHOOK_ERRORS', ''),
            'alerts':  os.environ.get('DISCORD_WEBHOOK_ALERTS', ''),
        },
        # Legacy single webhook (deprecated — use 'webhooks' above)
        'webhook_url': os.environ.get('DISCORD_WEBHOOK_URL', ''),
    }
}

