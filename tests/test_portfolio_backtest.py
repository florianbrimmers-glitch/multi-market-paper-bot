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
