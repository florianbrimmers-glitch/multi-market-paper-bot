"""Earnings drift, measured the way the bot trades it (read-only research).

  python run_drift_detail.py 730

run_earnings_study.py is the study the strategy was built on. Three reviews found it kinder than
live trading and blind in two places; this script measures those points on the same data:

1. Costs as measured live (review finding drift-5): the buy fills 0.12 % above the reference
   price, a stop fills 0.40 % below the stop price, the sale after ten days costs 0.05 %.
2. The universe as it was on the event day (price and 60-day IEX dollar volume before the event),
   not as it is at the end of the sample.
3. Every strong up-day, not only those with an earnings headline: which kind of headline, if
   any, carries the result (drift-4 and the events research).
4. The stop: 3 % (live), 5 %, 8 % and none, at equal size and at equal risk.

Rule as live: mid and small caps, move >= 6 %, close in the top quarter of the day's range,
volume >= 2 x the 20-day average, buy at the close, sell after 10 sessions.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import timedelta
from statistics import mean, median

import config
from data.news import MAX_SYMBOLS

ENTRY_SLIP = 0.0012   # buy fill above the 15:45 reference price (34 live buys)
STOP_SLIP = 0.0040    # stop fill below the stop price (13 live stop-outs without an opening gap)
EXIT_SLIP = 0.0005    # sale after ten days (assumed; too few live sales to measure)
HOLD = 10
MIN_CLASS_TRADES = 100

# First match wins. "earnings" is the live pattern (data/news.py), so that class is what the bot buys.
CLASSES = [
    ("earnings", r"\b(earnings|eps|q[1-4]|quarter(ly)?|fiscal|guidance)\b"),
    ("merger", r"\b(to be acquired|to acquire|acquires|acquisition|merger|merge with|takeover|buyout|"
               r"go private|take[- ]private|tender offer)\b"),
    ("analyst_up", r"\b(upgrades?|raises? (the )?(price )?target|raises? pt|initiates? coverage (on .* )?"
                   r"with (a |an )?(buy|overweight|outperform|strong buy))\b"),
    ("analyst_down", r"\b(downgrades?|lowers? (the )?(price )?target|cuts? (price )?target|lowers? pt)\b"),
    ("buyback", r"\b(buyback|share repurchase|stock repurchase|repurchase program|repurchase (of )?up to|"
                r"repurchase authorization)\b"),
    ("index", r"\b(join|joins|joining|added to|addition to|to replace)\b.*\b(s&p (500|midcap 400|"
              r"smallcap 600|400|600)|nasdaq[- ]100|russell (1000|2000|3000))\b"),
    ("insider", r"\b(insider (buy|buying|purchase|purchases)|(ceo|cfo|director|chairman|insiders?) "
                r"(buys|bought|purchases|purchased|acquires shares))\b"),
    ("offering", r"\b(public offering|secondary offering|registered direct|private placement|"
                 r"at-the-market|prices? (its )?(upsized )?offering|convertible (senior )?notes)\b"),
    ("clinical", r"\b(fda|phase [1-3i]+|topline|clinical|trial|pdufa|breakthrough therapy)\b"),
]
_CLASS_RX = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in CLASSES]

# What kind of earnings headline. First match wins; "report" and "transcript" are the company's
# own numbers, the others are what the live pattern also lets through.
EARNINGS_KINDS = [
    ("transcript", r"earnings call transcript|earnings conference call|conference call (transcript|highlights)"),
    ("preview", r"\b(preview|what to expect|ahead of|to report|set to report|likely to report|"
                r"earnings outlook|a look ahead|expected to report|scheduled to report|before earnings)\b"),
    ("date", r"\b(to announce|will announce|schedules?|sets? date|to host|to hold|to release|will release|"
             r"announces (date|timing))\b"),
    ("dividend", r"\b(dividend|distribution)\b"),
    ("report", r"\b(reports?|reported|posts?|posted|announces|delivers|results|beats?|miss(es)?|"
               r"tops?|eps \$|sales \$|revenue \$|vs\.? \$|estimate)\b"),
    ("analyst", r"\b(maintains|reiterates|upgrades?|downgrades?|price target|raises? pt|lowers? pt|analysts?)\b"),
    ("guidance", r"\b(guidance|outlook|forecast|sees fy|sees q[1-4])\b"),
]
_KIND_RX = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in EARNINGS_KINDS]


def classify(headlines: list[tuple[str, int]]) -> str:
    """Class of a candidate from its (headline, number of tagged symbols) items. Items that tag more
    than MAX_SYMBOLS companies are market roundups and do not count, as in the live rule."""
    own = [h for h, n in headlines if n <= MAX_SYMBOLS]
    if not own:
        return "no_headline"
    for name, rx in _CLASS_RX:
        if any(rx.search(h) for h in own):
            return name
    return "other_news"


def earnings_kind(headlines: list[tuple[str, int]]) -> str:
    """For a candidate of class earnings: the best kind among its earnings headlines. A company
    report or a call transcript wins over a preview, so 'preview' means nothing but previews."""
    live = _CLASS_RX[0][1]
    own = [h for h, n in headlines if n <= MAX_SYMBOLS and live.search(h)]
    kinds = set()
    for h in own:
        kinds.add(next((name for name, rx in _KIND_RX if rx.search(h)), "other"))
    for name in ("transcript", "report", "analyst", "guidance", "dividend", "date", "preview", "other"):
        if name in kinds:
            return name
    return "other"


def trade(bars, i: int, stop_pct: float | None, costs: bool = True) -> tuple[float, bool]:
    """Buy at the close of bar i, sell at the close of bar i+HOLD unless the stop is hit first.
    Returns (result in % of the money put in, stopped?). The stop sits stop_pct below the reference
    price (the close), as live; it fills at the stop or at a lower open, minus the stop slippage."""
    ref = bars[i].close
    entry = ref * (1 + ENTRY_SLIP) if costs else ref
    if stop_pct is not None:
        stop = ref * (1 - stop_pct / 100)
        for b in bars[i + 1:i + 1 + HOLD]:
            if b.low <= stop:
                fill = min(b.open, stop) * ((1 - STOP_SLIP) if costs else 1.0)
                return (fill / entry - 1) * 100, True
    out = bars[i + HOLD].close * ((1 - EXIT_SLIP) if costs else 1.0)
    return (out / entry - 1) * 100, False


def stats(rows: list[tuple[object, float]]) -> tuple[int, float, float]:
    """(count, mean, t) for (day, value) rows; t with the days as clusters, because several
    events of one day share that day's market move."""
    n = len(rows)
    if n == 0:
        return 0, float("nan"), float("nan")
    m = mean(v for _, v in rows)
    by_day: dict[object, float] = defaultdict(float)
    for d, v in rows:
        by_day[d] += v - m
    if len(by_day) < 2:
        return n, m, float("nan")
    g = len(by_day)
    var = g / (g - 1) * sum(x * x for x in by_day.values()) / (n * n)
    return n, m, (m / var ** 0.5 if var > 0 else float("nan"))


