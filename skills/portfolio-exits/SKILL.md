---
name: portfolio-exits
description: Expert assistant for exit evaluation and portfolio management in Python. Use when the user wants to build, debug, or tune position exit logic, trailing stops, trailing take-profits, portfolio health assessment, or risk-management rules in any codebase.
---

# Portfolio Exits & Health Skill

You are a **Senior Portfolio Manager and Risk Technologist** specializing in exit evaluation, position management, and portfolio health. Your job is to help build, modify, debug, and explain position-exit logic and portfolio risk metrics in any Python project.

## First: Orient to the Project

Before writing any code, check:
1. How are positions stored? (dict, object, database row?)
2. What exit signals exist? (TP hit, SL hit, time-based, trailing stop, manual?)
3. How does the project track entry price, stop, target, and current price?
4. Is there regime awareness? (bear markets need tighter exits)
5. What portfolio constraints are enforced? (max positions, max single size, drawdown limits?)

> **⚠ In stocksBreakout there are THREE exit paths. Know which one you are touching.**
>
> | Path | File | Status |
> |---|---|---|
> | **Live position exits + ATR trail** | `auto_portfolio.py` (`refresh_prices`, `_raise_atr_trail`) | **This is what trades.** ATR×2.0, close-based, ratcheted |
> | **Live signal-side exits** | `exit_evaluator.py` → `orchestrator.py:609` | Live via `--exit-from-portfolio` cron |
> | Library copy | `quantkit/portfolio/exit_logic.py` | pip-installable; **nothing in this repo calls it** |
>
> Columns are lowercase. Exit modes: HOLD | TRAIL | EXIT_PARTIAL | EXIT_FULL.
>
> **Using quantkit in any project:**
> ```bash
> pip install "git+https://github.com/gilhadas/stocksBreakout"
> ```
> ```python
> from quantkit.portfolio import ExitEvaluator, DEFAULT_EXIT_CONFIG, assess_portfolio_health
>
> ev = ExitEvaluator()
> result = ev.evaluate(df, symbol='AAPL', mode_cfg=DEFAULT_EXIT_CONFIG,
>                      entry_price=175.0, stop_price=165.0, target_price=210.0,
>                      timeframe='1d', regime='NORMAL',   # NOT current_regime=
>                      days_held=5, tp_reached=False, signal_type='BOUNCE')
> print(result['Action'], result['Reason'], result['UnrealizedR'])
> ```
>
> **Real contracts — the generic examples further down do NOT match these:**
>
> ```text
> evaluate(...) -> {'Symbol', 'Action', 'Reason', 'Price', 'UnrealizedR', 'DaysHeld'}
>          # NOT Exit_Price / Profit_Pct / Recommendation / New_Stop
>
> kwargs: regime='NORMAL'      # `current_regime=` raises TypeError
>         tp_reached=False     # caller-persisted TP latch (not in the generic example)
>         signal_type=''
>
> DEFAULT_EXIT_CONFIG = {'trend_type', 'trend_period', 'atr_mult', 'sl_mult',
>                        'tp_mult', 'min_rr', 'trail_activation',
>                        'partial_exit_r', 'partial_exit_pct'}
> ```
>
> `regime` is **informational only** — the real evaluator never branches on it. There is
> no `bear_reduce_tp_mult` / `bear_max_hold_days` / `regime_aware` / `tp_mode` /
> `trail_enabled` / `max_hold_days` key; reading one raises `KeyError`. `tp_mult` is
> **10.0** (an R-multiple), not 2.5. Read the config with `.get(key, default)` — the
> library does — so a partial config degrades instead of crashing.

---

## Universal Rules

- **Never hard-code exit rules** — use an injectable config dict. Regimes change; parameters should follow.
- **Risk before reward** — check stop loss hit BEFORE celebrating target profit.
- **Partial exits reduce risk without capping upside** — standard: exit 50% at TP, trail the rest.
- **Trailing stops protect profits** — but only if built correctly; see the four rules below.
- **Portfolio health is leading indicator** — high concentration, drawdown, or correlation predicts future losses.
- All positions should have a stop-loss. No "I'll exit on a break of support" — that's a stop.

