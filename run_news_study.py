"""Event study: what happens after a big move on a news day? (read-only research)

  python run_news_study.py 730 AAPL MSFT NVDA ...

Event = a daily move of at least MOVE_PCT with volume at least VOL_MULT x its 20-day average.
For each event the Alpaca news feed is checked for headlines in the 24 h up to that day's close
(earnings or other). Reports forward returns from the event day's close (1/3/5/10 days) and a
simple long trade on up-moves: buy at the close, exit after HOLD_DAYS or at a STOP_PCT stop.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import timedelta
from statistics import mean

from data import fetch_bars
from data.news import fetch_news
from models import AssetClass, Timeframe

MOVE_PCT = 3.0
VOL_MULT = 2.0
HOLD_DAYS = 5
STOP_PCT = 3.0
HORIZONS = (1, 3, 5, 10)


def events(symbol: str, days: int):
    bars = fetch_bars(symbol, AssetClass.EQUITY, Timeframe.D1, limit=10_000, days=days)
    out = []
    for i in range(21, len(bars) - max(HORIZONS)):
        prev, bar = bars[i - 1], bars[i]
        move = (bar.close / prev.close - 1) * 100
        avg_vol = mean(b.volume for b in bars[i - 20:i]) or 1
        if abs(move) < MOVE_PCT or bar.volume < VOL_MULT * avg_vol:
            continue
        fwd = {h: (bars[i + h].close / bar.close - 1) * 100 for h in HORIZONS}
        # Long trade bought at the event close: exit after `hold` days or at the stop, checked on
        # daily lows (a gap below the stop fills at the open).
        stop = bar.close * (1 - STOP_PCT / 100)
        trades = {}
        for hold in (5, 10):
            trades[hold] = (bars[i + hold].close / bar.close - 1) * 100
            for b in bars[i + 1:i + hold + 1]:
                if b.low <= stop:
                    trades[hold] = (min(b.open, stop) / bar.close - 1) * 100
                    break
        out.append({"day": bar.timestamp, "move": move, "fwd": fwd, "trade": trades[5], "trade10": trades[10]})
    baseline = mean((bars[j + HOLD_DAYS].close / bars[j].close - 1) * 100
                    for j in range(len(bars) - HOLD_DAYS)) if len(bars) > HOLD_DAYS else 0.0
    return out, baseline


def classify(symbol: str, ev: dict) -> str:
    # Daily bars start at 00:00 New York; +16 h is the 16:00 close. Window: previous close -> close.
    end = ev["day"] + timedelta(hours=16)
    news = fetch_news(symbol, end - timedelta(hours=24), end, limit=50)
    if not news:
        return "no news"
    return "earnings" if any(n.is_earnings for n in news) else "other news"


def main(argv: list[str]) -> None:
    days, symbols = int(argv[0]), argv[1:]
    groups: dict = defaultdict(list)
    baselines = []
    for sym in symbols:
        try:
            evs, base = events(sym, days)
        except Exception as e:  # one bad symbol shouldn't end the study
            print(f"{sym}: error {e}")
            continue
        baselines.append(base)
        for ev in evs:
            kind = classify(sym, ev)
            direction = "up" if ev["move"] > 0 else "down"
            groups[(direction, kind)].append(ev)
        print(f"{sym}: {len(evs)} events, normal {HOLD_DAYS}-day return {base:+.2f} %")
    print(f"\nEvents: move >= {MOVE_PCT} % on >= {VOL_MULT}x volume, last {days} days, {len(symbols)} symbols")
    print(f"Normal {HOLD_DAYS}-day return (all days): {mean(baselines) if baselines else 0:+.2f} %\n")
    print("| Richtung | News | Anzahl | +1 T | +3 T | +5 T | +10 T | Trefferquote 5 T | Trade 5 T | Trade 10 T "
          f"| Trade 10 T 1./2. Hälfte | (Stop {STOP_PCT:g} %) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (direction, kind), evs in sorted(groups.items()):
        f = {h: mean(e["fwd"][h] for e in evs) for h in HORIZONS}
        hit = sum(e["fwd"][HOLD_DAYS] > 0 for e in evs) / len(evs) * 100
        tr5, tr10 = mean(e["trade"] for e in evs), mean(e["trade10"] for e in evs)
        evs_sorted = sorted(evs, key=lambda e: e["day"])
        half = len(evs_sorted) // 2 or 1
        h1 = mean(e["trade10"] for e in evs_sorted[:half])
        h2 = mean(e["trade10"] for e in evs_sorted[half:]) if evs_sorted[half:] else float("nan")
        print(f"| {direction} | {kind} | {len(evs)} | {f[1]:+.2f} % | {f[3]:+.2f} % | {f[5]:+.2f} % | "
              f"{f[10]:+.2f} % | {hit:.0f} % | {tr5:+.2f} % | {tr10:+.2f} % | {h1:+.2f} / {h2:+.2f} % | |")
    print("\nPaper/backtest only. Past behaviour is not a prediction of future returns.")


if __name__ == "__main__":
    main(sys.argv[1:])
