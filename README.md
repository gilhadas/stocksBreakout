# Swing trading (paper account)
python breakout_scanner.py watchlist.txt --mode swing

# Daytrade (15min bars, paper)
python breakout_scanner.py watchlist.txt --mode daytrade

# Live account (BE CAREFUL!)
python breakout_scanner.py watchlist.txt --mode swing --live

# Custom parameters
python breakout_scanner.py watchlist.txt --mode swing --vol 1.5 --atr 0.8

# Scalping mode (1-minute bars)
python breakout_scanner.py watchlist.txt --mode scalping

# Scalping with custom volume threshold
python breakout_scanner.py watchlist.txt --mode scalping --vol 2.5

# LIVE scalping (DANGEROUS - will ask for confirmation)
python breakout_scanner.py watchlist.txt --mode scalping --live
```

## מה שונה ב-Scalping:

### 1. **Timeframe**: 1 min bars (default)
### 2. **VWAP הוא המסנן המרכזי**:
   - מחיר חייב להיות מעל VWAP
   - VWAP חייב להיות עולה (momentum)
   - לא משווים ל-SPY (יקח יותר מדי זמן)

### 3. **Filters הרבה יותר חמורים**:
   - Volume threshold: **2.0x** (לא 1.1x)
   - Spread check: Max **0.1%** bid-ask
   - Price range: **$5-$500** (no penny stocks, no super expensive)
   - Volume spike: צריך **3x average** ל-PREMIUM signal

### 4. **Stop Loss/Target הרבה יותר קטנים**:
   - SL: **0.5 ATR** (vs 2.0 for swing)
   - TP: **1.0 ATR** (vs 4.0 for swing)
   - R:R: ~**1:2** (not 1:4)

### 5. **Spread monitoring**:
   - בודק bid-ask spread לפני entry
   - דוחה מניות עם spread רחב

### 6. **אזהרות בטיחות**:
   - אם אתה רץ LIVE, מקבל prompt אישור
   - מזכיר לסגור הכל לפני market close
   - מזהיר על slippage risks

## דוגמא לפלט Scalping:
```
=================================================================
 SCALPING SIGNALS FOUND: 3
=================================================================

Symbol  Price    Vol  Dist   Stop  Target   R:R  Spread%  Quality
AAPL    185.50  3.2  0.18  185.25  185.85  2.0     0.05   PREMIUM
NVDA    520.30  2.8  0.22  519.90  520.90  1.8     0.08   HIGH
TSLA    245.10  2.5  0.15  244.85  245.45  2.1     0.06   HIGH

⚠️  SCALPING REMINDERS:
   • Exit at target or stop - no exceptions
   • Monitor spread widening during execution
   • Close all positions before market close
   • Watch for news events that spike volatility
