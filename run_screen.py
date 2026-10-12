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
            f"buy&hold%={hold:6.1f} bars={len(bars)}")


def main(argv: list[str]) -> None:
    strategy, timeframe, days, symbols = argv[0], Timeframe(argv[1]), int(argv[2]), argv[3:]
    print(f"{strategy} on {timeframe.value}, last {days} days\n")
    for sym in symbols:
        try:
            print(screen(sym, strategy, timeframe, days))
        except Exception as e:  # one bad symbol shouldn't end the screen
            print(f"{sym:<8} error: {e}")
    print("\nPaper/backtest only. Past behaviour is not a prediction of future returns.")


if __name__ == "__main__":
    main(sys.argv[1:])
