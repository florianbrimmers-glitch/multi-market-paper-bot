from __future__ import annotations

from engine import backtest_symbol
from models import Bar
from tests import fixtures as fx


def test_backtest_runs_end_to_end():
    bars = fx.uptrend(120)
    res = backtest_symbol("GLD", "trend_following", bars)
    assert len(res.equity_curve) == len(bars)
    assert res.ending_equity > 0
    assert "GLD" in res.summary()


def test_backtest_records_a_closed_trade():
    up = fx.uptrend(80)
    top = up[-1].close
    down = [fx.bar(top - i, 80 + i, high=top - i + 0.5, low=top - i - 0.5) for i in range(1, 60)]
    res = backtest_symbol("GLD", "trend_following", up + down)
    assert res.num_trades >= 1
    assert 0.0 <= res.win_rate <= 100.0


def test_hard_stop_limits_loss_on_crash():
    bars = fx.breakout_with_volume()
    last = bars[-1]
    crash = Bar(timestamp=last.timestamp, open=last.close, high=last.close,
                low=last.close * 0.5, close=last.close * 0.5, volume=1000.0)
    res = backtest_symbol("BTC/USD", "momentum_breakout", bars + [crash], crypto=True)
    assert res.trades and res.trades[0].reason_out == "hard stop hit"
    # Filled at the stop (<=1% below entry) and risk-sized: the account loss stays small.
    assert res.total_return_pct > -1.0


def test_stop_fills_at_the_open_when_price_gaps_through():
    from broker.mock import MockBroker
    from models import Order, OrderType, Side
    b = MockBroker()
    b.set_price("USO", 147.47)
    b.submit_order(Order(symbol="USO", side=Side.BUY, qty=168))
    b.submit_order(Order(symbol="USO", side=Side.SELL, qty=168, type=OrderType.STOP, stop_price=145.95))
    hit = b.trigger_stops("USO", bar_low=145.0, bar_open=145.45)  # opened below the stop
    assert hit[0].filled_price == 145.45


def test_backtest_eod_flat_closes_before_the_session_ends():
    from datetime import datetime, timedelta, timezone

    from engine import backtest_symbol
    from models import Bar
    # 15-minute bars over two sessions (EDT): a flat day, a drop mid-afternoon, no recovery.
    start = datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc)
    bars = []
    for day in range(2):
        for k in range(26):
            t = start + timedelta(days=day, minutes=15 * k)
            px = 100.0 if (day == 0 or k < 20) else 97.0
            bars.append(Bar(timestamp=t, open=px, high=px + 0.1, low=px - 0.1, close=px, volume=1000))
    held = backtest_symbol("SPY", "mean_reversion", bars, eod_mode="off", bar_minutes=15)
    flat = backtest_symbol("SPY", "mean_reversion", bars, eod_mode="all", bar_minutes=15)
    assert flat.trades and all(t.closed for t in flat.trades)
    assert flat.trades[-1].reason_out == "eod flat"
    assert not held.trades[-1].closed  # held overnight
