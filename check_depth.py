"""
Quick quote + depth check for manual trade validation.

Uses yfinance by default (no IB required). Falls back description when IB is running.

Usage:
  python check_depth.py AAPL
  python check_depth.py AAPL TSLA NVDA
  python check_depth.py AAPL --ib           # use IB for full depth (requires TWS/Gateway)
  python check_depth.py AAPL --ib --rows 20
"""

import argparse
import sys


# ── yfinance mode (default) ────────────────────────────────────────────────

def check_yf(symbols: list[str]):
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance not installed. Run: pip install yfinance")
        sys.exit(1)

    for symbol in symbols:
        print(f"\n{'═' * 60}")
        print(f"  {symbol} — Quote & Top-of-Book")
        print(f"{'═' * 60}")

        ticker = yf.Ticker(symbol)
        info = ticker.fast_info

        try:
            price      = info.last_price
            prev_close = info.previous_close
            day_high   = info.day_high
            day_low    = info.day_low
            volume     = info.last_volume
            avg_vol    = info.three_month_average_volume
        except Exception as e:
            print(f"  ⚠️  Could not fetch data: {e}")
            continue

        # Price context
        chg     = price - prev_close
        chg_pct = chg / prev_close * 100 if prev_close else 0
        arrow   = "▲" if chg >= 0 else "▼"
        print(f"\n  Price:     ${price:.2f}  {arrow} {chg:+.2f} ({chg_pct:+.2f}%)")
        print(f"  Day Range: ${day_low:.2f} – ${day_high:.2f}")

        # Position in day range
        if day_high != day_low:
            range_pos = (price - day_low) / (day_high - day_low) * 100
            bar_len   = int(range_pos / 10)
            bar       = '─' * bar_len + '●' + '─' * (10 - bar_len)
            print(f"  In Range:  [{bar}] {range_pos:.0f}%  (low→high)")

        # Volume
        vol_ratio = volume / avg_vol if avg_vol else 0
        vol_icon  = "🔥" if vol_ratio >= 2 else ("📈" if vol_ratio >= 1.5 else "")
        print(f"\n  Volume:    {volume:>12,}  ({vol_ratio:.2f}x avg) {vol_icon}")

        # Bid/Ask from full info (slower but has bid/ask)
        try:
            full   = ticker.info
            bid    = full.get('bid', 0)
            ask    = full.get('ask', 0)
            b_size = full.get('bidSize', 0)
            a_size = full.get('askSize', 0)

            if bid and ask:
                spread    = ask - bid
                spread_pct = spread / ask * 100
                print(f"\n  Bid:       ${bid:.2f}  x {b_size:,}")
                print(f"  Ask:       ${ask:.2f}  x {a_size:,}")
                print(f"  Spread:    ${spread:.3f}  ({spread_pct:.3f}%)")

                # Simple top-of-book imbalance
                total = b_size + a_size
                if total:
                    imb = (b_size - a_size) / total * 100
                    bar_len = int(abs(imb) / 10)
                    bar = ('🟢' * bar_len if imb > 0 else '🔴' * bar_len) or '⚪'
                    print(f"\n  Top-of-Book Imbalance: {imb:+.1f}%  {bar}")

                    if imb > 20:
                        verdict, icon = "Bullish pressure", "🟢"
                    elif imb > 0:
                        verdict, icon = "Slight buy lean", "🟡"
                    elif imb > -20:
                        verdict, icon = "Slight sell lean", "🟡"
                    else:
                        verdict, icon = "Selling pressure", "🔴"
                    print(f"  Verdict:   {icon} {verdict}")
            else:
                print("\n  ⚠️  No bid/ask data (market may be closed)")
        except Exception:
            print("\n  ⚠️  Could not fetch bid/ask")

        print(f"\n  Note: yfinance shows top-of-book only.")
        print(f"        Use --ib with TWS running for full depth.")


# ── IB mode (full depth) ───────────────────────────────────────────────────

def check_ib(symbols: list[str], num_rows: int, port: int):
    import asyncio

    # Python 3.14 compatibility
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    from ib_insync import IB
    from level2_analyzer import Level2Analyzer
    from config import IB_HOST, IB_CLIENT_ID

    async def _run():
        ib = IB()
        try:
            await ib.connectAsync(IB_HOST, port, clientId=IB_CLIENT_ID)
        except Exception as e:
            print(f"❌ Cannot connect to IB on {IB_HOST}:{port} — {e}")
            print("   Make sure TWS or IB Gateway is running.")
            sys.exit(1)

        analyzer = Level2Analyzer(ib)

        for symbol in symbols:
            print(f"\n{'═' * 60}")
            print(f"  {symbol} — Level 2 Market Depth (IB)")
            print(f"{'═' * 60}")

            depth = await analyzer.get_market_depth(symbol, num_rows=num_rows)

            if not depth:
                print("  ⚠️  No depth data (check IB market depth subscription)")
                continue

            print(f"\n  Best Bid:  ${depth['best_bid']:.2f}  ({depth['top_bid_size']:,} shares)")
            print(f"  Best Ask:  ${depth['best_ask']:.2f}  ({depth['top_ask_size']:,} shares)")
            print(f"  Spread:    ${depth['spread']:.2f}")
            print(f"\n  Bid Liquidity:  {depth['bid_liquidity']:>10,}")
            print(f"  Ask Liquidity:  {depth['ask_liquidity']:>10,}")
            print(f"  Bid/Ask Ratio:  {depth['bid_ask_ratio']:>10.2f}")

            imb     = depth['imbalance']
            bar_len = int(abs(imb) / 5)
            bar     = ('🟢' * bar_len if imb > 0 else '🔴' * bar_len) or '⚪'
            print(f"\n  Imbalance:      {imb:+.1f}%  {bar}")
            print(f"  Support Str:    {depth['support_strength']:.0f}%")
            print(f"  Resistance Str: {depth['resistance_strength']:.0f}%")

            quality, reason = analyzer.evaluate_entry_quality(depth)
            icons = {'EXCELLENT': '🟢', 'GOOD': '🟡', 'FAIR': '⚪', 'POOR': '🔴', 'UNKNOWN': '❓'}
            print(f"\n  Verdict: {icons.get(quality, '')} {quality} — {reason}")

            confirmed = analyzer.check_breakout_confirmation(depth, depth['best_ask'])
            print(f"  Breakout Confirmed: {'✅ YES' if confirmed else '❌ NO'}")

        ib.disconnect()

    asyncio.run(_run())


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    from config import IB_LIVE_PORT

    parser = argparse.ArgumentParser(description='Check quote/depth for breakout validation')
    parser.add_argument('symbols', nargs='+', help='Symbol(s) to check')
    parser.add_argument('--ib',   action='store_true', help='Use IB for full depth (requires TWS/Gateway)')
    parser.add_argument('--rows', type=int, default=10, help='Depth rows to request, IB only (default 10)')
    parser.add_argument('--port', type=int, default=IB_LIVE_PORT, help=f'IB port, IB only (default {IB_LIVE_PORT})')
    args = parser.parse_args()

    if args.ib:
        check_ib(args.symbols, num_rows=args.rows, port=args.port)
    else:
        check_yf(args.symbols)


if __name__ == '__main__':
    main()
