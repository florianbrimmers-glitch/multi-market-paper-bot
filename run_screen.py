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


def list_trades(symbol: str, days: int, since: str) -> None:
    """Every mean-reversion trade the backtest makes on `symbol` (15-min bars, live rules) since
    a date, e.g. to check what a skipped or rejected live entry would have done."""
    bars = fetch_bars(symbol, AssetClass.EQUITY, Timeframe.M15, limit=10_000, days=days)
    res = backtest_symbol(symbol, "mean_reversion", bars, eod_mode="off", bar_minutes=15)
    print(f"{symbol} mean reversion trades since {since} (times UTC, bar start)")
    for t in res.trades:
        if t.entry_time[:10] >= since:
            out = f"{t.exit_time[:16]} @ {t.exit_price:.2f} ({t.reason_out}) P/L {t.pnl:+.2f}" if t.closed else "open"
            print(f"- {t.entry_time[:16]} buy {t.qty:g} @ {t.entry_price:.2f} -> {out}")


def drift_scan() -> None:
    """What the earnings-drift pass would buy on the latest session in the snapshots. Read-only."""
    from collections import Counter
    from datetime import date, datetime, timedelta, timezone

    from data.universe import news_for, snapshots
    from engine import earnings_drift as drift

    probe = snapshots(["SPY"])
    day = probe["SPY"]["day"]
    drift._universe_cache.clear()
    universe = drift.build_universe((date.fromisoformat(day) + timedelta(days=1)).isoformat())
    snaps = snapshots(sorted(universe))
    day = Counter(q["day"] for q in snaps.values()).most_common(1)[0][0]
    cands = drift.candidates(universe, snaps, day)
    print(f"Session {day}: universe {len(universe)} mid/small caps, {len(cands)} strong up-moves")
    end = datetime.fromisoformat(day + "T20:00:00+00:00")
    news = {}
    syms = [c[0] for c in cands]
    for k in range(0, len(syms), 50):
        news.update(news_for(syms[k:k + 50], end - timedelta(hours=24), end))
    for sym, q, move in cands:
        heads = [n.headline for n in news.get(sym, []) if n.is_earnings]
        flag = "KAUF" if heads else "keine Zahlen"
        print(f"- {sym:<6} +{move:4.1f} %  {flag:<12} {heads[0][:90] if heads else ''}")


def main(argv: list[str]) -> None:
    if argv[0] == "drift_dry":  # the live earnings-drift pass for today's session, DRY_RUN (no orders)
        import os
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from brief.schedule import BriefState
        from broker import AlpacaBroker
        from engine import earnings_drift
        from models import Clock
        os.environ["DRY_RUN"] = "true"
        ny = ZoneInfo("America/New_York")
        now = datetime.now(ny)
        ts = now.replace(hour=15, minute=45, second=0, microsecond=0)
        clock = Clock(timestamp=ts, is_open=True, next_open=ts, next_close=ts.replace(hour=16, minute=0))
        with AlpacaBroker() as broker:
            for line in earnings_drift.run(broker, BriefState(), clock):
                print(line)
        print("done")
        return
    if argv[0] == "drift_scan":  # dry earnings-drift scan on the latest full session (no orders)
        drift_scan()
        return
    if argv[0] == "earnings_study":  # run_screen.py earnings_study <days> <move,close_pos,tiers|-> ...
        import run_earnings_study
        extra = [] if argv[2] == "-" else argv[2].split(";")
        run_earnings_study.main([argv[1]] + extra)
        return
    if argv[0] == "news_study":  # run_screen.py news_study <days> <unused> <symbols...>
        import run_news_study
        run_news_study.main([argv[1]] + argv[3:])
        return
    if argv[0] == "trades":  # run_screen.py trades <days> <since YYYY-MM-DD> <symbol>
        list_trades(argv[3], int(argv[1]), argv[2])
        return
    if argv[0] == "mr_variants":  # run_screen.py mr_variants <days> <unused> <symbols...>
        compare_mr_variants(argv[3:], int(argv[1]))
        return
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


def compare_mr_variants(symbols: list[str], days: int) -> None:
    """Mean reversion with the live 6-bar cooldown, holding overnight (live) vs. selling before
    the close, and with at most one stop-out per symbol and day (P/L summed by exit day)."""
    from collections import defaultdict

    variants = [("live: hold overnight", "off", None),
                ("sell all before the close", "all", None),
                ("sell winners before the close", "winners", None),
                ("max 1 stop per day", "off", 1),
                ("sell all + max 1 stop", "all", 1)]
    data = {s: fetch_bars(s, AssetClass.EQUITY, Timeframe.M15, limit=10_000, days=days) for s in symbols}
    print(f"Mean reversion variants, 15Min, last {days} days, {len(symbols)} markets "
          f"(cooldown 6 bars after a stop in all variants; buying power not modelled)\n")
    rows, per_sym = [], defaultdict(dict)
    for name, eod, max_stops in variants:
        by_day: dict = defaultdict(float)
        trades, gap_loss = [], 0.0
        for sym, bars in data.items():
            res = backtest_symbol(sym, "mean_reversion", bars, eod_mode=eod, bar_minutes=15,
                                  cooldown_bars=6, max_stops_per_day=max_stops)
            closed = res.closed_trades
            per_sym[sym][name] = sum(t.pnl for t in closed)
            for t in closed:
                by_day[t.exit_time[:10]] += t.pnl
                trades.append(t)
                if t.reason_out == "hard stop hit" and t.exit_time[:10] != t.entry_time[:10]:
                    gap_loss += t.pnl
        curve = peak = dd = 0.0
        for d in sorted(by_day):
            curve += by_day[d]
            peak = max(peak, curve)
            dd = max(dd, peak - curve)
        half = sorted(by_day)[len(by_day) // 2] if by_day else ""
        first = sum(v for d, v in by_day.items() if d < half)
        worst = ", ".join(f"{d[5:]} {v:+.0f}" for d, v in sorted(by_day.items(), key=lambda kv: kv[1])[:3])
        wins = sum(t.pnl > 0 for t in trades)
        stops = sum(t.reason_out == "hard stop hit" for t in trades)
        rows.append((name, sum(by_day.values()), first, sum(by_day.values()) - first, len(trades),
                     wins / len(trades) * 100 if trades else 0, stops, gap_loss, dd, worst))
    print("| Variante | P/L | 1. / 2. Hälfte | Trades | Treffer | Stops | davon über Nacht | max. Rückgang | schlechteste Tage |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name, pnl, a, b, n, wr, st, gap, dd, worst in rows:
        print(f"| {name} | {pnl:+.0f} $ | {a:+.0f} / {b:+.0f} $ | {n} | {wr:.0f} % | {st} | {gap:+.0f} $ | "
              f"{dd:.0f} $ | {worst} |")
    print("\nPer market (P/L $):")
    print("| Markt | " + " | ".join(v[0] for v in variants) + " |")
    print("|---|" + "---|" * len(variants))
    for sym, d in per_sym.items():
        print(f"| {sym} | " + " | ".join(f"{d[v[0]]:+.0f}" for v in variants) + " |")


if __name__ == "__main__":
    main(sys.argv[1:])
