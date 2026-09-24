from __future__ import annotations

import pytest

from models import Action
from strategies import build_strategy
from tests import fixtures as fx


def test_mean_reversion_enters_on_deep_drop():
    sig = build_strategy("mean_reversion").generate_signal("SPY", fx.flat_then_drop(), False)
    assert sig.action == Action.ENTER_LONG


def test_mean_reversion_holds_without_stretch():
    assert build_strategy("mean_reversion").generate_signal("SPY", fx.flat_series(), False).action == Action.HOLD


def test_mean_reversion_exits_after_reversion():
    assert build_strategy("mean_reversion").generate_signal("SPY", fx.flat_series(), True).action == Action.EXIT


def test_momentum_breakout_enters_on_volume_breakout():
    sig = build_strategy("momentum_breakout").generate_signal("BTC/USD", fx.breakout_with_volume(), False)
    assert sig.action == Action.ENTER_LONG


def test_momentum_breakout_rejects_low_volume_fakeout():
    sig = build_strategy("momentum_breakout").generate_signal("BTC/USD", fx.breakout_no_volume(), False)
    assert sig.action == Action.HOLD
    assert "fakeout" in sig.reason


def test_momentum_breakout_exits_on_channel_low_break():
    bars = fx.flat_series(20) + [fx.bar(95.0, 20, high=96.0, low=94.0)]
    assert build_strategy("momentum_breakout").generate_signal("BTC/USD", bars, True).action == Action.EXIT


def test_trend_following_enters_in_uptrend():
    assert build_strategy("trend_following").generate_signal("GLD", fx.uptrend(), False).action == Action.ENTER_LONG


def test_trend_following_exits_in_downtrend():
    assert build_strategy("trend_following").generate_signal("GLD", fx.downtrend(), True).action == Action.EXIT


@pytest.mark.parametrize("name", ["mean_reversion", "momentum_breakout", "trend_following"])
def test_insufficient_history_is_hold(name):
    assert build_strategy(name).generate_signal("X", fx.flat_series(n=3), False).action == Action.HOLD


def test_unknown_strategy_raises():
    with pytest.raises(ValueError):
        build_strategy("does_not_exist")