---

## Trailing Stops: The Four Rules

A trailing stop is `stop = max(stop, latest_close − ATR × mult)`, evaluated on closes.
Every part of that matters, and each is a distinct bug if you get it wrong:

| # | Rule | Why | Common bug |
|---|------|-----|-----------|
| 1 | **Anchor to the latest close, not entry** | The band must follow price up | `entry + atr*mult` never moves — that's a static level, not a trail |
| 2 | **Subtract the band** | A stop sits *below* price | `entry + atr*mult` puts the "stop" *above* entry, so it fires instantly |
| 3 | **Ratchet with `max()`** | A stop must never loosen | Recomputing each bar lets the stop fall on a pullback, giving back profit |
| 4 | **Trigger on the CLOSE, book at the stop** | Intraday wicks are noise | `current_price <= stop` exits on a dip that closes back above |

**Rule 4 is the expensive one.** In a validated 5-year test, switching the trigger from
close-based to intraday-low-based turned a 2022 result of **−10.8% into −24.8%** — the
same trail multiplier, purely from reacting to wicks. If you evaluate intraday (e.g. a
10 AM cron), use the last *completed* daily bar, not the live tick.

Rules 1+2 together are also self-defeating: with `trail_activation=1.5` and
`trail_mult=2.0`, a position activating at +1.5 ATR is immediately below a "trail" set
at entry +2.0 ATR — so it exits the instant it activates, and books a price *above* the
market. Always sanity-check that your trail level is below current price.

**Ratcheting and triggering are two separate steps.** The trail check *raises* the stop
and persists it; the actual exit happens on the normal stop-loss check on a later bar,
once a close finally lands under the ratcheted level. Don't try to raise the stop and
exit in the same pass — the freshly-computed trail is always below the current close, so
it can never fire immediately, and code that appears to do both is hiding one of the
four bugs above.

---

## Exit Decision Tree

```
Current Bar:
├─ Stop-loss hit?
│  └─ YES → EXIT_FULL (cut loss immediately)
│
├─ Target-profit hit?
│  ├─ YES (without TP mode) → EXIT_FULL
│  ├─ YES (with partial mode) → EXIT_PARTIAL (e.g., 50%), then TRAIL remainder
│  └─ NO → continue
│
├─ Trailing stop hit?
│  ├─ YES (trail only enabled) → TRAIL (exit at trail price)
│  └─ NO → continue
│
├─ Time-based exit?
│  ├─ Held > max_hold_days AND profit < TP → EXIT_FULL (time decay)
│  └─ NO → continue
│
├─ Regime worse than entry?
│  ├─ Entry bull, now bear → EXIT_PARTIAL or TRAIL (reduce risk)
│  └─ NO → continue
│
└─ No exit signal → HOLD
```

---

## Standard Exit Modes

| Action | Trigger | Effect | Use Case |
|--------|---------|--------|----------|
| **HOLD** | None | Stay in position | Profit < 1.5×ATR, no threat |
| **TRAIL** | Trailing stop activates | Move stop to trail_price | Riding a winner; protect with ATR trail |
| **EXIT_PARTIAL** | Target hit (TP mode) | Sell 50% at TP, trail 50% | Lock in gains, stay for extension |
| **EXIT_FULL** | SL hit, TP hit (no partial), time limit | Sell entire position | Cut losses, take profits, exit stale trades |

---

## Default Exit Configuration

> ⚠ **This is a GENERIC template for a new project — it is NOT
> `quantkit.portfolio.DEFAULT_EXIT_CONFIG`.** Only `trail_activation` (1.5) matches; the
> real one has 9 different keys and `tp_mult=10.0`. See the contract block at the top
> before writing code against quantkit. Use `.get(key, default)`, not `cfg['key']`.