def halves(rows: list[tuple[object, float]]) -> tuple[float, float]:
    rows = sorted(rows, key=lambda r: r[0])
    h = len(rows) // 2
    first = mean(v for _, v in rows[:h]) if h else float("nan")
    second = mean(v for _, v in rows[h:]) if rows[h:] else float("nan")
    return first, second


def find_candidates(bars: dict, t_small: float, t_large: float, point_in_time: bool = True) -> list[dict]:
    """Strong up-days by the live rule. point_in_time: price and liquidity from before the event."""
    out = []
    for s, b in bars.items():
        if len(b) < 80:
            continue
        end_dv = median(x.close * x.volume for x in b[-60:])
        for i in range(60, len(b) - HOLD):
            move = (b[i].close / b[i - 1].close - 1) * 100
            if move < config.DRIFT_MIN_MOVE_PCT:
                continue
            rng = b[i].high - b[i].low
            close_pos = (b[i].close - b[i].low) / rng if rng > 0 else 1.0
            avg_vol = mean(x.volume for x in b[i - 20:i]) or 1
            if close_pos < config.DRIFT_MIN_CLOSE_POS or b[i].volume < config.DRIFT_VOL_MULT * avg_vol:
                continue
            if point_in_time:
                dv = median(x.close * x.volume for x in b[i - 60:i])
                price = b[i].close
            else:
                dv, price = end_dv, b[-1].close
            if price < config.DRIFT_MIN_PRICE or dv < t_small or dv >= t_large:
                continue  # below the liquidity floor, or a large cap (top third)
            out.append({"sym": s, "i": i, "day": b[i].timestamp})
    return out


