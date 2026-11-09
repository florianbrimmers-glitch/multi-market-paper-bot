from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.portfolio_backtest import backtest_portfolio
from models import Bar


def _session(drop_to: float) -> list[Bar]:
    """15-min bars of one session (2024-01-02, 14:30–20:45 UTC): flat, then a drop late in the day."""
    t0 = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
    out = []
    for i in range(26):
        c = 100.0 if i < 20 else drop_to
        out.append(Bar(timestamp=t0 + timedelta(minutes=15 * i), open=c, high=c + 0.1, low=c - 0.1,
                       close=c, volume=1000.0))
    return out


def test_exposure_cap_limits_the_total_position():
    data = {s: _session(98.0) for s in ("AAA", "BBB", "CCC", "DDD")}
    free = backtest_portfolio(data, 4.0)
    capped = backtest_portfolio(data, 1.0)
    assert free.trades and capped.trades
    gross_free = sum(t.qty * t.entry_price for t in free.trades)
    gross_capped = sum(t.qty * t.entry_price for t in capped.trades)
    assert gross_capped <= 100_000 * 1.0 + 1e-6 < gross_free
    assert capped.smaller_entries >= 1
    assert max(capped.overnight_exposure.values()) <= 1.0 + 1e-6


def test_core_holding_follows_the_index_and_counts_against_the_cap():
    from datetime import date, timedelta
    day1 = _session(100.0)  # flat: no mean-reversion trades
    day2 = [b.model_copy(update={"timestamp": b.timestamp + timedelta(days=1)}) for b in day1]
    day3 = [b.model_copy(update={"timestamp": b.timestamp + timedelta(days=2)}) for b in day1]
    closes = {date(2024, 1, 2): 50.0, date(2024, 1, 3): 55.0, date(2024, 1, 4): 55.0}
    r = backtest_portfolio({"AAA": day1 + day2 + day3}, 4.0, core_closes=closes, core_pct=1.0)
    eq = list(r.equity_by_day.values())
    assert round(eq[-1]) == 110_000  # 100 % of equity in the index, which rose 10 %
    assert max(r.overnight_exposure.values()) >= 0.99


def test_entry_limit_per_check():
    data = {s: _session(98.0) for s in ("AAA", "BBB", "CCC", "DDD")}
    assert len(backtest_portfolio(data, 4.0).trades) > 2
    from collections import Counter
    per_tick = Counter(t.entry_time for t in backtest_portfolio(data, 4.0, max_entries_per_tick=2).trades)
    assert max(per_tick.values()) == 2
    assert len(backtest_portfolio(data, 4.0, max_entries_per_day=2).trades) == 2


def test_slippage_makes_every_round_trip_cost_money():
    data = {s: _session(98.0) for s in ("AAA", "BBB")}
    free = backtest_portfolio(data, 4.0)
    costly = backtest_portfolio(data, 4.0, slippage_bps=5)
    assert len(free.trades) == len(costly.trades) > 0
    for a, b in zip(free.trades, costly.trades):
        assert round(b.entry_price / a.entry_price, 6) == 1.0005  # bought 5 bps above the bar close
    assert list(costly.equity_by_day.values())[-1] < list(free.equity_by_day.values())[-1]
