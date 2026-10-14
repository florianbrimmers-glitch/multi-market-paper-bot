"""Screen symbols with one strategy on Alpaca history, under the live session rules.

  python run_screen.py mean_reversion 15Min 120 SPY QQQ IWM
  python run_screen.py trend_following 1Day 730 USO GLD

Used to decide which instruments get added (or re-enabled). Read-only: fetches bars, places nothing.
"""
from __future__ import annotations

import sys

from data import fetch_bars
from engine import backtest_symbol
from models import AssetClass, Timeframe

_MINUTES = {"15Min": 15, "1Hour": 60, "4Hour": 240, "1Day": 1440}


def screen(symbol: str, strategy: str, timeframe: Timeframe, days: int) -> str:
    crypto = "/" in symbol
    bars = fetch_bars(symbol, AssetClass.CRYPTO if crypto else AssetClass.EQUITY, timeframe,
                      limit=10_000, days=days)
    if len(bars) < 50:
        return f"{symbol:<8} too little data ({len(bars)} bars)"
    # Intraday mean reversion runs with the live session rules (no entries in the first/last
    # 30 minutes, nothing flattened at the close); daily bars have no session.
    intraday = strategy == "mean_reversion" and timeframe != Timeframe.D1 and not crypto
    res = backtest_symbol(symbol, strategy, bars, crypto=crypto,
                          eod_mode="off" if intraday else None,
                          bar_minutes=_MINUTES[timeframe.value] if intraday else None)
    closed = res.closed_trades
    half = len(closed) // 2
    first, second = sum(t.pnl for t in closed[:half]), sum(t.pnl for t in closed[half:])
    stops = sum(t.reason_out == "hard stop hit" for t in closed)
    hold = (bars[-1].close / bars[0].close - 1) * 100
    return (f"{res.summary()} stops={stops:<3} pnl$ 1st/2nd half={first:8.0f}/{second:8.0f} "
            f"buy&hold%={hold:6.1f} bars={len(bars)} from={bars[0].timestamp.date()}")


def main(argv: list[str]) -> None:
    if argv[0] == "market_filter":  # run_screen.py market_filter <days> <thresholds,> <symbols...>
        compare_market_filter(argv[3:], int(argv[1]), [float(x) for x in argv[2].split(",")])
        return
    strategy, timeframe, days, symbols = argv[0], Timeframe(argv[1]), int(argv[2]), argv[3:]
    print(f"{strategy} on {timeframe.value}, last {days} days\n")
    for sym in symbols:
        try:
            print(screen(sym, strategy, timeframe, days))
        except Exception as e:  # one bad symbol shouldn't end the screen
            print(f"{sym:<8} error: {e}")
    print("\nPaper/backtest only. Past behaviour is not a prediction of future returns.")



def compare_market_filter(symbols: list[str], days: int, thresholds: list[float]) -> None:
    """Mean reversion on 15-min bars with and without the SPY market filter, per symbol and for
    the whole list run side by side (P/L summed by exit day; buying power is not modelled)."""
    from collections import defaultdict

    spy = fetch_bars("SPY", AssetClass.EQUITY, Timeframe.M15, limit=10_000, days=days)
    data = {s: fetch_bars(s, AssetClass.EQUITY, Timeframe.M15, limit=10_000, days=days) for s in symbols}
    variants = [0.0] + thresholds
    print(f"Market filter on mean reversion, 15Min, last {days} days "
          f"(0 = off; x = half size when SPY is down more than x % since the open)\n")
    totals = {}
    for th in variants:
        by_day: dict = defaultdict(float)
        n = 0
        for sym, bars in data.items():
            res = backtest_symbol(sym, "mean_reversion", bars, eod_mode="off", bar_minutes=15,
                                  market_bars=spy, market_filter_pct=th)
            for t in res.closed_trades:
                by_day[t.exit_time[:10]] += t.pnl
                n += 1
            print(f"  filter {th:>4.1f}%  {res.summary()}")
        days_sorted = sorted(by_day)
        curve, peak, dd = 0.0, 0.0, 0.0
        for d in days_sorted:
            curve += by_day[d]
            peak = max(peak, curve)
            dd = max(dd, peak - curve)
        worst = sorted(by_day.items(), key=lambda kv: kv[1])[:5]
        totals[th] = (sum(by_day.values()), n, dd, worst)
        print()
    print("Portfolio (all symbols together):")
    for th, (pnl, n, dd, worst) in totals.items():
        w = ", ".join(f"{d} {v:+.0f}" for d, v in worst)
        print(f"  filter {th:>4.1f}%: P/L {pnl:+9.0f} $  trades {n:4d}  max drawdown {dd:7.0f} $  worst days: {w}")


if __name__ == "__main__":
    main(sys.argv[1:])