def _fmt(rows, extra: str = "") -> str:
    n, m, t = stats(rows)
    a, b = halves(rows)
    return f"{n} | {m:+.2f} % | {t:+.1f} | {a:+.2f} / {b:+.2f} %{extra}"


def report(bars: dict, cands: list[dict], base: list[tuple[object, float]], n_end_of_sample: int,
           days: int) -> None:
    """Print the tables. Each candidate carries 'class', 'kind' and 'heads'."""
    def rows(sel, stop=config.DRIFT_STOP_PCT * 100, costs=True):
        return [(c["day"], trade(bars[c["sym"]], c["i"], stop, costs)[0]) for c in sel]

    live = [c for c in cands if c["class"] == "earnings"]
    print(f"\n## 1. The live rule ({len(live)} trades in {days} days, mid and small caps, 3 % stop, "
          f"{HOLD} sessions)")
    print("| | Trades | per trade | t | 1st / 2nd half | stopped |")
    print("|---|---|---|---|---|---|")
    stopped = mean(trade(bars[c["sym"]], c["i"], 3.0)[1] for c in live) * 100 if live else float("nan")
    print(f"| before costs | {_fmt(rows(live, costs=False))} | {stopped:.0f} % |")
    print(f"| after live costs | {_fmt(rows(live))} | {stopped:.0f} % |")
    print(f"| same trade on ordinary days, after costs | {_fmt(base)} | |")
    n, m, _ = stats(rows(live))
    _, mb, _ = stats(base)
    print(f"\nEdge over ordinary days after costs: {m - mb:+.2f} percentage points per trade. "
          f"Candidates with the universe as at the end of the sample (old method): {n_end_of_sample}; "
          f"as on the event day: {len(cands)}.")

    print("\n## 2. Every strong up-day by kind of headline (after costs, 3 % stop)")
    print(f"A class counts only with at least {MIN_CLASS_TRADES} trades, at least +0.5 % per trade, "
          "both halves positive and t of at least 2.")
    print("| Headline | Trades | per trade | t | 1st / 2nd half | passes |")
    print("|---|---|---|---|---|---|")
    order = ["earnings"] + [n for n, _ in CLASSES[1:]] + ["other_news", "no_headline"]
    for name in order:
        sel = rows([c for c in cands if c["class"] == name])
        if not sel:
            continue
        n, m, t = stats(sel)
        a, b = halves(sel)
        ok = n >= MIN_CLASS_TRADES and m >= 0.5 and a > 0 and b > 0 and t >= 2
        print(f"| {name} | {_fmt(sel)} | {'yes' if ok else 'no'} |")
    print(f"| all strong up-days | {_fmt(rows(cands))} | |")
    print(f"| all without an earnings headline | {_fmt(rows([c for c in cands if c['class'] != 'earnings']))} | |")

    print("\n## 3. Earnings headlines by kind (what the live pattern lets through)")
    print("| Kind | Trades | per trade | t | 1st / 2nd half |")
    print("|---|---|---|---|---|")
    for kind in ("report", "transcript", "analyst", "guidance", "dividend", "date", "preview", "other"):
        sel = rows([c for c in live if c["kind"] == kind])
        if sel:
            print(f"| {kind} | {_fmt(sel)} |")
    for kind in ("preview", "date", "dividend", "other"):
        for c in [c for c in live if c["kind"] == kind][:6]:
            print(f"  example {kind}: {c['sym']} {str(c['day'])[:10]}: {c['heads'][0][:110]}")

    print("\n## 4. Stop width on the earnings trades (after costs)")
    print("Equal risk: the position is sized so that a stop-out costs the same as with the 3 % stop, "
          "so a wider stop means a smaller position.")
    print("| Stop | per trade, equal size | t | 1st / 2nd half | stopped | worst | per trade, equal risk |")
    print("|---|---|---|---|---|---|---|")
    for width in (3.0, 5.0, 8.0, None):
        sel = rows(live, stop=width)
        if not sel:
            continue
        n, m, t = stats(sel)
        a, b = halves(sel)
        share = mean(trade(bars[c["sym"]], c["i"], width)[1] for c in live) * 100
        equal_risk = f"{m * 3.0 / width:+.2f} %" if width else "–"
        print(f"| {f'{width:g} %' if width else 'none'} | {m:+.2f} % | {t:+.1f} | {a:+.2f} / {b:+.2f} % | "
              f"{share:.0f} % | {min(v for _, v in sel):+.1f} % | {equal_risk} |")
    print("\nPaper/backtest only. Past behaviour is not a prediction of future returns.")


