"""Earnings drift across all liquid US stocks (read-only research).

  python run_earnings_study.py 730

Universe: every active US stock with a last price >= MIN_PRICE and a median 60-day IEX dollar
volume >= MIN_IEX_DOLLARS (IEX carries only a few % of all volume, so $1M on IEX is roughly
$30-50M market-wide). Event: an up-move of >= MOVE_PCT on >= VOL_MULT x the 20-day volume with
earnings headlines between the previous close and the close. Trade: buy at the event close
(live: 15 minutes before), stop STOP_PCT, exit after 5 or 10 days. Also shown: entry at the next
open, results by liquidity tercile and by half of the period.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import timedelta
from statistics import mean, median

from data.universe import daily_bars, news_for, us_equities

MIN_PRICE = 10.0
MIN_IEX_DOLLARS = 1_000_000
MOVE_PCT = 3.0
VOL_MULT = 2.0
STOP_PCT = 3.0


def _m(xs) -> float:
    xs = list(xs)
    return mean(xs) if xs else float("nan")


def trade(bars, start: int, entry: float, hold: int) -> float:
    stop = entry * (1 - STOP_PCT / 100)
    for b in bars[start:start + hold]:
        if b.low <= stop:
            return (min(b.open, stop) / entry - 1) * 100
    return (bars[start + hold - 1].close / entry - 1) * 100


def main(argv: list[str]) -> None:
    days = int(argv[0]) if argv else 730
    symbols = us_equities()
    print(f"{len(symbols)} tradable US symbols")
    bars = daily_bars(symbols, days)
    liquid = {}
    for s, b in bars.items():
        if len(b) < 80 or b[-1].close < MIN_PRICE:
            continue
        dv = median(x.close * x.volume for x in b[-60:])
        if dv >= MIN_IEX_DOLLARS:
            liquid[s] = dv
    print(f"{len(liquid)} liquid symbols (price >= {MIN_PRICE:g} $, median IEX dollar volume >= "
          f"{MIN_IEX_DOLLARS / 1e6:g} M$)")
    cuts = sorted(liquid.values())
    t1, t2 = cuts[len(cuts) // 3], cuts[2 * len(cuts) // 3]

    candidates = defaultdict(list)  # event day -> [(symbol, index)]
    for s in liquid:
        b = bars[s]
        for i in range(21, len(b) - 11):
            move = (b[i].close / b[i - 1].close - 1) * 100
            avg_vol = mean(x.volume for x in b[i - 20:i]) or 1
            if move >= MOVE_PCT and b[i].volume >= VOL_MULT * avg_vol:
                candidates[b[i].timestamp].append((s, i))
    print(f"{sum(len(v) for v in candidates.values())} up-move events on {len(candidates)} days; checking news")

    events = []
    for day, items in sorted(candidates.items()):
        end = day + timedelta(hours=16)
        syms = sorted({s for s, _ in items})
        news = {}
        for k in range(0, len(syms), 50):
            news.update(news_for(syms[k:k + 50], end - timedelta(hours=24), end))
        for s, i in items:
            if any(n.is_earnings for n in news.get(s, [])):
                b = bars[s]
                tier = "groß" if liquid[s] >= t2 else "mittel" if liquid[s] >= t1 else "klein"
                events.append({"day": day, "tier": tier,
                               "c5": trade(b, i + 1, b[i].close, 5), "c10": trade(b, i + 1, b[i].close, 10),
                               "o5": trade(b, i + 1, b[i + 1].open, 5)})
    print(f"{len(events)} earnings up-move events\n")

    def row(name, evs):
        evs = sorted(evs, key=lambda e: e["day"])
        h = len(evs) // 2 or 1
        print(f"| {name} | {len(evs)} | {_m(e['c5'] for e in evs):+.2f} % | {_m(e['c10'] for e in evs):+.2f} % | "
              f"{_m(e['c5'] > 0 for e in evs) * 100:.0f} % | {_m(e['c5'] for e in evs[:h]):+.2f} / "
              f"{_m(e['c5'] for e in evs[h:]):+.2f} % | {_m(e['o5'] for e in evs):+.2f} % |")

    print(f"| Gruppe | Anzahl | Trade 5 T (Schluss) | Trade 10 T | Treffer 5 T | 5 T 1./2. Hälfte | "
          f"5 T Einstieg nächster Morgen |")
    print("|---|---|---|---|---|---|---|")
    row("alle", events)
    for tier in ("groß", "mittel", "klein"):
        row(f"Liquidität {tier}", [e for e in events if e["tier"] == tier])
    per_month = len(events) / (days / 30.4)
    print(f"\nAbout {per_month:.0f} signals per month.")
    print("Paper/backtest only. Past behaviour is not a prediction of future returns.")


if __name__ == "__main__":
    main(sys.argv[1:])
