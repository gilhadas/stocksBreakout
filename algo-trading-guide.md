# Algorithmic Trading Guide

Complete guide for using algorithmic order execution strategies.

## 🎯 Overview

AlgoTrading module provides professional order execution algorithms to:
- **Minimize market impact** - Split large orders intelligently
- **Optimize execution price** - Get better fills than market orders
- **Hide order size** - Prevent front-running
- **Access dark pools** - Find hidden liquidity
- **Match market rhythm** - Follow volume patterns

## 📊 Available Algorithms

### 1. ADAPTIVE (Recommended Default)
**Best for:** Most situations, especially beginners

IB's smart algorithm that automatically adjusts to market conditions.

```python
algo_type = AlgoType.ADAPTIVE
urgency = 'Normal'  # Patient | Normal | Urgent
```

**Urgency Levels:**
- `Patient` - Minimize cost, accept slower fill
- `Normal` - Balance speed and cost
- `Urgent` - Prioritize speed over cost

**When to use:**
- General trading
- Medium-sized orders
- Variable market conditions

---

### 2. TWAP (Time Weighted Average Price)
**Best for:** Large orders, quiet markets

Slices order evenly over specified time period.

```python
algo_type = AlgoType.TWAP
params = {
    'start_time': '09:35:00',  # When to start
    'end_time': '15:55:00',    # When to finish
    'strategy_type': 'Marketable'  # or 'Passive'
}
```

**Pros:**
- Predictable execution
- Minimal market impact
- Good for illiquid stocks

**Cons:**
- Ignores volume patterns
- May miss opportunities

**When to use:**
- Large positions (>10% ADV)
- Low urgency
- Stable markets

---

### 3. VWAP (Volume Weighted Average Price)
**Best for:** Matching institutional flow

Follows historical volume distribution throughout the day.

```python
algo_type = AlgoType.VWAP
params = {
    'start_time': '09:35:00',
    'end_time': '15:55:00',
    'max_pct_vol': '0.1',  # 10% of volume
}
```

**Pros:**
- Matches market volume
- Industry standard benchmark
- Less market impact than TWAP

**Cons:**
- Tied to historical patterns
- May miss unusual volume

**When to use:**
- Institutional-sized orders
- Liquid stocks
- Normal volume days

---

### 4. ICEBERG
**Best for:** Hiding order size

Shows only small portion of total size to market.

```python
algo_type = AlgoType.ICEBERG
params = {
    'display_size': 100,  # Show only 100 shares
    'limit_price': 185.50
}
```

**Pros:**
- Hides true size
- Prevents front-running
- Good for large orders

**Cons:**
- Slower execution
- May get partial fills

**When to use:**
- Large hidden orders
- Avoid moving the market
- Illiquid stocks

---

### 5. DARK_ICE
**Best for:** Finding hidden liquidity

Seeks dark pools before going to lit markets.

```python
algo_type = AlgoType.DARK_ICE
```

**Pros:**
- Accesses dark liquidity
- Reduces market impact
- Better for large blocks

**Cons:**
- May take longer
- Not always available

**When to use:**
- Block trades
- Minimize footprint
- Large cap stocks

---

### 6. ARRIVAL_PRICE
**Best for:** Minimizing slippage

Executes close to the price when order was submitted.

```python
algo_type = AlgoType.ARRIVAL_PRICE
urgency = 'Normal'  # Patient | Normal | Urgent
```

**Pros:**
- Minimizes deviation from entry
- Smart urgency control
- Good tracking

**Cons:**
- May sacrifice opportunities
- Requires liquidity

**When to use:**
- Time-sensitive entries
- Fair value important
- Breakout signals

---

### 7. PERCENT_VOL
**Best for:** Participating in volume

Maintains % of market volume.

```python
algo_type = AlgoType.PERCENT_VOL
params = {
    'pct_vol': 10,  # 10% of volume
}
```

**Pros:**
- Controls market participation
- Flexible completion
- Good for size

**Cons:**
- Unpredictable timing
- Depends on volume

**When to use:**
- Accumulation
- Volume-dependent strategies
- Patient fills

---

## 🚀 Usage Examples

### Basic Execution

```python
from algo_trader import AlgoTrader, AlgoType
from ib_insync import IB

# Connect to IB
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=1)

# Create trader
trader = AlgoTrader(ib)

# Execute with algo
result = await trader.execute_with_algo(
    symbol='AAPL',
    action='BUY',
    quantity=1000,
    algo_type=AlgoType.VWAP,
    urgency='Normal'
)

# Monitor execution
if result['success']:
    execution = await trader.monitor_execution(result['order_id'])
    print(f"Filled: {execution['filled']} @ ${execution['avg_price']}")
```

### With Webhook Server

Edit `webhook_server.py`:

```python
CONFIG = {
    'use_algo_trading': True,
    'default_algo': 'VWAP',  # Choose your algo
    'algo_urgency': 'Normal',
}
```

Now all webhook trades use VWAP execution!

### Advanced: Custom Parameters

```python
# TWAP over specific period
await trader.execute_with_algo(
    symbol='NVDA',
    action='BUY',
    quantity=5000,
    algo_type=AlgoType.TWAP,
    start_time='10:00:00',
    end_time='14:00:00',
    strategy_type='Passive'
)

# VWAP with volume limit
await trader.execute_with_algo(
    symbol='TSLA',
    action='SELL',
    quantity=2000,
    algo_type=AlgoType.VWAP,
    max_pct_vol='0.05'  # Only 5% of volume
)

# Iceberg with custom display
await trader.execute_with_algo(
    symbol='MSFT',
    action='BUY',
    quantity=10000,
    algo_type=AlgoType.ICEBERG,
    display_size=500,
    limit_price=420.50
)
```

## 📊 Algorithm Selection Guide

| Order Size | Urgency | Liquidity | Recommended Algo |
|------------|---------|-----------|------------------|
| Small (<1% ADV) | Any | High | ADAPTIVE |
| Medium (1-5% ADV) | Low | High | VWAP |
| Medium (1-5% ADV) | High | High | ADAPTIVE (Urgent) |
| Large (>5% ADV) | Low | Any | TWAP or VWAP |
| Large (>5% ADV) | High | High | ARRIVAL_PRICE |
| Any | Any | Low | ICEBERG |
| Block | Low | High | DARK_ICE |

**ADV** = Average Daily Volume

## 🔍 Monitoring & Analytics

### Check Active Orders

```python
# Get all active algo orders
active = trader.get_active_orders()
for order in active:
    print(f"{order['symbol']}: {order['filled']}/{order['quantity']} filled")
```

### Execution Statistics

```python
# Get execution stats
stats = trader.get_execution_stats()
print(f"Fill rate: {stats['fill_rate']}")
print(f"Avg duration: {stats['avg_duration_seconds']}s")
```

### Cancel Order

```python
# Cancel if needed
await trader.cancel_order(order_id)
```

## ⚠️ Important Considerations

### Costs
- Algo orders may have additional fees
- Check your IB subscription level
- Some algos require market data subscriptions

### Data Requirements
- Real-time data recommended
- Delayed data may reduce effectiveness
- Level 2 data helps for some algos

### Market Hours
- Most algos only work during RTH (9:30-16:00 ET)
- TWAP/VWAP need specific time windows
- Check algo availability per stock

### Order Size
- Minimum sizes may apply
- Very small orders may not benefit
- Consider regular orders for <100 shares

## 🧪 Testing Strategy

1. **Paper Trading First**
   ```python
   ib.connect('127.0.0.1', 7497)  # Paper port
   ```

2. **Start with ADAPTIVE**
   - Easiest to use
   - Works in most situations
   - Good baseline

3. **Test Each Algo**
   - Try on different stocks
   - Different times of day
   - Various market conditions

4. **Compare Results**
   - Track fill prices
   - Measure vs. VWAP benchmark
   - Calculate savings

5. **Move to Live Carefully**
   ```python
   ib.connect('127.0.0.1', 7496)  # Live port
   ```

## 📈 Performance Benchmarks

### Good Execution
- Fill within 0.1% of VWAP
- Complete within expected timeframe
- No significant market impact

### Poor Execution
- Fill >0.5% worse than VWAP
- Incomplete fills
- Visible market impact

## 🔧 Troubleshooting

### Order Rejected

**Common reasons:**
- Invalid time window
- Stock not supported
- Insufficient permissions
- Missing market data

**Solution:**
```python
# Check contract first
contract = Stock('AAPL', 'SMART', 'USD')
ib.qualifyContracts(contract)

# Verify algo supported
# Check IB documentation for specific stock
```

### Slow Fills

**Possible causes:**
- Too passive settings
- Low liquidity
- Tight price limit

**Solution:**
- Use 'Normal' or 'Urgent' urgency
- Increase max % volume
- Adjust time window

### Partial Fills

**Why it happens:**
- Not enough volume
- Time window expired
- Price moved away

**Solution:**
- Extend time window
- Increase urgency
- Use ADAPTIVE instead

## 📚 Additional Resources

- [IB Algo Orders Guide](https://www.interactivebrokers.com/en/index.php?f=19091)
- [Algorithm Performance Studies](https://www.interactivebrokers.com/en/index.php?f=1359)
- [ib_insync Documentation](https://ib-insync.readthedocs.io/)

## 📄 License

Same as main scanner - MIT License. Use at your own risk.

**IMPORTANT:** Algorithmic trading carries significant risk. Test thoroughly before using with real money.
