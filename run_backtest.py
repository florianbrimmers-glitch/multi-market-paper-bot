"""Backtest configured instruments on recent Alpaca history.

  python run_backtest.py            # all instruments
  python run_backtest.py SPY QQQ    # a subset
"""
from __future__ import annotations

import sys

import config
from data import fetch_bars
from engine import backtest_symbol
from models import AssetClass


def main(argv: list[str]) -> None:
    wanted = set(argv) or {i.symbol for i in config.INSTRUMENTS}
    for inst in config.INSTRUMENTS:
        if inst.symbol in wanted:
            bars = fetch_bars(inst.symbol, inst.asset_class, inst.timeframe, limit=1000)
            print(backtest_symbol(inst.symbol, inst.strategy, bars,
                                  crypto=inst.asset_class == AssetClass.CRYPTO).summary())
    print("\nPaper/backtest only. Past behaviour is not a prediction of future returns.")


if __name__ == "__main__":
    main(sys.argv[1:])