def main(argv: list[str]) -> None:
    from data.universe import daily_bars, news_for, us_equities

    days = int(argv[0]) if argv else 730
    symbols = us_equities()
    print(f"{len(symbols)} tradable US symbols")
    bars = daily_bars(symbols, days)
    # Liquidity cuts in dollars from the end of the sample (as the old study and the live universe).
    liquid = sorted(median(x.close * x.volume for x in b[-60:]) for b in bars.values()
                    if len(b) >= 80 and b[-1].close >= config.DRIFT_MIN_PRICE)
    liquid = [dv for dv in liquid if dv >= config.DRIFT_MIN_IEX_DOLLARS]
    t_large = liquid[2 * len(liquid) // 3]
    print(f"{len(liquid)} liquid symbols; large caps start at a median IEX dollar volume of {t_large / 1e6:.1f} M$")
    cands = find_candidates(bars, config.DRIFT_MIN_IEX_DOLLARS, t_large)
    n_old = len(find_candidates(bars, config.DRIFT_MIN_IEX_DOLLARS, t_large, point_in_time=False))
    by_day: dict[object, list[dict]] = defaultdict(list)
    for c in cands:
        by_day[c["day"]].append(c)
    print(f"{len(cands)} strong up-days on {len(by_day)} days; reading headlines")
    for day, items in sorted(by_day.items()):
        end = day + timedelta(hours=16)
        syms = sorted({c["sym"] for c in items})
        news: dict = {}
        for k in range(0, len(syms), 50):
            news.update(news_for(syms[k:k + 50], end - timedelta(hours=24), end))
        for c in items:
            heads = [(n.headline, len(n.symbols)) for n in news.get(c["sym"], [])]
            c["class"] = classify(heads)
            c["kind"] = earnings_kind(heads) if c["class"] == "earnings" else ""
            c["heads"] = [h for h, n in heads if n <= MAX_SYMBOLS] or [""]
    # Yardstick: the same trade (3 % stop, costs) on every fifth ordinary day of the same stocks.
    base = []
    for s in {c["sym"] for c in cands}:
        b = bars[s]
        base += [(b[i].timestamp, trade(b, i, config.DRIFT_STOP_PCT * 100)[0])
                 for i in range(60, len(b) - HOLD, 5)]
    report(bars, cands, base, n_old, days)


if __name__ == "__main__":
    main(sys.argv[1:])
