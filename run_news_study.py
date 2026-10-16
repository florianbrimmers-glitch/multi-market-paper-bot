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


def _m(xs) -> float:
    xs = list(xs)
    return mean(xs) if xs else float("nan")


def _trade(bars, start: int, entry: float, hold: int) -> float:
    """Long bought at `entry` on bar `start`, held `hold` days (exit at that day's close) or stopped."""
    stop = entry * (1 - STOP_PCT / 100)
    for b in bars[start:start + hold]:
        if b.low <= stop:
            return (min(b.open, stop) / entry - 1) * 100 if b is not bars[start] else -STOP_PCT
    return (bars[start + hold - 1].close / entry - 1) * 100


def gap_events(bars):
    """Opening gaps of >= MOVE_PCT, bought right at the open (the earliest regular-session entry
    after a release before the open or after the previous close)."""
    out = []
    for i in range(21, len(bars) - 10):
        gap = (bars[i].open / bars[i - 1].close - 1) * 100
        if gap < MOVE_PCT:
            continue
        out.append({"day": bars[i].timestamp, "move": gap,
                    "open5": _trade(bars, i, bars[i].open, 5), "open10": _trade(bars, i, bars[i].open, 10)})
    return out


def events(bars):
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
        # Same event, but bought at the next day's open (live: volume is only final after the close).
        nxt = {hold: _trade(bars, i + 1, bars[i + 1].open, hold) for hold in (5, 10)}
        out.append({"day": bar.timestamp, "move": move, "fwd": fwd, "trade": trades[5], "trade10": trades[10],
                    "next5": nxt[5], "next10": nxt[10]})
    baseline = mean((bars[j + HOLD_DAYS].close / bars[j].close - 1) * 100
                    for j in range(len(bars) - HOLD_DAYS)) if len(bars) > HOLD_DAYS else 0.0
    return out, baseline


def classify(symbol: str, ev: dict, until_hours: float = 16) -> str:
    # Daily bars start at 00:00 New York; +16 h is the 16:00 close, +9.5 h the 09:30 open.
    end = ev["day"] + timedelta(hours=until_hours)
    news = fetch_news(symbol, end - timedelta(hours=24), end, limit=50)
    if not news:
        return "no news"
    return "earnings" if any(n.is_earnings for n in news) else "other news"


def main(argv: list[str]) -> None:
    days, symbols = int(argv[0]), argv[1:]
    groups: dict = defaultdict(list)
    baselines = []
    gap_all = []
    for sym in symbols:
        try:
            bars = fetch_bars(sym, AssetClass.EQUITY, Timeframe.D1, limit=10_000, days=days)
            evs, base = events(bars)
        except Exception as e:  # one bad symbol shouldn't end the study
            print(f"{sym}: error {e}")
            continue
        baselines.append(base)
        for ev in evs:
            kind = classify(sym, ev)
            direction = "up" if ev["move"] > 0 else "down"
            groups[(direction, kind)].append(ev)
        for g in gap_events(bars):
            g["kind"] = classify(sym, g, until_hours=9.5)
            gap_all.append(g)
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
    print("\nEntry variants for up-moves on earnings (stop %g %%):" % STOP_PCT)
    up = groups.get(("up", "earnings"), [])
    if up:
        def row(name, k5, k10, evs):
            evs = sorted(evs, key=lambda e: e["day"])
            half = len(evs) // 2 or 1
            print(f"| {name} | {len(evs)} | {_m(e[k5] for e in evs):+.2f} % | {_m(e[k10] for e in evs):+.2f} % | "
                  f"{sum(e[k5] > 0 for e in evs) / len(evs) * 100:.0f} % | "
                  f"{_m(e[k10] for e in evs[:half]):+.2f} / {_m(e[k10] for e in evs[half:]):+.2f} % |")
        print("| Einstieg | Anzahl | Trade 5 T | Trade 10 T | Treffer 5 T | 10 T 1./2. Hälfte |")
        print("|---|---|---|---|---|---|")
        row("Schluss des Ereignistags", "trade", "trade10", up)
        row("Eröffnung am nächsten Tag", "next5", "next10", up)
    gaps = [g for g in gap_all if g["kind"] == "earnings"]
    if gaps:
        print(f"| Sofort zur Eröffnung nach Zahlen (Gap >= {MOVE_PCT:g} %) | {len(gaps)} | "
              f"{_m(g['open5'] for g in gaps):+.2f} % | {_m(g['open10'] for g in gaps):+.2f} % | "
              f"{sum(g['open5'] > 0 for g in gaps) / len(gaps) * 100:.0f} % | "
              f"{_m(g['open10'] for g in sorted(gaps, key=lambda g: g['day'])[:len(gaps) // 2 or 1]):+.2f} / "
              f"{_m(g['open10'] for g in sorted(gaps, key=lambda g: g['day'])[len(gaps) // 2:]):+.2f} % |")
    print("\nPaper/backtest only. Past behaviour is not a prediction of future returns.")


if __name__ == "__main__":
    main(sys.argv[1:])
