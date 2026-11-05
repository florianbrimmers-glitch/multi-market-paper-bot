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
    if argv[0] == "year":  # run_screen.py year <YYYY> <unused> <mean-reversion symbols...>
        compare_year(int(argv[1]), argv[3:])
        return
    if argv[0] == "core":  # run_screen.py core <years,> <core shares,> <mean-reversion symbols...>
        compare_core([int(y) for y in argv[1].split(",")], [float(x) for x in argv[2].split(",")], argv[3:])
        return
    if argv[0] == "leverage":  # run_screen.py leverage <days> <caps,> <symbols...>
        compare_leverage(argv[3:], int(argv[1]), argv[2].split(","))
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


def compare_leverage(symbols: list[str], days: int, caps: list[str]) -> None:
    """All mean-reversion markets in one account, with total exposure capped at x times equity.
    A cap 'x/y@m' allows x all day but only y for entries in the last m minutes of the session."""
    import config
    from engine.portfolio_backtest import backtest_portfolio

    group_of = {i.symbol: i.correlation_group for i in config.INSTRUMENTS}
    data = {s: fetch_bars(s, AssetClass.EQUITY, Timeframe.M15, limit=10_000, days=days) for s in symbols}
    print(f"Mean reversion, {len(symbols)} markets in one account, 15Min, last {days} days "
          f"(risk/position rules as live, half size for group partners, cooldown 6 bars)\n")
    print("| Obergrenze Gesamtposition | Ergebnis | 1. / 2. Hälfte | Trades | kleiner gekauft | "
          "größter Rückgang | Hebel über Nacht Ø / max | Nächte über 2x | schlechteste Tage |")
    print("|---|---|---|---|---|---|---|---|---|")
    for spec in caps:
        base, _, late = spec.partition("/")
        late_cap, _, minutes = late.partition("@")
        r = backtest_portfolio(data, float(base), group_of=group_of,
                               late_exposure=float(late_cap) if late_cap else None,
                               late_minutes=float(minutes or 120))
        eq = list(r.equity_by_day.items())
        if not eq:
            continue
        end = eq[-1][1]
        mid = eq[len(eq) // 2][1]
        peak = dd = 0.0
        prev = 100_000.0
        daily = []
        for d, v in eq:
            peak = max(peak, v)
            dd = max(dd, (peak - v) / peak * 100)
            daily.append((v - prev, d))
            prev = v
        worst = ", ".join(f"{d[5:]} {v:+.0f}" for v, d in sorted(daily)[:3])
        ov = list(r.overnight_exposure.values()) or [0.0]
        label = f"{base}x" + (f", letzte {minutes or 120} Min. {late_cap}x" if late_cap else "")
        print(f"| {label} | {end - 100_000:+.0f} $ ({(end / 1000 - 100):+.1f} %) | {mid - 100_000:+.0f} / "
              f"{end - mid:+.0f} $ | {len(r.trades)} | {r.smaller_entries} | {dd:.1f} % | "
              f"{sum(ov) / len(ov):.1f} / {max(ov):.1f} | {sum(v > 2 for v in ov)} von {len(ov)} | {worst} |")


def compare_year(year: int, symbols: list[str]) -> None:
    """The live strategies over one calendar year vs. MSCI World (URTH) at 1x and 2x daily leverage.
    Mean reversion: all markets in one account (4x intraday, 2x for last-hour entries). Earnings
    drift: the study's events, 5 % of equity each (0.15 % risk / 3 % stop), at most 10 open,
    10 days or the stop. Both on 100,000 $, P/L added (shared buying power not modelled)."""
    import json
    import os
    import tempfile
    from collections import defaultdict
    from datetime import date, datetime, timedelta, timezone

    import config
    import run_earnings_study
    from data.universe import daily_bars
    from engine.portfolio_backtest import backtest_portfolio

    start, end = date(year, 1, 1), date(year, 12, 31)
    end_dt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    lookback = (datetime.now(timezone.utc) - datetime(year - 1, 12, 1, tzinfo=timezone.utc)).days
    months = [f"{year}-{m:02d}" for m in range(1, 13)]

    # Mean reversion
    group_of = {i.symbol: i.correlation_group for i in config.INSTRUMENTS}
    data = {s: fetch_bars(s, AssetClass.EQUITY, Timeframe.M15, limit=60_000,
                          days=(end_dt - datetime(year - 1, 12, 1, tzinfo=timezone.utc)).days, end=end_dt)
            for s in symbols}
    mr = backtest_portfolio(data, 4.0, group_of=group_of, late_exposure=config.overnight_exposure_cap(),
                            late_minutes=config.late_entry_min())
    mr_eq = {d: v for d, v in mr.equity_by_day.items() if start.isoformat() <= d <= end.isoformat()}
    first = min(mr_eq)
    # December of the year before warms the indicators; the year starts from that equity
    base_mr = max(((d, v) for d, v in mr.equity_by_day.items() if d < first), default=("", 100_000.0))[1]
    mr_month = {}
    for d in sorted(mr_eq):
        mr_month[d[:7]] = mr_eq[d] - base_mr

    # Earnings drift
    os.environ.pop("SEC_USER_AGENT", None)
    out = os.path.join(tempfile.gettempdir(), "drift_events.json")
    os.environ["DRIFT_EVENTS_OUT"] = out
    run_earnings_study.main([str(lookback), "6", "0.75", "mittel,klein"])
    events = sorted((e for e in json.load(open(out)) if str(start) <= e["day"][:10] <= str(end)),
                    key=lambda e: e["day"])
    open_until: list[date] = []
    drift_pl = defaultdict(float)
    taken = 0
    for e in events:
        d = date.fromisoformat(e["day"][:10])
        open_until = [x for x in open_until if x > d]
        if len(open_until) >= 10:
            continue
        exit_day = d + timedelta(days=14)  # 10 trading days
        open_until.append(exit_day)
        taken += 1
        drift_pl[min(exit_day, end).isoformat()[:7]] += 100_000 * 0.05 * e["ret"] / 100
    cum, drift_month = 0.0, {}
    for m in months:
        cum += drift_pl.get(m, 0.0)
        drift_month[m] = cum

    # MSCI World (iShares MSCI World ETF, USD), daily-reset leverage; 2x pays interest on the
    # borrowed half (about 4.3 % a year in 2025) plus ~0.5 % fees
    urth = [b for b in daily_bars(["URTH"], lookback).get("URTH", [])]
    closes = [(b.timestamp.date(), b.close) for b in urth if b.timestamp.date() <= end]
    prev = max((c for c in closes if c[0] < start), key=lambda c: c[0])
    days_ = [c for c in closes if c[0] >= start]
    eq1 = eq2 = eq2n = 100_000.0
    last = prev[1]
    w1, w2, w2n = {}, {}, {}
    for d, c in days_:
        r = c / last - 1
        last = c
        eq1 *= 1 + r
        eq2 *= 1 + 2 * r
        eq2n *= 1 + 2 * r - (0.043 + 0.005) / 252
        w1[d.isoformat()[:7]], w2[d.isoformat()[:7]], w2n[d.isoformat()[:7]] = eq1, eq2, eq2n

    def dd(curve):
        peak, worst = 100_000.0, 0.0
        for v in curve:
            peak = max(peak, v)
            worst = max(worst, (peak - v) / peak * 100)
        return worst

    print(f"\n{year}: strategies vs. MSCI World (100,000 $ start, USD)\n")
    print("| Monat | Mean Reversion | Earnings Drift | Bot zusammen | MSCI World 1x | MSCI World 2x | 2x nach Zinsen |")
    print("|---|---|---|---|---|---|---|")
    for m in months:
        if m not in w1:
            continue
        mrv = mr_month.get(m, max((v for k, v in mr_month.items() if k <= m), default=0.0))
        print(f"| {m} | {mrv:+,.0f} $ | {drift_month[m]:+,.0f} $ | {mrv + drift_month[m]:+,.0f} $ | "
              f"{w1[m] - 1e5:+,.0f} $ | {w2[m] - 1e5:+,.0f} $ | {w2n[m] - 1e5:+,.0f} $ |")
    mr_curve = [100_000 + (v - base_mr) for _, v in sorted(mr_eq.items())]
    tot_mr = list(mr_month.values())[-1]
    tot_dr = drift_month[months[-1]]
    print(f"\nJahr: Mean Reversion {tot_mr / 1000:+.1f} %, Earnings Drift {tot_dr / 1000:+.1f} % "
          f"({taken} von {len(events)} Signalen gekauft), Bot zusammen {(tot_mr + tot_dr) / 1000:+.1f} %")
    print(f"MSCI World 1x {eq1 / 1000 - 100:+.1f} %, 2x {eq2 / 1000 - 100:+.1f} %, "
          f"2x nach Zinsen/Gebühren {eq2n / 1000 - 100:+.1f} %")
    print(f"Größter Rückgang: Mean Reversion {dd(mr_curve):.1f} %, MSCI World 1x "
          f"{dd([100_000 * c / prev[1] for _, c in days_]):.1f} %, MSCI World 2x "
          f"{dd(list(_lev_curve(prev[1], days_, 2))):.1f} %")
    print(f"Mean Reversion: {len([t for t in mr.trades if t.entry_time[:4] == str(year)])} Trades, "
          f"Hebel über Nacht max {max(v for d, v in mr.overnight_exposure.items() if d[:4] == str(year)):.1f}x")


# Average Fed funds rate per year + 2 % broker spread: interest on a negative cash balance
MARGIN_RATE = {2022: 0.017 + 0.02, 2023: 0.050 + 0.02, 2024: 0.051 + 0.02, 2025: 0.042 + 0.02}


def compare_core(years: list[int], shares: list[float], symbols: list[str]) -> None:
    """Mean reversion in one account plus a permanent MSCI World holding (URTH) of x times equity,
    rebalanced daily (1.0 = the same as half the account in a 2x ETF), interest on borrowed cash;
    the holding counts against the 4x intraday / 2x last-hour caps."""
    from datetime import datetime, timezone

    import config
    from data.universe import daily_bars
    from engine.portfolio_backtest import backtest_portfolio

    group_of = {i.symbol: i.correlation_group for i in config.INSTRUMENTS}
    lookback = (datetime.now(timezone.utc) - datetime(min(years) - 1, 12, 1, tzinfo=timezone.utc)).days
    urth = {b.timestamp.date(): b.close for b in daily_bars(["URTH"], lookback).get("URTH", [])}
    rows = {}
    for year in years:
        end_dt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        data = {s: fetch_bars(s, AssetClass.EQUITY, Timeframe.M15, limit=60_000,
                              days=(end_dt - datetime(year - 1, 12, 1, tzinfo=timezone.utc)).days, end=end_dt)
                for s in symbols}
        for share in shares:
            r = backtest_portfolio(data, 4.0, group_of=group_of, late_exposure=config.overnight_exposure_cap(),
                                   late_minutes=config.late_entry_min(), core_closes=urth, core_pct=share,
                                   margin_rate=MARGIN_RATE.get(year, 0.06))
            eq = sorted(r.equity_by_day.items())
            base = max(((d, v) for d, v in eq if d < f"{year}-01-01"), default=("", 100_000.0))[1]
            curve = [v for d, v in eq if d[:4] == str(year)]
            peak, dd = base, 0.0
            for v in curve:
                peak = max(peak, v)
                dd = max(dd, (peak - v) / peak * 100)
            mr_trades = len([t for t in r.trades if t.entry_time[:4] == str(year)])
            ov = [v for d, v in r.overnight_exposure.items() if d[:4] == str(year)] or [0.0]
            rows[(year, share)] = (curve[-1] / base - 1) * 100, dd, mr_trades, max(ov), r.smaller_entries
            print(f"{year} MSCI-Anteil {share:.1f}x: {rows[(year, share)][0]:+.1f} %, Rückgang {dd:.1f} %, "
                  f"{mr_trades} MR-Trades, Hebel über Nacht max {max(ov):.1f}x, {r.smaller_entries} kleiner gekauft")
    print("\n| MSCI-World-Anteil | " + " | ".join(str(y) for y in years) + " | zusammen | pro Jahr | "
          "schlimmster Rückgang | MR-Trades |")
    print("|---|" + "---|" * (len(years) + 4))
    for share in shares:
        total = 1.0
        for y in years:
            total *= 1 + rows[(y, share)][0] / 100
        per_year = total ** (1 / len(years)) - 1
        label = "nur Mean Reversion" if share == 0 else f"{share:.1f}x Konto (= {share * 50:.0f} % in 2x-ETF)"
        print(f"| {label} | " + " | ".join(f"{rows[(y, share)][0]:+.1f} %" for y in years) +
              f" | {(total - 1) * 100:+.0f} % | {per_year * 100:+.1f} % | "
              f"{max(rows[(y, share)][1] for y in years):.1f} % | {sum(rows[(y, share)][2] for y in years)} |")


def _lev_curve(start_price: float, days_: list, lev: float):
    eq, last = 100_000.0, start_price
    for _, c in days_:
        eq *= 1 + lev * (c / last - 1)
        last = c
        yield eq


if __name__ == "__main__":
    main(sys.argv[1:])
