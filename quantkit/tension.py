"""
Tension Index — the "coiled spring" composite that precedes a breakout
======================================================================

The Tension Index (TI ∈ [0, 1]) measures the *structural state that precedes a
high-quality breakout* rather than a lagging momentum reading. It combines four
sub-scores, each ∈ [0, 1]:

    C — Compression      : volatility "point of silence" (BB + ATR squeeze)
    V — Volume consensus : position vs the Value Area + institutional follow-through
    F — Confirmation     : market/sector context (rolling correlation, beta, RS)
    A — Fractal alignment: lower-timeframe structure agrees with the higher-TF trend

    TI_raw = wC·C + wV·V + wF·F + wA·A

Two multiplicative *hard gates* then penalise contradictions:
    - a lower-TF breakout that fights the daily trend (fractal contradiction)
    - a breakout into a risk-off, high-correlation tape (likely fakeout)

Design notes
------------
- **Sector-agnostic / general purpose.** Nothing here assumes a particular
  sector; the sector ETF is supplied by the caller.
- **Portable.** Depends only on ``quantkit.indicators`` + pandas/numpy. It does
  not import the top-level ``config`` / ``scanner`` modules, so the file can be
  lifted into another project that vendors ``quantkit`` unchanged.
- **Never raises on bad data.** Short history / all-NaN inputs return safe
  zeroed defaults, mirroring ``compute_volume_profile``'s short-data guard.

All DataFrames use lowercase OHLCV columns (open/high/low/close/volume), matching
the rest of ``quantkit``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple

import numpy as np
import pandas as pd

from quantkit.indicators import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_volume_ratio,
    compute_volume_profile,
    calculate_minervini_template,
)


# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class TensionConfig:
    """Tunable weights & parameters for the Tension Index.

    Sub-score component weights each sum to 1.0 so every sub-score stays in
    [0, 1]; the four top-level weights also sum to 1.0.
    """

    # Top-level blend
    w_compression: float = 0.30
    w_volume: float = 0.30
    w_confirmation: float = 0.20
    w_fractal: float = 0.20

    # Compression (C) component weights
    c_bb: float = 0.35
    c_atr: float = 0.30
    c_squeeze: float = 0.20
    c_duration: float = 0.15

    # Volume consensus (V) component weights
    v_va_accept: float = 0.30
    v_vol_expand: float = 0.25
    v_close_pos: float = 0.20
    v_base_below: float = 0.15
    v_rel_pos: float = 0.10

    # Confirmation (F) component weights
    f_market: float = 0.30
    f_sector: float = 0.25
    f_rs: float = 0.20
    f_corr_band: float = 0.15
    f_beta: float = 0.10

    # Fractal (A) component weights
    a_htf_trend: float = 0.60
    a_align: float = 0.40

    # Periods / windows
    bb_period: int = 20
    bb_rank_window: int = 100        # trailing window for BB-width percentile rank
    atr_period: int = 14
    atr_long: int = 50               # long ATR window for the squeeze ratio
    kc_period: int = 20              # Keltner channel period (squeeze test)
    kc_mult: float = 1.5             # Keltner ATR multiplier
    duration_ref: int = 10           # bars of consolidation that map to full credit
    vp_lookback: int = 60            # volume-profile lookback
    vol_period: int = 20             # volume MA period for Vol_Ratio
    vol_expand_ref: float = 2.0      # Vol_Ratio that maps to full expansion credit
    hvn_below_pct: float = 0.05      # an HVN within 5% below price = accepted shelf
    corr_win: int = 20               # rolling correlation/beta window (bars)
    rs_lookback: int = 20            # relative-strength lookback (bars)
    brk_lookback: int = 20           # prior-high window used to flag a breakout bar

    # Thresholds
    silence_bb_pct: float = 0.80     # BB compression >= this ⇒ "point of silence"
    silence_min_bars: int = 5        # …held at least this many bars
    corr_lo: float = 0.20            # healthy participation band (lower)
    corr_hi: float = 0.80            # healthy participation band (upper)
    high_corr: float = 0.70          # correlation above which a risk-off break is suspect
    contradiction_htf: float = 0.25  # daily trend below this + LTF up = contradiction
    coil_thresh: float = 0.60        # C above this (no break yet) ⇒ COILED state
    release_v: float = 0.50          # V above this on a breakout bar ⇒ RELEASING state
    min_bars: int = 20               # minimum bars before TI is meaningful

    # Hard-gate multipliers
    gate_fractal: float = 0.50       # penalty when LTF break fights the daily trend
    gate_fakeout: float = 0.60       # penalty for risk-off + high-corr breakout

    # Regimes treated as risk-off (accepts both quantkit and scanner vocabularies)
    risk_off_regimes: Set[str] = field(
        default_factory=lambda: {"bear", "BEARISH", "RED_MARKET", "bearish"}
    )


DEFAULT = TensionConfig()


# ──────────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────────
def _last(series: pd.Series, default: float = float("nan")) -> float:
    """Last non-NaN-safe float of a series."""
    if series is None or len(series) == 0:
        return default
    val = series.iloc[-1]
    return float(val) if pd.notna(val) else default


def _clip01(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    return float(min(1.0, max(0.0, x)))


def _percentile_rank(series: pd.Series, window: int) -> float:
    """Fraction of the trailing `window` values <= the current value (0..1).

    A low BB-width relative to its own history yields a low rank.
    """
    s = series.dropna()
    if len(s) < 5:
        return float("nan")
    tail = s.iloc[-window:]
    current = tail.iloc[-1]
    return float((tail <= current).mean())


def _consec_true(mask: pd.Series) -> int:
    """Count consecutive True values ending at the last bar."""
    if mask is None or len(mask) == 0:
        return 0
    count = 0
    for val in reversed(mask.tolist()):
        if bool(val):
            count += 1
        else:
            break
    return count


def _aligned_returns(close_a: pd.Series, close_b: pd.Series,
                     win: int) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    """Aligned daily returns of two close series over the last `win` bars.

    Aligns on the common index when both are datetime-indexed; otherwise falls
    back to positional tail-alignment. Returns (None, None) if too few points.
    """
    if close_a is None or close_b is None:
        return None, None
    ra = close_a.pct_change().dropna()
    rb = close_b.pct_change().dropna()
    if len(ra) < 5 or len(rb) < 5:
        return None, None

    common = ra.index.intersection(rb.index)
    if len(common) >= max(5, win // 2):
        ra, rb = ra.loc[common], rb.loc[common]
    else:
        n = min(len(ra), len(rb))
        ra = pd.Series(ra.iloc[-n:].values)
        rb = pd.Series(rb.iloc[-n:].values)

    ra, rb = ra.iloc[-win:], rb.iloc[-win:]
    if len(ra) < 5:
        return None, None
    return ra, rb


# ──────────────────────────────────────────────────────────────────────────────
# 1. Compression — "the point of silence"
# ──────────────────────────────────────────────────────────────────────────────
def compression_score(df: pd.DataFrame, cfg: TensionConfig = DEFAULT) -> Dict:
    """Volatility compression: low (in its own history), flat, and persistent.

    Returns dict: {C, bb_comp, atr_comp, squeeze, duration, point_of_silence}.
    """
    blank = {"C": 0.0, "bb_comp": 0.0, "atr_comp": 0.0,
             "squeeze": 0.0, "duration": 0.0, "point_of_silence": False}
    if df is None or len(df) < cfg.bb_period + 2:
        return blank

    upper, lower, width, avg_width, is_consol = calculate_bollinger_bands(df, cfg.bb_period)

    # BB-width percentile rank → compression is the inverse (low width = high comp)
    rank = _percentile_rank(width, cfg.bb_rank_window)
    bb_comp = 0.0 if rank != rank else _clip01(1.0 - rank)

    # Short-vs-long ATR squeeze ratio
    atr = calculate_atr(df, cfg.atr_period)
    atr_long = atr.rolling(cfg.atr_long).mean()
    atr_now, atr_ref = _last(atr), _last(atr_long)
    atr_comp = _clip01(1.0 - atr_now / atr_ref) if (atr_ref and atr_ref > 0) else 0.0

    # TTM-style squeeze: Bollinger Bands inside the Keltner channel
    mid = df["close"].ewm(span=cfg.kc_period, adjust=False).mean()
    atr_kc = calculate_atr(df, cfg.kc_period)
    kc_upper = mid + cfg.kc_mult * atr_kc
    kc_lower = mid - cfg.kc_mult * atr_kc
    squeeze = 1.0 if (
        _last(upper) < _last(kc_upper) and _last(lower) > _last(kc_lower)
    ) else 0.0

    # Persistence: consecutive bars in a low-volatility (consolidating) state
    consec = _consec_true(is_consol.fillna(False))
    duration = _clip01(consec / cfg.duration_ref)

    C = (cfg.c_bb * bb_comp + cfg.c_atr * atr_comp
         + cfg.c_squeeze * squeeze + cfg.c_duration * duration)

    point_of_silence = bool(bb_comp >= cfg.silence_bb_pct and consec >= cfg.silence_min_bars)

    return {"C": _clip01(C), "bb_comp": bb_comp, "atr_comp": atr_comp,
            "squeeze": squeeze, "duration": duration,
            "point_of_silence": point_of_silence}


# ──────────────────────────────────────────────────────────────────────────────
# 2. Volume consensus — institutional follow-through vs noise
# ──────────────────────────────────────────────────────────────────────────────
def volume_consensus_score(df: pd.DataFrame, vp: Optional[Dict] = None,
                           cfg: TensionConfig = DEFAULT) -> Dict:
    """Order-flow consensus relative to the Value Area.

    Consensus (not noise) = acceptance above the value area's upper edge, on a
    sustained volume expansion, with the bar closing in its upper third and a
    high-volume node (accepted shelf) built just beneath price.

    Returns dict: {V, va_accept, vol_expand, close_pos, base_below, rel_pos}.
    """
    blank = {"V": 0.0, "va_accept": 0.0, "vol_expand": 0.0,
             "close_pos": 0.0, "base_below": 0.0, "rel_pos": 0.0}
    if df is None or len(df) < 10:
        return blank

    if vp is None:
        vp = compute_volume_profile(df, lookback=cfg.vp_lookback)

    last = df.iloc[-1]
    close = float(last["close"])
    vah = float(vp["value_area_high"])
    val = float(vp["value_area_low"])
    vpoc = float(vp["vpoc"])

    # Acceptance relative to the value area
    if close > vah:
        va_accept = 1.0
    elif close > vpoc:
        va_accept = 0.5
    else:
        va_accept = 0.0

    # Sustained volume expansion (reuse the standard Vol_Ratio)
    vol_ratio = _last(calculate_volume_ratio(df, cfg.vol_period), default=0.0)
    vol_expand = _clip01(vol_ratio / cfg.vol_expand_ref)

    # Close in the upper third of the bar = follow-through, not a wick rejection
    bar_range = float(last["high"]) - float(last["low"])
    close_pos = _clip01((close - float(last["low"])) / bar_range) if bar_range > 0 else 0.0

    # An accepted-volume shelf sitting just below price
    floor_price = close * (1.0 - cfg.hvn_below_pct)
    base_below = 1.0 if any(
        floor_price <= n <= close for n in vp.get("high_volume_nodes", [])
    ) else 0.0

    # Position within the value area, mapped to 0..1 (top of value = 1)
    span = (vah - val)
    rel = (close - vpoc) / span if span > 1e-9 else 0.0
    rel_pos = _clip01((max(-1.0, min(1.0, rel)) + 1.0) / 2.0)

    V = (cfg.v_va_accept * va_accept + cfg.v_vol_expand * vol_expand
         + cfg.v_close_pos * close_pos + cfg.v_base_below * base_below
         + cfg.v_rel_pos * rel_pos)

    return {"V": _clip01(V), "va_accept": va_accept, "vol_expand": vol_expand,
            "close_pos": close_pos, "base_below": base_below, "rel_pos": rel_pos}


# ──────────────────────────────────────────────────────────────────────────────
# 3. Confirmation — market & sector context (rolling correlation / beta / RS)
# ──────────────────────────────────────────────────────────────────────────────
def confirmation_score(df: pd.DataFrame, spy_df: Optional[pd.DataFrame] = None,
                       sector_df: Optional[pd.DataFrame] = None,
                       regime: Optional[str] = None, spy_perf: float = 0.0,
                       cfg: TensionConfig = DEFAULT) -> Dict:
    """False-breakout filter: is the market/sector confirming this move?

    `df` should be the ticker's *daily* series (correlation/RS are higher-TF
    concepts). `spy_df` / `sector_df` are daily OHLCV for the index / the
    ticker's own sector ETF. Falls back gracefully when context is missing.

    Returns dict including F plus the flags the hard gates need
    (market_risk_off, high_corr, mkt_corr, sect_beta).
    """
    blank = {"F": 0.5, "mkt_corr": float("nan"), "sect_beta": float("nan"),
             "rs_ok": False, "market_ok": True, "sector_ok": False,
             "corr_band": 0.5, "beta_quality": 0.0,
             "market_risk_off": False, "high_corr": False}
    if df is None or len(df) < 5:
        return blank

    close = df["close"]

    # Relative strength vs SPY (reuse the project's RS convention)
    rs_ok = False
    if len(close) > cfg.rs_lookback:
        stock_perf = float(close.iloc[-1] / close.iloc[-cfg.rs_lookback - 1] - 1.0)
        rs_ok = stock_perf > spy_perf

    # Market regime gate (accepts both quantkit and scanner regime vocabularies)
    if regime is not None:
        market_ok = regime not in cfg.risk_off_regimes
    elif spy_df is not None and len(spy_df) >= 50:
        sma50 = spy_df["close"].rolling(50).mean().iloc[-1]
        market_ok = bool(pd.notna(sma50) and spy_df["close"].iloc[-1] > sma50)
    else:
        market_ok = True
    market_risk_off = not market_ok

    # Rolling correlation vs SPY → "healthy participation, not lockstep noise"
    mkt_corr = float("nan")
    if spy_df is not None:
        ra, rb = _aligned_returns(close, spy_df["close"], cfg.corr_win)
        if ra is not None:
            mkt_corr = float(ra.corr(rb))
    if mkt_corr != mkt_corr:
        corr_band = 0.5
    else:
        corr_band = 1.0 if (cfg.corr_lo <= mkt_corr <= cfg.corr_hi) else 0.5
    high_corr = bool(mkt_corr == mkt_corr and mkt_corr >= cfg.high_corr)

    # Sector confirmation: sector ETF trending up AND positive sector RS; beta sane
    sector_ok = False
    sect_beta = float("nan")
    beta_quality = 0.0
    if sector_df is not None and len(sector_df) >= 50:
        s_close = sector_df["close"]
        sma50 = s_close.rolling(50).mean().iloc[-1]
        sect_above_ma = bool(pd.notna(sma50) and s_close.iloc[-1] > sma50)
        if len(s_close) > cfg.rs_lookback:
            sect_rs = float(s_close.iloc[-1] / s_close.iloc[-cfg.rs_lookback - 1] - 1.0)
        else:
            sect_rs = 0.0
        sector_ok = sect_above_ma and sect_rs > 0

        ra, rb = _aligned_returns(close, s_close, cfg.corr_win)
        if ra is not None:
            var = float(rb.var())
            if var > 1e-12:
                sect_beta = float(ra.cov(rb) / var)
                # Reward participation without excessive leverage (beta ≈ 0.5–2.0)
                beta_quality = 1.0 if (0.5 <= sect_beta <= 2.0) else 0.4

    F = (cfg.f_market * float(market_ok) + cfg.f_sector * float(sector_ok)
         + cfg.f_rs * float(rs_ok) + cfg.f_corr_band * corr_band
         + cfg.f_beta * beta_quality)

    return {"F": _clip01(F), "mkt_corr": mkt_corr, "sect_beta": sect_beta,
            "rs_ok": rs_ok, "market_ok": market_ok, "sector_ok": sector_ok,
            "corr_band": corr_band, "beta_quality": beta_quality,
            "market_risk_off": market_risk_off, "high_corr": high_corr}


# ──────────────────────────────────────────────────────────────────────────────
# 4. Fractal alignment — lower-TF structure vs higher-TF (daily) trend
# ──────────────────────────────────────────────────────────────────────────────
def _htf_trend_strength(daily: pd.DataFrame) -> Tuple[float, bool]:
    """Higher-timeframe trend strength in [0, 1] and an up/down flag.

    Uses the Minervini Stage-2 score (0–8) when enough history exists; otherwise
    a lightweight SMA-stack proxy.
    """
    if daily is None or len(daily) < 30:
        return 0.0, False
    close = daily["close"]
    sma50 = close.rolling(50).mean().iloc[-1] if len(daily) >= 50 else close.rolling(20).mean().iloc[-1]
    up = bool(pd.notna(sma50) and close.iloc[-1] > sma50)

    if len(daily) >= 50:
        _, score = calculate_minervini_template(daily)
        return _clip01(score / 8.0), up

    # Proxy: fraction of {close>sma20, close>sma50ish, sma20>sma50ish}
    sma20 = close.rolling(20).mean().iloc[-1]
    sma_slow = close.rolling(min(50, len(daily) - 1)).mean().iloc[-1]
    bits = [close.iloc[-1] > sma20, close.iloc[-1] > sma_slow, sma20 > sma_slow]
    return _clip01(np.mean([float(b) for b in bits])), up


def _ltf_breakout_up(df: pd.DataFrame, lookback: int) -> bool:
    """True if the last bar closed above the prior `lookback`-bar high."""
    if df is None or len(df) < lookback + 2:
        return False
    prior_high = df["high"].rolling(lookback).max().iloc[-2]
    return bool(pd.notna(prior_high) and df["close"].iloc[-1] > prior_high)


def fractal_alignment_score(df: pd.DataFrame, daily_df: Optional[pd.DataFrame] = None,
                            cfg: TensionConfig = DEFAULT) -> Dict:
    """Does the lower-TF structure agree with the higher-TF (daily) trend?

    When `daily_df` is None (a daily-mode scan), `df` itself is the daily series
    and the check degrades to "recent breakout aligned with the longer-term
    daily trend" — it never requires intraday data.

    Returns dict: {A, htf_trend, htf_up, ltf_break_up, agree, fractal_contradiction}.
    """
    daily_for_trend = daily_df if daily_df is not None else df
    htf_trend, htf_up = _htf_trend_strength(daily_for_trend)
    ltf_break_up = _ltf_breakout_up(df, cfg.brk_lookback)

    ltf_strength = compression_score(df, cfg)["C"]  # a coiled base behind the break

    if ltf_break_up:
        agree = 1.0 if htf_up else 0.0
    else:
        agree = 0.5 if htf_up else 0.0  # no break: mild credit for an aligned uptrend

    A = cfg.a_htf_trend * htf_trend + cfg.a_align * (agree * ltf_strength if ltf_break_up else agree * 0.5)

    fractal_contradiction = bool(ltf_break_up and htf_trend < cfg.contradiction_htf)

    return {"A": _clip01(A), "htf_trend": htf_trend, "htf_up": htf_up,
            "ltf_break_up": ltf_break_up, "agree": agree,
            "fractal_contradiction": fractal_contradiction}


# ──────────────────────────────────────────────────────────────────────────────
# Composite
# ──────────────────────────────────────────────────────────────────────────────
def compute_tension_index(df: pd.DataFrame, *,
                          daily_df: Optional[pd.DataFrame] = None,
                          spy_df: Optional[pd.DataFrame] = None,
                          sector_df: Optional[pd.DataFrame] = None,
                          regime: Optional[str] = None,
                          spy_perf: float = 0.0,
                          timeframe: str = "1 day",
                          cfg: TensionConfig = DEFAULT) -> Dict:
    """Compute the full Tension Index and its sub-scores.

    Parameters
    ----------
    df         : OHLCV at the scan timeframe (daily, or intraday e.g. 5-min).
    daily_df   : the ticker's daily OHLCV — required for the fractal hierarchy
                 and correlation when `df` is intraday; pass None in daily mode.
    spy_df     : SPY daily OHLCV (market context).
    sector_df  : the ticker's sector-ETF daily OHLCV (sector context).
    regime     : optional regime label (quantkit 'bull'/'bear'/'mixed' or
                 scanner 'NORMAL'/'BEARISH'/… are both understood).
    spy_perf   : SPY return over the RS lookback (for relative strength).
    cfg        : TensionConfig overriding any weights/parameters.

    Returns
    -------
    dict with 'tension_index' (float 0–1), the four sub-scores, 'state'
    ('COILED' | 'RELEASING' | 'NONE'), 'point_of_silence', and selected
    diagnostic sub-fields.
    """
    safe = {
        "tension_index": 0.0, "compression": 0.0, "volume_consensus": 0.0,
        "confirmation": 0.0, "fractal_alignment": 0.0, "state": "NONE",
        "point_of_silence": False, "breakout_bar": False,
        "fractal_contradiction": False, "mkt_corr": float("nan"),
        "sect_beta": float("nan"),
    }
    if df is None or len(df) < cfg.min_bars or pd.isna(df["close"].iloc[-1]):
        return safe

    # Correlation/RS are higher-TF concepts → use the ticker's daily series when
    # the scan timeframe is intraday.
    ticker_for_context = daily_df if daily_df is not None else df

    comp = compression_score(df, cfg)
    vol = volume_consensus_score(df, cfg=cfg)
    conf = confirmation_score(ticker_for_context, spy_df, sector_df,
                              regime=regime, spy_perf=spy_perf, cfg=cfg)
    frac = fractal_alignment_score(df, daily_df, cfg)

    C, V, F, A = comp["C"], vol["V"], conf["F"], frac["A"]

    ti = (cfg.w_compression * C + cfg.w_volume * V
          + cfg.w_confirmation * F + cfg.w_fractal * A)

    breakout_bar = frac["ltf_break_up"]

    # Hard gates (multiplicative penalties)
    if frac["fractal_contradiction"]:
        ti *= cfg.gate_fractal
    if conf["market_risk_off"] and conf["high_corr"] and breakout_bar:
        ti *= cfg.gate_fakeout

    ti = _clip01(ti)

    # State machine
    if breakout_bar and V >= cfg.release_v:
        state = "RELEASING"
    elif C >= cfg.coil_thresh and not breakout_bar:
        state = "COILED"
    else:
        state = "NONE"

    return {
        "tension_index": round(ti, 4),
        "compression": round(C, 4),
        "volume_consensus": round(V, 4),
        "confirmation": round(F, 4),
        "fractal_alignment": round(A, 4),
        "state": state,
        "point_of_silence": comp["point_of_silence"],
        "breakout_bar": breakout_bar,
        "fractal_contradiction": frac["fractal_contradiction"],
        "htf_trend": round(frac["htf_trend"], 4),
        "mkt_corr": conf["mkt_corr"],
        "sect_beta": conf["sect_beta"],
    }