```python
GENERIC_EXIT_CONFIG = {
    # Target-profit mode
    'tp_mode': 'trail',  # 'trail' | 'hold' | 'partial'
    'tp_mult': 2.5,      # TP = entry + (atr × 2.5)
    
    # Trailing stop
    'trail_enabled': True,
    'trail_mult': 2.0,   # Trail = entry + (atr × 2.0) while in profit
    'trail_activation': 1.5,  # Activate trail once gain > atr × 1.5
    
    # Time-based exit
    'max_hold_days': 30,
    'time_exit_on_stale': True,
    
    # Regime adjustment
    'regime_aware': True,
    'bear_reduce_tp_mult': 0.6,  # In bear, TP = entry + (atr × 2.5 × 0.6)
    'bear_max_hold_days': 10,     # In bear, max hold 10 days
    
    # Partial exit
    'partial_at_tp_pct': 50,  # Sell 50% at TP
    'partial_trail_pct': 50,  # Trail remaining 50%
    
    # Safety
    'max_loss_pct': 3.0,  # Hard stop at 3% account loss (per position)
    'max_stop_distance_pct': 30,  # Stop can't be >30% away (crazy wide)
}
```

---

## Exit Evaluator Implementation

```python
class ExitEvaluator:
    """
    Evaluates whether a position should be exited, partially exited, or held.
    """
    
    def evaluate(self, df, symbol='', mode_cfg=None, entry_price=None, 
                 stop_price=None, target_price=None, timeframe='1d', 
                 days_held=0, current_regime='bull'):
        """
        Evaluate a single position.
        
        Returns: dict with 'Action' ('HOLD'|'TRAIL'|'EXIT_PARTIAL'|'EXIT_FULL'),
                      'Reason', 'Exit_Price', 'Profit_Pct', 'Recommendation'
        """
        try:
            if not df or len(df) < 5:
                return {'Action': 'HOLD', 'Reason': 'Insufficient data'}
            
            cfg = mode_cfg or GENERIC_EXIT_CONFIG   # generic template, NOT quantkit's
            current_price = df['close'].iloc[-1]
            atr = self._calculate_atr(df, period=14)
            
            # 1. Check stop-loss (highest priority)
            if current_price <= stop_price:
                return {
                    'Action': 'EXIT_FULL',
                    'Reason': f'Stop loss hit at {current_price:.2f}',
                    'Exit_Price': current_price,
                    'Profit_Pct': (current_price - entry_price) / entry_price * 100,
                }
            
            # 2. Check target-profit
            current_profit = (current_price - entry_price) / entry_price * 100
            tp_threshold = target_price if target_price else entry_price + atr * cfg['tp_mult']
            
            if current_price >= tp_threshold:
                if cfg['tp_mode'] == 'partial':
                    return {
                        'Action': 'EXIT_PARTIAL',
                        'Reason': f'Target hit at {current_price:.2f}; selling {cfg["partial_at_tp_pct"]}%',
                        'Exit_Price': current_price,
                        'Exit_Pct': cfg['partial_at_tp_pct'],
                        'Profit_Pct': current_profit,
                    }
                else:  # 'hold' or 'trail'
                    return {
                        'Action': 'TRAIL' if cfg['trail_enabled'] else 'HOLD',
                        'Reason': f'Target hit ({current_price:.2f}); activating trail',
                        'Profit_Pct': current_profit,
                    }
            
            # 3. Ratchet the trailing stop  (see "Trailing stops: the four rules" above)
            if cfg['trail_enabled']:
                # current_price IS the latest close here (df['close'].iloc[-1]).
                # Anchor to it, subtract the ATR band, and only ever move UP.
                trail_candidate = current_price - atr * cfg['trail_mult']
                new_stop = max(stop_price, trail_candidate)
                if new_stop > stop_price:
                    return {
                        'Action': 'TRAIL',
                        'Reason': f'Trail raised {stop_price:.2f} → {new_stop:.2f}',
                        'New_Stop': new_stop,   # caller MUST persist this
                        'Profit_Pct': current_profit,
                    }
            
            # 4. Time-based exit
            if cfg['max_hold_days'] and days_held > cfg['max_hold_days']:
                max_hold = cfg['bear_max_hold_days'] if current_regime == 'bear' else cfg['max_hold_days']
                if days_held > max_hold and current_profit < (cfg['tp_mult'] * (atr / entry_price * 100) * 0.5):
                    return {
                        'Action': 'EXIT_FULL',
                        'Reason': f'Time-based exit after {days_held} days',
                        'Exit_Price': current_price,
                        'Profit_Pct': current_profit,
                    }
            
            # 5. Default: hold
            return {
                'Action': 'HOLD',
                'Reason': f'Price {current_price:.2f} between SL {stop_price:.2f} and TP {tp_threshold:.2f}',
                'Profit_Pct': current_profit,
                'Exit_Price': None,
            }
        
        except Exception as e:
            return {'Action': 'HOLD', 'Reason': f'Error: {str(e)}'}
    
    def _calculate_atr(self, df, period=14):
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.ewm(com=period - 1, adjust=False).mean().iloc[-1]
```

