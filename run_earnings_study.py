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


def managed_trade(bars, start: int, entry: float, hold: int, mode: str) -> float:
    """10-day hold with a stop that can move up (checked on daily lows, raised on closes):
    fixed = 3 % below entry; be5 = to the entry once a close is 5 % up; trail3 / trail5 = that far
    below the highest close so far (never below the initial stop)."""
    stop = entry * (1 - STOP_PCT / 100)
    high = entry
    for b in bars[start:start + hold]:
        if b.low <= stop:
            return (min(b.open, stop) / entry - 1) * 100
        high = max(high, b.close)
        if mode == "be5" and b.close >= entry * 1.05:
            stop = max(stop, entry)
        elif mode == "trail3":
            stop = max(stop, high * 0.97)
        elif mode == "trail5":
            stop = max(stop, high * 0.95)
    return (bars[start + hold - 1].close / entry - 1) * 100


EXIT_MODES = {"fixed": "fester Stop −3 % (jetzt)", "be5": "Stop auf Einstand ab +5 %",
              "trail3": "Stop 3 % unter Hoch", "trail5": "Stop 5 % unter Hoch"}


def main(argv: list[str]) -> None:
    """argv: days [min_move_pct] [min_close_position 0..1] [tiers comma-separated]."""
    days = int(argv[0]) if argv else 730
    min_move = float(argv[1]) if len(argv) > 1 else MOVE_PCT
    min_close_pos = float(argv[2]) if len(argv) > 2 else 0.0
    tiers = set(argv[3].split(",")) if len(argv) > 3 else {"groß", "mittel", "klein"}
    print(f"Variant: move >= {min_move:g} %, close in the top {100 - min_close_pos * 100:.0f} % of the day's "
          f"range, liquidity {', '.join(sorted(tiers))}")
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

    def tier_of(s):
        return "groß" if liquid[s] >= t2 else "mittel" if liquid[s] >= t1 else "klein"

    candidates = defaultdict(list)  # event day -> [(symbol, index)]
    base = defaultdict(list)        # same trade on ordinary days, as the yardstick
    for s in liquid:
        if tier_of(s) not in tiers:
            continue
        b = bars[s]
        for i in range(21, len(b) - 11):
            if i % 5 == 0:
                base[tier_of(s)].append(trade(b, i + 1, b[i].close, 5))
            move = (b[i].close / b[i - 1].close - 1) * 100
            avg_vol = mean(x.volume for x in b[i - 20:i]) or 1
            rng = b[i].high - b[i].low
            close_pos = (b[i].close - b[i].low) / rng if rng > 0 else 1.0
            if move >= min_move and b[i].volume >= VOL_MULT * avg_vol and close_pos >= min_close_pos:
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
                tier = tier_of(s)
                events.append({"day": day, "tier": tier,
                               "c5": trade(b, i + 1, b[i].close, 5), "c10": trade(b, i + 1, b[i].close, 10),
                               "o5": trade(b, i + 1, b[i + 1].open, 5),
                               **{f"x_{m}": managed_trade(b, i + 1, b[i].close, 10, m) for m in EXIT_MODES}})
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
        if tier in tiers:
            row(f"Liquidität {tier}", [e for e in events if e["tier"] == tier])
    print("\nYardstick, same trade (buy at the close, 3 % stop, 5 days) on ordinary days:")
    for tier, vals in base.items():
        print(f"- Liquidität {tier}: {_m(vals):+.2f} % ({len(vals)} samples)")
    drift = sorted((e for e in events if e["tier"] in ("mittel", "klein")), key=lambda e: e["day"])
    if drift:
        h = len(drift) // 2 or 1
        print(f"\nStop variants, mid + small caps ({len(drift)} trades, buy at the close, 10 days):")
        print("| Stop | Trade Ø | Treffer | 1. / 2. Hälfte | schlechtester | bester |")
        print("|---|---|---|---|---|---|")
        for m, label in EXIT_MODES.items():
            v = [e[f"x_{m}"] for e in drift]
            print(f"| {label} | {_m(v):+.2f} % | {_m(x > 0 for x in v) * 100:.0f} % | "
                  f"{_m(e[f'x_{m}'] for e in drift[:h]):+.2f} / {_m(e[f'x_{m}'] for e in drift[h:]):+.2f} % | "
                  f"{min(v):+.1f} % | {max(v):+.1f} % |")
    per_month = len(events) / (days / 30.4)
    print(f"\nAbout {per_month:.0f} signals per month.")
    print("Paper/backtest only. Past behaviour is not a prediction of future returns.")


if __name__ == "__main__":
    main(sys.argv[1:])
