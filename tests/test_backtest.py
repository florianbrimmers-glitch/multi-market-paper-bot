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