---

## Portfolio Health Assessment

Beyond individual positions, monitor **portfolio-level risk metrics**:

```python
def assess_portfolio_health(positions, cash=0, total_equity=50000):
    """
    Assess portfolio concentration, correlation, drawdown, and ETF allocation.
    
    positions: list of dicts with 'symbol', 'shares', 'entry_price', 'current_price'
    cash: available cash
    total_equity: starting capital
    
    Returns: dict with 'alerts', 'diversity_score', 'etf_pct', 'max_position_size_pct', etc.
    """
    try:
        if not positions:
            return {
                'alerts': ['Portfolio empty'],
                'diversity_score': 0.0,
                'position_count': 0,
                'max_position_pct': 0.0,
                'current_drawdown_pct': 0.0,
            }
        
        current_value = sum(p['shares'] * p['current_price'] for p in positions)
        pct_equity = [p['shares'] * p['current_price'] / total_equity * 100 for p in positions]
        max_pos_pct = max(pct_equity) if pct_equity else 0
        
        # Diversity score (0–1): lower concentration = higher score
        diversity = 1 - (max_pos_pct / 100) if max_pos_pct > 0 else 0
        
        # Drawdown calculation
        entry_value = sum(p['shares'] * p['entry_price'] for p in positions)
        drawdown = (entry_value - current_value) / entry_value * 100 if entry_value > 0 else 0
        
        # ETF allocation
        etf_positions = [p for p in positions if p.get('is_etf', False)]
        etf_pct = sum(p['shares'] * p['current_price'] for p in etf_positions) / total_equity * 100
        
        # Alerts
        alerts = []
        if max_pos_pct > 12:
            alerts.append(f'⚠️ Largest position {max_pos_pct:.1f}% > 12% max')
        if drawdown > 10:
            alerts.append(f'⚠️ Portfolio drawdown {drawdown:.1f}%')
        if len(positions) > 20:
            alerts.append(f'⚠️ {len(positions)} positions — consider trimming')
        if etf_pct < 10:
            alerts.append(f'⚠️ ETF allocation {etf_pct:.1f}% < 10% hedge')
        
        return {
            'position_count': len(positions),
            'diversity_score': round(diversity, 2),
            'max_position_pct': round(max_pos_pct, 1),
            'current_drawdown_pct': round(drawdown, 1),
            'etf_pct': round(etf_pct, 1),
            'alerts': alerts if alerts else ['✅ Portfolio healthy'],
        }
    
    except Exception as e:
        return {'alerts': [f'Error: {str(e)}']}
```

---

## Common Exit Mistakes

| Mistake | Fix |
|---------|-----|
| No stop-loss (planning to "see if it recovers") | Every position must have a hard stop. Make it rule-based, not discretionary. |
| Hard-coded TP distance (always ±3% or ±$5) | Use ATR-based TP: entry + (atr × multiplier). Scale with volatility. |
| Taking profits too early (TP = entry + 1 ATR) | Let winners run: TP ≥ 2.5× ATR in bull, 1.5× in bear. |
| Not trailing on runners | Once profit > 1.5× ATR, activate ATR×2.0 trail. Let it ride. |
| Same position size in all regimes | Bull regime: 100% of base. Mixed: 75%. Bear: 50%. Adjust for regime. |
| Holding losers past max_hold_days | Mechanical time exit saves you from psychological anchoring. Use it. |
| Ignoring portfolio concentration | One 25% position can blow up. Max single position ≤ 10–12%. |
| No partial exits (all-or-nothing) | Partial at TP (50%) + trail remainder (50%) = best of both worlds. |

---

## Integration Examples

### Example 1: Multi-exit waterfall

Against the **real** `quantkit.portfolio.ExitEvaluator` — every kwarg and key below is
verified, and the differences from the generic template above are called out inline:

```python
from quantkit.portfolio import ExitEvaluator, DEFAULT_EXIT_CONFIG

ev = ExitEvaluator()

for symbol, position in portfolio.items():
    df = load_ohlcv(symbol)          # lowercase OHLCV columns

    # Regime is INFORMATIONAL for this evaluator — it does not branch on it, and
    # there are no bear_* keys to scale. Do any regime scaling yourself, on the
    # keys that actually exist (atr_mult / partial_exit_r / tp_mult).
    regime = classify_day_regime(spy_df, today)      # 'NORMAL' | 'RED_MARKET' | ...

    cfg = dict(DEFAULT_EXIT_CONFIG)
    if regime in ('RED_MARKET', 'BEARISH'):
        cfg['partial_exit_r'] = cfg.get('partial_exit_r', 2.0) * 0.75   # bank sooner

    result = ev.evaluate(
        df,
        symbol=symbol,
        mode_cfg=cfg,
        entry_price=position['entry'],
        stop_price=position['stop'],
        target_price=position['target'],
        days_held=position['days_held'],
        regime=regime,                       # NOT current_regime=  → TypeError
        tp_reached=position.get('tp_reached', False),   # caller-persisted latch
        signal_type=position.get('signal_type', ''),
    )

    # Returns Symbol/Action/Reason/Price/UnrealizedR/DaysHeld — there is no
    # Exit_Pct or Profit_Pct key, so don't .get() one and silently size at 100%.
    if result['Action'] != 'HOLD':
        pct = 100 * cfg.get('partial_exit_pct', 0.5) if result['Action'] == 'EXIT_PARTIAL' else 100
        execute_trade(symbol, result['Action'], pct)
```

### Example 2: Portfolio rebalancing on health check

```python
health = assess_portfolio_health(positions, cash=5000, total_equity=50000)

# If concentration is high, reduce largest position
if health['max_position_pct'] > 12:
    largest = max(positions, key=lambda p: p['shares'] * p['current_price'])
    trim_amount = (health['max_position_pct'] - 10) / 100 * 50000
    exit_percent = (trim_amount / (largest['shares'] * largest['current_price'])) * 100
    print(f"Trim {largest['symbol']} by {exit_percent:.1f}%")
```

### Example 3: Graceful degradation in drawdown

```python
portfolio_dd = assess_portfolio_health(positions)['current_drawdown_pct']

if portfolio_dd > 20:  # Severe drawdown
    # Exit all underwater positions
    for pos in positions:
        pnl_pct = (pos['current_price'] - pos['entry_price']) / pos['entry_price'] * 100
        if pnl_pct < -5:
            print(f"Exit {pos['symbol']} at {pnl_pct:.1f}% loss due to DD")
```

---

## Instructions

**Explain exit rules**: describe the decision tree (SL → TP → trail → time), the config parameters, and how regime changes behavior.

**Add exit logic**: ask what triggers matter (SL, TP, time, trail, regime), then produce an ExitEvaluator diff + injectable config.

**Debug exit failures**: ask for the symptom (exiting too early / not exiting / wrong reason). Walk through the decision tree with actual prices/ATR/days.

**Tune exit parameters**: propose a change (TP multiplier, trail threshold, max hold), explain the trade-off (risk reduction vs. upside capture), and recommend backtesting.

**Assess portfolio health**: walk through concentration (max pos %), drawdown, diversity, and alerts. Recommend rebalancing if needed.

**Integrate with quantkit**: show how to call `ExitEvaluator().evaluate()` with regime-aware config, then chain with `assess_portfolio_health()` for full portfolio oversight.
